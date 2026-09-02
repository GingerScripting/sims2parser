import SwiftUI
import SimStudioCore

/// BCON: up to 255 u16 tuning constants and a flag byte.
struct BconEditor: View {
    @Binding var draft: JSONValue
    @State private var hex = false

    private var count: Int { draft["values"]?.arrayValue?.count ?? 0 }
    private let columns = [GridItem(.adaptive(minimum: 150), spacing: 8)]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("File name") { TextField("", text: $draft.string("filename")) }
            Toggle("Flag 0x80 set", isOn: Binding(
                get: { ((draft["flag"]?.intValue ?? 0) & 0x80) != 0 },
                set: { draft["flag"] = .int($0 ? 0x80 : 0) }))
            HStack {
                EditorHeading("\(count) constants")
                Spacer()
                Toggle("Hex", isOn: $hex).toggleStyle(.switch).controlSize(.small)
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: 6) {
                ForEach(0..<count, id: \.self) { i in
                    HStack(spacing: 4) {
                        Text("\(i)").font(.caption).foregroundStyle(.secondary).frame(width: 28, alignment: .trailing)
                        if hex {
                            HexField(value: $draft["values"][i].intValue, digits: 4, width: 80)
                        } else {
                            NumberField(value: $draft["values"][i].intValue, width: 80)
                        }
                    }
                }
            }
            HStack {
                Button("Add Constant") { if count < 255 { draft["values"]?.append(.int(0)) } }
                    .disabled(count >= 255)
                Button("Remove Last") { if count > 0 { draft["values"]?.remove(at: count - 1) } }
                    .disabled(count == 0)
            }
            Text("BHAVs index these by position, so inserting in the middle shifts every constant after it.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
