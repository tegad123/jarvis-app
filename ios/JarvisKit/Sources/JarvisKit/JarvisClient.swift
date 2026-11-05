import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public protocol HTTPTransport {
    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
    func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error>
}

public extension HTTPTransport {
    func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (data, response) = try await send(request)
                    guard (200..<300).contains(response.statusCode) else {
                        continuation.finish(throwing: JarvisError.http(response.statusCode, APIDecoding.message(from: data, status: response.statusCode)))
                        return
                    }
                    continuation.yield(data)
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}

public final class URLSessionTransport: HTTPTransport {
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.httpShouldSetCookies = false
        config.httpCookieAcceptPolicy = .never
        config.timeoutIntervalForRequest = 60
        session = URLSession(configuration: config)
    }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw JarvisError.badResponse }
        return (data, http)
    }

    public func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        #if os(Linux)
        // FoundationNetworking on Linux has no URLSession.bytes. The iOS build
        // yields each NDJSON line as it arrives. Tests feed the parser chunks.
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (data, response) = try await send(request)
                    guard (200..<300).contains(response.statusCode) else {
                        continuation.finish(throwing: JarvisError.http(response.statusCode, APIDecoding.message(from: data, status: response.statusCode)))
                        return
                    }
                    continuation.yield(data)
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
        #else
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        continuation.finish(throwing: JarvisError.badResponse)
                        return
                    }
                    if !(200..<300).contains(http.statusCode) {
                        var collected = Data()
                        for try await byte in bytes { collected.append(byte) }
                        continuation.finish(throwing: JarvisError.http(http.statusCode, APIDecoding.message(from: collected, status: http.statusCode)))
                        return
                    }
                    var chunk = Data()
                    for try await byte in bytes {
                        try Task.checkCancellation()
                        chunk.append(byte)
                        if byte == 10 {
                            continuation.yield(chunk)
                            chunk.removeAll(keepingCapacity: true)
                        }
                    }
                    if !chunk.isEmpty { continuation.yield(chunk) }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: CancellationError())
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
        #endif
    }
}

enum APIDecoding {
    static func message(from data: Data, status: Int) -> String {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return "The server answered \(status)."
        }
        if let text = obj["detail"] as? String, !text.isEmpty { return text }
        if let rows = obj["detail"] as? [[String: Any]] {
            let msgs = rows.compactMap { $0["msg"] as? String }
            if let first = msgs.first { return first }
        }
        if let text = obj["error"] as? String, !text.isEmpty { return text }
        return "The server answered \(status)."
    }

    static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        do { return try JSONDecoder().decode(T.self, from: data) }
        catch { throw JarvisError.badResponse }
    }
}

struct NDJSONParser {
    private var buffer = ""

    mutating func feed(_ data: Data) -> [ChatLine] {
        buffer += String(decoding: data, as: UTF8.self)
        var lines: [ChatLine] = []
        while let range = buffer.range(of: "\n") {
            let line = String(buffer[..<range.lowerBound])
            buffer.removeSubrange(..<range.upperBound)
            if let event = Self.event(line) { lines.append(event) }
        }
        return lines
    }

    mutating func finish() -> [ChatLine] {
        let rest = buffer
        buffer = ""
        if let event = Self.event(rest) { return [event] }
        return []
    }

    static func event(_ raw: String) -> ChatLine? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let data = trimmed.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = obj["type"] as? String else { return nil }
        switch type {
        case "delta":
            return ChatLine(kind: .delta, text: obj["text"] as? String ?? "")
        case "error":
            return ChatLine(kind: .error, text: obj["message"] as? String ?? "The reply stopped.")
        case "done":
            return ChatLine(kind: .done, text: "")
        default:
            return ChatLine(kind: .other, text: type)
        }
    }
}

/// Talks to one tenant. The PIN is a parameter of login and is not stored.
public final class JarvisClient {
    private let transport: HTTPTransport
    private var cookies = CookieJar()
    public private(set) var origin: TenantOrigin?
    public var aiConsent: Bool

    public init(transport: HTTPTransport, aiConsent: Bool = false) {
        self.transport = transport
        self.aiConsent = aiConsent
    }

    public func useOrigin(_ raw: String) throws {
        switch TenantOrigin.parse(raw) {
        case .failure(let error):
            throw error
        case .success(let next):
            if origin?.host != next.host { cookies.clearAll() }
            origin = next
        }
    }

    public func clearOrigin() {
        cookies.clearAll()
        origin = nil
    }

    public func login(personKey: String, pin: String) async throws -> SessionInfo {
        let body = try JSONSerialization.data(withJSONObject: ["person_key": personKey, "pin": pin])
        let data = try await send(method: "POST", path: "/api/login", body: body, acceptError: false)
        return try APIDecoding.decode(SessionInfo.self, from: data)
    }

    /// Turns off this person's sign-in and records a pending deletion request.
    /// Refuses a body that claims the account was deleted.
    public func requestAccountDeletion(pin: String) async throws -> DeletionReceipt {
        let body = try JSONSerialization.data(withJSONObject: [
            "confirm": AIDisclosure.confirmPhrase,
            "pin": pin,
        ])
        let data = try await send(method: "POST", path: "/api/account/deletion-request", body: body, acceptError: false)
        let receipt = try APIDecoding.decode(DeletionReceipt.self, from: data)
        if receipt.deleted || receipt.deletion != "pending" || !receipt.deactivated {
            throw JarvisError.http(502, "The server's reply did not match a pending request. Nothing here was treated as deleted.")
        }
        if let host = origin?.host { cookies.clear(host: host) }
        return receipt
    }

    /// Owner-only. Describes full team deletion and does not perform it.
    public func teamDeletionImpact() async throws -> TeamDeletionImpact {
        let data = try await send(method: "GET", path: "/api/account/tenant-deletion", body: nil, acceptError: false)
        return try APIDecoding.decode(TeamDeletionImpact.self, from: data)
    }

    /// Owner-only one-time nonce. Does not delete anything.
    public func teamDeletionChallenge() async throws -> TeamDeletionImpact {
        let data = try await send(
            method: "POST", path: "/api/account/tenant-deletion/challenge", body: Data("{}".utf8),
            acceptError: false, headers: ["X-Jarvis-Confirm": "delete-tenant"])
        return try APIDecoding.decode(TeamDeletionImpact.self, from: data)
    }

    /// Deletes this tenant's local records. Refuses a reply that does not say
    /// the local data is gone and sign-in is blocked, and keeps `stillExists`
    /// for the screen to show.
    public func deleteTeam(pin: String, phrase: String, nonce: String) async throws -> TeamDeletionReceipt {
        let body = try JSONSerialization.data(withJSONObject: [
            "confirm": phrase,
            "pin": pin,
            "nonce": nonce,
        ])
        let data = try await send(
            method: "POST", path: "/api/account/tenant-deletion", body: body, acceptError: false,
            headers: ["X-Jarvis-Confirm": "delete-tenant"])
        let receipt = try APIDecoding.decode(TeamDeletionReceipt.self, from: data)
        if receipt.action != "team-deleted" || !receipt.deletedLocal || !receipt.signInBlocked {
            throw JarvisError.http(502, "The server did not confirm that this team's local records were removed.")
        }
        if let host = origin?.host { cookies.clear(host: host) }
        return receipt
    }

    public func logout() async {
        _ = try? await send(method: "POST", path: "/api/logout", body: nil, acceptError: true)
        if let host = origin?.host { cookies.clear(host: host) }
    }

    public func session() async throws -> SessionInfo {
        let data = try await send(method: "GET", path: "/api/session", body: nil, acceptError: false)
        return try APIDecoding.decode(SessionInfo.self, from: data)
    }

    public func people() async throws -> [PersonChoice] {
        let data = try await send(method: "GET", path: "/api/people", body: nil, acceptError: false)
        struct Box: Decodable { var people: [PersonChoice] }
        return try APIDecoding.decode(Box.self, from: data).people
    }

    public func leads() async throws -> [LeadItem] {
        let data = try await send(method: "GET", path: "/api/leads", body: nil, acceptError: false)
        struct Box: Decodable { var leads: [LeadItem] }
        return try APIDecoding.decode(Box.self, from: data).leads
    }

    public func lead(id: String) async throws -> LeadDetail {
        let data = try await send(method: "GET", path: "/api/leads/\(Self.path(id))", body: nil, acceptError: false)
        return try APIDecoding.decode(LeadDetail.self, from: data)
    }

    public func logContact(leadID: String, kind: String, note: String?, reached: Bool?, nextDate: String?) async throws -> LeadDetail {
        var payload: [String: Any] = ["kind": kind]
        if let note, !note.isEmpty { payload["note"] = note }
        if kind != "note", let reached { payload["reached"] = reached }
        if let nextDate, !nextDate.isEmpty { payload["next_touch_at"] = nextDate }
        let body = try JSONSerialization.data(withJSONObject: payload)
        _ = try await send(method: "POST", path: "/api/leads/\(Self.path(leadID))/touches", body: body, acceptError: false)
        return try await lead(id: leadID)
    }

    public func tasks() async throws -> [TaskItem] {
        let data = try await send(method: "GET", path: "/api/tasks", body: nil, acceptError: false)
        struct Box: Decodable { var tasks: [TaskItem] }
        return try APIDecoding.decode(Box.self, from: data).tasks
    }

    public func approvals() async throws -> [ApprovalItem] {
        let data = try await send(method: "GET", path: "/api/approvals", body: nil, acceptError: false)
        struct Box: Decodable { var approvals: [ApprovalItem] }
        return try APIDecoding.decode(Box.self, from: data).approvals
    }

    public func decide(approvalID: String, approve: Bool) async throws {
        let word = approve ? "approved" : "rejected"
        let body = try JSONSerialization.data(withJSONObject: ["decision": word])
        _ = try await send(method: "POST", path: "/api/approvals/\(Self.path(approvalID))", body: body, acceptError: false)
    }

    /// Ask. Refuses before any network call when consent is off.
    public func sendChat(message: String, threadID: String = "direct") -> AsyncThrowingStream<ChatLine, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard aiConsent else { throw JarvisError.consentRequired }
                    let body = try JSONSerialization.data(withJSONObject: [
                        "thread_id": threadID,
                        "message": message,
                    ])
                    let request = try self.makeRequest(method: "POST", path: "/api/chat", body: body)
                    var parser = NDJSONParser()
                    for try await chunk in self.transport.stream(request) {
                        try Task.checkCancellation()
                        for line in parser.feed(chunk) { continuation.yield(line) }
                    }
                    for line in parser.finish() { continuation.yield(line) }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: JarvisError.cancelled)
                } catch let error as JarvisError {
                    continuation.finish(throwing: error)
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    public func cookieNamesForTests() -> [String] {
        guard let host = origin?.host else { return [] }
        return cookies.names(for: host)
    }

    private func send(method: String, path: String, body: Data?, acceptError: Bool, headers: [String: String] = [:]) async throws -> Data {
        let request = try makeRequest(method: method, path: path, body: body, headers: headers)
        let (data, response) = try await transport.send(request)
        if let header = response.value(forHTTPHeaderField: "Set-Cookie"), let host = origin?.host {
            cookies.store(setCookieHeader: header, host: host)
        }
        if !acceptError && !(200..<300).contains(response.statusCode) {
            throw JarvisError.http(response.statusCode, APIDecoding.message(from: data, status: response.statusCode))
        }
        return data
    }

    private func makeRequest(method: String, path: String, body: Data?, headers: [String: String] = [:]) throws -> URLRequest {
        guard let origin else { throw JarvisError.noOrigin }
        var request = URLRequest(url: origin.endpoint(path))
        request.httpMethod = method
        request.httpBody = body
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        if let cookie = cookies.header(for: origin.host) {
            request.setValue(cookie, forHTTPHeaderField: "Cookie")
        }
        return request
    }

    private static func path(_ id: String) -> String {
        id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? id
    }
}
