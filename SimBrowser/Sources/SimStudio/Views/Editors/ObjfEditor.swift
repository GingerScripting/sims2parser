import SwiftUI
import SimStudioCore

/// OBJf: the object's function table — (guard, action) tree ids per slot.
struct ObjfEditor: View {
    @Binding var draft: JSONValue
    let meta: Meta?

    private var count: Int { draft["entries"]?.arrayValue?.count ?? 0 }

    private func slotName(_ i: Int) -> String {
        meta?.objfSlots[String(i)] ?? "function \(i)"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("File name") { TextField("", text: $draft.string("filename")) }
            EditorHeading("\(count) slots")
            HStack(spacing: 8) {
                Text("#").frame(width: 30, alignment: .trailing)
                Text("Slot").frame(width: 120, alignment: .leading)
                Text("Guard").frame(width: 96, alignment: .leading)
                Text("Action").frame(width: 96, alignment: .leading)
            }
            .font(.caption).foregroundStyle(.secondary)
            ForEach(0..<count, id: \.self) { i in
                HStack(spacing: 8) {
                    Text("\(i)").frame(width: 30, alignment: .trailing).foregroundStyle(.secondary)
                    Text(slotName(i)).frame(width: 120, alignment: .leading)
                    HexField(value: $draft["entries"][i].int("guard"), digits: 4)
                    HexField(value: $draft["entries"][i].int("action"), digits: 4)
                    RemoveButton { draft["entries"]?.remove(at: i) }
                }
            }
            Button("Add Slot") {
                draft["entries"]?.append(.object(["$type": .string("ObjfEntry"), "guard": .int(0), "action": .int(0)]))
            }
            Text("Tree ids: 0x1000+ are the object's own BHAVs, 0x0100–0x0FFF globals, 0x2000+ semi-globals. 0 means none.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}
