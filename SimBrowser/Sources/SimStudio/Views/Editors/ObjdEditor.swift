import SwiftUI

/// OBJD: 108 u16 words, with the confirmed ones named and the GUID pairs
/// exposed as 32-bit values. The daemon serves the name tables, so nothing
/// here knows which word is which.
struct ObjdEditor: View {
    @Binding var draft: JSONValue
    let meta: Meta?

    private var named: [(name: String, index: Int)] {
        (meta?.objdFields ?? [:]).map { (name: $0.key, index: $0.value) }.sorted { $0.index < $1.index }
    }
    private var u32s: [(name: String, low: Int)] {
        (meta?.objdU32Fields ?? [:]).map { (name: $0.key, low: $0.value) }.sorted { $0.low < $1.low }
    }
    private var wordCount: Int { draft["words"]?.arrayValue?.count ?? 0 }

    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 8)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("File name") { TextField("", text: $draft.string("filename")) }
            LabeledContent("Object name") { TextField("", text: $draft.string("name")) }

            EditorHeading("Known fields")
            ForEach(named, id: \.index) { f in
                LabeledContent("\(f.name)  [word \(f.index)]") {
                    NumberField(value: $draft["words"][f.index].intValue)
                }
            }

            EditorHeading("GUIDs")
            ForEach(u32s, id: \.low) { f in
                LabeledContent("\(f.name)  [words \(f.low), \(f.low + 1)]") {
                    HexField(value: $draft["$props"][f.name].intValue, digits: 8, width: 130)
                }
            }
            Text("A GUID edited here wins over its two words below when applied.")
                .font(.caption).foregroundStyle(.secondary)

            EditorHeading("All \(wordCount) words")
            LazyVGrid(columns: columns, alignment: .leading, spacing: 6) {
                ForEach(0..<wordCount, id: \.self) { i in
                    HStack(spacing: 4) {
                        Text("\(i)").font(.caption).foregroundStyle(.secondary).frame(width: 28, alignment: .trailing)
                        HexField(value: $draft["words"][i].intValue, digits: 4, width: 80)
                    }
                }
            }
        }
    }
}
