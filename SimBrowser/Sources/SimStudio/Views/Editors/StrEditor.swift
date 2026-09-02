import SwiftUI
import SimStudioCore

/// STR# / TTAs / CTSS: a language-tagged string table.
struct StrEditor: View {
    @Binding var draft: JSONValue
    let meta: Meta?

    private var withDescFormat: Int { meta?.strFormats["with_desc"] ?? 0xFFFD }
    private var noDescFormat: Int { meta?.strFormats["no_desc"] ?? 0xFFFF }
    private var withDesc: Bool { (draft["format"]?.intValue ?? noDescFormat) == withDescFormat }
    private var count: Int { draft["entries"]?.arrayValue?.count ?? 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("Name") {
                TextField("", text: $draft.string("name"))
            }
            Picker("Format", selection: $draft.int("format")) {
                Text("Value only (0xFFFF)").tag(noDescFormat)
                Text("Value and description (0xFFFD)").tag(withDescFormat)
            }
            .frame(maxWidth: 420)

            EditorHeading("\(count) entries")
            HStack(spacing: 8) {
                Text("Lang").frame(width: 50, alignment: .leading)
                Text("Value").frame(maxWidth: .infinity, alignment: .leading)
                if withDesc { Text("Description").frame(maxWidth: .infinity, alignment: .leading) }
                Color.clear.frame(width: 20)
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            ForEach(0..<count, id: \.self) { i in
                HStack(spacing: 8) {
                    NumberField(value: $draft["entries"][i].int("lang"), width: 50)
                    TextField("", text: $draft["entries"][i].string("value"))
                    if withDesc {
                        TextField("", text: $draft["entries"][i].string("desc"))
                    }
                    RemoveButton { draft["entries"]?.remove(at: i) }
                }
            }
            Button("Add Entry") {
                draft["entries"]?.append(.object([
                    "$type": .string("StrEntry"), "lang": .int(1), "value": .string(""), "desc": .string("")]))
            }
            Text("Language 1 is US English. Strings are Latin-1; characters outside it are replaced on save.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
