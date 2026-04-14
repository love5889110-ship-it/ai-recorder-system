#!/usr/bin/env python3
"""
微信群聊天记录自动读取 + AI 解读
方案：激活微信窗口 → 截图 → macOS Vision OCR → AI 生成摘要

依赖：
    pip install pyobjc-framework-Vision pyobjc-framework-Quartz requests

前置条件：
    系统设置 → 隐私与安全性 → 屏幕录制 → 添加 Terminal（截图需要）
"""

import sys
import time
import random
import argparse
import subprocess
import os
import tempfile
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

import requests

# 加载配置
sys.path.insert(0, str(Path(__file__).parent))
import config

# 全局 log 回调（Web模式下由 web_app.py 注入，将日志推送到 SSE）
_log_callback: Optional[Callable[[str], None]] = None
_cancel_requested: bool = False
_hard_cancel: bool = False

def set_log_callback(cb: Callable[[str], None]):
    global _log_callback
    _log_callback = cb

def request_cancel():
    global _cancel_requested
    _cancel_requested = True

def request_hard_cancel():
    global _cancel_requested, _hard_cancel
    _cancel_requested = True
    _hard_cancel = True

def reset_cancel():
    global _cancel_requested, _hard_cancel
    _cancel_requested = False
    _hard_cancel = False


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def rand_sleep(min_s=None, max_s=None):
    t = random.uniform(min_s or config.DELAY_MIN, max_s or config.DELAY_MAX)
    time.sleep(t)


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _log_callback:
        _log_callback(line)


def load_profile(profile_path: str) -> dict:
    """加载 profile JSON，路径相对于脚本目录"""
    base = Path(__file__).parent
    p = Path(profile_path)
    if not p.is_absolute():
        p = base / p
    if not p.exists():
        raise FileNotFoundError(f"Profile 文件不存在: {p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def run_apple_script(script: str) -> str:
    """执行 AppleScript，返回输出"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0 and result.stderr:
        raise RuntimeError(f"AppleScript 错误: {result.stderr.strip()}")
    return result.stdout.strip()


# ──────────────────────────────────────────────
# 截图 + Vision OCR
# ──────────────────────────────────────────────

def ocr_image(image_path: str) -> str:
    """用 macOS Vision 框架对图片做 OCR，返回识别的文本"""
    import Vision
    from Cocoa import NSURL
    from Foundation import NSDictionary

    image_url = NSURL.fileURLWithPath_(image_path)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLanguages_(["zh-Hans", "zh-Hant", "en-US"])
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        image_url, NSDictionary.dictionary()
    )

    success, error = handler.performRequests_error_([request], None)
    if not success:
        log(f"OCR 失败: {error}")
        return ""

    lines = []
    for obs in (request.results() or []):
        text, conf = obs.topCandidates_(1)[0].string(), obs.topCandidates_(1)[0].confidence()
        if conf > 0.3 and text.strip():
            lines.append(text.strip())

    return "\n".join(lines)


def screenshot_wechat_window() -> str:
    """截取微信窗口，返回临时文件路径"""
    tmp = tempfile.mktemp(suffix=".png")
    # -l 指定窗口 ID 截图，-x 静音
    # 先获取微信窗口 ID
    script = '''
tell application "WeChat" to activate
delay 0.5
tell application "System Events"
    tell process "WeChat"
        set winID to id of window 1
        return winID
    end tell
end tell
'''
    try:
        win_id = run_apple_script(script)
        if win_id:
            subprocess.run(
                ["screencapture", "-x", "-l", win_id, tmp],
                check=True, capture_output=True
            )
            return tmp
    except Exception:
        pass

    # 备选：全屏截图
    subprocess.run(["screencapture", "-x", tmp], check=True)
    return tmp


def scroll_and_capture(scroll_times: int = 8, stop_before_date: str = None,
                       chat_x: int = None, chat_y: int = None) -> list[str]:
    """
    在微信聊天窗口内向上滚动，每次截图
    chat_x/chat_y: 消息列表区域中央坐标，每次 Page Up 前先点击以确保焦点
    stop_before_date: 格式 "YYYY-MM-DD"，遇到该日期之前的内容就停止
    """
    screenshots = []

    log("截取聊天窗口（当前视图）...")
    path = screenshot_wechat_window()
    screenshots.append(path)

    # 构建停止关键词：匹配目标日期前一天的各种微信日期格式
    stop_keywords = []
    if stop_before_date:
        from datetime import datetime, timedelta
        d = datetime.strptime(stop_before_date, "%Y-%m-%d")
        day_before = d - timedelta(days=1)
        stop_keywords = [
            day_before.strftime("%Y/%m/%d"),       # 2026/04/06
            day_before.strftime("%Y-%m-%d"),        # 2026-04-06
            day_before.strftime("%m月%d日"),        # 04月06日
            day_before.strftime("%-m月%-d日"),      # 4月6日
            day_before.strftime("%m/%d"),           # 04/06
        ]
        today = datetime.now().date()
        days_ago = (today - day_before.date()).days
        if days_ago == 1:
            stop_keywords += ["昨天"]
        elif days_ago == 2:
            stop_keywords += ["前天"]

    last_texts = []  # 用于检测是否已翻到顶（连续5张相同则停止）

    for i in range(scroll_times):
        if _cancel_requested:
            log("  [取消] 收到停止指令，中止滚动")
            break
        # 移动鼠标到消息区 + 发送滚轮事件（微信需要鼠标在窗口内才响应滚轮）
        if chat_x and chat_y:
            try:
                import Quartz
                # 移动鼠标到消息区中央
                move = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventMouseMoved,
                    Quartz.CGPoint(chat_x, chat_y),
                    Quartz.kCGMouseButtonLeft
                )
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, move)
                time.sleep(0.1)
                # 连续发8次滚轮事件，每屏幕滚动幅度足够大
                for _ in range(8):
                    scroll = Quartz.CGEventCreateScrollWheelEvent(
                        None, Quartz.kCGScrollEventUnitLine, 1, 20
                    )
                    Quartz.CGEventPost(Quartz.kCGHIDEventTap, scroll)
                    time.sleep(0.02)
            except Exception as e:
                log(f"  [滚动] Quartz 失败: {e}")
        rand_sleep(0.8, 1.3)

        path = screenshot_wechat_window()
        quick_text = ocr_image(path)

        # 检测是否翻到顶（连续5张内容完全相同）
        last_texts.append(quick_text)
        if len(last_texts) > 5:
            last_texts.pop(0)
        if len(last_texts) == 5 and len(set(last_texts)) == 1:
            log(f"  截图 {i+2}：内容连续5张相同，已到达顶部，停止滚动")
            screenshots.append(path)
            break

        if stop_keywords:
            if any(kw in quick_text for kw in stop_keywords):
                screenshots.append(path)
                log(f"  截图 {i+2}：检测到目标日期前的内容，停止滚动")
                break

        screenshots.append(path)
        log(f"  截图 {i+2}/{scroll_times+1}")

    return screenshots


# ──────────────────────────────────────────────
# 微信操作流程
# ──────────────────────────────────────────────

def open_wechat_group(group_name: str, target_date: str = None) -> list[str]:
    """
    打开目标群聊，滚动截图，OCR 提取文本
    返回文本行列表
    """
    log(f"激活微信，定位群聊：{group_name}")

    # 记录当前前台应用
    try:
        current_app = run_apple_script(
            'tell application "System Events" to get name of first application process whose frontmost is true'
        )
    except Exception:
        current_app = None

    # 激活微信
    run_apple_script('tell application "WeChat" to activate')
    rand_sleep(1.5, 2.5)

    try:
        # 获取窗口位置和尺寸，动态计算各区域坐标
        win_info = run_apple_script(
            'tell application "System Events" to tell process "WeChat" '
            'to return {position of window 1, size of window 1}'
        )
        # 返回格式 "39, 38, 1002, 868"（position x, y, size w, h）
        nums = [int(x.strip()) for x in win_info.replace("{", "").replace("}", "").split(",")]
        win_x, win_y, win_w, win_h = nums[0], nums[1], nums[2], nums[3]

        # 聊天消息区中央（右侧 60% 处，垂直居中）——用于确保滚动焦点
        chat_x = win_x + int(win_w * 0.62)
        chat_y = win_y + int(win_h * 0.50)

        # 用 Cmd+F 搜索群名，精确定位目标群（不依赖列表顺序）
        log(f"通过搜索定位群聊：{group_name}")
        # 转义群名中可能影响 AppleScript 字符串的特殊字符
        escaped_name = group_name.replace('\\', '\\\\').replace('"', '\\"')
        run_apple_script(f'''
tell application "WeChat" to activate
delay 0.8
tell application "System Events"
    tell process "WeChat"
        -- 打开搜索框
        keystroke "f" using command down
        delay 0.8
        -- 全选清空，再输入群名
        keystroke "a" using command down
        delay 0.2
        keystroke "{escaped_name}"
        delay 1.5
        -- 回车跳转到第一个匹配结果
        key code 36
        delay 1.2
    end tell
end tell
''')
        rand_sleep(0.5, 1.0)

        # 截图 + OCR，传入目标日期让脚本遇到前天就自动停止滚动
        log("开始截图读取聊天记录...")
        screenshots = scroll_and_capture(
            scroll_times=config.MAX_SCROLL_TIMES,
            stop_before_date=target_date,
            chat_x=chat_x,
            chat_y=chat_y,
        )

        log(f"共截取 {len(screenshots)} 张截图，开始 OCR...")
        all_text_lines = []
        prev_line = None  # 连续去重：只跳过与上一行完全相同的行

        for i, path in enumerate(screenshots):
            text = ocr_image(path)
            for line in text.split("\n"):
                line = line.strip()
                if line and len(line) > 1 and line != prev_line:
                    all_text_lines.append(line)
                    prev_line = line
            # 清理临时文件
            try:
                os.unlink(path)
            except Exception:
                pass
            log(f"  OCR {i+1}/{len(screenshots)} 完成，累计 {len(all_text_lines)} 行")

        log(f"OCR 完成，共提取 {len(all_text_lines)} 行文本")
        return all_text_lines

    finally:
        # 关闭搜索框，最小化微信
        try:
            run_apple_script(
                'tell application "System Events" to key code 53'  # Escape
            )
        except Exception:
            pass

        # 最小化微信窗口
        try:
            run_apple_script(
                'tell application "System Events" to tell process "WeChat" to set miniaturized of window 1 to true'
            )
        except Exception:
            pass

        # 恢复原前台应用
        if current_app and current_app not in ("WeChat", ""):
            try:
                run_apple_script(f'tell application "{current_app}" to activate')
            except Exception:
                pass


# ──────────────────────────────────────────────
# 链接内容抓取
# ──────────────────────────────────────────────

def fetch_url_content(url: str) -> str:
    """抓取 URL 网页正文，失败记录日志并返回空字符串"""
    import re as _re
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://mp.weixin.qq.com/",
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        # 优先提取 <p> 段落文字（公众号文章效果更好）
        paras = _re.findall(r'<p[^>]*>(.*?)</p>', resp.text, _re.DOTALL)
        if paras:
            text = " ".join(_re.sub(r'<[^>]+>', '', p).strip() for p in paras)
        else:
            text = _re.sub(r'<[^>]+>', ' ', resp.text)
        text = _re.sub(r'\s+', ' ', text).strip()
        return text[:3000]
    except Exception as e:
        log(f"  链接抓取失败: {url[:60]} ({e})")
        return ""


def extract_and_fetch_urls(lines: list[str]) -> str:
    """从 OCR 文本行中提取 URL，抓取内容，返回追加文本"""
    import re as _re
    all_text = "\n".join(lines)
    urls = list(set(_re.findall(r'https?://[^\s\u4e00-\u9fff\]）)>]{10,}', all_text)))
    if not urls:
        return ""

    results = []
    for url in urls[:8]:  # 最多抓 8 个链接，避免太慢
        log(f"  抓取链接内容：{url[:60]}...")
        content = fetch_url_content(url)
        if content:
            results.append(f"[链接内容] {url}\n{content}")

    return "\n\n".join(results)


# ──────────────────────────────────────────────
# AI 解读
# ──────────────────────────────────────────────

def build_prompt(lines: list[str], date: str, url_content: str = "", profile: dict = None) -> str:
    raw_text = "\n".join(lines)
    url_section = f"\n\n【链接/文章抓取内容】\n{url_content}" if url_content else ""

    persona = (profile or {}).get("prompt_persona", "你是一位专门服务 CEO 的 AI 认知顾问")
    audience = (profile or {}).get("prompt_audience",
        "读者是CEO，AI认知处于起步阶段，没有技术背景，正在推动公司内部AI生产力转型，寻找面向中小企业的AI创业机会")
    focus = (profile or {}).get("prompt_focus",
        "所有技术术语必须用大白话解释（类比、举例）；标注主要发言人；从CEO视角说清楚内部转型价值和创业机会")
    group_name = (profile or {}).get("group_name", config.WECHAT_GROUP_NAME)

    return f"""{persona}。以下是「{group_name}」{date} 的聊天记录（通过截图 OCR 提取，昵称和内容交替出现，可能有少量识别错误）。

【读者画像】
{audience}

【核心要求】
1. **按话题聚合**：把同一话题的多轮对话合并为一个话题块，不要逐条展开单条消息
2. {focus}
3. 链接和文章结合抓取到的正文内容进行解读；若链接无法抓取，仍需在报告中列出链接标题和分享人
4. 图片内容：OCR 可能识别出图片中的部分文字；若某条消息明显是图片/截图但内容无法解析，请在报告中标注「[含图片，内容未能解析]」并注明分享人，提示读者手动查看

请按以下6个板块输出（Markdown 格式，内容要充实，不要敷衍）：

---

## {date} 群聊解读报告

### 一、今日速览（30秒读完）
列出今天最重要的 3~5 个话题，每条格式：
- **[话题标题]**：用一句话说清楚讨论了什么 → 对读者的意义是什么

### 二、话题深度解读
**将全天讨论归并为若干独立话题**（通常 3~8 个话题），每个话题一个小节，格式：

#### 话题：[话题名称]
**参与讨论**：[发言人昵称列表]

**讨论内容**：
[把这个话题下多轮对话的核心内容合并还原，用第三人称叙述，不要逐条引用]

**大白话解释**：[用日常比喻解释核心概念，比如"这就像..."、"简单说就是..."]

**怎么用**：
- 内部应用：[具体场景]
- 延伸机会：[值得关注的方向]

### 三、重要链接与资源解读
包括：公众号文章卡片、外部链接、群友分享的工具/产品。**即使链接无法抓取内容，也必须列出**。
| 资源 | 分享人 | 是什么 | 推荐指数 | 理由/摘要 |
|------|--------|--------|---------|---------|
| [名称](链接或名称) | 昵称 | 一句话说明 | ⭐⭐⭐ | 结合抓取内容或上下文说明价值 |

### 四、关键信号
哪些讨论说明 AI 正在改变某个行业或流程？
- 每条给出：**信号** + **可落地的场景** + **预计效果**

### 五、机会雷达
识别出哪些痛点/需求还没有被很好解决：
- 每条给出：**机会描述** + **目标客户** + **为什么现在是窗口期**

### 六、今日金句
最有启发的一句话，加上为什么有价值。

---
原始聊天记录（{len(lines)} 行，OCR 提取，昵称与内容交替）：
{raw_text[:11000]}{url_section}
"""


def call_minimax(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {config.MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": "你是一个 AI 认知助手，擅长从群聊记录中提炼有价值的信息。"},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
    }
    resp = requests.post(config.MINIMAX_API_URL, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_claude(prompt: str, max_tokens: int = 4000) -> str:
    headers = {
        "x-api-key": config.CLAUDE_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "system": "你是一个 AI 认知助手，擅长从群聊记录中提炼有价值的信息。",
    }
    resp = requests.post(
        f"{config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages",
        headers=headers, json=payload, timeout=120
    )
    resp.raise_for_status()
    data = resp.json()
    for block in data.get("content", []):
        if block.get("type") == "text":
            return block["text"]
    raise ValueError(f"Claude 响应无文本内容: {data}")


def generate_digest(lines: list[str], date: str, profile: dict = None) -> str:
    if not lines:
        return f"# {date} 群聊解读报告\n\n> 未获取到内容，请检查日志。\n"

    # 抓取 OCR 文本中的链接内容，追加到 prompt
    log("提取并抓取链接内容...")
    url_content = extract_and_fetch_urls(lines)
    if url_content:
        log(f"链接内容抓取完成，共 {len(url_content)} 字符")

    prompt = build_prompt(lines, date, url_content=url_content, profile=profile)
    log(f"调用 AI 生成解读（{config.AI_PROVIDER}，{len(lines)} 行输入）...")

    if config.AI_PROVIDER == "claude":
        if not config.CLAUDE_API_KEY:
            raise ValueError("未配置 ANTHROPIC_API_KEY")
        return call_claude(prompt)
    else:
        if not config.MINIMAX_API_KEY:
            raise ValueError("未配置 MINIMAX_API_KEY")
        return call_minimax(prompt)


# ──────────────────────────────────────────────
# 保存输出
# ──────────────────────────────────────────────

def save_digest(content: str, date: str, profile: dict = None) -> Path:
    output_dir_str = (profile or {}).get("output_dir", config.OUTPUT_DIR)
    output_dir = Path(os.path.expanduser(output_dir_str))
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{date}.md"
    group_name = (profile or {}).get("group_name", config.WECHAT_GROUP_NAME)
    header = (
        f"---\n"
        f"date: {date}\n"
        f"source: {group_name}\n"
        f"generated_at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"---\n\n"
    )
    filepath.write_text(header + content, encoding="utf-8")
    return filepath


# ──────────────────────────────────────────────
# 主入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="微信群聊天记录自动读取 + AI 解读")
    parser.add_argument("--date", help="指定日期（默认昨天），格式 YYYY-MM-DD")
    parser.add_argument("--profile", default="profiles/xiaohu_vip.json",
                        help="指定 profile 文件路径（默认 profiles/xiaohu_vip.json）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅截图+OCR，打印识别到的文本，不调用 AI")
    parser.add_argument("--skip-wechat", action="store_true",
                        help="跳过微信读取，用测试文本走 AI 流程")
    args = parser.parse_args()

    # 加载 profile
    try:
        profile = load_profile(args.profile)
        log(f"已加载 profile：{profile.get('name', args.profile)}")
    except FileNotFoundError:
        log(f"Profile 文件不存在：{args.profile}，使用默认配置")
        profile = {}

    # 目标日期
    if args.date:
        target_date = args.date
    else:
        yesterday = datetime.now() - timedelta(days=1)
        target_date = yesterday.strftime("%Y-%m-%d")

    log(f"目标日期：{target_date}")

    # 读取微信内容
    group_name = profile.get("group_name", config.WECHAT_GROUP_NAME)
    if args.skip_wechat:
        log("跳过微信读取（--skip-wechat）")
        lines = ["Claude 4.5 发布了新功能", "新的 AI Agent 框架值得关注"]
    else:
        lines = open_wechat_group(group_name, target_date=target_date)

    if args.dry_run:
        log(f"[dry-run] 共 {len(lines)} 行，前 30 行：")
        for i, line in enumerate(lines[:30]):
            print(f"  {i+1:3}. {line}")
        return

    # AI 生成解读
    digest = generate_digest(lines, target_date, profile=profile)

    # 保存文件
    filepath = save_digest(digest, target_date, profile=profile)
    log(f"已保存：{filepath}")
    print(f"\n输出文件：{filepath}")


if __name__ == "__main__":
    main()
