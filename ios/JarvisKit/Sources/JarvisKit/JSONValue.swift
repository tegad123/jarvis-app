import Foundation

/// JSON whose shape the server does not fix: an approval's payload, a feed
/// event's attachments. Objects keep the order the server wrote them in,
/// because an approval's fields are shown in that order (To, Subject, Message).
public enum JSONValue: Equatable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object(JSONObject)

    public var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    public var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    public var numberValue: Double? {
        if case .number(let n) = self { return n }
        return nil
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let a) = self { return a }
        return nil
    }

    public var objectValue: JSONObject? {
        if case .object(let o) = self { return o }
        return nil
    }

    public var isNull: Bool { self == .null }

    public subscript(key: String) -> JSONValue? { objectValue?[key] }

    /// What the web prints for a value: `String(v)`, a list joined with
    /// commas, and a dash for nothing.
    public var display: String {
        switch self {
        case .null: return "—"
        case .bool(let b): return b ? "true" : "false"
        case .number(let n): return JSONValue.format(n)
        case .string(let s): return s
        case .array(let items): return items.map(\.plain).joined(separator: ", ")
        case .object: return "[object Object]"
        }
    }

    private var plain: String {
        if case .null = self { return "" }
        return display
    }

    static func format(_ n: Double) -> String {
        if n.isFinite, n == n.rounded(), abs(n) < 1e15 { return String(Int64(n)) }
        return String(n)
    }
}

public struct JSONObject: Equatable, Sendable {
    public private(set) var keys: [String] = []
    public private(set) var values: [String: JSONValue] = [:]

    public init() {}

    public subscript(key: String) -> JSONValue? { values[key] }

    public var pairs: [(key: String, value: JSONValue)] {
        keys.compactMap { key in values[key].map { (key: key, value: $0) } }
    }

    public var isEmpty: Bool { keys.isEmpty }

    mutating func set(_ key: String, _ value: JSONValue) {
        if values[key] == nil { keys.append(key) }
        values[key] = value
    }
}

extension JSONValue: Decodable {
    private struct Key: CodingKey {
        var stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    /// Through `Decodable` the key order is whatever the decoder hands back,
    /// so it is sorted to be the same every time. `parse` keeps the server's.
    public init(from decoder: Decoder) throws {
        if let keyed = try? decoder.container(keyedBy: Key.self) {
            var object = JSONObject()
            for key in keyed.allKeys.sorted(by: { $0.stringValue < $1.stringValue }) {
                object.set(key.stringValue, try keyed.decode(JSONValue.self, forKey: key))
            }
            self = .object(object)
            return
        }
        if var list = try? decoder.unkeyedContainer() {
            var items: [JSONValue] = []
            while !list.isAtEnd { items.append(try list.decode(JSONValue.self)) }
            self = .array(items)
            return
        }
        let single = try decoder.singleValueContainer()
        if single.decodeNil() {
            self = .null
        } else if let b = try? single.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? single.decode(Double.self) {
            self = .number(n)
        } else {
            self = .string(try single.decode(String.self))
        }
    }
}

public extension JSONValue {
    /// Parses JSON keeping each object's key order. Throws `badResponse` on
    /// anything that is not JSON.
    static func parse(_ data: Data) throws -> JSONValue {
        var reader = Reader(bytes: Array(data))
        reader.skipSpace()
        let value = try reader.value(depth: 0)
        reader.skipSpace()
        guard reader.atEnd else { throw JarvisError.badResponse }
        return value
    }

    private struct Reader {
        let bytes: [UInt8]
        var at = 0

        var atEnd: Bool { at >= bytes.count }

        mutating func skipSpace() {
            while at < bytes.count, bytes[at] == 0x20 || bytes[at] == 0x0A || bytes[at] == 0x0D || bytes[at] == 0x09 {
                at += 1
            }
        }

        mutating func take(_ byte: UInt8) -> Bool {
            if at < bytes.count, bytes[at] == byte {
                at += 1
                return true
            }
            return false
        }

        mutating func literal(_ word: String) -> Bool {
            let want = Array(word.utf8)
            guard at + want.count <= bytes.count, Array(bytes[at..<(at + want.count)]) == want else { return false }
            at += want.count
            return true
        }

        mutating func value(depth: Int) throws -> JSONValue {
            guard depth < 64, at < bytes.count else { throw JarvisError.badResponse }
            switch bytes[at] {
            case UInt8(ascii: "{"):
                at += 1
                var object = JSONObject()
                skipSpace()
                if take(UInt8(ascii: "}")) { return .object(object) }
                while true {
                    skipSpace()
                    let key = try string()
                    skipSpace()
                    guard take(UInt8(ascii: ":")) else { throw JarvisError.badResponse }
                    skipSpace()
                    object.set(key, try value(depth: depth + 1))
                    skipSpace()
                    if take(UInt8(ascii: ",")) { continue }
                    guard take(UInt8(ascii: "}")) else { throw JarvisError.badResponse }
                    return .object(object)
                }
            case UInt8(ascii: "["):
                at += 1
                var items: [JSONValue] = []
                skipSpace()
                if take(UInt8(ascii: "]")) { return .array(items) }
                while true {
                    skipSpace()
                    items.append(try value(depth: depth + 1))
                    skipSpace()
                    if take(UInt8(ascii: ",")) { continue }
                    guard take(UInt8(ascii: "]")) else { throw JarvisError.badResponse }
                    return .array(items)
                }
            case UInt8(ascii: "\""):
                return .string(try string())
            case UInt8(ascii: "t"):
                guard literal("true") else { throw JarvisError.badResponse }
                return .bool(true)
            case UInt8(ascii: "f"):
                guard literal("false") else { throw JarvisError.badResponse }
                return .bool(false)
            case UInt8(ascii: "n"):
                guard literal("null") else { throw JarvisError.badResponse }
                return .null
            default:
                return .number(try number())
            }
        }

        mutating func number() throws -> Double {
            let start = at
            while at < bytes.count {
                let b = bytes[at]
                let digit = b >= 0x30 && b <= 0x39
                if digit || b == UInt8(ascii: "-") || b == UInt8(ascii: "+") || b == UInt8(ascii: ".")
                    || b == UInt8(ascii: "e") || b == UInt8(ascii: "E") {
                    at += 1
                } else {
                    break
                }
            }
            guard at > start, let n = Double(String(decoding: bytes[start..<at], as: UTF8.self)) else {
                throw JarvisError.badResponse
            }
            return n
        }

        mutating func hex4() throws -> UInt32 {
            guard at + 4 <= bytes.count,
                  let unit = UInt32(String(decoding: bytes[at..<(at + 4)], as: UTF8.self), radix: 16) else {
                throw JarvisError.badResponse
            }
            at += 4
            return unit
        }

        mutating func string() throws -> String {
            guard take(UInt8(ascii: "\"")) else { throw JarvisError.badResponse }
            var out: [UInt8] = []
            while at < bytes.count {
                let b = bytes[at]
                at += 1
                if b == UInt8(ascii: "\"") { return String(decoding: out, as: UTF8.self) }
                if b != UInt8(ascii: "\\") {
                    out.append(b)
                    continue
                }
                guard at < bytes.count else { break }
                let escaped = bytes[at]
                at += 1
                switch escaped {
                case UInt8(ascii: "n"): out.append(0x0A)
                case UInt8(ascii: "t"): out.append(0x09)
                case UInt8(ascii: "r"): out.append(0x0D)
                case UInt8(ascii: "b"): out.append(0x08)
                case UInt8(ascii: "f"): out.append(0x0C)
                case UInt8(ascii: "u"):
                    var scalar = try hex4()
                    if (0xD800...0xDBFF).contains(scalar), at + 1 < bytes.count,
                       bytes[at] == UInt8(ascii: "\\"), bytes[at + 1] == UInt8(ascii: "u") {
                        at += 2
                        let low = try hex4()
                        if (0xDC00...0xDFFF).contains(low) {
                            scalar = 0x10000 + ((scalar - 0xD800) << 10) + (low - 0xDC00)
                        }
                    }
                    let character = Unicode.Scalar(scalar) ?? "\u{FFFD}"
                    out.append(contentsOf: Array(String(Character(character)).utf8))
                default:
                    out.append(escaped)
                }
            }
            throw JarvisError.badResponse
        }
    }
}
