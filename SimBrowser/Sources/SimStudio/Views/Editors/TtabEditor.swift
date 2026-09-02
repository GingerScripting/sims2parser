import SwiftUI
import SimStudioCore

/// TTAB: the pie-menu interaction table. Each entry is an opaque block of
/// bytes with three fields the parser has pinned — action tree, guard tree,
/// and the TTAs string index — exposed as named properties.
struct TtabEditor: View {
    @Binding var draft: JSONValue
    let meta: Meta?

    private var count: Int { draft["entries"]?.arrayValue?.count ?? 0 }
    private var version: Int { draft["version"]?.intValue ?? 0 }
    private var entrySize: Int? { meta?.ttabLayouts[String(version)]?.entrySize }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("File name") { TextField("", text: $draft.string("name")) }
            LabeledContent("Trailing name") { TextField("", text: $draft.string("trailing_name")) }
            LabeledContent("Version") {
                Text(String(format: "0x%02X", version)).font(.system(.body, design: .monospaced))
            }

            EditorHeading("\(count) interactions")
            HStack(spacing: 8) {
                Text("#").frame(width: 30, alignment: .trailing)
                Text("Action").frame(width: 96, alignment: .leading)
                Text("Guard").frame(width: 96, alignment: .leading)
                Text("TTAs index").frame(width: 90, alignment: .leading)
                Text("Raw").frame(maxWidth: .infinity, alignment: .leading)
            }
            .font(.caption).foregroundStyle(.secondary)
            ForEach(0..<count, id: \.self) { i in
                HStack(spacing: 8) {
                    Text("\(i)").frame(width: 30, alignment: .trailing).foregroundStyle(.secondary)
                    HexField(value: $draft["entries"][i]["$props"].int("action"), digits: 4)
                    HexField(value: $draft["entries"][i]["$props"].int("guard"), digits: 4)
                    NumberField(value: $draft["entries"][i]["$props"].int("ttas_index"))
                    Text((draft["entries"]?[i]?["raw"]?.hexBytes ?? []).hexString)
                        .font(.system(.caption, design: .monospaced))
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    RemoveButton { draft["entries"]?.remove(at: i) }
                }
            }
            HStack {
                Button("Add Interaction") { addEntry() }
                    .disabled(entrySize == nil && count == 0)
                Text(count > 0 ? "New entries copy the last one's bytes." : "")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Only the three named fields are understood; the rest of each entry (motive advertising, flags) is preserved byte for byte.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func addEntry() {
        var entry: JSONValue
        if count > 0, let last = draft["entries"]?[count - 1] {
            entry = last
        } else if let size = entrySize {
            entry = .object(["$type": .string("TtabEntry"), "raw": .hex([UInt8](repeating: 0, count: size)),
                             "_ttas_offset": .int(meta?.ttabLayouts[String(version)]?.ttasOffset ?? 8)])
        } else {
            return
        }
        entry["$props"] = .object(["action": .int(0), "guard": .int(0), "ttas_index": .int(0)])
        draft["entries"]?.append(entry)
    }
}
