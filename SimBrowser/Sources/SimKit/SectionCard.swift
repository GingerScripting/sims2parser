import SwiftUI

/// The rounded card with a small-caps heading that both apps use to group a
/// panel's content. Sim Browser's detail pane is a stack of these.
public struct SectionCard<Content: View>: View {
    let title: String
    let content: Content

    public init(_ title: String, @ViewBuilder _ content: () -> Content) {
        self.title = title
        self.content = content()
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased())
                .font(.caption).fontWeight(.semibold)
                .foregroundStyle(.secondary)
                .kerning(0.5)
            content
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 10))
    }
}
