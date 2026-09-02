import SwiftUI

/// GLOB: which semi-global tree set the object inherits.
struct GlobEditor: View {
    @Binding var draft: JSONValue

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("File name") { TextField("", text: $draft.string("filename")) }
            LabeledContent("Semi-global") { TextField("", text: $draft.string("semi_global")) }
            Text("The name of a semi-global group in the game's own objects, e.g. “ChairGlobals”.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
