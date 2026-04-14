"""
对话处理模块：音频处理 + 联系人画像合并

调用链：
  process_audio(audio_path) → Whisper转录 → Claude提炼 → 结构化结果
  save_and_update_contact()  → 写DB + 合并累计画像
  process_pending_uploads()  → 扫描待处理目录，批量处理（供凌晨定时任务调用）
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

import sys
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

# 忽略自签名证书（代理服务器）
import urllib3
urllib3.disable_warnings()
import requests as _requests
_orig_post = _requests.post
def _patched_post(*args, **kwargs):
    kwargs.setdefault('verify', False)
    return _orig_post(*args, **kwargs)
_requests.post = _patched_post

import config
import knowledge as kb
from wechat_digest import call_claude


EXTRACT_PROMPT = """你是商务助手。从以下对话转录中提炼关键信息，严格JSON输出，不要多余文字。
注意：JSON字符串值内不得使用双引号，如需强调词语请用【】代替。

{{
  "summary": "完整摘要，不限字数，准确描述对话核心内容",
  "scene": "商务拜访/内部会议/电话沟通/随手备忘/饭局社交/谈判签约/培训学习/家人朋友/其他",
  "participants": "涉及的人物（说话人/被提及人）",
  "needs": "对方核心需求（详细）",
  "pain_points": "痛点或顾虑（详细）",
  "next_action": "我方下一步行动（具体、可执行）",
  "commitments": "双方承诺或约定的事项（无则空字符串）",
  "role": "客户/供应商/内部/投资人/其他",
  "project": "涉及的项目或商机名称（无则空字符串）",
  "tags": ["标签1", "标签2"],
  "key_signals": "关键决策信号（预算/时间节点/决策权/竞品等）",
  "sentiment": "对方情绪倾向（积极/中立/消极/犹豫）",
  "follow_up_date": "提到的下次跟进时间（无则空字符串）"
}}

对话时长约{duration}秒，预判场景：{scene_hint}。转录内容：
{transcript}"""


def process_audio(audio_path: str, date: str = None, model_size: str = "small",
                  log_fn=None) -> dict:
    """
    转录音频并用 Claude 提炼关键信息。
    返回 dict：{transcript, summary, needs, pain_points, next_action,
                role, project, tags, key_signals, duration_sec}
    """
    def log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # 计算时长
    duration_sec = 0
    try:
        import soundfile as sf
        info = sf.info(audio_path)
        duration_sec = int(info.duration)
    except Exception:
        pass

    # Whisper 转录（带时间戳）
    log(f"[对话处理] 开始转录：{Path(audio_path).name}（时长 {duration_sec}s）")
    from voice_recorder import transcribe_with_timestamps
    segments = transcribe_with_timestamps(audio_path, model_size=model_size)
    transcript = "\n".join(seg["text"] for seg in segments)
    log(f"[对话处理] 转录完成，共 {len(transcript)} 字，{len(segments)} 段")

    MIN_TRANSCRIPT_CHARS = 40  # 少于40字视为无实质内容（路人杂音、几个字的噪音）
    transcript_len = len(transcript.strip())
    if transcript_len < MIN_TRANSCRIPT_CHARS:
        log(f"[对话处理] 转录内容过短（{transcript_len}字 < {MIN_TRANSCRIPT_CHARS}），跳过提炼")
        return {
            "transcript": transcript, "summary": "", "needs": "",
            "pain_points": "", "next_action": "", "role": "其他",
            "project": "", "tags": [], "key_signals": "",
            "scene": "", "participants": "", "commitments": "",
            "sentiment": "", "follow_up_date": "",
            "duration_sec": duration_sec, "date": date,
            "skipped": True,
        }

    # 场景预判（时长 + 关键词）
    def _guess_scene(dur: int, text: str) -> str:
        t = text.lower()
        if dur < 30 or (dur < 60 and len(t) < 200):
            return "随手备忘"
        if any(w in t for w in ["喂", "你好，我是", "打扰了", "方便说话吗"]):
            return "电话沟通"
        if dur > 900 or any(w in t for w in ["会议", "大家好", "开始我们", "下一个议题"]):
            return "内部会议"
        if dur > 300:
            return "商务拜访"
        return "其他"

    scene_hint = _guess_scene(duration_sec, transcript)

    # 构建带时间戳的转录（最多6000字）
    ts_lines = []
    char_count = 0
    for seg in segments:
        line = f"[{int(seg['start']//60):02d}:{int(seg['start'])%60:02d}] {seg['text']}"
        char_count += len(line)
        if char_count > 6000:
            break
        ts_lines.append(line)
    transcript_for_prompt = "\n".join(ts_lines)

    # Claude 提炼
    log(f"[对话处理] 调用 Claude 提炼（场景预判：{scene_hint}）...")
    prompt = EXTRACT_PROMPT.format(
        duration=duration_sec,
        scene_hint=scene_hint,
        transcript=transcript_for_prompt,
    )
    try:
        raw = call_claude(prompt, max_tokens=2000)
        # 去掉 markdown 代码块包裹
        raw = re.sub(r'```(?:json)?\s*', '', raw)
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("未找到 JSON")
        json_str = raw[start:end]
        json_str = re.sub(r',\s*([}\]])', r'\1', json_str)
        try:
            result = json.loads(json_str)
        except json.JSONDecodeError:
            # 兜底：用状态机把 JSON 字符串值内部的裸双引号改成【】
            chars = list(json_str)
            in_str = False
            esc = False
            i = 0
            while i < len(chars):
                ch = chars[i]
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    if not in_str:
                        in_str = True
                    else:
                        # 往后找第一个非空字符，判断是否是 JSON 结构符
                        j = i + 1
                        while j < len(chars) and chars[j] in ' \t\r\n':
                            j += 1
                        next_ch = chars[j] if j < len(chars) else ''
                        if next_ch in ':,}]':
                            in_str = False  # 正常结束
                        else:
                            chars[i] = '【'  # 内嵌引号
                i += 1
            result = json.loads(''.join(chars))
    except Exception as e:
        log(f"[对话处理] Claude 提炼失败：{e}，使用默认值")
        result = {
            "summary": transcript[:200], "needs": "", "pain_points": "",
            "next_action": "", "role": "其他", "project": "",
            "tags": [], "key_signals": "",
            "scene": scene_hint, "participants": "", "commitments": "",
            "sentiment": "", "follow_up_date": "",
        }

    result["transcript"] = transcript
    result["duration_sec"] = duration_sec
    result["date"] = date
    # 确保新字段有默认值
    for f in ("scene", "participants", "commitments", "sentiment", "follow_up_date"):
        result.setdefault(f, "")
    log(f"[对话处理] 提炼完成：[{result.get('scene', '')}] {result.get('summary', '')[:80]}")
    return result


def save_and_update_contact(conv_data: dict, contact_name: str = "",
                             company: str = "", source: str = "conversation",
                             audio_path: str = "") -> tuple:
    """
    保存对话记录并更新联系人画像。
    返回 (conv_id, contact_id)。contact_name 为空时 contact_id=None。
    """
    contact_id = None

    if contact_name.strip():
        contact_id = kb.get_or_create_contact(contact_name.strip(), company.strip())

        # 合并累计画像
        existing = kb.get_contact(contact_id)
        old_summary = (existing or {}).get("summary", "")
        new_info = conv_data.get("summary", "")
        if old_summary and new_info:
            from extract import merge_summary
            merged = merge_summary(contact_name, old_summary, new_info)
        else:
            merged = new_info or old_summary

        # 提取角色和标签
        role = conv_data.get("role", "")
        tags = conv_data.get("tags", [])

        kb.update_contact_after_meeting(contact_id, merged, role=role, tags=tags)

    key_points = {
        "needs": conv_data.get("needs", ""),
        "pain_points": conv_data.get("pain_points", ""),
        "next_action": conv_data.get("next_action", ""),
        "key_signals": conv_data.get("key_signals", ""),
        "role": conv_data.get("role", ""),
        "project": conv_data.get("project", ""),
        "tags": conv_data.get("tags", []),
        "scene": conv_data.get("scene", ""),
        "participants": conv_data.get("participants", ""),
        "commitments": conv_data.get("commitments", ""),
        "sentiment": conv_data.get("sentiment", ""),
        "follow_up_date": conv_data.get("follow_up_date", ""),
        "source": source,
    }

    conv_id = kb.save_conversation(
        contact_id=contact_id,
        date=conv_data.get("date", datetime.now().strftime("%Y-%m-%d")),
        transcript=conv_data.get("transcript", ""),
        summary=conv_data.get("summary", ""),
        key_points=key_points,
        duration_sec=conv_data.get("duration_sec", 0),
        audio_path=audio_path,
    )

    return conv_id, contact_id


def process_pending_uploads(upload_dir: str, log_fn=None, model_size: str = "small") -> list[dict]:
    """
    扫描待处理音频目录，批量转录+提炼，存入数据库。
    contact_id=None（待用户在网页上事后绑定联系人）。
    返回处理结果列表。
    """
    def log(msg):
        print(msg)
        if log_fn:
            log_fn(msg)

    upload_path = Path(upload_dir)
    if not upload_path.exists():
        return []

    # 支持的音频格式
    audio_files = []
    for ext in ("*.wav", "*.WAV", "*.m4a", "*.M4A", "*.mp3", "*.ogg", "*.flac", "*.aac", "*.AAC"):
        audio_files.extend(upload_path.rglob(ext))

    # 过滤已处理（旁边有同名 .done 标记文件）
    pending = [f for f in audio_files if not f.with_suffix(".done").exists()]

    if not pending:
        log("[批处理] 没有待处理的音频文件")
        return []

    log(f"[批处理] 发现 {len(pending)} 个待处理音频")
    results = []

    for audio_file in pending:
        try:
            # 尝试从目录名推断日期（格式 YYYY-MM-DD）
            parent_name = audio_file.parent.name
            if re.match(r'\d{4}-\d{2}-\d{2}', parent_name):
                date = parent_name
            else:
                date = datetime.fromtimestamp(audio_file.stat().st_mtime).strftime("%Y-%m-%d")

            log(f"[批处理] 处理：{audio_file.name}")
            conv_data = process_audio(str(audio_file), date=date,
                                       model_size=model_size, log_fn=log_fn)
            conv_id, _ = save_and_update_contact(
                conv_data, source="upload", audio_path=str(audio_file)
            )

            # 标记已处理
            audio_file.with_suffix(".done").touch()

            results.append({"file": audio_file.name, "conv_id": conv_id,
                             "summary": conv_data.get("summary", "")})
            log(f"[批处理] 完成：{audio_file.name} → conv_id={conv_id}")

        except Exception as e:
            import traceback
            log(f"[批处理] 失败：{audio_file.name} → {e}\n{traceback.format_exc()}")
            results.append({"file": audio_file.name, "error": str(e)})

    return results
