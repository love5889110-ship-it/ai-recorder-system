#!/usr/bin/env python3
"""
微信群解读 Web 界面
启动：python3 web_app.py
访问：http://localhost:5678
"""

import json
import os
import queue
import schedule
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

import sys
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
import config
import knowledge as kb

app = Flask(__name__)

# 任务队列：task_id -> queue.Queue（存放 SSE 消息）
_task_queues: dict[str, queue.Queue] = {}
# 任务结果：task_id -> {"status": ..., "report": ...}
_task_results: dict[str, dict] = {}

# 定时配置文件路径
SCHEDULE_CONFIG = BASE_DIR / "schedule_config.json"


# ──────────────────────────────────────────────
# 定时任务
# ──────────────────────────────────────────────

def load_schedule_config() -> dict:
    if SCHEDULE_CONFIG.exists():
        try:
            return json.loads(SCHEDULE_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False, "time": "08:00", "profiles": []}


def save_schedule_config(cfg: dict):
    SCHEDULE_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def run_scheduled_job():
    """定时触发：对所有启用的 profile 跑昨天的报告"""
    cfg = load_schedule_config()
    if not cfg.get("enabled"):
        return
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    profiles = cfg.get("profiles") or [p["_file"] for p in list_profiles()]
    print(f"[定时任务] {datetime.now().strftime('%H:%M:%S')} 开始执行，日期={yesterday}，共 {len(profiles)} 个群", flush=True)
    for pf in profiles:
        task_id = str(uuid.uuid4())[:8]
        _task_queues[task_id] = queue.Queue()
        _task_results[task_id] = {"status": "running"}
        t = threading.Thread(target=run_task, args=(task_id, pf, yesterday), daemon=True)
        t.start()
        print(f"[定时任务] 已启动 {pf} → task_id={task_id}", flush=True)


def apply_schedule(cfg: dict):
    """清除旧定时任务，按配置重新注册"""
    schedule.clear("digest")
    if cfg.get("enabled") and cfg.get("time"):
        schedule.every().day.at(cfg["time"]).do(run_scheduled_job).tag("digest")
        print(f"[定时任务] 已注册每天 {cfg['time']} 执行", flush=True)
    else:
        print("[定时任务] 已关闭", flush=True)


def schedule_loop():
    """后台线程：每分钟检查是否有到期任务"""
    while True:
        schedule.run_pending()
        time.sleep(30)


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────

def list_profiles() -> list[dict]:
    profiles_dir = BASE_DIR / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    result = []
    for f in sorted(profiles_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_file"] = f.name
            result.append(data)
        except Exception:
            pass
    return result


def list_reports() -> list[dict]:
    """扫描所有 profile 的输出目录，收集已生成的报告"""
    reports = {}
    for profile in list_profiles():
        out_dir = Path(os.path.expanduser(profile.get("output_dir", config.OUTPUT_DIR)))
        if out_dir.exists():
            for md in out_dir.glob("*.md"):
                date_str = md.stem
                if date_str not in reports:
                    reports[date_str] = {
                        "date": date_str,
                        "path": str(md),
                        "group": profile.get("group_name", ""),
                        "profile_name": profile.get("name", ""),
                    }
    return sorted(reports.values(), key=lambda x: x["date"], reverse=True)


def run_task(task_id: str, profile_file: str, target_date: str):
    """后台线程：执行完整的截图→OCR→AI解读流程"""
    q = _task_queues[task_id]

    def send(msg: str, type_: str = "log"):
        q.put({"type": type_, "data": msg})

    try:
        import wechat_digest as wd
        wd.reset_cancel()
        wd.set_log_callback(lambda msg: send(msg, "log"))

        send(f"[任务开始] profile={profile_file}, date={target_date}", "log")

        # 补全 profiles/ 前缀
        if not profile_file.startswith("/") and not profile_file.startswith("profiles/"):
            profile_file = f"profiles/{profile_file}"

        profile = wd.load_profile(profile_file)
        send(f"已加载配置：{profile.get('name', profile_file)}", "log")

        group_name = profile.get("group_name", config.WECHAT_GROUP_NAME)

        lines = wd.open_wechat_group(group_name, target_date=target_date)
        send(f"OCR 完成，共 {len(lines)} 行", "log")

        # 硬停止检查：截图结束后不继续分析
        if wd._hard_cancel:
            send("[立即终止] 任务已终止，不生成报告", "log")
            _task_results[task_id] = {"status": "cancelled"}
            send("error", "done")
            return

        digest = wd.generate_digest(lines, target_date, profile=profile)

        filepath = wd.save_digest(digest, target_date, profile=profile)
        send(f"已保存：{filepath}", "log")

        try:
            send("[知识归并] 开始提取实体，归并知识库...", "log")
            from extract import merge_report
            stats = merge_report(
                target_date, group_name, digest,
                log_fn=lambda m: send(m, "log")
            )
            send(f"[知识归并] 完成：新增 {stats['new_entities']} 个实体，更新 {stats['updated_entities']} 个", "log")
        except Exception as e:
            send(f"[知识归并] 跳过（{e}）", "log")

        send(digest, "report")
        _task_results[task_id] = {"status": "done", "report": digest, "filepath": str(filepath)}
        send("done", "done")

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        send(f"[错误] {e}\n{err}", "error")
        _task_results[task_id] = {"status": "error", "error": str(e)}
        send("error", "done")


# ──────────────────────────────────────────────
# 路由
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/profiles", methods=["GET"])
def api_list_profiles():
    return jsonify(list_profiles())


@app.route("/api/profiles", methods=["POST"])
def api_save_profile():
    data = request.json
    if not data or not data.get("_file"):
        return jsonify({"error": "缺少 _file 字段"}), 400
    filename = data.pop("_file")
    safe = "".join(c for c in filename if c.isalnum() or c in "_-.")
    if not safe.endswith(".json"):
        safe += ".json"
    profiles_dir = BASE_DIR / "profiles"
    profiles_dir.mkdir(exist_ok=True)
    path = profiles_dir / safe
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "file": safe})


@app.route("/api/profiles/<filename>", methods=["DELETE"])
def api_delete_profile(filename):
    path = BASE_DIR / "profiles" / filename
    if path.exists():
        path.unlink()
    return jsonify({"ok": True})


@app.route("/api/reports", methods=["GET"])
def api_list_reports():
    return jsonify(list_reports())


@app.route("/api/reports/<date>", methods=["GET"])
def api_get_report(date):
    reports = {r["date"]: r for r in list_reports()}
    if date not in reports:
        return jsonify({"error": "报告不存在"}), 404
    content = Path(reports[date]["path"]).read_text(encoding="utf-8")
    return jsonify({"date": date, "content": content, **reports[date]})


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.json or {}
    profile_file = data.get("profile", "profiles/xiaohu_vip.json")
    target_date = data.get("date")
    if not target_date:
        yesterday = datetime.now() - timedelta(days=1)
        target_date = yesterday.strftime("%Y-%m-%d")

    task_id = str(uuid.uuid4())[:8]
    _task_queues[task_id] = queue.Queue()
    _task_results[task_id] = {"status": "running"}

    t = threading.Thread(target=run_task, args=(task_id, profile_file, target_date), daemon=True)
    t.start()

    return jsonify({"task_id": task_id, "date": target_date})


@app.route("/api/stream/<task_id>")
def api_stream(task_id):
    if task_id not in _task_queues:
        return jsonify({"error": "任务不存在"}), 404

    def event_stream():
        q = _task_queues[task_id]
        while True:
            try:
                msg = q.get(timeout=60)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                yield "data: {\"type\":\"heartbeat\"}\n\n"

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────
# 定时任务 API
# ──────────────────────────────────────────────

@app.route("/api/cancel/<task_id>", methods=["POST"])
def api_cancel(task_id):
    import wechat_digest as wd
    mode = request.args.get("mode", "soft")
    if mode == "hard":
        wd.request_hard_cancel()
        msg = "[立即终止] 已发送终止信号，当前截图完成后立即停止..."
    else:
        wd.request_cancel()
        msg = "[停止并分析] 正在停止截图，已截内容将继续分析..."
    q = _task_queues.get(task_id)
    if q:
        q.put({"type": "log", "data": msg})
    return jsonify({"ok": True})


@app.route("/api/schedule", methods=["GET"])
def api_get_schedule():
    cfg = load_schedule_config()
    # 计算下次执行时间
    next_run = None
    jobs = schedule.get_jobs("digest")
    if jobs:
        next_run = jobs[0].next_run.strftime("%Y-%m-%d %H:%M") if jobs[0].next_run else None
    return jsonify({**cfg, "next_run": next_run})


@app.route("/api/schedule", methods=["POST"])
def api_save_schedule():
    cfg = request.json or {}
    # 只允许合法字段
    saved = {
        "enabled": bool(cfg.get("enabled", False)),
        "time": cfg.get("time", "08:00"),
        "profiles": cfg.get("profiles", []),
    }
    save_schedule_config(saved)
    apply_schedule(saved)
    return jsonify({"ok": True})


# ──────────────────────────────────────────────
# 知识图谱 API
# ──────────────────────────────────────────────

@app.route("/api/graph")
def api_graph():
    limit = request.args.get("limit", 200, type=int)
    return jsonify(kb.get_graph(limit=limit))


@app.route("/api/entities")
def api_list_entities():
    type_ = request.args.get("type")
    limit = request.args.get("limit", 100, type=int)
    q = request.args.get("q", "").strip()
    if q:
        entities = kb.search_entities(q, limit=limit)
    else:
        entities = kb.list_entities(type_=type_ or None, limit=limit)
    return jsonify(entities)


@app.route("/api/entities/<int:entity_id>")
def api_get_entity(entity_id):
    entity = kb.get_entity(entity_id)
    if not entity:
        return jsonify({"error": "实体不存在"}), 404
    relations = kb.get_relations(entity_id)
    mentions = kb.get_mentions(entity_id)
    return jsonify({"entity": entity, "relations": relations, "mentions": mentions})


@app.route("/api/merge/<date>", methods=["POST"])
def api_trigger_merge(date):
    from extract import merge_report
    reports = {r["date"]: r for r in list_reports()}
    if date not in reports:
        return jsonify({"error": "报告不存在"}), 404
    content = Path(reports[date]["path"]).read_text(encoding="utf-8")
    group_name = reports[date].get("group", "")
    try:
        stats = merge_report(date, group_name, content)
        return jsonify({"ok": True, **stats})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/obsidian", methods=["POST"])
def api_export_obsidian():
    data = request.json or {}
    vault_dir = data.get("vault_dir", "~/Documents/Obsidian Vault")
    try:
        count = kb.export_to_obsidian(vault_dir)
        return jsonify({"ok": True, "count": count, "path": vault_dir + "/AI知识图谱"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# 对话记录 API
# ──────────────────────────────────────────────

UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


def run_nightly_batch():
    """凌晨批处理：处理所有待转录音频 + 微信群定时任务"""
    print(f"[凌晨批处理] {datetime.now().strftime('%H:%M:%S')} 开始", flush=True)
    try:
        from conversation import process_pending_uploads
        results = process_pending_uploads(str(UPLOADS_DIR))
        print(f"[凌晨批处理] 对话音频处理完成，共 {len(results)} 个", flush=True)
    except Exception as e:
        print(f"[凌晨批处理] 对话处理出错：{e}", flush=True)
    # 触发微信群定时任务
    run_scheduled_job()


@app.route("/api/conversations", methods=["GET"])
def api_list_conversations():
    source = request.args.get("source")
    contact_id = request.args.get("contact_id", type=int)
    project = request.args.get("project", "").strip()
    tag = request.args.get("tag", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    limit = request.args.get("limit", 50, type=int)

    convs = kb.list_conversations(contact_id=contact_id, limit=200)

    # 过滤
    result = []
    for c in convs:
        kp = json.loads(c.get("key_points") or "{}")
        if source and kp.get("source") != source:
            continue
        if project and project.lower() not in (kp.get("project") or "").lower():
            continue
        if tag and tag not in (kp.get("tags") or []):
            continue
        if date_from and c.get("date", "") < date_from:
            continue
        if date_to and c.get("date", "") > date_to:
            continue
        # 关联联系人信息
        contact = kb.get_contact(c["contact_id"]) if c.get("contact_id") else None
        result.append({**c, "key_points": kp, "contact": contact})
        if len(result) >= limit:
            break

    return jsonify(result)


@app.route("/api/conversations/upload", methods=["POST"])
def api_upload_conversation():
    if "file" not in request.files:
        return jsonify({"error": "缺少 file 字段"}), 400

    f = request.files["file"]
    date = request.form.get("date", datetime.now().strftime("%Y-%m-%d"))
    source = request.form.get("source", "upload")
    duration_sec = request.form.get("duration_sec", 0, type=int)

    # 保存到 uploads/YYYY-MM-DD/
    day_dir = UPLOADS_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:8]}_{f.filename}"
    save_path = day_dir / filename
    f.save(str(save_path))

    # 保存元数据（供批处理读取 source/duration）
    meta_path = save_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps({
        "source": source, "date": date, "duration_sec": duration_sec,
        "original_name": f.filename
    }, ensure_ascii=False), encoding="utf-8")

    # 上传完成后自动异步处理
    def _auto_process(path):
        try:
            from conversation import process_audio, save_and_update_contact
            import re
            parent_name = Path(path).parent.name
            d = parent_name if re.match(r'\d{4}-\d{2}-\d{2}', parent_name) else date
            conv_data = process_audio(str(path), date=d, model_size="small")
            if conv_data.get("skipped"):
                print(f"[自动处理] 转录内容过短，跳过保存: {Path(path).name}")
            else:
                save_and_update_contact(conv_data, source=source, audio_path=str(path))
            Path(path).with_suffix(".done").touch()
        except Exception as e:
            import traceback
            print(f"[自动处理] 失败: {e}\n{traceback.format_exc()}")

    threading.Thread(target=_auto_process, args=(save_path,), daemon=True).start()

    return jsonify({"ok": True, "file": filename, "path": str(save_path)})


@app.route("/api/conversations/<int:conv_id>", methods=["GET"])
def api_get_conversation(conv_id):
    conv = kb.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "不存在"}), 404
    conv["key_points"] = json.loads(conv.get("key_points") or "{}")
    contact = kb.get_contact(conv["contact_id"]) if conv.get("contact_id") else None
    return jsonify({**conv, "contact": contact})


@app.route("/api/conversations/<int:conv_id>/audio")
def api_audio_conversation(conv_id):
    """流式返回对话录音文件（供前端试听）"""
    conv = kb.get_conversation(conv_id)
    if not conv or not conv.get("audio_path"):
        return jsonify({"error": "无录音文件"}), 404
    path = conv["audio_path"]
    # 支持相对路径（相对于 BASE_DIR）和绝对路径
    p = Path(path) if Path(path).is_absolute() else BASE_DIR / path
    if not p.exists():
        return jsonify({"error": "文件不存在"}), 404
    ext = p.suffix.lower()
    mime = "audio/mp4" if ext in (".m4a", ".aac", ".mp4") else "audio/wav"
    return send_file(str(p), mimetype=mime, conditional=True)


@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def api_delete_conversation(conv_id):
    kb.delete_conversation(conv_id)
    return jsonify({"ok": True})


@app.route("/api/conversations/merge", methods=["POST"])
def api_merge_conversations():
    ids = (request.json or {}).get("ids", [])
    if len(ids) < 2:
        return jsonify({"error": "至少选择2条对话"}), 400
    try:
        new_id = kb.merge_conversations(ids)
        # 对合并后的 transcript 重跑 Claude 提炼（不重跑 Whisper）
        conv = kb.get_conversation(new_id)
        merged_transcript = conv.get('transcript', '')
        if merged_transcript.strip():
            from conversation import EXTRACT_PROMPT
            from wechat_digest import call_claude
            import re as _re
            duration = conv.get('duration_sec', 0)
            def _guess(dur, text):
                t = text.lower()
                if dur < 30: return '随手备忘'
                if any(w in t for w in ['喂', '你好，我是']): return '电话沟通'
                if dur > 900: return '内部会议'
                if dur > 300: return '商务拜访'
                return '其他'
            scene_hint = _guess(duration, merged_transcript)
            prompt = EXTRACT_PROMPT.format(
                duration=duration, scene_hint=scene_hint,
                transcript=merged_transcript[:6000])
            try:
                raw = call_claude(prompt, max_tokens=2000)
                raw = _re.sub(r'```(?:json)?\s*', '', raw)
                raw = raw.replace('\u201c', '「').replace('\u201d', '」')
                s = raw.find('{'); e = raw.rfind('}') + 1
                result = json.loads(raw[s:e]) if s != -1 and e > 0 else {}
                with kb.get_conn() as conn:
                    conn.execute(
                        "UPDATE conversations SET summary=?, key_points=?, scene=?, sentiment=?, follow_up_date=?, commitments=? WHERE id=?",
                        (result.get('summary', ''), json.dumps(result, ensure_ascii=False),
                         result.get('scene', ''), result.get('sentiment', ''),
                         result.get('follow_up_date', ''), result.get('commitments', ''),
                         new_id)
                    )
            except Exception as ex:
                print(f"[合并提炼] 失败: {ex}")
        return jsonify({"ok": True, "conv_id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/conversations/<int:conv_id>", methods=["PATCH"])
def api_patch_conversation(conv_id):
    """绑定联系人 / 更新项目标签"""
    conv = kb.get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "不存在"}), 404

    data = request.json or {}
    contact_name = data.get("contact_name", "").strip()
    company = data.get("company", "").strip()
    project = data.get("project", "")
    tags = data.get("tags", [])

    contact_id = conv.get("contact_id")
    if contact_name:
        from conversation import save_and_update_contact
        kp = json.loads(conv.get("key_points") or "{}")
        conv_data = {
            "summary": conv.get("summary", ""),
            "needs": kp.get("needs", ""),
            "pain_points": kp.get("pain_points", ""),
            "next_action": kp.get("next_action", ""),
            "role": data.get("role", kp.get("role", "")),
            "project": project or kp.get("project", ""),
            "tags": tags or kp.get("tags", []),
            "key_signals": kp.get("key_signals", ""),
            "transcript": conv.get("transcript", ""),
            "duration_sec": conv.get("duration_sec", 0),
            "date": conv.get("date", ""),
        }
        _, contact_id = save_and_update_contact(conv_data, contact_name, company)
        kb.update_conversation_contact(conv_id, contact_id)

    # 更新 key_points 中的 project/tags
    if project or tags:
        kp = json.loads(conv.get("key_points") or "{}")
        if project:
            kp["project"] = project
        if tags:
            kp["tags"] = tags
        with kb.get_conn() as conn:
            conn.execute("UPDATE conversations SET key_points=? WHERE id=?",
                         (json.dumps(kp, ensure_ascii=False), conv_id))

    return jsonify({"ok": True, "contact_id": contact_id})


@app.route("/api/conversations/process", methods=["POST"])
def api_batch_process():
    """手动触发批处理（处理 uploads/ 下所有待处理音频）"""
    task_id = str(uuid.uuid4())[:8]
    _task_queues[task_id] = queue.Queue()
    _task_results[task_id] = {"status": "running"}

    def do_batch():
        q = _task_queues[task_id]
        def send(msg):
            q.put({"type": "log", "data": msg})
        try:
            from conversation import process_pending_uploads
            results = process_pending_uploads(
                str(UPLOADS_DIR),
                log_fn=send,
                model_size=request.json.get("model_size", "small") if request.json else "small"
            )
            _task_results[task_id] = {"status": "done", "results": results}
            q.put({"type": "done", "data": "done"})
        except Exception as e:
            import traceback
            _task_results[task_id] = {"status": "error", "error": str(e)}
            q.put({"type": "error", "data": f"{e}\n{traceback.format_exc()}"})
            q.put({"type": "done", "data": "error"})

    threading.Thread(target=do_batch, daemon=True).start()
    return jsonify({"task_id": task_id})


# ──────────────────────────────────────────────
# 联系人 API
# ──────────────────────────────────────────────

@app.route("/api/contacts", methods=["GET", "POST"])
def api_list_contacts():
    if request.method == "POST":
        data = request.json or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "缺少 name"}), 400
        contact_id = kb.get_or_create_contact(name, data.get("company", ""))
        return jsonify({"ok": True, "id": contact_id})
    q = request.args.get("q", "").strip()
    company = request.args.get("company", "").strip()
    role_type = request.args.get("role_type", "").strip()
    limit = request.args.get("limit", 100, type=int)

    if q:
        contacts = kb.search_contacts(q, limit=limit)
    else:
        contacts = kb.list_contacts(company=company or None, limit=limit)

    if role_type:
        contacts = [c for c in contacts if c.get("role") == role_type]

    return jsonify(contacts)


@app.route("/api/contacts/<int:contact_id>", methods=["GET"])
def api_get_contact(contact_id):
    contact = kb.get_contact(contact_id)
    if not contact:
        return jsonify({"error": "不存在"}), 404
    convs = kb.list_conversations(contact_id=contact_id, limit=50)
    for c in convs:
        c["key_points"] = json.loads(c.get("key_points") or "{}")
    contact["tags"] = json.loads(contact.get("tags") or "[]")
    return jsonify({"contact": contact, "conversations": convs})


@app.route("/api/contacts/<int:contact_id>", methods=["PATCH"])
def api_patch_contact(contact_id):
    contact = kb.get_contact(contact_id)
    if not contact:
        return jsonify({"error": "不存在"}), 404
    data = request.json or {}
    with kb.get_conn() as conn:
        if "company" in data or "role" in data or "summary" in data:
            conn.execute(
                "UPDATE contact_profiles SET company=COALESCE(?,company), role=COALESCE(?,role), summary=COALESCE(?,summary) WHERE id=?",
                (data.get("company"), data.get("role"), data.get("summary"), contact_id)
            )
    return jsonify({"ok": True})


@app.route("/api/insights", methods=["GET"])
def api_insights():
    """跨来源洞察：高频联系人 + 未完成行动 + 项目动态"""
    # 最近30天对话
    from datetime import timedelta
    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    convs = kb.list_conversations(limit=200)
    recent = [c for c in convs if c.get("date", "") >= since]

    # 高频联系人
    contact_counts = {}
    for c in recent:
        cid = c.get("contact_id")
        if cid:
            contact_counts[cid] = contact_counts.get(cid, 0) + 1
    top_contacts = sorted(contact_counts.items(), key=lambda x: -x[1])[:5]
    top_contact_list = []
    for cid, cnt in top_contacts:
        contact = kb.get_contact(cid)
        if contact:
            top_contact_list.append({**contact, "recent_count": cnt})

    # 未完成 next_action
    pending_actions = []
    for c in recent:
        kp = json.loads(c.get("key_points") or "{}")
        action = kp.get("next_action", "").strip()
        if action:
            contact = kb.get_contact(c["contact_id"]) if c.get("contact_id") else None
            pending_actions.append({
                "action": action,
                "date": c.get("date"),
                "contact": contact.get("name") if contact else "未知",
                "conv_id": c["id"],
            })

    # 活跃项目
    projects = {}
    for c in recent:
        kp = json.loads(c.get("key_points") or "{}")
        proj = kp.get("project", "").strip()
        if proj:
            projects[proj] = projects.get(proj, 0) + 1

    return jsonify({
        "top_contacts": top_contact_list,
        "pending_actions": pending_actions[:20],
        "active_projects": sorted(projects.items(), key=lambda x: -x[1])[:10],
    })


if __name__ == "__main__":
    # 启动时加载定时配置
    _cfg = load_schedule_config()
    apply_schedule(_cfg)
    # 凌晨批处理定时任务（每天06:00）
    schedule.every().day.at("06:00").do(run_nightly_batch).tag("batch")
    # 后台线程跑 schedule
    threading.Thread(target=schedule_loop, daemon=True).start()

    print("=" * 50)
    print("微信群解读 Web 界面已启动")
    print("请在浏览器打开：http://localhost:5678")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5678, debug=False, threaded=True)
