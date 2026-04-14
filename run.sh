#!/bin/bash
# run.sh - 启动脚本，供 launchd 调用

set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"

# 加载环境变量（从 .env 文件或 ltc-os 项目共享）
ENV_FILE="$DIR/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# 从 shell 环境继承 ANTHROPIC_API_KEY（launchd 不继承登录 shell 环境变量，从 .env 读取）
if [ -z "$ANTHROPIC_API_KEY" ]; then
    source ~/.zprofile 2>/dev/null || true
    source ~/.zshrc 2>/dev/null || true
fi

# 激活 Python 虚拟环境（如果存在）
VENV="$DIR/venv"
if [ -d "$VENV" ]; then
    source "$VENV/bin/activate"
fi

# 随机延迟 0~240 秒（在 8:03~8:07 之间随机执行，错开整点）
JITTER=$((RANDOM % 240))
echo "[$(date '+%H:%M:%S')] 随机等待 ${JITTER} 秒..."
sleep "$JITTER"

# 执行主脚本
echo "[$(date '+%H:%M:%S')] 开始执行 wechat_digest.py"
python3 "$DIR/wechat_digest.py" 2>&1

echo "[$(date '+%H:%M:%S')] 执行完成"
