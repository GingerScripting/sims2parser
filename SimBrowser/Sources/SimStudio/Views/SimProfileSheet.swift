import SwiftUI
import AppKit
import SimStudioCore
import SimKit

/// SimPE's "Sim Profile" popup: portrait, name and household, then the
/// prose the daemon wrote from the fields and the game's own biography.
struct SimProfileSheet: View {
    let detail: SimDetail
    let portrait: NSImage?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(detail.fullName.isEmpty ? "Sim #\(detail.nid)" : detail.fullName)
                        .font(.title2).fontWeight(.bold)
                    if let h = detail.household, !h.isEmpty {
                        Text("From the \(h) household").foregroundStyle(.secondary)
                    }
                }
                Spacer()
                PortraitView(image: portrait, size: 128)
            }
            .padding(20)
            Divider()
            IsolatedPane {
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        ForEach(Array(detail.profileParagraphs.enumerated()), id: \.offset) { _, p in
                            Text(p).textSelection(.enabled)
                        }
                        if detail.profileParagraphs.isEmpty {
                            Text("No profile could be written for this sim.").foregroundStyle(.secondary)
                        }
                        if !detail.bio.isEmpty {
                            EditorHeading("In-game biography")
                            Text(detail.bio).textSelection(.enabled)
                        }
                    }
                    .padding(20)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            Divider()
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(width: 540, height: 520)
    }
}

/// A rounded portrait, or a placeholder while there is none.
struct PortraitView: View {
    let image: NSImage?
    let size: CGFloat

    var body: some View {
        Group {
            if let image {
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fill)
            } else {
                ZStack {
                    Rectangle().fill(.quaternary)
                    Image(systemName: "person.crop.square")
                        .font(.system(size: size * 0.5)).foregroundStyle(.tertiary)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size / 12))
    }
}
