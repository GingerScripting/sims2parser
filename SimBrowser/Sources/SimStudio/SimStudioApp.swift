import SwiftUI
import AppKit
import UniformTypeIdentifiers

@main
struct SimStudioApp: App {
    init() {
        // Writing to a daemon that has just died would otherwise kill the app
        // with SIGPIPE before the write call can report the error.
        signal(SIGPIPE, SIG_IGN)
        // Needed when launched as a bare SwiftPM executable so the window comes to front.
        NSApplication.shared.setActivationPolicy(.regular)
        DispatchQueue.main.async { NSApplication.shared.activate(ignoringOtherApps: true) }
    }

    var body: some Scene {
        // One window per package URL. Not a document-based scene on purpose:
        // FileDocument hands Swift the file's bytes and wants bytes back, and
        // the whole point is that the app never holds any — the daemon does.
        WindowGroup(for: URL.self) { $url in
            if let url {
                PackageRoot(url: url)
            } else {
                WelcomeView()
            }
        }
        .commands {
            StudioCommands()
        }
    }
}

// MARK: - Focus plumbing so menu commands reach the front window's session

struct SessionFocusKey: FocusedValueKey {
    typealias Value = PackageSession
}

extension FocusedValues {
    var session: PackageSession? {
        get { self[SessionFocusKey.self] }
        set { self[SessionFocusKey.self] = newValue }
    }
}

struct StudioCommands: Commands {
    @FocusedValue(\.session) private var session

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            OpenPackageButton()
        }
        CommandGroup(replacing: .saveItem) {
            Button("Save") { Task { await session?.save() } }
                .keyboardShortcut("s", modifiers: .command)
                .disabled(session == nil || session!.isReadonly || !session!.isDirty)
            Button("Save As…") { session.map { SavePanels.saveAs($0) } }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(session == nil)
        }
        CommandGroup(replacing: .undoRedo) {
            Button(session?.undoLabel ?? "Undo") { Task { await session?.undo() } }
                .keyboardShortcut("z", modifiers: .command)
                .disabled(!(session?.canUndo ?? false))
            Button(session?.redoLabel ?? "Redo") { Task { await session?.redo() } }
                .keyboardShortcut("z", modifiers: [.command, .shift])
                .disabled(!(session?.canRedo ?? false))
        }
    }
}

/// File > Open… — needs a View to reach `openWindow`.
struct OpenPackageButton: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Open Package…") {
            let urls = SavePanels.chooseOpen()
            DispatchQueue.main.async { for url in urls { openWindow(value: url) } }
        }
        .keyboardShortcut("o", modifiers: .command)
    }
}

// MARK: - Panels

@MainActor
enum SavePanels {
    static let packageType: UTType = UTType(filenameExtension: "package") ?? .data

    static func chooseOpen() -> [URL] {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [packageType]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.message = "Choose a Sims 2 .package to open"
        return panel.runModal() == .OK ? panel.urls : []
    }

    /// Save As: the panel only yields a URL; the daemon writes the file.
    static func saveAs(_ session: PackageSession) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [packageType]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = session.title
        panel.directoryURL = session.currentURL.deletingLastPathComponent()
        panel.message = "Neighborhood and game-install folders are refused; save copies elsewhere."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await session.saveAs(url) }
    }

    /// Copy Hood: choose (or create) an empty folder outside the game's
    /// Neighborhoods tree; the daemon copies the hood folder into it and
    /// writes the edited neighborhood package there.
    static func hoodSaveAs(_ session: PackageSession) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "Copy Here"
        panel.message = "Choose an empty folder for the copy of \(session.hoodMeta?.hoodId ?? "the hood"). The game's Neighborhoods folder is refused."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        Task { await session.hoodSaveAs(url) }
    }

    static func chooseExport(suggested: String) -> URL? {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = suggested
        panel.canCreateDirectories = true
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func chooseImport() -> URL? {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Choose a file whose bytes replace this resource"
        return panel.runModal() == .OK ? panel.url : nil
    }
}

// MARK: - The empty window

/// Launch-time options from the environment, for driving the app from a
/// shell where Finder's open-document event is not available.
enum Launch {
    /// `SIMSTUDIO_OPEN=/path/to/file.package` opens that file at launch.
    static let openURL: URL? = ProcessInfo.processInfo.environment["SIMSTUDIO_OPEN"]
        .map { URL(fileURLWithPath: $0) }
}

struct WelcomeView: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(\.dismiss) private var dismiss
    // onAppear fires more than once for a window's root view.
    @MainActor private static var autoOpened = false

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "shippingbox")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text("Sim Studio").font(.title)
            Text("Open a Sims 2 .package to browse and edit its resources.\n"
                 + "Neighborhood saves and the game's own files open read-only.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Open Package…") {
                let urls = SavePanels.chooseOpen()
                if !urls.isEmpty { open(urls) }
            }
            .keyboardShortcut("o", modifiers: .command)
        }
        .padding(40)
        .frame(minWidth: 480, minHeight: 320)
        .onOpenURL { url in open([url]) }
        .onAppear {
            trace("WelcomeView appeared (autoOpened=\(Self.autoOpened))")
            if let url = Launch.openURL, !Self.autoOpened {
                Self.autoOpened = true
                open([url])
            }
        }
    }

    /// Open package windows and close this empty one. Deferred by a beat:
    /// calling openWindow inside the first onAppear (or the launch-time
    /// open-document event) races window creation and yields two windows
    /// for one URL — seen with SIMSTUDIO_TRACE.
    private func open(_ urls: [URL]) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            for url in urls { openWindow(value: url) }
            dismiss()
        }
    }
}
