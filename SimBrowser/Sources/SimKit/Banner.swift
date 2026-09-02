import SwiftUI

/// A full-width notice strip: an SF Symbol, one line of text, a tinted
/// background. Both apps stack these above their content for session-level
/// facts — a read-only file, a damaged token store.
public struct Banner: View {
    let systemImage: String
    let tint: Color
    let text: String

    public init(_ text: String, systemImage: String, tint: Color) {
        self.text = text
        self.systemImage = systemImage
        self.tint = tint
    }

    public var body: some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage).foregroundStyle(tint)
            Text(text).font(.callout)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(tint.opacity(0.15))
    }
}
