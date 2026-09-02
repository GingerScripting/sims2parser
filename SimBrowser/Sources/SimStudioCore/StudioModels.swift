import Foundation

/// A resource's identity: type, group, instance, and the v7.2 resource id.
public struct TGI: Codable, Hashable, CustomStringConvertible {
    public var type: UInt32
    public var group: UInt32
    public var instance: UInt32
    public var instanceHi: UInt32 = 0

    public init(type: UInt32, group: UInt32, instance: UInt32, instanceHi: UInt32 = 0) {
        self.type = type; self.group = group; self.instance = instance; self.instanceHi = instanceHi
    }

    public enum CodingKeys: String, CodingKey {
        case type, group, instance
        case instanceHi = "instance_hi"
    }

    public var description: String {
        var s = "g=\(hex8(group)) i=\(hex8(instance))"
        if instanceHi != 0 { s += " r=\(hex8(instanceHi))" }
        return s
    }

    public var json: JSONValue {
        .object(["type": .number(Double(type)), "group": .number(Double(group)),
                 "instance": .number(Double(instance)), "instance_hi": .number(Double(instanceHi))])
    }
}

/// What `open`, `status`, and every mutating call return.
public struct PackageSummary: Decodable {
    public let path: String
    public let readonly: Bool
    public let readonlyReason: String
    public let version: String
    public let count: Int
    public let compressedCount: Int
    public let dirty: Bool
    public let canUndo: Bool
    public let canRedo: Bool
    public let undoLabel: String?
    public let redoLabel: String?

    public enum CodingKeys: String, CodingKey {
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
public struct ResourceRow: Decodable, Identifiable, Hashable {
    public let type: UInt32
    public let typeName: String
    public let group: UInt32
    public let instance: UInt32
    public let instanceHi: UInt32
    public let size: Int
    public let compressed: Bool
    public let decodable: Bool
    public let bhav: Bool
    public let flags: Int

    public enum CodingKeys: String, CodingKey {
        case type, group, instance, size, compressed, decodable, bhav, flags
        case typeName = "type_name"
        case instanceHi = "instance_hi"
    }

    public init(from decoder: Decoder) throws {
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

    public init(type: UInt32, typeName: String, group: UInt32, instance: UInt32, instanceHi: UInt32,
         size: Int, compressed: Bool, decodable: Bool, bhav: Bool, flags: Int) {
        self.type = type; self.typeName = typeName; self.group = group; self.instance = instance
        self.instanceHi = instanceHi; self.size = size; self.compressed = compressed
        self.decodable = decodable; self.bhav = bhav; self.flags = flags
    }

    public var tgi: TGI { TGI(type: type, group: group, instance: instance, instanceHi: instanceHi) }
    public var id: TGI { tgi }
    public var isTexture: Bool { flags & 8 != 0 }
    public var isMesh: Bool { flags & 16 != 0 }
    public var hasPreview: Bool { isTexture || isMesh }

    /// Build the table from the daemon's compact `index` reply:
    /// `{"columns": [...], "rows": [[type, group, instance, hi, size, flags]], "type_names": {...}}`.
    public static func rows(fromIndex raw: Any) throws -> [ResourceRow] {
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

    public var compressedSort: Int { compressed ? 1 : 0 }
    public var decoderSort: Int { decodable ? 2 : (bhav ? 1 : 0) }
    public var decoderLabel: String { decodable ? "yes" : (bhav ? "tree" : "") }
}

/// The decompiler's view of a BHAV, rendered by the daemon.
public struct BhavRender: Decodable {
    public let flat: String?
    public let tree: String?
    public let error: String?
    public let name: String?
    public let format: Int?
    public let type: Int?
    public let argc: Int?
    public let localc: Int?
    public let count: Int?
}

/// Everything `get_resource` returns.
public struct ResourceDetail: Decodable {
    public let row: ResourceRow
    public let hex: String
    public let decoded: JSONValue?
    public let decodeError: String?
    public let bhav: BhavRender?

    public enum CodingKeys: String, CodingKey {
        case row, hex, decoded
        case bhav = "bhav_render"
        case decodeError = "decode_error"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // The row is nested, not spread: a detail-only key can then never
        // collide with a row key (the Bool `bhav` flag once did).
        row = try c.decode(ResourceRow.self, forKey: .row)
        hex = try c.decode(String.self, forKey: .hex)
        let d = try c.decodeIfPresent(JSONValue.self, forKey: .decoded)
        decoded = (d?.isNull ?? true) ? nil : d
        decodeError = try c.decodeIfPresent(String.self, forKey: .decodeError)
        bhav = try c.decodeIfPresent(BhavRender.self, forKey: .bhav)
    }

    public var tgi: TGI { row.tgi }
    public var bytes: [UInt8] { [UInt8](hex: hex) ?? [] }
}

/// What `undo`/`redo` return: which step, which TGIs to refresh, plus the
/// usual summary.
public struct HistoryResult: Decodable {
    public let label: String
    public let tgis: [TGI]
    public let summary: PackageSummary

    public enum CodingKeys: String, CodingKey { case label, tgis }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = try c.decode(String.self, forKey: .label)
        tgis = try c.decode([TGI].self, forKey: .tgis)
        summary = try PackageSummary(from: decoder)
    }
}

/// A resource row plus the summary — what add/rename return.
public struct RowResult: Decodable {
    public let row: ResourceRow
    public let summary: PackageSummary
    public init(from decoder: Decoder) throws {
        row = try ResourceRow(from: decoder)
        summary = try PackageSummary(from: decoder)
    }
}

public struct PutResult: Decodable {
    public let size: Int
    public let changed: Bool
    public let summary: PackageSummary
    public enum CodingKeys: String, CodingKey { case size, changed }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        size = try c.decode(Int.self, forKey: .size)
        changed = try c.decode(Bool.self, forKey: .changed)
        summary = try PackageSummary(from: decoder)
    }
}

/// What the BHAV editor needs to label instructions: primitive names, the
/// operand layouts the toolkit has pinned, and the exit sentinels.
public struct BhavMeta: Decodable {
    public struct Field: Decodable, Identifiable {
        public let name: String
        public let offset: Int
        public let size: Int
        public let values: [String: String]?
        public var id: String { name }
    }

    public struct Format: Decodable {
        public let instrSize: Int
        public let operandLen: Int
        public let addrWidth: Int
        public enum CodingKeys: String, CodingKey {
            case instrSize = "instr_size"
            case operandLen = "operand_len"
            case addrWidth = "addr_width"
        }
    }

    public let primitives: [String: String]
    public let layouts: [String: [Field]]
    public let sentinels: [String: Int]
    public let formats: [String: Format]

    public func operandWidth(_ format: Int) -> Int? { formats[String(format)]?.operandLen }

    public func opcodeName(_ op: Int) -> String {
        if op < 0x100 { return primitives[String(op)] ?? String(format: "Primitive 0x%04X", op) }
        if op < 0x1000 { return String(format: "Global BHAV 0x%04X", op) }
        if op < 0x2000 { return String(format: "Local BHAV 0x%04X", op) }
        return String(format: "Semiglobal BHAV 0x%04X", op)
    }

    public func destLabel(_ d: Int) -> String {
        if d == sentinels["true"] { return "→ TRUE" }
        if d == sentinels["false"] { return "→ FALSE" }
        if d >= (sentinels["floor"] ?? 0xFFFC) { return "→ ERROR" }
        return "→ \(d)"
    }

    public var sortedPrimitives: [(code: Int, name: String)] {
        primitives.compactMap { k, v in Int(k).map { (code: $0, name: v) } }.sorted { $0.code < $1.code }
    }
}

/// What `bhav_transform` returns.
public struct BhavTransform: Decodable {
    public let decoded: JSONValue
    public let warnings: [String]
}

/// Static tables served by the daemon so the app carries no format knowledge.
public struct Meta: Decodable {
    public let `protocol`: Int
    public let typeNames: [String: String]
    public let decodableTypes: [UInt32]
    public let bhavType: UInt32
    public let objdFields: [String: Int]
    public let objdU32Fields: [String: Int]
    public let objdWordCount: Int
    public let objfSlots: [String: String]
    public let strFormats: [String: Int]
    public let ttabLayouts: [String: TtabLayout]

    public struct TtabLayout: Decodable {
        public let entrySize: Int
        public let ttasOffset: Int
        public enum CodingKeys: String, CodingKey {
            case entrySize = "entry_size"
            case ttasOffset = "ttas_offset"
        }
    }

    public enum CodingKeys: String, CodingKey {
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

    public func typeName(_ type: UInt32) -> String {
        typeNames[String(type)] ?? hex8(type)
    }

    /// Known types, sorted by name, for the New Resource picker.
    public var knownTypes: [(id: UInt32, name: String)] {
        typeNames.compactMap { k, v in UInt32(k).map { (id: $0, name: v) } }
            .sorted { $0.name.lowercased() < $1.name.lowercased() }
    }
}

// MARK: - Object Workshop and tools

public struct ObjectInfo: Decodable, Identifiable, Hashable {
    public let index: Int
    public let instance: UInt32
    public let group: UInt32
    public let guid: UInt32
    public let originalGuid: UInt32
    public let filename: String
    public let name: String
    public let price: Int
    public let ttabId: Int
    public let ctssId: Int
    public var id: Int { index }

    public enum CodingKeys: String, CodingKey {
        case index, instance, group, guid, filename, name, price
        case originalGuid = "original_guid"
        case ttabId = "ttab_id"
        case ctssId = "ctss_id"
    }
}

public struct ObjectsResult: Decodable {
    public let objects: [ObjectInfo]
}

public struct ClonePatch: Decodable, Identifiable {
    public let instance: Int
    public let bhavName: String
    public let instrIndex: Int
    public let opcode: Int
    public let opcodeName: String
    public let operandOffset: Int
    public let knownLayout: Bool
    public let applied: Bool
    public var id: String { "\(instance)/\(instrIndex)/\(operandOffset)" }

    public enum CodingKeys: String, CodingKey {
        case instance, opcode, applied
        case bhavName = "bhav_name"
        case instrIndex = "instr_index"
        case opcodeName = "opcode_name"
        case operandOffset = "operand_offset"
        case knownLayout = "known_layout"
    }
}

public struct CloneResult: Decodable {
    public let sourceGuid: UInt32
    public let newGuid: UInt32
    public let resourceCount: Int
    public let changed: Int
    public let patches: [ClonePatch]
    public let warnings: [String]
    public let summary: PackageSummary

    public enum CodingKeys: String, CodingKey {
        case changed, patches, warnings
        case sourceGuid = "source_guid"
        case newGuid = "new_guid"
        case resourceCount = "resource_count"
    }

    public init(from decoder: Decoder) throws {
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

public struct ScanResult: Decodable {
    public let packages: Int
    public let guids: Int
    public let collisions: [String: [String]]
    public let duplicates: [String: [String]]
}

public struct MergeResult: Decodable {
    public let added: Int
    public let replaced: Int
    public let skipped: Int
    public let summary: PackageSummary
    public enum CodingKeys: String, CodingKey { case added, replaced, skipped }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        added = try c.decode(Int.self, forKey: .added)
        replaced = try c.decode(Int.self, forKey: .replaced)
        skipped = try c.decode(Int.self, forKey: .skipped)
        summary = try PackageSummary(from: decoder)
    }
}

public struct SplitResult: Decodable {
    public let path: String
    public let written: Int
    public let summary: PackageSummary
    public enum CodingKeys: String, CodingKey { case path, written }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        written = try c.decode(Int.self, forKey: .written)
        summary = try PackageSummary(from: decoder)
    }
}

public struct Finding: Decodable, Identifiable {
    public let severity: String
    public let code: String
    public let title: String
    public let detail: [String]
    public let fix: String?
    public let id = UUID()
    public enum CodingKeys: String, CodingKey { case severity, code, title, detail, fix }
}

public struct DoctorResult: Decodable {
    public let root: String
    public let packages: Int
    public let findings: [Finding]
}

public struct TexturePreview: Decodable {
    public let name: String
    public let width: Int
    public let height: Int
    public let format: String
    public let levels: Int
    public let shownWidth: Int
    public let shownHeight: Int
    public let pngB64: String

    public enum CodingKeys: String, CodingKey {
        case name, width, height, format, levels
        case shownWidth = "shown_width"
        case shownHeight = "shown_height"
        case pngB64 = "png_b64"
    }
}

public struct MeshPreview: Decodable {
    public struct Group: Decodable, Identifiable {
        public let name: String
        public let faces: Int
        public var id: String { name }
    }
    public let name: String
    public let obj: String
    public let faces: Int
    public let groups: [Group]
    public let partial: Bool
}

/// A server progress event.
public struct TaskProgress: Equatable {
    public let op: String
    public let done: Int
    public let total: Int
    public let note: String

    public init?(_ value: JSONValue) {
        guard value["event"]?.stringValue == "progress", let op = value["op"]?.stringValue else { return nil }
        self.op = op
        done = value["done"]?.intValue ?? 0
        total = value["total"]?.intValue ?? 0
        note = value["note"]?.stringValue ?? ""
    }
}

// MARK: - Neighborhoods

public struct FieldDef: Decodable, Identifiable {
    public let name: String
    public let kind: String      // id | int | bool | meter | enum:<table> | flags:<table>
    public let offset: Int
    public let fmt: String
    public var id: String { name }

    public var table: String? {
        let parts = kind.split(separator: ":", maxSplits: 1)
        return parts.count == 2 ? String(parts[1]) : nil
    }
    public var isEnum: Bool { kind.hasPrefix("enum:") }
    public var isFlags: Bool { kind.hasPrefix("flags:") }
    public var section: String {
        if let dot = name.firstIndex(of: ".") { return String(name[..<dot]) }
        return "profile"
    }
    public var label: String {
        let base = name.contains(".") ? String(name.split(separator: ".").last ?? "") : name
        return base.replacingOccurrences(of: "_", with: " ")
    }
}

/// hoodcheck's verdict on the neighborhood's token store. The prose comes
/// from hoodcheck.py, so the app and the CLI always say the same thing.
public struct HoodCheck: Decodable {
    public let healthy: Bool
    public let summary: String
    public let declared: Int
    public let actual: Int
    public let sdscCount: Int
    public let missingNids: [Int]

    public enum CodingKeys: String, CodingKey {
        case healthy, summary, declared, actual
        case sdscCount = "sdsc_count"
        case missingNids = "missing_nids"
    }
}

public struct HoodMeta: Decodable {
    public let isHood: Bool
    public let hoodId: String?
    public let check: HoodCheck?
    public let sdscFields: [FieldDef]
    public let sdscTables: [String: [String: String]]
    public let srelFields: [FieldDef]
    public let srelTables: [String: [String: String]]
    public let memoryOwnerSlot: Int
    public let memorySubjectSlot: Int

    public enum CodingKeys: String, CodingKey {
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
    public func options(_ table: String, from tables: [String: [String: String]]) -> [(value: Int, label: String)] {
        (tables[table] ?? [:]).compactMap { k, v in Int(k).map { (value: $0, label: v) } }
            .sorted { $0.label.lowercased() < $1.label.lowercased() }
    }
}

public struct SimRow: Decodable, Identifiable, Hashable {
    public let nid: Int
    public let guid: UInt32
    public let first: String
    public let last: String
    public let age: String
    public let gender: String
    public let familyId: Int
    public let career: String
    public let careerTitle: String
    public let aspirations: [String]
    public let npcType: Int
    public let charFile: String
    public var id: Int { nid }
    public var fullName: String { [first, last].filter { !$0.isEmpty }.joined(separator: " ") }

    public enum CodingKeys: String, CodingKey {
        case nid, guid, first, last, age, gender, career, aspirations
        case familyId = "family_id"
        case careerTitle = "career_title"
        case npcType = "npc_type"
        case charFile = "char_file"
    }
}

public struct HoodSims: Decodable {
    public let sims: [SimRow]
    public let characters: Int
}

public struct Relationship: Decodable, Identifiable {
    public let target: Int
    public let name: String
    public let size: Int
    public let fields: [String: Int]
    public var id: Int { target }
}

public struct SimToken: Decodable, Identifiable, Equatable {
    public var guid: UInt32
    public var name: String
    public var raw: String
    public var values: [Int]
    public var id: String { "\(guid)-\(raw)-\(values.map(String.init).joined(separator: ","))" }

    public var json: JSONValue {
        .object(["guid": .number(Double(guid)), "raw": .string(raw), "values": .array(values.map { .int($0) })])
    }
}

public struct TokenGroup: Decodable {
    public let first: [SimToken]
    public let second: [SimToken]
    public let editable: Bool
    public let error: String?
}

public struct SimDetail: Decodable {
    public let nid: Int
    public let tgi: TGI
    public let fields: [String: Int]
    public let resolved: JSONValue
    public let first: String
    public let last: String
    public let bio: String
    public let charFile: String
    public let relationships: [Relationship]
    public let tokens: TokenGroup

    public enum CodingKeys: String, CodingKey {
        case nid, tgi, fields, resolved, first, last, bio, relationships, tokens
        case charFile = "char_file"
    }

    public var fullName: String { [first, last].filter { !$0.isEmpty }.joined(separator: " ") }
}

extension TGI: Identifiable {
    public var id: TGI { self }
}
