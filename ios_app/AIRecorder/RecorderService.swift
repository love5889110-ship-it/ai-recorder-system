import AVFoundation
import Combine

enum RecorderState {
    case idle
    case listening
    case recording
}

class RecorderService: NSObject, ObservableObject {
    static let shared = RecorderService()

    @Published var state: RecorderState = .idle
    @Published var todayCount: Int = 0
    @Published var currentDuration: TimeInterval = 0

    // VAD 参数
    private let RMS_THRESHOLD: Float = 0.015
    private let TRIGGER_SECONDS: Double = 2.0    // 持续有声 2s 后开始录制
    private let SILENCE_SECONDS: Double = 60.0   // 静音 60s 后停止

    private var audioEngine = AVAudioEngine()
    private var audioFile: AVAudioFile?
    private var currentRecordingURL: URL?
    private var voiceStartTime: Date?
    private var silenceStartTime: Date?
    private var recordingStartTime: Date?
    private var durationTimer: Timer?
    private var isEngineRunning = false

    private override init() {
        super.init()
    }

    // MARK: - 权限申请 + 自动启动

    func requestPermissionAndStart() {
        AVAudioSession.sharedInstance().requestRecordPermission { [weak self] granted in
            DispatchQueue.main.async {
                if granted {
                    self?.startListening()
                }
            }
        }
    }

    // MARK: - 开始监听（VAD 模式）

    func startListening() {
        guard state == .idle else { return }

        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .default, options: [.allowBluetooth, .allowBluetoothA2DP])
            try session.setActive(true)

            let inputNode = audioEngine.inputNode
            let format = inputNode.inputFormat(forBus: 0)

            inputNode.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
                self?.processAudioBuffer(buffer)
            }

            try audioEngine.start()
            isEngineRunning = true

            DispatchQueue.main.async { self.state = .listening }
        } catch {
            print("[RecorderService] 启动失败: \(error)")
        }
    }

    // MARK: - 音频处理 + VAD 判断

    private func processAudioBuffer(_ buffer: AVAudioPCMBuffer) {
        let rms = calculateRMS(buffer)
        let now = Date()

        switch state {
        case .listening:
            if rms > RMS_THRESHOLD {
                if voiceStartTime == nil { voiceStartTime = now }
                if now.timeIntervalSince(voiceStartTime!) >= TRIGGER_SECONDS {
                    DispatchQueue.main.async { self.startRecording() }
                }
            } else {
                voiceStartTime = nil
            }

        case .recording:
            if rms < RMS_THRESHOLD {
                if silenceStartTime == nil { silenceStartTime = now }
                if now.timeIntervalSince(silenceStartTime!) >= SILENCE_SECONDS {
                    DispatchQueue.main.async { self.stopRecordingAndRestart() }
                }
            } else {
                silenceStartTime = nil
            }
            // 写入录音文件
            if let audioFile = audioFile {
                try? audioFile.write(from: buffer)
            }

        default:
            break
        }
    }

    // MARK: - 开始录制

    private func startRecording() {
        guard state == .listening else { return }

        let url = buildSavePath()
        currentRecordingURL = url

        do {
            let format = audioEngine.inputNode.inputFormat(forBus: 0)
            audioFile = try AVAudioFile(forWriting: url, settings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 16000.0,
                AVNumberOfChannelsKey: 1,
                AVEncoderBitRateKey: 64000
            ])
        } catch {
            // 回退为 WAV（pcm 格式更易写入）
            audioFile = try? AVAudioFile(forWriting: url.deletingPathExtension().appendingPathExtension("wav"),
                                          settings: audioEngine.inputNode.inputFormat(forBus: 0).settings)
        }

        voiceStartTime = nil
        silenceStartTime = nil
        recordingStartTime = Date()
        state = .recording

        durationTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self, let start = self.recordingStartTime else { return }
            self.currentDuration = Date().timeIntervalSince(start)
        }
    }

    // MARK: - 停止录制，重新监听

    func stopRecordingAndRestart() {
        guard state == .recording else { return }

        durationTimer?.invalidate()
        durationTimer = nil
        audioFile = nil
        currentDuration = 0
        state = .listening
        voiceStartTime = nil
        silenceStartTime = nil

        todayCount = countTodayRecordings()
    }

    func stopAll() {
        durationTimer?.invalidate()
        audioFile = nil
        if isEngineRunning {
            audioEngine.inputNode.removeTap(onBus: 0)
            audioEngine.stop()
            isEngineRunning = false
        }
        try? AVAudioSession.sharedInstance().setActive(false)
        state = .idle
    }

    // MARK: - 工具函数

    private func calculateRMS(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let channelData = buffer.floatChannelData?[0] else { return 0 }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<frameLength {
            sum += channelData[i] * channelData[i]
        }
        return sqrt(sum / Float(frameLength))
    }

    private func buildSavePath() -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dateStr = DateFormatter.yyyyMMdd.string(from: Date())
        let dir = docs.appendingPathComponent("recordings/\(dateStr)", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let timestamp = Int(Date().timeIntervalSince1970)
        return dir.appendingPathComponent("\(timestamp).m4a")
    }

    func recordingsDir() -> URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return docs.appendingPathComponent("recordings", isDirectory: true)
    }

    private func countTodayRecordings() -> Int {
        let dateStr = DateFormatter.yyyyMMdd.string(from: Date())
        let dir = recordingsDir().appendingPathComponent(dateStr)
        let files = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        return files.filter { $0.hasSuffix(".m4a") || $0.hasSuffix(".wav") }.count
    }
}

extension DateFormatter {
    static let yyyyMMdd: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
