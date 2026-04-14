# 微信群聊天记录自动解读

每日 8 点自动读取「XiaoHu.AI学院 VVVIP群」前一日聊天记录，用 AI 生成结构化解读，保存到本地 Markdown 文件。

## 文件结构

```
ai-wechat-digest/
  ├── wechat_digest.py          # 主脚本
  ├── config.py                 # 配置（群名、API Key、输出目录）
  ├── run.sh                    # 定时任务入口脚本
  ├── com.user.wechat-digest.plist  # launchd 定时任务配置
  └── logs/                     # 运行日志
```

输出目录：`~/Documents/WeChat-Digest/YYYY-MM-DD.md`

---

## 安装步骤（只需做一次）

### 1. 安装 Python 依赖

```bash
cd ~/ai-wechat-digest

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install atomacos pyobjc-framework-Cocoa requests
```

### 2. 配置 API Key

创建 `~/ai-wechat-digest/.env` 文件：

```bash
# 使用 MiniMax（与 ltc-os 项目共享同一个 key）
MINIMAX_API_KEY=your_minimax_api_key_here

# 或使用 Claude（二选一）
# AI_PROVIDER=claude
# ANTHROPIC_API_KEY=your_claude_api_key_here
```

> 如果已有 `~/ltc-os/.env.local`，run.sh 会自动从中读取 MINIMAX_API_KEY，无需重复配置。

### 3. 授权辅助功能权限

系统设置 → 隐私与安全性 → 辅助功能 → 点击「+」添加：
- **Terminal**（或 iTerm2）
- 如果使用 launchd 运行，还需添加 **Python** 或 **bash**

这是 atomacos 读取微信 UI 的必要权限，不授权则无法读取。

### 4. 首次手动测试

```bash
# 确保微信已登录（最小化也可以）

# dry-run：只读取消息，不调用 AI，验证能否找到群聊
python3 wechat_digest.py --dry-run

# 完整测试：读取 + AI 解读 + 保存文件
python3 wechat_digest.py --date 2026-04-07
```

看到类似输出即为成功：
```
[08:03:12] 启动微信访问：XiaoHu.AI学院 VVVIP群
[08:03:14] 通过搜索框定位群聊...
[08:03:17] 已打开群聊
[08:03:18] 读取消息中...
[08:03:19] 共读取到 127 条消息文本片段
[08:03:19] 调用 AI 生成解读（provider: minimax）...
[08:03:25] 已保存解读报告：/Users/zhangyang/Documents/WeChat-Digest/2026-04-07.md
```

### 5. 配置定时任务（launchd）

```bash
# 复制 plist 到 LaunchAgents
cp ~/ai-wechat-digest/com.user.wechat-digest.plist \
   ~/Library/LaunchAgents/

# 加载定时任务
launchctl load ~/Library/LaunchAgents/com.user.wechat-digest.plist

# 验证是否加载成功
launchctl list | grep wechat-digest
```

---

## 日常使用

解读报告每天自动生成，保存路径：`~/Documents/WeChat-Digest/`

用 Obsidian 打开该文件夹，可直接浏览所有历史报告。

**手动触发（任意时间执行）：**
```bash
python3 ~/ai-wechat-digest/wechat_digest.py
```

**查看运行日志：**
```bash
tail -f ~/ai-wechat-digest/logs/stdout.log
```

---

## 常见问题

**Q: 找不到群聊 / 搜索结果为空**
- 确认微信已登录且群名与 `config.py` 中 `WECHAT_GROUP_NAME` 完全一致
- 手动在微信中搜索一次群名，确认能搜到

**Q: 报错 "无法连接微信"**
- 确认微信已启动（最小化可以，但必须登录）

**Q: 报错 "辅助功能权限不足"**
- 重新检查系统设置中的辅助功能授权

**Q: 取消定时任务**
```bash
launchctl unload ~/Library/LaunchAgents/com.user.wechat-digest.plist
```

---

## 降封号风险设计说明

| 措施 | 说明 |
|------|------|
| 只读不写 | 脚本全程不发送任何消息 |
| 低频率 | 一天一次，单次 < 30 秒 |
| 随机延迟 | 每步操作间 1.5~3.5 秒随机等待 |
| 非整点触发 | 8:03 触发 + 0~4 分钟随机抖动 |
| 无 hook/注入 | 使用 macOS 系统原生 Accessibility API |
| 操作后还原 | 读完立刻最小化微信，恢复原前台应用 |
