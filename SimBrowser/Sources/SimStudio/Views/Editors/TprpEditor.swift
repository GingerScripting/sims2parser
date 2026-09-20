import SwiftUI
import SimStudioCore

/// TPRP: the names a behaviour function's parameters and locals were given.
/// Purely labels — nothing here changes what the BHAV does — so the form is
/// two lists. Each parameter also carries a one-byte flag the daemon keeps
/// in `param_flags`; adding a parameter adds a flag of 1, the common value.
struct TprpEditor: View {
    @Binding var draft: JSONValue

    private var params: Int { draft["params"]?.arrayValue?.count ?? 0 }
    private var locals: Int { draft["locals"]?.arrayValue?.count ?? 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            LabeledContent("Name") {
                TextField("", text: $draft.string("name"))
            }
            Text("Labels for the behaviour function of the same instance number. Parameters are "
                 + "what the function is called with; locals are its scratch variables.")
                .font(.caption).foregroundStyle(.secondary)

            labelList("Parameters", key: "params", count: params, prefix: "param") {
                draft["params"]?.append(.string("param \(params)"))
                var flags = draft["param_flags"]?.hexBytes ?? []
                flags.append(1)
                draft["param_flags"] = .hex(flags)
            } remove: { i in
                draft["params"]?.remove(at: i)
                var flags = draft["param_flags"]?.hexBytes ?? []
                if i < flags.count { flags.remove(at: i) }
                draft["param_flags"] = .hex(flags)
            }
            labelList("Locals", key: "locals", count: locals, prefix: "local") {
                draft["locals"]?.append(.string("local \(locals)"))
            } remove: { i in
                draft["locals"]?.remove(at: i)
            }
        }
    }

    private func labelList(_ title: String, key: String, count: Int, prefix: String,
                           add: @escaping () -> Void, remove: @escaping (Int) -> Void) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            EditorHeading("\(count == 1 ? "1 \(prefix)" : "\(count) \(prefix)s")")
            ForEach(0..<count, id: \.self) { i in
                HStack(spacing: 8) {
                    Text("\(prefix) \(i)").font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary).frame(width: 64, alignment: .trailing)
                    TextField("", text: label(key, i))
                    RemoveButton { remove(i) }
                }
            }
            Button("Add \(prefix.capitalized)", action: add)
        }
    }

    /// A string element of a plain array in the draft.
    private func label(_ key: String, _ i: Int) -> Binding<String> {
        Binding(get: { draft[key]?[i]?.stringValue ?? "" },
                set: { draft[key]?[i] = .string($0) })
    }
}
