import SwiftUI

/// Decimal integer entry.
struct NumberField: View {
    @Binding var value: Int
    var width: CGFloat = 90

    var body: some View {
        TextField("", value: $value, format: .number)
            .font(.system(.body, design: .monospaced))
            .frame(width: width)
    }
}

/// Hex entry that also accepts decimal. Shows `0x` + `digits` digits.
struct HexField: View {
    @Binding var value: Int
    var digits: Int = 4
    var width: CGFloat = 96
    @State private var text = ""
    @State private var bad = false

    private func render(_ v: Int) -> String { String(format: "0x%0\(digits)X", v) }

    var body: some View {
        TextField("", text: $text)
            .font(.system(.body, design: .monospaced))
            .frame(width: width)
            .foregroundStyle(bad ? .red : .primary)
            .onAppear { text = render(value) }
            .onChange(of: value) { v in
                if parseNumber(text, hexByDefault: true) != v { text = render(v) }
            }
            .onChange(of: text) { t in
                if let n = parseNumber(t, hexByDefault: true), n >= 0 {
                    bad = false
                    if n != value { value = n }
                } else {
                    bad = !t.isEmpty
                }
            }
    }
}

/// Section heading inside an editor.
struct EditorHeading: View {
    let text: String
    init(_ text: String) { self.text = text }
    var body: some View {
        Text(text.uppercased())
            .font(.caption).fontWeight(.semibold)
            .foregroundStyle(.secondary)
            .kerning(0.5)
            .padding(.top, 6)
    }
}

/// A trailing remove button for list rows.
struct RemoveButton: View {
    let action: () -> Void
    var body: some View {
        Button(role: .destructive, action: action) {
            Image(systemName: "minus.circle")
        }
        .buttonStyle(.borderless)
        .help("Remove")
    }
}
