# AI 对话助手 - iOS App

## 功能

- **自动 VAD 录音**：检测到对话声音（持续 2 秒）自动开始录制，静音 60 秒自动停止
- **后台持续运行**：锁屏、切换 App 后仍然监听（iOS 官方 background audio 模式）
- **自动上传**：录音文件通过 WiFi 上传到 Mac 服务器，与后端 `/api/conversations/upload` 兼容

---

## Xcode 建项目步骤

### 1. 新建项目

```
Xcode → File → New → Project
选择：iOS → App
Product Name: AIRecorder
Interface: SwiftUI
Language: Swift
Bundle Identifier: com.你的名字.AIRecorder
```

### 2. 替换/导入文件

把以下文件**拖入** Xcode 项目（替换自动生成的同名文件）：

| 文件 | 说明 |
|------|------|
| `AIRecorderApp.swift` | App 入口（替换原有） |
| `ContentView.swift` | 主界面（替换原有） |
| `RecorderService.swift` | 拖入项目根目录 |
| `UploadManager.swift` | 拖入项目根目录 |

### 3. 配置 Info.plist

在 Xcode 中：
- 点击项目 → Target → Info
- 添加 `Privacy - Microphone Usage Description`：`需要麦克风权限以自动录制对话`
- 添加 `Required background modes` → `App plays audio or streams audio/video using AirPlay`

或直接用本目录的 `Info.plist` 内容替换 Xcode 生成的版本。

### 4. 允许 HTTP（局域网上传）

在 Info.plist 中加入 `NSAppTransportSecurity` 配置（已包含在本 Info.plist 中）。

### 5. 连接手机安装

```
iPhone 连接 Mac → Xcode 顶部选择你的设备 → Cmd+R 运行
首次需要：设置 → 通用 → VPN与设备管理 → 信任开发者证书
```

无需付费开发者账号，Apple ID 登录即可侧载（免费账号每 7 天需重新安装）。

---

## 使用方法

1. 打开 App，点击授权麦克风
2. 界面显示"**监听中**"（绿色圆圈），无需其他操作
3. 开始对话时，App 自动切换到"**录制中**"（红色圆圈）
4. 对话结束后静音 60 秒，自动保存并重新进入监听
5. 设置服务器地址（Mac 的局域网 IP:5678），点"**立即上传**"

---

## 录音文件位置

```
iPhone Documents/recordings/
└── 2026-04-11/
    ├── 1712820000.m4a         ← 录音文件
    ├── 1712820000.m4a.uploaded ← 已上传标记
    └── 1712823600.m4a
```

---

## 后端兼容性

上传格式与现有 Python 后端完全兼容：
- `POST /api/conversations/upload`
- multipart/form-data，字段：`file` / `date` / `source=ios`
