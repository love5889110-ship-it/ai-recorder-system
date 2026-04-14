import SwiftUI

struct ContentView: View {
    @EnvironmentObject var recorder: RecorderService
    @StateObject private var uploader = UploadManager()
    @State private var recordings: [RecordingItem] = []
    @State private var serverUrl: String = UserDefaults.standard.string(forKey: "serverUrl") ?? "http://192.168.1.100:5678"
    @State private var showUploadLog = false

    var body: some View {
        NavigationView {
            ScrollView {
                VStack(spacing: 0) {

                    // ── 状态卡片 ──────────────────────────────
                    VStack(spacing: 16) {
                        // 状态圆圈
                        ZStack {
                            Circle()
                                .fill(stateColor.opacity(0.15))
                                .frame(width: 100, height: 100)
                            Circle()
                                .fill(stateColor)
                                .frame(width: 60, height: 60)
                                .shadow(color: stateColor.opacity(0.5), radius: 12)
                            Image(systemName: stateIcon)
                                .font(.system(size: 24, weight: .medium))
                                .foregroundColor(.white)
                        }
                        .padding(.top, 24)

                        // 状态文字
                        VStack(spacing: 4) {
                            Text(stateTitle)
                                .font(.system(size: 20, weight: .semibold))
                            Text(stateSubtitle)
                                .font(.system(size: 14))
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                        }

                        // 录制时长（录音中才显示）
                        if recorder.state == .recording {
                            Text(formatDuration(recorder.currentDuration))
                                .font(.system(size: 32, weight: .light, design: .monospaced))
                                .foregroundColor(.red)
                        }

                        // 今日统计
                        HStack(spacing: 24) {
                            StatBadge(label: "今日录制", value: "\(recorder.todayCount) 段")
                            StatBadge(label: "待上传", value: "\(recordings.filter { !$0.isUploaded }.count) 个")
                        }
                        .padding(.bottom, 8)
                    }
                    .frame(maxWidth: .infinity)
                    .background(Color(.systemBackground))

                    Divider().padding(.horizontal)

                    // ── 上传设置 ──────────────────────────────
                    VStack(alignment: .leading, spacing: 12) {
                        Label("服务器上传", systemImage: "icloud.and.arrow.up")
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(.secondary)
                            .padding(.horizontal)
                            .padding(.top, 16)

                        HStack {
                            TextField("http://192.168.x.x:5678", text: $serverUrl)
                                .textFieldStyle(.roundedBorder)
                                .autocapitalization(.none)
                                .keyboardType(.URL)
                                .onChange(of: serverUrl) { val in
                                    UserDefaults.standard.set(val, forKey: "serverUrl")
                                    uploader.serverUrl = val
                                }
                            Button {
                                Task { await uploader.uploadPending() }
                                showUploadLog = true
                            } label: {
                                Label(uploader.isUploading ? "上传中..." : "立即上传",
                                      systemImage: "arrow.up.circle.fill")
                                    .font(.system(size: 14, weight: .medium))
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(uploader.isUploading)
                        }
                        .padding(.horizontal)

                        if showUploadLog && !uploader.uploadLog.isEmpty {
                            ScrollView {
                                Text(uploader.uploadLog)
                                    .font(.system(size: 11, design: .monospaced))
                                    .foregroundColor(.green)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(10)
                            }
                            .frame(height: 120)
                            .background(Color.black)
                            .cornerRadius(8)
                            .padding(.horizontal)
                        }
                    }

                    Divider().padding(.horizontal).padding(.top, 8)

                    // ── 录音列表 ──────────────────────────────
                    VStack(alignment: .leading, spacing: 0) {
                        HStack {
                            Label("录音记录", systemImage: "waveform")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(.secondary)
                            Spacer()
                            Button {
                                recordings = uploader.listRecordings()
                            } label: {
                                Image(systemName: "arrow.clockwise")
                                    .font(.system(size: 13))
                            }
                        }
                        .padding(.horizontal)
                        .padding(.vertical, 12)

                        if recordings.isEmpty {
                            Text("暂无录音")
                                .font(.system(size: 14))
                                .foregroundColor(.secondary)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 24)
                        } else {
                            ForEach(recordings) { item in
                                RecordingRow(item: item)
                                Divider().padding(.leading, 56)
                            }
                        }
                    }
                    .padding(.bottom, 40)
                }
            }
            .navigationTitle("AI 对话助手")
            .navigationBarTitleDisplayMode(.large)
            .onAppear {
                uploader.serverUrl = serverUrl
                recordings = uploader.listRecordings()
            }
            .onReceive(recorder.$state) { _ in
                recordings = uploader.listRecordings()
            }
        }
    }

    // MARK: - 状态映射

    var stateColor: Color {
        switch recorder.state {
        case .idle:      return .gray
        case .listening: return .green
        case .recording: return .red
        }
    }

    var stateIcon: String {
        switch recorder.state {
        case .idle:      return "mic.slash"
        case .listening: return "ear"
        case .recording: return "mic.fill"
        }
    }

    var stateTitle: String {
        switch recorder.state {
        case .idle:      return "未启动"
        case .listening: return "监听中"
        case .recording: return "录制中"
        }
    }

    var stateSubtitle: String {
        switch recorder.state {
        case .idle:      return "请授权麦克风权限"
        case .listening: return "等待对话声音，自动触发录制"
        case .recording: return "检测到对话，正在录制..."
        }
    }

    func formatDuration(_ seconds: TimeInterval) -> String {
        let m = Int(seconds) / 60
        let s = Int(seconds) % 60
        return String(format: "%02d:%02d", m, s)
    }
}

// MARK: - 子组件

struct StatBadge: View {
    let label: String
    let value: String
    var body: some View {
        VStack(spacing: 2) {
            Text(value)
                .font(.system(size: 22, weight: .semibold))
            Text(label)
                .font(.system(size: 11))
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 10)
        .background(Color(.secondarySystemBackground))
        .cornerRadius(10)
    }
}

struct RecordingRow: View {
    let item: RecordingItem
    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: item.isUploaded ? "checkmark.circle.fill" : "clock.circle")
                .font(.system(size: 24))
                .foregroundColor(item.isUploaded ? .green : .orange)
                .frame(width: 32)
                .padding(.leading, 16)

            VStack(alignment: .leading, spacing: 2) {
                Text(formatTime(item.filename))
                    .font(.system(size: 15, weight: .medium))
                Text("\(item.date)  ·  \(item.duration)  ·  \(item.fileSize)")
                    .font(.system(size: 12))
                    .foregroundColor(.secondary)
            }

            Spacer()
            Text(item.isUploaded ? "已上传" : "待上传")
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(item.isUploaded ? .green : .orange)
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background((item.isUploaded ? Color.green : Color.orange).opacity(0.12))
                .cornerRadius(6)
                .padding(.trailing, 16)
        }
        .padding(.vertical, 10)
        .background(Color(.systemBackground))
    }

    func formatTime(_ filename: String) -> String {
        // filename 是 unix timestamp，转为时间显示
        let base = filename.replacingOccurrences(of: ".m4a", with: "")
                           .replacingOccurrences(of: ".wav", with: "")
        if let ts = Double(base) {
            let date = Date(timeIntervalSince1970: ts)
            let f = DateFormatter()
            f.dateFormat = "HH:mm"
            return f.string(from: date)
        }
        return filename
    }
}
