import SwiftUI
import AppKit

/// Mirrors a dirty flag onto the hosting window's `isDocumentEdited`, so an
/// unsaved change shows the native way — the dot in the close button — rather
/// than as text appended to the title. SwiftUI has no modifier for this on
/// macOS 13, but any view can reach its window once it is in a hierarchy.
public struct WindowEditedMarker: NSViewRepresentable {
    let isEdited: Bool

    public init(isEdited: Bool) { self.isEdited = isEdited }

    public func makeNSView(context: Context) -> NSView {
        let v = NSView(frame: .zero)
        v.setContentHuggingPriority(.required, for: .horizontal)
        v.setContentHuggingPriority(.required, for: .vertical)
        return v
    }

    public func updateNSView(_ nsView: NSView, context: Context) {
        // The window is not attached during the first update.
        DispatchQueue.main.async { [isEdited] in
            nsView.window?.isDocumentEdited = isEdited
        }
    }
}
