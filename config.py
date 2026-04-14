"""
配置文件 - 按需修改
"""
import os

# ===== 微信配置 =====
# 目标群聊名称（需与微信中显示的完全一致）
WECHAT_GROUP_NAME = "XiaoHu.AI学院 VVVIP群"

# 微信 Bundle ID（macOS 版微信）
WECHAT_BUNDLE_ID = "com.tencent.xinWeChat"

# ===== AI API 配置 =====
# 优先从环境变量读取，其次使用下面的默认值
# 支持 MiniMax 或 Claude API（二选一）

AI_PROVIDER = os.getenv("AI_PROVIDER", "claude")  # 默认用 Claude

# MiniMax 配置
MINIMAX_API_KEY = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_MODEL = "MiniMax-Text-01"
MINIMAX_API_URL = "https://api.minimax.chat/v1/text/chatcompletion_v2"

# Claude 配置（备选）
CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_AUTH_TOKEN", ""))
CLAUDE_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
CLAUDE_MODEL = "claude-sonnet-4-6"

# ===== 输出配置 =====
OUTPUT_DIR = os.path.expanduser("~/Documents/Obsidian Vault/微信群解读")

# ===== 操作行为配置 =====
# 每步操作之间的随机延迟范围（秒）
DELAY_MIN = 1.5
DELAY_MAX = 3.5

# 截图滚动次数上限（每次 Page Up，截一张图）
# 遇到目标日期前一天的时间戳会自动提前停止，此值是安全上限
# 活跃群一天内容约需 80~120 次滚动覆盖
MAX_SCROLL_TIMES = 150

# 读取消息的最大条数上限
MAX_MESSAGES = 500
