import SwiftUI

@main
struct AIRecorderApp: App {
    @StateObject private var recorder = RecorderService.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(recorder)
                .onAppear {
                    recorder.requestPermissionAndStart()
                }
        }
    }
}
