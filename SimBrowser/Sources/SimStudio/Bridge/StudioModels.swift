import Foundation

/// A resource's identity: type, group, instance, and the v7.2 resource id.
struct TGI: Codable, Hashable, CustomStringConvertible {
    var type: UInt32
    var group: UInt32
    var instance: UInt32
    var instanceHi: UInt32 = 0

    enum CodingKeys: String, CodingKey {
        case type, group, instance
        case instanceHi = "instance_hi"
    }

    var description: String {
        var s = "g=\(hex8(group)) i=\(hex8(instance))"
        if instanceHi != 0 { s += " r=\(hex8(instanceHi))" }
        return s
    }

    var json: JSONValue {
        .object(["type": .number(Double(type)), "group": .number(Double(group)),
                 "instance": .number(Double(instance)), "instance_hi": .number(Double(instanceHi))])
    }
}

/// What `open`, `status`, and every mutating call return.
struct PackageSummary: Decodable {
    let path: String
    let readonly: Bool
    let readonlyReason: String
    let version: String
    let count: Int
    let compressedCount: Int
    let dirty: Bool
    let canUndo: Bool
    let canRedo: Bool
    let undoLabel: String?
    let redoLabel: String?

    enum CodingKeys: String, CodingKey {
        case path, readonly, version, count, dirty
        case readonlyReason = "readonly_reason"
        case compressedCount = "compressed_count"
        case canUndo = "can_undo"
        case canRedo = "can_redo"
        case undoLabel = "undo_label"
        case redoLabel = "redo_label"
    }
}

/// One row of the resource table.
struct ResourceRow: Decodable, Identifiable, Hashable {
    let type: UInt32
    let typeName: String
    let group: UInt32
    let instance: UInt32
    let instanceHi: UInt32
    let size: Int
    let compressed: Bool
    let decodable: Bool
    let bhav: Bool

    enum CodingKeys: String, CodingKey {
        case type, group, instance, size, compressed, decodable, bhav
        case typeName = "type_name"
        case instanceHi = "instance_hi"
    }

    var tgi: TGI { TGI(type: type, group: group, instance: instance, instanceHi: instanceHi) }
    var id: TGI { tgi }

    /// Build the table from the daemon's compact `index` reply:
    /// `{"columns": [...], "rows": [[type, group, instance, hi, size, flags]], "type_names": {...}}`.
    static func rows(fromIndex raw: Any) throws -> [ResourceRow] {
        guard let obj = raw as? [String: Any],
              let rows = obj["rows"] as? [[Any]],
              let names = obj["type_names"] as? [String: String] else {
            throw RPCFailure.transport("unexpected index shape")
        }
        var out: [ResourceRow] = []
        out.reserveCapacity(rows.count)
        for r in rows where r.count >= 6 {
            guard let t = (r[0] as? NSNumber)?.uint32Value, let g = (r[1] as? NSNumber)?.uint32Value,
                  let i = (r[2] as? NSNumber)?.uint32Value, let hi = (r[3] as? NSNumber)?.uint32Value,
                  let size = (r[4] as? NSNumber)?.intValue, let flags = (r[5] as? NSNumber)?.intValue
            else { continue }
            out.append(ResourceRow(type: t, typeName: names[String(t)] ?? hex8(t), group: g, instance: i,
                                   instanceHi: hi, size: size, compressed: flags & 1 != 0,
                                   decodable: flags & 2 != 0, bhav: flags & 4 != 0))
        }
        return out
    }

    var compressedSort: Int { compressed ? 1 : 0 }
    var decoderSort: Int { decodable ? 2 : (bhav ? 1 : 0) }
    var decoderLabel: String { decodable ? "yes" : (bhav ? "tree" : "") }
}

/// The decompiler's view of a BHAV, rendered by the daemon.
struct BhavRender: Decodable {
    let flat: String?
    let tree: String?
    let error: String?
    let name: String?
    let format: Int?
    let type: Int?
    let argc: Int?
    let localc: Int?
    let count: Int?
}

/// Everything `get_resource` returns.
struct ResourceDetail: Decodable {
    let row: ResourceRow
    let hex: String
    let decoded: JSONValue?
    let decodeError: String?
    let bhav: BhavRender?

    enum CodingKeys: String, CodingKey {
        case hex, decoded, bhav
        case decodeError = "decode_error"
    }

    init(from decoder: Decoder) throws {
        row = try ResourceRow(from: decoder)
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hex = try c.decode(String.self, forKey: .hex)
        let d = try c.decodeIfPresent(JSONValue.self, forKey: .decoded)
        decoded = (d?.isNull ?? true) ? nil : d
        decodeError = try c.decodeIfPresent(String.self, forKey: .decodeError)
        bhav = try c.decodeIfPresent(BhavRender.self, forKey: .bhav)
    }

    var tgi: TGI { row.tgi }
    var bytes: [UInt8] { [UInt8](hex: hex) ?? [] }
}

/// What `undo`/`redo` return: which step, which TGIs to refresh, plus the
/// usual summary.
struct HistoryResult: Decodable {
    let label: String
    let tgis: [TGI]
    let summary: PackageSummary

    enum CodingKeys: String, CodingKey { case label, tgis }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = try c.decode(String.self, forKey: .label)
        tgis = try c.decode([TGI].self, forKey: .tgis)
        summary = try PackageSummary(from: decoder)
    }
}

/// A resource row plus the summary — what add/rename return.
struct RowResult: Decodable {
    let row: ResourceRow
    let summary: PackageSummary
    init(from decoder: Decoder) throws {
        row = try ResourceRow(from: decoder)
        summary = try PackageSummary(from: decoder)
    }
}

struct PutResult: Decodable {
    let size: Int
    let changed: Bool
    let summary: PackageSummary
    enum CodingKeys: String, CodingKey { case size, changed }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        size = try c.decode(Int.self, forKey: .size)
        changed = try c.decode(Bool.self, forKey: .changed)
        summary = try PackageSummary(from: decoder)
    }
}

/// What the BHAV editor needs to label instructions: primitive names, the
/// operand layouts the toolkit has pinned, and the exit sentinels.
struct BhavMeta: Decodable {
    struct Field: Decodable, Identifiable {
        let name: String
        let offset: Int
        let size: Int
        let values: [String: String]?
        var id: String { name }
    }

    struct Format: Decodable {
        let instrSize: Int
        let operandLen: Int
        let addrWidth: Int
        enum CodingKeys: String, CodingKey {
            case instrSize = "instr_size"
            case operandLen = "operand_len"
            case addrWidth = "addr_width"
        }
    }

    let primitives: [String: String]
    let layouts: [String: [Field]]
    let sentinels: [String: Int]
    let formats: [String: Format]

    func operandWidth(_ format: Int) -> Int? { formats[String(format)]?.operandLen }

    func opcodeName(_ op: Int) -> String {
        if op < 0x100 { return primitives[String(op)] ?? String(format: "Primitive 0x%04X", op) }
        if op < 0x1000 { return String(format: "Global BHAV 0x%04X", op) }
        if op < 0x2000 { return String(format: "Local BHAV 0x%04X", op) }
        return String(format: "Semiglobal BHAV 0x%04X", op)
    }

    func destLabel(_ d: Int) -> String {
        if d == sentinels["true"] { return "→ TRUE" }
        if d == sentinels["false"] { return "→ FALSE" }
        if d >= (sentinels["floor"] ?? 0xFFFC) { return "→ ERROR" }
        return "→ \(d)"
    }

    var sortedPrimitives: [(code: Int, name: String)] {
        primitives.compactMap { k, v in Int(k).map { (code: $0, name: v) } }.sorted { $0.code < $1.code }
    }
}

/// What `bhav_transform` returns.
struct BhavTransform: Decodable {
    let decoded: JSONValue
    let warnings: [String]
}

/// Static tables served by the daemon so the app carries no format knowledge.
struct Meta: Decodable {
    let `protocol`: Int
    let typeNames: [String: String]
    let decodableTypes: [UInt32]
    let bhavType: UInt32
    let objdFields: [String: Int]
    let objdU32Fields: [String: Int]
    let objdWordCount: Int
    let objfSlots: [String: String]
    let strFormats: [String: Int]
    let ttabLayouts: [String: TtabLayout]

    struct TtabLayout: Decodable {
        let entrySize: Int
        let ttasOffset: Int
        enum CodingKeys: String, CodingKey {
            case entrySize = "entry_size"
            case ttasOffset = "ttas_offset"
        }
    }

    enum CodingKeys: String, CodingKey {
        case `protocol`
        case typeNames = "type_names"
        case decodableTypes = "decodable_types"
        case bhavType = "bhav_type"
        case objdFields = "objd_fields"
        case objdU32Fields = "objd_u32_fields"
        case objdWordCount = "objd_word_count"
        case objfSlots = "objf_slots"
        case strFormats = "str_formats"
        case ttabLayouts = "ttab_layouts"
    }

    func typeName(_ type: UInt32) -> String {
        typeNames[String(type)] ?? hex8(type)
    }

    /// Known types, sorted by name, for the New Resource picker.
    var knownTypes: [(id: UInt32, name: String)] {
        typeNames.compactMap { k, v in UInt32(k).map { (id: $0, name: v) } }
            .sorted { $0.name.lowercased() < $1.name.lowercased() }
    }
}
