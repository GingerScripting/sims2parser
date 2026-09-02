import Foundation
import SwiftUI

/// A dynamic JSON tree. Decoded resources arrive from the daemon in this
/// shape — `{"$type": "StrResource", "entries": [...], "$props": {...}}` —
/// and the editors mutate it in place before sending it back. Keys are kept
/// verbatim (`$type`, `_name_raw`), which is why this is not run through
/// `convertFromSnakeCase`.
public enum JSONValue: Equatable, Hashable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])
}

extension JSONValue: Codable {
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let n = try? c.decode(Double.self) { self = .number(n); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([JSONValue].self) { self = .array(a); return }
        if let o = try? c.decode([String: JSONValue].self) { self = .object(o); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported JSON value")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let b): try c.encode(b)
        case .number(let n):
            // Integral values go out as integers so Python sees an int, not
            // 4294967295.0, for a u32 field.
            if n == n.rounded() && abs(n) < 9e15 { try c.encode(Int64(n)) } else { try c.encode(n) }
        case .string(let s): try c.encode(s)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        }
    }
}

public extension JSONValue {
    public subscript(key: String) -> JSONValue? {
        get {
            if case .object(let o) = self { return o[key] }
            return nil
        }
        set {
            guard case .object(var o) = self else { return }
            o[key] = newValue
            self = .object(o)
        }
    }

    public subscript(index: Int) -> JSONValue? {
        get {
            if case .array(let a) = self, a.indices.contains(index) { return a[index] }
            return nil
        }
        set {
            guard case .array(var a) = self, a.indices.contains(index) else { return }
            if let v = newValue { a[index] = v } else { a.remove(at: index) }
            self = .array(a)
        }
    }

    public var stringValue: String? { if case .string(let s) = self { return s }; return nil }
    public var doubleValue: Double? { if case .number(let n) = self { return n }; return nil }
    public var intValue: Int? { doubleValue.map { Int($0) } }
    public var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        if case .number(let n) = self { return n != 0 }
        return nil
    }
    public var arrayValue: [JSONValue]? { if case .array(let a) = self { return a }; return nil }
    public var objectValue: [String: JSONValue]? { if case .object(let o) = self { return o }; return nil }
    public var isNull: Bool { if case .null = self { return true }; return false }

    /// The dataclass name the daemon tagged this object with.
    public var typeName: String? { self["$type"]?.stringValue }

    /// Bytes carried as `{"$hex": "..."}`.
    public var hexBytes: [UInt8]? {
        guard let s = self["$hex"]?.stringValue else { return nil }
        return [UInt8](hex: s)
    }

    public static func hex(_ bytes: [UInt8]) -> JSONValue { .object(["$hex": .string(bytes.hexString)]) }
    public static func int(_ v: Int) -> JSONValue { .number(Double(v)) }

    mutating func append(_ value: JSONValue) {
        guard case .array(var a) = self else { return }
        a.append(value)
        self = .array(a)
    }

    mutating func remove(at index: Int) {
        guard case .array(var a) = self, a.indices.contains(index) else { return }
        a.remove(at: index)
        self = .array(a)
    }

    /// Pretty JSON text, for the generic editor and for debugging.
    public var prettyPrinted: String {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        return (try? enc.encode(self)).flatMap { String(data: $0, encoding: .utf8) } ?? "null"
    }

    public static func parse(_ text: String) throws -> JSONValue {
        try JSONDecoder().decode(JSONValue.self, from: Data(text.utf8))
    }
}

// MARK: - Bindings into a JSON tree

public extension Binding where Value == JSONValue {
    public subscript(key: String) -> Binding<JSONValue> {
        Binding<JSONValue>(
            get: { wrappedValue[key] ?? .null },
            set: { wrappedValue[key] = $0 })
    }

    public subscript(index: Int) -> Binding<JSONValue> {
        Binding<JSONValue>(
            get: { wrappedValue[index] ?? .null },
            set: { wrappedValue[index] = $0 })
    }

    public func string(_ key: String) -> Binding<String> {
        Binding<String>(
            get: { wrappedValue[key]?.stringValue ?? "" },
            set: { wrappedValue[key] = .string($0) })
    }

    public func int(_ key: String) -> Binding<Int> {
        Binding<Int>(
            get: { wrappedValue[key]?.intValue ?? 0 },
            set: { wrappedValue[key] = .int($0) })
    }

    public var intValue: Binding<Int> {
        Binding<Int>(
            get: { wrappedValue.intValue ?? 0 },
            set: { wrappedValue = .int($0) })
    }

    public var stringValue: Binding<String> {
        Binding<String>(
            get: { wrappedValue.stringValue ?? "" },
            set: { wrappedValue = .string($0) })
    }
}

// MARK: - Hex helpers

public extension Array where Element == UInt8 {
    public init?(hex: String) {
        var out: [UInt8] = []
        out.reserveCapacity(hex.utf8.count / 2)
        var high: UInt8?
        for ch in hex.utf8 {
            let nibble: UInt8
            switch ch {
            case 48...57: nibble = ch - 48
            case 65...70: nibble = ch - 55
            case 97...102: nibble = ch - 87
            case 32, 10, 13, 9: continue
            default: return nil
            }
            if let h = high { out.append(h << 4 | nibble); high = nil } else { high = nibble }
        }
        if high != nil { return nil }
        self = out
    }

    public var hexString: String {
        var s = ""
        s.reserveCapacity(count * 2)
        for b in self { s += String(format: "%02x", b) }
        return s
    }
}

public func hex8(_ v: UInt32) -> String { String(format: "0x%08X", v) }
public func hex4(_ v: Int) -> String { String(format: "0x%04X", v & 0xFFFF) }

/// Parse "0x1F", "1F" (when hex is the default), or "31".
public func parseNumber(_ text: String, hexByDefault: Bool = false) -> Int? {
    let t = text.trimmingCharacters(in: .whitespaces)
    if t.hasPrefix("0x") || t.hasPrefix("0X") { return Int(t.dropFirst(2), radix: 16) }
    if hexByDefault { return Int(t, radix: 16) }
    return Int(t)
}
