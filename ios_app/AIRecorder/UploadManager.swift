import Foundation

struct RecordingItem: Identifiable {
    let id = UUID()
    let url: URL
    let date: String
    let filename: String
    let duration: String
    let isUploaded: Bool
    let fileSize: String
}

class UploadManager: ObservableObject {
    @Published var uploadLog: String = ""
    @Published var isUploading: Bool = false

    var serverUrl: String = "http://192.168.1.100:5678"

    private let recorder = RecorderService.shared

    // MARK: - 扫描录音列表（供 UI 展示）

    func listRecordings() -> [RecordingItem] {
        let baseDir = recorder.recordingsDir()
        guard let dateDirs = try? FileManager.default.contentsOfDirectory(
            at: baseDir, includingPropertiesForKeys: [.isDirectoryKey], options: .skipsHiddenFiles
        ) else { return [] }

        var items: [RecordingItem] = []
        let sortedDirs = dateDirs.sorted { $0.lastPathComponent > $1.lastPathComponent }.prefix(3)

        for dateDir in sortedDirs {
            guard let files = try? FileManager.default.contentsOfDirectory(
                at: dateDir, includingPropertiesForKeys: [.fileSizeKey, .creationDateKey], options: .skipsHiddenFiles
            ) else { continue }

            let audioFiles = files.filter {
                let ext = $0.pathExtension.lowercased()
                return ext == "m4a" || ext == "wav"
            }.sorted { $0.lastPathComponent > $1.lastPathComponent }

            for file in audioFiles {
                let markerURL = file.appendingPathExtension("uploaded")
                let isUploaded = FileManager.default.fileExists(atPath: markerURL.path)
                let attrs = try? FileManager.default.attributesOfItem(atPath: file.path)
                let size = (attrs?[.size] as? Int ?? 0)
                let sizeStr = size > 1024*1024 ? String(format: "%.1fMB", Double(size)/1024/1024)
                                               : String(format: "%.0fKB", Double(size)/1024)

                items.append(RecordingItem(
                    url: file,
                    date: dateDir.lastPathComponent,
                    filename: file.lastPathComponent,
                    duration: estimateDuration(size: size),
                    isUploaded: isUploaded,
                    fileSize: sizeStr
                ))
            }
        }
        return items
    }

    private func estimateDuration(size: Int) -> String {
        // M4A AAC 64kbps ≈ 8000 bytes/s
        let seconds = max(1, size / 8000)
        if seconds < 60 { return "\(seconds)s" }
        return "\(seconds/60)m\(seconds%60)s"
    }

    // MARK: - 批量上传所有未上传文件

    func uploadPending() async {
        guard !serverUrl.isEmpty else { return }

        await MainActor.run {
            isUploading = true
            uploadLog = ""
        }

        let items = listRecordings().filter { !$0.isUploaded }

        if items.isEmpty {
            await log("没有待上传的文件")
            await MainActor.run { isUploading = false }
            return
        }

        await log("发现 \(items.count) 个待上传文件")

        var successCount = 0
        for item in items {
            await log("上传: \(item.filename)...")
            do {
                try await uploadFile(item)
                // 创建 .uploaded 标记
                let marker = item.url.appendingPathExtension("uploaded")
                try? "".write(to: marker, atomically: true, encoding: .utf8)
                await log("✅ \(item.filename) 上传成功")
                successCount += 1
            } catch {
                await log("❌ \(item.filename) 失败: \(error.localizedDescription)")
            }
        }

        await log("完成：\(successCount)/\(items.count) 成功")
        await MainActor.run { isUploading = false }
    }

    // MARK: - 单文件上传

    private func uploadFile(_ item: RecordingItem) async throws {
        guard let url = URL(string: "\(serverUrl)/api/conversations/upload") else {
            throw URLError(.badURL)
        }

        let fileData = try Data(contentsOf: item.url)
        let boundary = "Boundary-\(UUID().uuidString)"

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        var body = Data()
        // file 字段
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"\(item.filename)\"\r\n")
        body.append("Content-Type: audio/m4a\r\n\r\n")
        body.append(fileData)
        body.append("\r\n")
        // date 字段
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"date\"\r\n\r\n")
        body.append(item.date)
        body.append("\r\n")
        // source 字段
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"source\"\r\n\r\n")
        body.append("ios")
        body.append("\r\n")
        body.append("--\(boundary)--\r\n")

        request.httpBody = body

        let (_, response) = try await URLSession.shared.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
            throw URLError(.badServerResponse)
        }
    }

    @MainActor
    private func log(_ msg: String) {
        let ts = DateFormatter.hms.string(from: Date())
        uploadLog += "[\(ts)] \(msg)\n"
    }
}

extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) { append(data) }
    }
}

extension DateFormatter {
    static let hms: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()
}
