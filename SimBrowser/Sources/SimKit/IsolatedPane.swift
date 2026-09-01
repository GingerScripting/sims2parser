import SwiftUI
import AppKit

/// Generic isolation wrapper: hosts content in its own NSHostingView so
/// scrollable views inside it can't trigger the macOS 26 root-layout
/// extension bug. A ScrollView in a window's root SwiftUI hierarchy makes
/// SwiftUI extend the whole layout past the window edges ("concentric"
/// glass insets), sliding sibling views ~16pt off-window — measured with
/// Sim Browser's GeometryProbe. Anything scrollable goes inside one of these.
public struct IsolatedPane<Content: View>: NSViewRepresentable {
    let content: Content

    public init(@ViewBuilder _ content: () -> Content) { self.content = content() }

    public func makeNSView(context: Context) -> NSHostingView<Content> {
        let v = NSHostingView(rootView: content)
        v.setContentHuggingPriority(.defaultLow, for: .horizontal)
        v.setContentHuggingPriority(.defaultLow, for: .vertical)
        return v
    }

    public func updateNSView(_ nsView: NSHostingView<Content>, context: Context) {
        nsView.rootView = content
    }
}
