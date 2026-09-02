import SwiftUI
import SimStudioCore

/// Fallback for a decoded type without a dedicated form: the JSON the
/// daemon produced, editable as text. Anything the daemon can rebuild from
/// this is a valid edit; anything else comes back as a build error on Apply.
struct GenericEditor: View {
    @Binding var draft: JSONValue
    @State private var text = ""
    @State private var parseError: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("No form for \(draft.typeName ?? "this type") yet — editing the decoded JSON directly.")
                .font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $text)
                .font(.system(.body, design: .monospaced))
                .frame(minHeight: 300)
            if let e = parseError {
                Label(e, systemImage: "exclamationmark.triangle").foregroundStyle(.red).font(.caption)
            }
        }
        .onAppear { text = draft.prettyPrinted }
        .onChange(of: draft) { new in
            if (try? JSONValue.parse(text)) != new { text = new.prettyPrinted }
        }
        .onChange(of: text) { t in
            do {
                let v = try JSONValue.parse(t)
                parseError = nil
                if v != draft { draft = v }
            } catch {
                parseError = "Not valid JSON: \(error.localizedDescription)"
            }
        }
    }
}
