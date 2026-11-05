import Foundation

struct StoredCookie: Equatable {
    var name: String
    var value: String
    var host: String
}

/// Session cookies for one process. Values are not printed. Cleared on logout
/// and when the tenant origin changes. The PIN is never put here.
struct CookieJar: Equatable {
    private(set) var items: [StoredCookie] = []

    mutating func store(setCookieHeader: String, host: String) {
        for piece in setCookieHeader.split(separator: ",") {
            // One Set-Cookie. A date like "Expires=Thu, 01 Jan" would contain a
            // comma; the session cookie FastAPI sets has no Expires date, only
            // Max-Age, so splitting on comma is safe for the cookies we accept.
            let header = String(piece)
            let first = header.split(separator: ";", maxSplits: 1).first.map(String.init) ?? header
            let parts = first.split(separator: "=", maxSplits: 1)
            guard parts.count == 2 else { continue }
            let name = String(parts[0]).trimmingCharacters(in: .whitespaces)
            let value = String(parts[1]).trimmingCharacters(in: .whitespaces)
            guard !name.isEmpty, !name.lowercased().hasPrefix("expires") else { continue }
            items.removeAll { $0.host == host && $0.name == name }
            let expired = header.lowercased().contains("max-age=0")
            if value.isEmpty || expired { continue }
            items.append(StoredCookie(name: name, value: value, host: host))
        }
    }

    func header(for host: String) -> String? {
        let pairs = items.filter { $0.host == host }.map { "\($0.name)=\($0.value)" }
        return pairs.isEmpty ? nil : pairs.joined(separator: "; ")
    }

    func names(for host: String) -> [String] {
        items.filter { $0.host == host }.map(\.name)
    }

    mutating func clear(host: String) {
        items.removeAll { $0.host == host }
    }

    mutating func clearAll() {
        items.removeAll()
    }
}
