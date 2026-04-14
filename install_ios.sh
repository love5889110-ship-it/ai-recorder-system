#!/bin/bash
# AI 对话助手 - iOS 一键编译安装脚本
# 用法：bash install_ios.sh

set -e

BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${BLUE}[•]${NC} $1"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

echo ""
echo "================================================"
echo "       AI 对话助手 iOS 一键安装"
echo "================================================"
echo ""

# ── 1. 检查 Xcode ────────────────────────────────
log "检查 Xcode..."
if ! xcode-select -p &>/dev/null; then
  err "未检测到 Xcode。请从 App Store 搜索安装 Xcode（免费），安装完成后重新运行此脚本"
fi
XCODE_VER=$(xcodebuild -version 2>/dev/null | head -1)
ok "已安装：$XCODE_VER"

# 接受 Xcode 许可（避免交互提示）
sudo xcodebuild -license accept 2>/dev/null || true

# ── 2. 安装 Homebrew 工具 ────────────────────────
log "检查 Homebrew..."
if ! command -v brew &>/dev/null; then
  err "未检测到 Homebrew。请运行：/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
fi

if ! command -v xcodegen &>/dev/null; then
  log "安装 xcodegen..."
  brew install xcodegen
fi
ok "xcodegen 就绪"

if ! command -v ios-deploy &>/dev/null; then
  log "安装 ios-deploy..."
  brew install ios-deploy
fi
ok "ios-deploy 就绪"

# ── 3. 获取签名身份 ──────────────────────────────
log "检查代码签名..."
TEAM_ID=$(security find-identity -v -p codesigning 2>/dev/null | grep -E "Apple Development|iPhone Developer" | head -1 | grep -oE '[A-Z0-9]{10}' | head -1)

if [ -z "$TEAM_ID" ]; then
  warn "未找到签名证书。需要先在 Xcode 中登录 Apple ID（免费）"
  echo ""
  echo "  操作步骤："
  echo "  1. 打开 Xcode（即将自动打开）"
  echo "  2. 菜单：Xcode → Settings → Accounts"
  echo "  3. 点击左下角 + → Apple ID → 输入你的 Apple ID 和密码"
  echo "  4. 回到此终端，按回车继续"
  echo ""
  open -a Xcode
  read -p "  登录完成后按回车键继续... " _
  TEAM_ID=$(security find-identity -v -p codesigning 2>/dev/null | grep -E "Apple Development|iPhone Developer" | head -1 | grep -oE '[A-Z0-9]{10}' | head -1)
  [ -z "$TEAM_ID" ] && err "仍未找到签名证书，请确认 Xcode Accounts 中已登录 Apple ID"
fi
ok "签名 Team ID：$TEAM_ID"

# ── 4. 写入 Team ID 到 project.yml ───────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_YML="$SCRIPT_DIR/ios_app/project.yml"

# 替换 DEVELOPMENT_TEAM（支持重复运行）
sed -i '' "s/DEVELOPMENT_TEAM: \"[^\"]*\"/DEVELOPMENT_TEAM: \"$TEAM_ID\"/" "$PROJECT_YML"
ok "已写入签名配置"

# ── 5. 生成 Xcode 项目 ───────────────────────────
log "生成 Xcode 项目..."
cd "$SCRIPT_DIR/ios_app"
xcodegen generate --spec project.yml --project . 2>&1 | tail -3
ok "项目生成完成"

# ── 6. 检测连接的 iPhone ─────────────────────────
log "检测 iPhone..."
DEVICE_LINE=$(xcrun xctrace list devices 2>/dev/null | grep -v Simulator | grep -E "iPhone|iPad" | head -1)

if [ -z "$DEVICE_LINE" ]; then
  echo ""
  warn "未检测到 iPhone。请："
  echo "  1. 用 Lightning/USB-C 线连接 iPhone"
  echo "  2. iPhone 屏幕弹出「信任此电脑？」→ 点信任"
  echo "  3. 按回车继续"
  read -p "  连接完成后按回车键... " _
  DEVICE_LINE=$(xcrun xctrace list devices 2>/dev/null | grep -v Simulator | grep -E "iPhone|iPad" | head -1)
  [ -z "$DEVICE_LINE" ] && err "仍未检测到设备，请检查 USB 连接"
fi

DEVICE_ID=$(echo "$DEVICE_LINE" | grep -oE '[0-9a-f]{8}-[0-9a-f]{16}' | head -1)
DEVICE_NAME=$(echo "$DEVICE_LINE" | sed 's/ ([^)]*)//' | xargs)
ok "已连接：$DEVICE_NAME ($DEVICE_ID)"

# ── 7. 编译 ──────────────────────────────────────
log "编译中（首次约 2-3 分钟）..."
BUILD_DIR="$SCRIPT_DIR/ios_app/build"
xcodebuild \
  -project AIRecorder.xcodeproj \
  -scheme AIRecorder \
  -destination "id=$DEVICE_ID" \
  -configuration Debug \
  -derivedDataPath "$BUILD_DIR" \
  CODE_SIGN_STYLE=Automatic \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  CODE_SIGNING_ALLOWED=YES \
  2>&1 | grep -E "error:|warning:|Build succeeded|FAILED|Compiling" | grep -v "^$" || true

# 检查编译结果
APP_PATH=$(find "$BUILD_DIR" -name "AIRecorder.app" -not -path "*/Simulator/*" 2>/dev/null | head -1)
[ -z "$APP_PATH" ] && err "编译失败。请查看上方错误信息"
ok "编译成功：$APP_PATH"

# ── 8. 安装到 iPhone ─────────────────────────────
log "安装到 $DEVICE_NAME..."
ios-deploy --bundle "$APP_PATH" --id "$DEVICE_ID" --no-wifi --justlaunch 2>&1 | tail -5

echo ""
echo "================================================"
ok "安装完成！"
echo ""
echo "  📱 手机操作："
echo "  1. 找到并打开「AI对话助手」"
echo "  2. 弹出麦克风权限 → 点「允许」"
echo "  3. 看到绿色圆圈「监听中」即可放手机"
echo "  4. 有对话声音时自动开始录制"
echo ""
echo "  ⚠️  首次安装需信任开发者证书："
echo "  设置 → 通用 → VPN与设备管理 → 信任"
echo "================================================"
echo ""
