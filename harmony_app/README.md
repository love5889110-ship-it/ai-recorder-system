# 鸿蒙 AI对话助手

自动 VAD 录音 + 后台常驻 + WiFi 自动上传到 Mac。
适配 Mate XT 三折叠（HarmonyOS NEXT）。

---

## 一键安装

在 Mac 终端运行：

```bash
bash ~/ai-wechat-digest/install_harmony.sh
```

脚本会自动完成：
- 检测 / 引导安装 DevEco Studio（华为官方 IDE，免费）
- 自动填入 Mac 局域网 IP
- 检测 USB 连接的手机
- 编译并安装 App

**首次运行**如果没装 DevEco Studio，脚本会打开下载页，安装好后再次运行即可。

---

## 手动安装（DevEco Studio 图形界面）

1. [下载 DevEco Studio](https://developer.huawei.com/consumer/cn/deveco-studio/) 并安装
2. 打开 DevEco Studio → File → Open → 选择 `harmony_app/` 目录
3. 打开 `entry/src/main/ets/entryability/EntryAbility.ets`，把 `serverUrl` 改成 Mac 的 IP
4. 手机开启开发者模式 + USB 调试，USB 连接 Mac
5. 顶部选择手机设备 → 点绿色三角 Run

---

## App 使用

安装后**无需任何操作**：
- 开机自动启动 VAD 监听（后台常驻）
- 检测到对话声音（持续 3 秒）→ 自动开始录制
- 静音 60 秒 → 自动停止、保存、重新监听
- WiFi 环境下录完立即上传到 Mac

网页查看：打开浏览器访问 `http://<Mac-IP>:5678`

---

## 开启手机开发者模式

1. 手机「设置」→「关于手机」→「版本号」连点 7 次
2. 「设置」→「开发者选项」→ 开启「USB 调试」
3. USB 连接 Mac，手机弹出「允许调试？」→ 点「允许」

---

## 项目结构

```
harmony_app/
├── build-profile.json5
├── oh-package.json5
├── AppScope/app.json5
└── entry/src/main/
    ├── module.json5                    # 权限声明，支持 phone + foldable
    └── ets/
        ├── entryability/EntryAbility.ets  # 开机自动启动 VAD
        ├── pages/Index.ets               # 状态显示界面
        └── service/
            ├── RecorderService.ets       # VAD 监听 + 录音管理
            └── UploadManager.ets         # WiFi 上传
```

---

## 上传接口兼容性

```
POST /api/conversations/upload
multipart/form-data:
  file: <M4A 录音文件>
  date: "2026-04-11"
  source: "harmony"
```
