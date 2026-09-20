import SwiftUI
import SimStudioCore
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
        // A window with no URL is the New Object screen; ⌘N opens another.
        WindowGroup(id: "studio", for: URL.self) { $url in
            if let url {
                PackageRoot(url: url)
            } else {
                NewObjectView()
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
            NewObjectButton()
            OpenPackageButton()
        }
        CommandGroup(replacing: .saveItem) {
            Button("Save") { Task { await session?.save() } }
                .keyboardShortcut("s", modifiers: .command)
                .disabled(session == nil || session!.isReadonly || !session!.isDirty)
            Button("Save As…") { session.map { SavePanels.saveAs($0) } }
                .keyboardShortcut("s", modifiers: [.command, .shift])
                .disabled(session == nil || session!.isProject)
            Divider()
            Button("Export Package…") { session.map { ProjectActions.export($0) } }
                .keyboardShortcut("e", modifiers: [.command, .shift])
                .disabled(!(session?.isProject ?? false))
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

/// File > New Object — a fresh window with no document.
struct NewObjectButton: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("New Object…") { openWindow(id: "studio") }
            .keyboardShortcut("n", modifiers: .command)
    }
}

/// File > Open… — needs a View to reach `openWindow`. Takes a project or a package.
struct OpenPackageButton: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Open…") {
            let urls = SavePanels.chooseOpen()
            DispatchQueue.main.async {
                for url in urls {
                    NSDocumentController.shared.noteNewRecentDocumentURL(url)
                    openWindow(value: url)
                }
            }
        }
        .keyboardShortcut("o", modifiers: .command)
    }
}

// MARK: - Panels

@MainActor
enum SavePanels {
    static let packageType: UTType = UTType(filenameExtension: "package") ?? .data
    /// The project bundle; declared in the app's Info.plist by make_app.sh.
    static let projectType: UTType = UTType(exportedAs: "org.macadmins.sims2.simobject", conformingTo: .package)
    static let projectsFolder = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Documents/Sims 2 Objects", isDirectory: true)

    static func chooseOpen() -> [URL] {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [projectType, packageType]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.treatsFilePackagesAsDirectories = false
        panel.message = "Choose an object project (.simobject) or a Sims 2 .package"
        return panel.runModal() == .OK ? panel.urls : []
    }

    static func chooseProjectToOpen() -> URL? {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [projectType]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.directoryURL = projectsFolder
        panel.message = "Choose an object project"
        return panel.runModal() == .OK ? panel.url : nil
    }

    /// Where a new project goes. Defaults to ~/Documents/Sims 2 Objects,
    /// created on first use; the game's own folders are refused by the daemon.
    static func chooseProject(named name: String) -> URL? {
        try? FileManager.default.createDirectory(at: projectsFolder, withIntermediateDirectories: true)
        let panel = NSSavePanel()
        panel.allowedContentTypes = [projectType]
        panel.canCreateDirectories = true
        panel.nameFieldStringValue = name.replacingOccurrences(of: "/", with: "-") + ".simobject"
        panel.directoryURL = projectsFolder
        panel.message = "Where to keep the project. Export later writes the .package the game loads."
        panel.prompt = "Create"
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        return url.pathExtension == "simobject" ? url : url.appendingPathExtension("simobject")
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
    /// `SIMSTUDIO_SIM=<nid>` opens a neighborhood straight into Sims mode
    /// with that sim selected; `SIMSTUDIO_TAB=<character|skills|…>` picks
    /// the editor page.
    static let sim: Int? = ProcessInfo.processInfo.environment["SIMSTUDIO_SIM"].flatMap(Int.init)
    static let tab: SimTab? = ProcessInfo.processInfo.environment["SIMSTUDIO_TAB"].flatMap(SimTab.init(rawValue:))
    /// `SIMSTUDIO_PAGE=<identity|resources>` picks a project window's page.
    static let page: String? = ProcessInfo.processInfo.environment["SIMSTUDIO_PAGE"]
    /// `SIMSTUDIO_PICK=<guid>` selects that catalog object on the New Object
    /// screen and moves to the naming step, for snapshots.
    static let pick: UInt32? = ProcessInfo.processInfo.environment["SIMSTUDIO_PICK"]
        .flatMap { UInt32($0.hasPrefix("0x") ? String($0.dropFirst(2)) : $0, radix: $0.hasPrefix("0x") ? 16 : 10) }
    /// `SIMSTUDIO_SNAPSHOT=/path/out.png` writes the window's contents there
    /// a few seconds after it opens — the way to see the app from a shell
    /// that has no screen-recording permission.
    static let snapshot: URL? = ProcessInfo.processInfo.environment["SIMSTUDIO_SNAPSHOT"]
        .map { URL(fileURLWithPath: $0) }

    /// Render the window that shows `session` to `snapshot`.
    @MainActor static func takeSnapshot(title: String) {
        guard let url = snapshot else { return }
        // A sheet is its own window; when one is up, that is what to show.
        let sheet = NSApp.windows.first { $0.isSheet && $0.isVisible }
        guard let window = sheet ?? NSApp.windows.first(where: { $0.title == title && $0.isVisible }) ?? NSApp.keyWindow,
              let view = window.contentView?.superview ?? window.contentView,
              let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else {
            trace("snapshot: no window titled \(title); windows: "
                  + NSApp.windows.map { "'\($0.title)' visible=\($0.isVisible)" }.joined(separator: ", "))
            return
        }
        view.layoutSubtreeIfNeeded()
        view.displayIfNeeded()
        view.cacheDisplay(in: view.bounds, to: rep)
        // Flatten onto the window colour: the cached rep keeps the window's
        // transparency, which reads as black wherever nothing was drawn.
        let flat = NSImage(size: rep.size)
        flat.lockFocus()
        (window.backgroundColor ?? .windowBackgroundColor).setFill()
        NSRect(origin: .zero, size: rep.size).fill()
        rep.draw(in: NSRect(origin: .zero, size: rep.size))
        flat.unlockFocus()
        if let tiff = flat.tiffRepresentation, let out = NSBitmapImageRep(data: tiff),
           let png = out.representation(using: .png, properties: [:]) {
            try? png.write(to: url)
            trace("snapshot: wrote \(url.path) (\(Int(rep.size.width))x\(Int(rep.size.height)))")
        }
    }
}
