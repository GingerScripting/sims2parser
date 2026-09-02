import SwiftUI

/// BHAV: the header, the instruction list, and an operand editor for the
/// selected instruction. Structural edits (insert, delete, move) go through
/// the daemon's `bhav_transform`, which renumbers branch targets; field
/// edits change the draft directly. Nothing reaches the package until Apply.
struct BhavEditor: View {
    @Binding var draft: JSONValue
    @ObservedObject var session: PackageSession
    @State private var selected: Int?
    @State private var warnings: [String] = []

    private var meta: BhavMeta? { session.bhavMeta }
    private var instructions: [JSONValue] { draft["instructions"]?.arrayValue ?? [] }

    struct Row: Identifiable {
        let id: Int
        let opcode: Int
        let name: String
        let t: String
        let f: String
        let operands: String
    }

    private var rows: [Row] {
        instructions.enumerated().map { i, ins in
            let op = ins["opcode"]?.intValue ?? 0
            let t = ins["true_dest"]?.intValue ?? 0
            let f = ins["false_dest"]?.intValue ?? 0
            let ops = (ins["operands"]?.hexBytes ?? []).hexString
            return Row(id: i, opcode: op, name: meta?.opcodeName(op) ?? String(format: "0x%04X", op),
                       t: meta?.destLabel(t) ?? "\(t)", f: meta?.destLabel(f) ?? "\(f)", operands: ops)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            HStack(spacing: 0) {
                VStack(spacing: 0) {
                    Table(rows, selection: $selected) {
                        TableColumn("#") { Text("\($0.id)").monospacedDigit() }.width(36)
                        TableColumn("Instruction") { Text($0.name) }.width(min: 160, ideal: 220)
                        TableColumn("True") { Text($0.t).font(.system(.body, design: .monospaced)) }.width(70)
                        TableColumn("False") { Text($0.f).font(.system(.body, design: .monospaced)) }.width(70)
                        TableColumn("Operands") {
                            Text($0.operands).font(.system(.caption, design: .monospaced)).foregroundStyle(.secondary)
                        }
                    }
                    Divider()
                    structureBar
                }
                .frame(minWidth: 420)
                Divider()
                ScrollView {
                    if let i = selected, i < instructions.count {
                        InstructionForm(instruction: $draft["instructions"][i], index: i,
                                        count: instructions.count, meta: meta, format: format)
                            .padding(14)
                    } else {
                        Text("Select an instruction").foregroundStyle(.secondary).padding()
                    }
                }
                .frame(minWidth: 300)
            }
            if !warnings.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(warnings, id: \.self) { w in
                        Label(w, systemImage: "exclamationmark.triangle").font(.caption)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 6)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.yellow.opacity(0.15))
            }
        }
        .task { await session.loadBhavMeta() }
    }

    private var header: some View {
        HStack(spacing: 14) {
            TextField("Name", text: $draft.string("name")).frame(maxWidth: 320)
            LabeledContent("Type") { NumberField(value: $draft.int("bhav_type"), width: 44) }
            LabeledContent("Args") { NumberField(value: $draft.int("argc"), width: 44) }
            LabeledContent("Locals") { NumberField(value: $draft.int("localc"), width: 44) }
            LabeledContent("Flags") { NumberField(value: $draft.int("flags"), width: 44) }
            Text(String(format: "format 0x%04X · %d instructions", format, instructions.count))
                .font(.caption).foregroundStyle(.secondary)
            if format < 0x8007 {
                Button("Convert to 0x8007") { transform("convert", at: 0) }
                    .controlSize(.small)
                    .help("Older formats hold 8 operand bytes and one-byte branch targets; 0x8007 lifts both limits. Every instruction is kept.")
            }
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    private var format: Int { draft["format_version"]?.intValue ?? 0 }

    private var structureBar: some View {
        HStack(spacing: 8) {
            Button("Insert Above") { transform("insert", at: selected ?? 0) }
            Button("Insert Below") { transform("insert", at: (selected ?? instructions.count - 1) + 1) }
            Button("Delete") { if let i = selected { transform("delete", at: i) } }
                .disabled(selected == nil)
            Button { if let i = selected, i > 0 { transform("move", at: i, to: i - 1) } } label: { Image(systemName: "arrow.up") }
                .disabled((selected ?? 0) == 0)
            Button { if let i = selected, i + 1 < instructions.count { transform("move", at: i, to: i + 1) } } label: { Image(systemName: "arrow.down") }
                .disabled(selected == nil || (selected ?? 0) + 1 >= instructions.count)
            Spacer()
        }
        .controlSize(.small)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
    }

    private func transform(_ op: String, at index: Int, to: Int? = nil) {
        Task {
            guard let r = await session.bhavTransform(draft, op: op, index: index, to: to) else { return }
            draft = r.decoded
            warnings = r.warnings
            switch op {
            case "insert": selected = index
            case "delete": selected = min(index, max(0, instructions.count - 1))
            case "move": selected = to
            default: break                      // convert keeps the selection
            }
            if instructions.isEmpty { selected = nil }
        }
    }
}

/// One instruction: opcode, both branch targets, and operands by name where
/// the layout is known, raw hex otherwise.
struct InstructionForm: View {
    @Binding var instruction: JSONValue
    let index: Int
    let count: Int
    let meta: BhavMeta?
    var format: Int = 0x8007

    private var opcode: Int { instruction["opcode"]?.intValue ?? 0 }
    private var layout: [BhavMeta.Field]? { meta?.layouts[String(opcode)] }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Instruction \(index)").font(.headline)
            LabeledContent("Opcode") {
                HStack {
                    HexField(value: $instruction.int("opcode"), digits: 4)
                    Menu {
                        ForEach(meta?.sortedPrimitives ?? [], id: \.code) { p in
                            Button(String(format: "0x%04X  %@", p.code, p.name)) { instruction["opcode"] = .int(p.code) }
                        }
                    } label: {
                        Text(meta?.opcodeName(opcode) ?? "")
                            .lineLimit(1)
                    }
                    .menuStyle(.borderlessButton)
                }
            }
            destRow("True", key: "true_dest")
            destRow("False", key: "false_dest")

            EditorHeading("Operands")
            if let width = meta?.operandWidth(format), width < 16 {
                Text("This format holds \(width) operand bytes; the rest must stay zero. Convert the tree to 0x8007 to use all 16.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let layout {
                ForEach(layout) { f in
                    LabeledContent(f.name) {
                        HStack {
                            HexField(value: operandBinding(f), digits: f.size * 2, width: CGFloat(60 + f.size * 18))
                            if let values = f.values {
                                Picker("", selection: operandBinding(f)) {
                                    ForEach(values.keys.compactMap { Int($0) }.sorted(), id: \.self) { k in
                                        Text(values[String(k)] ?? "").tag(k)
                                    }
                                    let current = operandBinding(f).wrappedValue
                                    if values[String(current)] == nil {
                                        Text(String(format: "0x%02X (unknown)", current)).tag(current)
                                    }
                                }
                                .labelsHidden()
                                .frame(maxWidth: 200)
                            }
                        }
                    }
                }
                Text("Fields beyond these are unnamed; edit them as raw bytes below.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            LabeledContent("Raw") {
                RawBytesField(bytes: Binding(
                    get: { instruction["operands"]?.hexBytes ?? [] },
                    set: { instruction["operands"] = .hex($0) }), expected: 16)
            }
        }
    }

    private func destRow(_ label: String, key: String) -> some View {
        LabeledContent(label) {
            HStack {
                HexField(value: $instruction.int(key), digits: 4)
                Text(meta?.destLabel(instruction[key]?.intValue ?? 0) ?? "").foregroundStyle(.secondary)
                Menu {
                    Button("Next instruction (\(index + 1))") { instruction[key] = .int(index + 1) }
                        .disabled(index + 1 >= count)
                    Button("→ TRUE") { instruction[key] = .int(meta?.sentinels["true"] ?? 0xFFFD) }
                    Button("→ FALSE") { instruction[key] = .int(meta?.sentinels["false"] ?? 0xFFFE) }
                    Button("→ ERROR") { instruction[key] = .int(meta?.sentinels["error"] ?? 0xFFFC) }
                } label: { Image(systemName: "arrow.triangle.branch") }
                .menuStyle(.borderlessButton)
                .frame(width: 40)
            }
        }
    }

    /// A little-endian integer field inside the 16 operand bytes.
    private func operandBinding(_ f: BhavMeta.Field) -> Binding<Int> {
        Binding<Int>(
            get: {
                let b = instruction["operands"]?.hexBytes ?? []
                var v = 0
                for k in 0..<f.size where f.offset + k < b.count {
                    v |= Int(b[f.offset + k]) << (8 * k)
                }
                return v
            },
            set: { v in
                var b = instruction["operands"]?.hexBytes ?? [UInt8](repeating: 0, count: 16)
                while b.count < 16 { b.append(0) }
                for k in 0..<f.size where f.offset + k < b.count {
                    b[f.offset + k] = UInt8((v >> (8 * k)) & 0xFF)
                }
                instruction["operands"] = .hex(b)
            })
    }
}

/// Hex text for a fixed-length byte string; rejects the wrong length.
struct RawBytesField: View {
    @Binding var bytes: [UInt8]
    let expected: Int
    @State private var text = ""
    @State private var bad = false

    var body: some View {
        TextField("", text: $text)
            .font(.system(.body, design: .monospaced))
            .foregroundStyle(bad ? .red : .primary)
            .onAppear { text = bytes.hexString }
            .onChange(of: bytes) { b in
                if [UInt8](hex: text) != b { text = b.hexString }
            }
            .onChange(of: text) { t in
                if let b = [UInt8](hex: t), b.count == expected {
                    bad = false
                    if b != bytes { bytes = b }
                } else {
                    bad = true
                }
            }
    }
}
