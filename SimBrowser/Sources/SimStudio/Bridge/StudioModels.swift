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
    let flags: Int

    enum CodingKeys: String, CodingKey {
        case type, group, instance, size, compressed, decodable, bhav, flags
        case typeName = "type_name"
        case instanceHi = "instance_hi"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decode(UInt32.self, forKey: .type)
        typeName = try c.decode(String.self, forKey: .typeName)
        group = try c.decode(UInt32.self, forKey: .group)
        instance = try c.decode(UInt32.self, forKey: .instance)
        instanceHi = try c.decode(UInt32.self, forKey: .instanceHi)
        size = try c.decode(Int.self, forKey: .size)
        compressed = try c.decode(Bool.self, forKey: .compressed)
        decodable = try c.decode(Bool.self, forKey: .decodable)
        bhav = try c.decode(Bool.self, forKey: .bhav)
        flags = try c.decodeIfPresent(Int.self, forKey: .flags) ?? 0
    }

    init(type: UInt32, typeName: String, group: UInt32, instance: UInt32, instanceHi: UInt32,
         size: Int, compressed: Bool, decodable: Bool, bhav: Bool, flags: Int) {
        self.type = type; self.typeName = typeName; self.group = group; self.instance = instance
        self.instanceHi = instanceHi; self.size = size; self.compressed = compressed
        self.decodable = decodable; self.bhav = bhav; self.flags = flags
    }

    var tgi: TGI { TGI(type: type, group: group, instance: instance, instanceHi: instanceHi) }
    var id: TGI { tgi }
    var isTexture: Bool { flags & 8 != 0 }
    var isMesh: Bool { flags & 16 != 0 }
    var hasPreview: Bool { isTexture || isMesh }

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
                                   decodable: flags & 2 != 0, bhav: flags & 4 != 0, flags: flags))
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

// MARK: - Object Workshop and tools

struct ObjectInfo: Decodable, Identifiable, Hashable {
    let index: Int
    let instance: UInt32
    let group: UInt32
    let guid: UInt32
    let originalGuid: UInt32
    let filename: String
    let name: String
    let price: Int
    let ttabId: Int
    let ctssId: Int
    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, instance, group, guid, filename, name, price
        case originalGuid = "original_guid"
        case ttabId = "ttab_id"
        case ctssId = "ctss_id"
    }
}

struct ObjectsResult: Decodable {
    let objects: [ObjectInfo]
}

struct ClonePatch: Decodable, Identifiable {
    let instance: Int
    let bhavName: String
    let instrIndex: Int
    let opcode: Int
    let opcodeName: String
    let operandOffset: Int
    let knownLayout: Bool
    let applied: Bool
    var id: String { "\(instance)/\(instrIndex)/\(operandOffset)" }

    enum CodingKeys: String, CodingKey {
        case instance, opcode, applied
        case bhavName = "bhav_name"
        case instrIndex = "instr_index"
        case opcodeName = "opcode_name"
        case operandOffset = "operand_offset"
        case knownLayout = "known_layout"
    }
}

struct CloneResult: Decodable {
    let sourceGuid: UInt32
    let newGuid: UInt32
    let resourceCount: Int
    let changed: Int
    let patches: [ClonePatch]
    let warnings: [String]
    let summary: PackageSummary

    enum CodingKeys: String, CodingKey {
        case changed, patches, warnings
        case sourceGuid = "source_guid"
        case newGuid = "new_guid"
        case resourceCount = "resource_count"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sourceGuid = try c.decode(UInt32.self, forKey: .sourceGuid)
        newGuid = try c.decode(UInt32.self, forKey: .newGuid)
        resourceCount = try c.decode(Int.self, forKey: .resourceCount)
        changed = try c.decode(Int.self, forKey: .changed)
        patches = try c.decode([ClonePatch].self, forKey: .patches)
        warnings = try c.decode([String].self, forKey: .warnings)
        summary = try PackageSummary(from: decoder)
    }
}

struct ScanResult: Decodable {
    let packages: Int
    let guids: Int
    let collisions: [String: [String]]
    let duplicates: [String: [String]]
}

struct MergeResult: Decodable {
    let added: Int
    let replaced: Int
    let skipped: Int
    let summary: PackageSummary
    enum CodingKeys: String, CodingKey { case added, replaced, skipped }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        added = try c.decode(Int.self, forKey: .added)
        replaced = try c.decode(Int.self, forKey: .replaced)
        skipped = try c.decode(Int.self, forKey: .skipped)
        summary = try PackageSummary(from: decoder)
    }
}

struct SplitResult: Decodable {
    let path: String
    let written: Int
    let summary: PackageSummary
    enum CodingKeys: String, CodingKey { case path, written }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        written = try c.decode(Int.self, forKey: .written)
        summary = try PackageSummary(from: decoder)
    }
}

struct Finding: Decodable, Identifiable {
    let severity: String
    let code: String
    let title: String
    let detail: [String]
    let fix: String?
    let id = UUID()
    enum CodingKeys: String, CodingKey { case severity, code, title, detail, fix }
}

struct DoctorResult: Decodable {
    let root: String
    let packages: Int
    let findings: [Finding]
}

struct TexturePreview: Decodable {
    let name: String
    let width: Int
    let height: Int
    let format: String
    let levels: Int
    let shownWidth: Int
    let shownHeight: Int
    let pngB64: String

    enum CodingKeys: String, CodingKey {
        case name, width, height, format, levels
        case shownWidth = "shown_width"
        case shownHeight = "shown_height"
        case pngB64 = "png_b64"
    }
}

struct MeshPreview: Decodable {
    struct Group: Decodable, Identifiable {
        let name: String
        let faces: Int
        var id: String { name }
    }
    let name: String
    let obj: String
    let faces: Int
    let groups: [Group]
    let partial: Bool
}

/// A server progress event.
struct Progress: Equatable {
    let op: String
    let done: Int
    let total: Int
    let note: String

    init?(_ value: JSONValue) {
        guard value["event"]?.stringValue == "progress", let op = value["op"]?.stringValue else { return nil }
        self.op = op
        done = value["done"]?.intValue ?? 0
        total = value["total"]?.intValue ?? 0
        note = value["note"]?.stringValue ?? ""
    }
}

// MARK: - Neighborhoods

struct FieldDef: Decodable, Identifiable {
    let name: String
    let kind: String      // id | int | bool | meter | enum:<table> | flags:<table>
    let offset: Int
    let fmt: String
    var id: String { name }

    var table: String? {
        let parts = kind.split(separator: ":", maxSplits: 1)
        return parts.count == 2 ? String(parts[1]) : nil
    }
    var isEnum: Bool { kind.hasPrefix("enum:") }
    var isFlags: Bool { kind.hasPrefix("flags:") }
    var section: String {
        if let dot = name.firstIndex(of: ".") { return String(name[..<dot]) }
        return "profile"
    }
    var label: String {
        let base = name.contains(".") ? String(name.split(separator: ".").last ?? "") : name
        return base.replacingOccurrences(of: "_", with: " ")
    }
}

/// hoodcheck's verdict on the neighborhood's token store.
struct HoodCheck: Decodable {
    let healthy: Bool
    let error: String
    let declared: Int
    let actual: Int
    let sdscCount: Int
    let missingNids: [Int]
    let trailing: Int
    let chunkAligned: Bool

    enum CodingKeys: String, CodingKey {
        case healthy, error, declared, actual, trailing
        case sdscCount = "sdsc_count"
        case missingNids = "missing_nids"
        case chunkAligned = "chunk_aligned"
    }

    var summary: String {
        if !error.isEmpty { return "Token store could not be checked: \(error)" }
        var s = "Token store declares \(declared) sim groups but holds \(actual)"
        if !missingNids.isEmpty { s += " — missing sims \(missingNids.prefix(6).map(String.init).joined(separator: ", "))" }
        if chunkAligned { s += ". The store ends on a buffer-chunk boundary, the mark of a save that was cut off" }
        return s + ". The game loops forever loading this hood; hoodcheck.py --repair can write a padded copy."
    }
}

struct HoodMeta: Decodable {
    let isHood: Bool
    let hoodId: String?
    let check: HoodCheck?
    let sdscFields: [FieldDef]
    let sdscTables: [String: [String: String]]
    let srelFields: [FieldDef]
    let srelTables: [String: [String: String]]
    let memoryOwnerSlot: Int
    let memorySubjectSlot: Int

    enum CodingKeys: String, CodingKey {
        case check
        case isHood = "is_hood"
        case hoodId = "hood_id"
        case sdscFields = "sdsc_fields"
        case sdscTables = "sdsc_tables"
        case srelFields = "srel_fields"
        case srelTables = "srel_tables"
        case memoryOwnerSlot = "memory_owner_slot"
        case memorySubjectSlot = "memory_subject_slot"
    }

    /// Sorted (value, label) pairs for an enum/flags table.
    func options(_ table: String, from tables: [String: [String: String]]) -> [(value: Int, label: String)] {
        (tables[table] ?? [:]).compactMap { k, v in Int(k).map { (value: $0, label: v) } }
            .sorted { $0.label.lowercased() < $1.label.lowercased() }
    }
}

struct SimRow: Decodable, Identifiable, Hashable {
    let nid: Int
    let guid: UInt32
    let first: String
    let last: String
    let age: String
    let gender: String
    let familyId: Int
    let career: String
    let careerTitle: String
    let aspirations: [String]
    let npcType: Int
    let charFile: String
    var id: Int { nid }
    var fullName: String { [first, last].filter { !$0.isEmpty }.joined(separator: " ") }

    enum CodingKeys: String, CodingKey {
        case nid, guid, first, last, age, gender, career, aspirations
        case familyId = "family_id"
        case careerTitle = "career_title"
        case npcType = "npc_type"
        case charFile = "char_file"
    }
}

struct HoodSims: Decodable {
    let sims: [SimRow]
    let characters: Int
}

struct Relationship: Decodable, Identifiable {
    let target: Int
    let name: String
    let size: Int
    let fields: [String: Int]
    var id: Int { target }
}

struct SimToken: Decodable, Identifiable, Equatable {
    var guid: UInt32
    var name: String
    var raw: String
    var values: [Int]
    var id: String { "\(guid)-\(raw)-\(values.map(String.init).joined(separator: ","))" }

    var json: JSONValue {
        .object(["guid": .number(Double(guid)), "raw": .string(raw), "values": .array(values.map { .int($0) })])
    }
}

struct TokenGroup: Decodable {
    let first: [SimToken]
    let second: [SimToken]
    let editable: Bool
    let error: String?
}

struct SimDetail: Decodable {
    let nid: Int
    let tgi: TGI
    let fields: [String: Int]
    let resolved: JSONValue
    let first: String
    let last: String
    let bio: String
    let charFile: String
    let relationships: [Relationship]
    let tokens: TokenGroup

    enum CodingKeys: String, CodingKey {
        case nid, tgi, fields, resolved, first, last, bio, relationships, tokens
        case charFile = "char_file"
    }

    var fullName: String { [first, last].filter { !$0.isEmpty }.joined(separator: " ") }
}
