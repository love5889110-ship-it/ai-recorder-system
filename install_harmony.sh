#!/bin/bash
# ════════════════════════════════════════════════
# 鸿蒙 AI录音助手 一键安装脚本
# 用法：bash install_harmony.sh
# ════════════════════════════════════════════════
set -e

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${BLUE}[•]${NC} $1"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1\n"; exit 1; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
ask()  { echo -e "${YELLOW}[?]${NC} $1"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HARMONY_DIR="$SCRIPT_DIR/harmony_app"

echo ""
echo "════════════════════════════════════════════════"
echo "       AI 对话助手 鸿蒙版 一键安装"
echo "════════════════════════════════════════════════"
echo ""

# ── 1. 检测 DevEco Studio ────────────────────────
log "检测 DevEco Studio..."

DEVECO_APP=""
for p in \
  "/Applications/DevEco-Studio.app" \
  "/Applications/DevEco Studio.app" \
  "$HOME/Applications/DevEco-Studio.app" \
  "$HOME/Applications/DevEco Studio.app"; do
  if [ -d "$p" ]; then
    DEVECO_APP="$p"
    break
  fi
done

if [ -z "$DEVECO_APP" ]; then
  echo ""
  warn "未检测到 DevEco Studio（鸿蒙官方 IDE）"
  echo ""
  echo "  DevEco Studio 是华为官方鸿蒙开发工具，必须安装才能编译。"
  echo ""
  echo "  ─── 下载步骤 ───────────────────────────────"
  echo "  1. 打开浏览器，访问："
  echo "     https://developer.huawei.com/consumer/cn/deveco-studio/"
  echo "  2. 点击「立即下载」→ 选择 macOS 版本"
  echo "  3. 下载完 .dmg 文件，双击安装"
  echo "  4. 首次打开 DevEco Studio，按向导安装 HarmonyOS SDK"
  echo "     （向导会自动下载，约 1-2GB，需要等待）"
  echo "  5. SDK 安装完成后，回到这里按回车继续"
  echo "  ────────────────────────────────────────────"
  echo ""
  open "https://developer.huawei.com/consumer/cn/deveco-studio/" 2>/dev/null || true
  read -p "  DevEco Studio 安装完成后按回车键继续... " _

  # 再次检测
  for p in \
    "/Applications/DevEco-Studio.app" \
    "/Applications/DevEco Studio.app" \
    "$HOME/Applications/DevEco-Studio.app" \
    "$HOME/Applications/DevEco Studio.app"; do
    if [ -d "$p" ]; then
      DEVECO_APP="$p"
      break
    fi
  done
  [ -z "$DEVECO_APP" ] && err "仍未找到 DevEco Studio，请确认已安装到 /Applications/"
fi
ok "DevEco Studio：$DEVECO_APP"

# ── 2. 找 hdc 工具 ────────────────────────────────
log "查找 hdc 调试工具..."

HDC=""
for p in \
  "$DEVECO_APP/Contents/sdk/default/openharmony/toolchains/hdc" \
  "$DEVECO_APP/Contents/tools/hdc" \
  "$HOME/Library/Huawei/sdk/default/openharmony/toolchains/hdc" \
  "$HOME/Library/Application Support/Huawei/sdk/default/openharmony/toolchains/hdc" \
  "/usr/local/bin/hdc"; do
  if [ -x "$p" ]; then
    HDC="$p"
    break
  fi
done

if [ -z "$HDC" ]; then
  # 深度搜索
  HDC=$(find "$DEVECO_APP" "$HOME/Library/Huawei" -name "hdc" -type f 2>/dev/null | head -1)
fi

if [ -z "$HDC" ]; then
  warn "未找到 hdc 工具，将使用 DevEco Studio 图形界面安装"
  echo ""
  echo "  ─── 图形界面安装步骤（已自动填好 IP） ──────────"
  echo "  1. DevEco Studio 打开项目：$HARMONY_DIR"
  echo "  2. 手机 USB 连接电脑，手机弹出「允许调试」→ 点允许"
  echo "  3. 顶部工具栏选择你的手机设备"
  echo "  4. 点绿色三角「Run」按钮，等待编译安装（约 3 分钟）"
  echo "  5. 手机上弹出权限请求时，全部点「允许」"
  echo "  ────────────────────────────────────────────"

  # 先更新 IP，然后打开项目
  MAC_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "192.168.1.100")
  log "自动填入 Mac IP：$MAC_IP"
  sed -i '' "s|http://192.168.[0-9]*\.[0-9]*:5678|http://${MAC_IP}:5678|g" \
    "$HARMONY_DIR/entry/src/main/ets/entryability/EntryAbility.ets" 2>/dev/null || true

  open -a "$DEVECO_APP" "$HARMONY_DIR" 2>/dev/null || open "$DEVECO_APP"
  echo ""
  ok "已打开 DevEco Studio，请按上面步骤操作"
  echo ""
  exit 0
fi

ok "hdc 路径：$HDC"
export PATH="$(dirname "$HDC"):$PATH"

# ── 3. 自动填入 Mac IP ────────────────────────────
log "检测 Mac 局域网 IP..."
MAC_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "")

if [ -z "$MAC_IP" ]; then
  warn "无法自动检测 IP，请手动输入 Mac 局域网 IP"
  read -p "  请输入 Mac IP（格式 192.168.x.x）：" MAC_IP
fi
ok "Mac IP：$MAC_IP"

# 替换 EntryAbility.ets 中的 serverUrl
sed -i '' "s|http://192.168.[0-9]*\.[0-9]*:5678|http://${MAC_IP}:5678|g" \
  "$HARMONY_DIR/entry/src/main/ets/entryability/EntryAbility.ets"
ok "已自动填入服务器地址：http://${MAC_IP}:5678"

# ── 4. 检测手机连接 ───────────────────────────────
log "检测连接的华为手机..."

DEVICE_LIST=$("$HDC" list targets 2>/dev/null || echo "")

if [ -z "$DEVICE_LIST" ] || echo "$DEVICE_LIST" | grep -q "Empty"; then
  echo ""
  warn "未检测到手机，请："
  echo "  1. 手机 → 设置 → 关于手机 → 版本号（连点 7 次）→ 开启开发者选项"
  echo "  2. 设置 → 开发者选项 → 开启「USB 调试」"
  echo "  3. 用 USB 线连接手机和 Mac"
  echo "  4. 手机屏幕弹出「允许 USB 调试？」→ 点「允许」"
  echo "  5. 回到这里按回车继续"
  echo ""
  read -p "  连接完成后按回车键... " _

  DEVICE_LIST=$("$HDC" list targets 2>/dev/null || echo "")
  if [ -z "$DEVICE_LIST" ] || echo "$DEVICE_LIST" | grep -q "Empty"; then
    echo ""
    warn "仍未检测到手机，切换到图形界面安装模式"
    open -a "$DEVECO_APP" "$HARMONY_DIR"
    echo ""
    echo "  DevEco Studio 已打开，请手动运行项目到手机"
    exit 0
  fi
fi

DEVICE_ID=$(echo "$DEVICE_LIST" | head -1 | tr -d '[:space:]')
ok "已连接设备：$DEVICE_ID"

# ── 5. 用 DevEco Studio 命令行编译 ───────────────
log "开始编译（首次约 3-5 分钟，需下载依赖）..."

# 找 hvigorw（鸿蒙构建工具）
HVIGOR="$HARMONY_DIR/hvigorw"
if [ ! -f "$HVIGOR" ]; then
  # 从 DevEco Studio 目录找
  HVIGOR_BIN=$(find "$DEVECO_APP" -name "hvigorw" -type f 2>/dev/null | head -1)
  if [ -n "$HVIGOR_BIN" ]; then
    cp "$HVIGOR_BIN" "$HARMONY_DIR/hvigorw"
    chmod +x "$HARMONY_DIR/hvigorw"
    HVIGOR="$HARMONY_DIR/hvigorw"
  fi
fi

if [ -f "$HVIGOR" ]; then
  cd "$HARMONY_DIR"
  chmod +x hvigorw
  ./hvigorw assembleApp --mode module -p product=default 2>&1 | \
    grep -E "BUILD|error|warning|FAILED|SUCCESS" | tail -20 || true

  # 找生成的 .hap 文件
  HAP_PATH=$(find "$HARMONY_DIR" -name "*.hap" 2>/dev/null | head -1)
else
  warn "未找到 hvigorw 构建工具，使用 DevEco Studio 图形界面..."
  open -a "$DEVECO_APP" "$HARMONY_DIR"
  echo ""
  echo "  ─── 手动编译安装步骤 ──────────────────────────"
  echo "  1. DevEco Studio 中，确认顶部设备已选中你的手机"
  echo "  2. 点击绿色三角 Run 按钮"
  echo "  3. 等待编译完成（进度条走完）"
  echo "  4. 手机自动安装并启动 App"
  echo "  ────────────────────────────────────────────"
  exit 0
fi

if [ -z "$HAP_PATH" ]; then
  err "编译失败，未生成 .hap 文件。请查看上方错误，或用 DevEco Studio 图形界面编译"
fi
ok "编译成功：$HAP_PATH"

# ── 6. 安装到手机 ─────────────────────────────────
log "安装到手机..."
"$HDC" -t "$DEVICE_ID" install "$HAP_PATH"
ok "安装完成！"

# ── 7. 启动 App ───────────────────────────────────
log "启动 App..."
"$HDC" -t "$DEVICE_ID" shell aa start -a EntryAbility -b com.aiassistant.recorder 2>/dev/null || true

echo ""
echo "════════════════════════════════════════════════"
ok "完成！"
echo ""
echo "  📱 手机操作："
echo "  1. 找到并打开「AI对话助手」"
echo "  2. 弹出麦克风权限 → 点「允许」"
echo "  3. 看到绿色圆圈「监听中」即可放手机"
echo "  4. 有对话声音时（持续3秒）自动开始录制"
echo "  5. 录完自动上传到 http://${MAC_IP}:5678"
echo ""
echo "  🌐 网页查看录音：http://${MAC_IP}:5678"
echo "════════════════════════════════════════════════"
echo ""
