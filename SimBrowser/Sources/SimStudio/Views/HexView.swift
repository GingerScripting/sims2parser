import SwiftUI
import SimStudioCore

/// A classic 16-bytes-per-row dump: offset, hex, ASCII. Rows are built on
/// demand so a multi-megabyte texture costs nothing until scrolled to.
struct HexView: View {
    let bytes: [UInt8]

    private var rowCount: Int { (bytes.count + 15) / 16 }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(0..<rowCount, id: \.self) { row in
                    Text(line(row))
                        .font(.system(.body, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(16)
            .textSelection(.enabled)
        }
    }

    private func line(_ row: Int) -> String {
        let start = row * 16
        let end = min(start + 16, bytes.count)
        var hex = ""
        var ascii = ""
        for i in start..<end {
            let b = bytes[i]
            hex += String(format: i - start == 8 ? " %02x " : "%02x ", b)
            ascii.append(b >= 0x20 && b < 0x7F ? Character(UnicodeScalar(b)) : ".")
        }
        let pad = String(repeating: "   ", count: 16 - (end - start)) + ((end - start) <= 8 ? " " : "")
        return String(format: "%08x  ", start) + hex + pad + " |" + ascii + "|"
    }
}
