import AppKit
import Foundation

/// Debug-only: when the app is launched with SIMBROWSER_GEOMETRY_LOG=<path>,
/// samples sidebar-list geometry to that file 4×/second so layout shifts can
/// be diagnosed from real interaction. Inert in normal launches.
enum GeometryProbe {
    private static var timer: Timer?

    static func startIfRequested() {
        guard timer == nil,
              let path = ProcessInfo.processInfo.environment["SIMBROWSER_GEOMETRY_LOG"]
        else { return }
        append("probe: started pid=\(ProcessInfo.processInfo.processIdentifier)\n", to: path)
        timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { _ in
            sample(to: path)
        }
    }

    private static func sample(to path: String) {
        var lines: [String] = []

        func walk(_ v: NSView) {
            if let sv = v as? NSScrollView, let table = sv.documentView as? NSTableView {
                let clip = sv.contentView
                let svWin = sv.convert(sv.bounds, to: nil)
                var line = "scrollX=\(svWin.origin.x) scrollW=\(svWin.width)"
                    + " clipOriginX=\(clip.bounds.origin.x)"
                    + " tableW=\(table.frame.width)"
                    + " cols=\(table.tableColumns.map { $0.width })"
                if table.numberOfRows > 0,
                   let rv = table.rowView(atRow: 0, makeIfNecessary: false) {
                    let rf = rv.convert(rv.bounds, to: nil)
                    line += " row0X=\(rf.origin.x) row0W=\(rf.width) sel=\(rv.isSelected)"
                    if let content = rv.subviews.first {
                        let cf = content.convert(content.bounds, to: nil)
                        line += " cell0X=\(cf.origin.x) cell0W=\(cf.width)"
                        if let inner = content.subviews.first {
                            let inf = inner.convert(inner.bounds, to: nil)
                            line += " inner0X=\(inf.origin.x) inner0W=\(inf.width)"
                        }
                    }
                }
                lines.append(line)
            }
            v.subviews.forEach(walk)
        }

        for win in NSApp.windows where win.isVisible {
            if let root = win.contentView { walk(root) }
        }

        // Ancestor chain of the first table scroll view: whichever ancestor's
        // X moves is the view actually causing the shift.
        outer: for win in NSApp.windows where win.isVisible {
            guard let root = win.contentView else { continue }
            var stack: [NSView] = [root]
            while let v = stack.popLast() {
                if let sv = v as? NSScrollView, sv.documentView is NSTableView {
                    var chain: [String] = []
                    var cur: NSView? = sv
                    while let c = cur {
                        let f = c.convert(c.bounds, to: nil)
                        let name = String(describing: type(of: c)).prefix(34)
                        chain.append("\(name)@x=\(f.origin.x),w=\(f.width)")
                        cur = c.superview
                    }
                    lines.append("chain: " + chain.joined(separator: " < "))
                    break outer
                }
                stack.append(contentsOf: v.subviews)
            }
        }

        let ts = ISO8601DateFormatter().string(from: Date())
        let text = lines.isEmpty
            ? "\(ts) no table scroll view found\n"
            : lines.enumerated().map { "\(ts) sv#\($0.offset) \($0.element)" }.joined(separator: "\n") + "\n"
        append(text, to: path)
    }

    static func mark(_ text: String) {
        guard let path = ProcessInfo.processInfo.environment["SIMBROWSER_GEOMETRY_LOG"] else { return }
        append("marker: \(text)\n", to: path)
    }

    private static func append(_ text: String, to path: String) {
        if !FileManager.default.fileExists(atPath: path) {
            FileManager.default.createFile(atPath: path, contents: nil)
        }
        if let fh = FileHandle(forWritingAtPath: path), let data = text.data(using: .utf8) {
            fh.seekToEndOfFile()
            fh.write(data)
            fh.closeFile()
        }
    }
}
