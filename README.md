# AI 对话录音与知识管理系统

自动录制日常对话（会议/拜访/通话），上传后用 Whisper 转录 + AI 提炼关键信息，汇聚到 Web 前端统一管理。同时支持微信群聊记录自动解读，知识图谱构建。

---

## 系统架构

```
手机 (HarmonyOS App)
    ↓ VAD 检测到人声 → 自动开始录音
    ↓ 静音 3 分钟 → 自动停止并上传
    ↓ POST /api/conversations/upload
服务端 (Python Flask, 端口 5678)
    ↓ Whisper 语音转文字
    ↓ Claude / MiniMax AI 提炼关键信息
    ↓ 写入 SQLite 数据库
前端 Web (http://localhost:5678)
    ↓ 5 个 Tab 展示所有信息
```

同步支持：
- macOS 定时读取微信群聊，AI 生成日报
- 联系人管理、知识图谱可视化

---

## 目录结构

```
ai-recorder-system/
├── web_app.py              # Flask 主服务（17 个 API 路由）
├── conversation.py         # Whisper 转录 + AI 提炼逻辑
├── knowledge.py            # SQLite 数据库操作层
├── extract.py              # 知识图谱实体提取
├── wechat_digest.py        # 微信群聊读取（macOS Accessibility API）
├── voice_recorder.py       # 本地录音备用工具
├── config.py               # 全局配置（API Key / 群名 / 输出路径）
├── templates/
│   └── index.html          # 前端单页应用（1544 行，5 个 Tab）
├── harmony_app/            # 鸿蒙 App 旧版源码（最新版见独立仓库）
├── ios_app/                # iOS App 源码（Swift）
├── family/                 # 家庭子系统（摄像头/家庭日报）
├── uploads/                # 上传的音频文件（gitignore）
├── knowledge.db            # SQLite 数据库（gitignore）
└── logs/                   # 运行日志（gitignore）
```

---

## 前端功能（5 个 Tab）

| Tab | 功能 |
|-----|------|
| **今日速览** | 当日统计（对话数/联系人/待处理行动）+ 最新动态混合流 + 今日跟进提醒 |
| **信息流** | 所有对话/事件列表，支持来源/角色/项目/日期多维度过滤，批量合并对话 |
| **联系人** | CRM 管理，联系人详情 + 关联对话历史，支持绑定公司/角色/标签 |
| **知识图谱** | Vis Network 可视化，实体关系网络，支持搜索和详情侧栏 |
| **配置** | 定时任务设置、微信群配置、Obsidian 导出路径 |

---

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端主页 |
| POST | `/api/conversations/upload` | 上传音频，异步转录+提炼 |
| GET | `/api/conversations` | 列表对话（支持多维过滤） |
| GET | `/api/conversations/<id>` | 对话详情（含转录原文+AI提炼） |
| GET | `/api/conversations/<id>/audio` | 流式返回音频文件（试听） |
| POST | `/api/conversations/process` | 手动触发批处理（扫描 uploads 目录） |
| POST | `/api/conversations/merge` | 合并多条对话+重新提炼 |
| GET | `/api/insights` | 跨来源洞察（高频联系人/活跃项目/待处理行动） |
| GET | `/api/graph` | 知识图谱数据 |
| GET | `/api/contacts` | 联系人列表 |
| POST | `/api/run` | 立即执行微信群截图+解读任务 |
| GET | `/api/stream/<task_id>` | SSE 流式获取任务实时日志 |

---

## 快速开始

### 1. 安装依赖

```bash
cd ai-recorder-system
python3 -m venv venv
source venv/bin/activate
pip install flask openai-whisper anthropic requests atomacos pyobjc-framework-Cocoa
```

> Whisper 需要 ffmpeg：`brew install ffmpeg`

### 2. 配置 API Key

创建 `.env` 文件：

```bash
# AI 提供商（二选一）
AI_PROVIDER=claude              # 或 minimax
ANTHROPIC_API_KEY=sk-ant-...    # Claude API Key
# MINIMAX_API_KEY=...           # MiniMax API Key（备选）

# Whisper 模型（可选，默认 base）
WHISPER_MODEL=base              # tiny / base / small / medium / large
```

### 3. 启动服务

```bash
python web_app.py
# 服务运行在 http://0.0.0.0:5678
```

打开浏览器访问 `http://localhost:5678`

### 4. 连接手机录音 App

见 [ai-recorder-harmonyos](https://github.com/love5889110-ship-it/ai-recorder-harmonyos)，将 App 的服务器地址设为：
```
http://<你的电脑IP>:5678
```

---

## 微信群自动解读（可选）

仅支持 macOS，需授权辅助功能权限。

```bash
# 修改 config.py 中的群名
WECHAT_GROUP_NAME = "你的群名"

# 手动测试
python wechat_digest.py --date 2026-04-14

# 配置定时任务（每天 08:00 自动运行）
cp com.user.wechat-digest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.wechat-digest.plist
```

---

## AI 提炼输出字段

每条对话经 AI 分析后输出：

| 字段 | 说明 |
|------|------|
| `summary` | 3-5句话核心摘要 |
| `key_points` | 关键信息要点列表 |
| `action_items` | 待处理行动（我方/对方） |
| `contacts` | 涉及人员（姓名/公司/角色） |
| `scene` | 场景分类（商务拜访/内部会议/客户通话/培训/家庭/其他等） |
| `sentiment` | 整体情绪（positive/neutral/negative） |
| `transcript` | Whisper 转录原文 |

---

## 系统要求

- Python 3.9+
- macOS 12+（微信群功能）/ Linux（仅录音功能）
- 磁盘空间：每小时录音约 30MB（m4a 格式）
- Whisper `base` 模型：CPU 转录约 1分钟/分钟录音；`small` 模型精度更高但更慢
