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

    mutating func feed(_ data: Data) -> [ChatEvent] {
        buffer += String(decoding: data, as: UTF8.self)
        var events: [ChatEvent] = []
        while let range = buffer.range(of: "\n") {
            let line = String(buffer[..<range.lowerBound])
            buffer.removeSubrange(..<range.upperBound)
            if let event = ChatEvent.parse(line) { events.append(event) }
        }
        return events
    }

    mutating func finish() -> [ChatEvent] {
        let rest = buffer
        buffer = ""
        if let event = ChatEvent.parse(rest) { return [event] }
        return []
    }
}

/// Talks to one tenant. The PIN is a parameter of login and is not stored.
///
/// Two kinds of session, matching the server: the web's cookie (PIN sign-in,
/// POST /api/login) and the phone's bearer tokens (Google through the system
/// sign-in sheet, app/devices.py). Both live in memory only.
public final class JarvisClient {
    /// Where /app/start sends the one-time code. The server's default
    /// APP_MOBILE_REDIRECTS, and the app's URL scheme.
    public static let appRedirect = "jarvis://auth"
    public static let appCallbackScheme = "jarvis"

    private let transport: HTTPTransport
    private var cookies = CookieJar()
    private var tokens: AppTokens?
    private var tokensExpire: Date?
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
            if origin?.host != next.host {
                cookies.clearAll()
                tokens = nil
                tokensExpire = nil
            }
            origin = next
        }
    }

    public func clearOrigin() {
        cookies.clearAll()
        tokens = nil
        tokensExpire = nil
        origin = nil
    }

    /// True when this client is signed in through the phone path.
    public var hasDeviceSession: Bool { tokens != nil }

    // MARK: Sign-in

    /// What this address offers. An older server has no such route and
    /// offers everything, as the web assumes.
    public func authMethods() async -> AuthMethods {
        guard let data = try? await send(method: "GET", path: "/api/auth/methods"),
              let methods = try? APIDecoding.decode(AuthMethods.self, from: data) else {
            return AuthMethods()
        }
        return methods
    }

    public func people() async throws -> [PersonChoice] {
        let data = try await send(method: "GET", path: "/api/people")
        struct Box: Decodable { var people: [PersonChoice] }
        return try APIDecoding.decode(Box.self, from: data).people
    }

    public func login(personKey: String, pin: String) async throws -> SessionInfo {
        let body = try Self.json(["person_key": personKey, "pin": pin])
        let data = try await send(method: "POST", path: "/api/login", body: body)
        return try APIDecoding.decode(SessionInfo.self, from: data)
    }

    /// The page to open in the system sign-in sheet. It signs the person in
    /// with Google (or the web session they already have) and comes back to
    /// `appRedirect` with a one-time code.
    public func appStartURL(challenge: String) throws -> URL {
        guard let origin else { throw JarvisError.noOrigin }
        return origin.endpoint("/app/start", query: [
            URLQueryItem(name: "challenge", value: challenge),
            URLQueryItem(name: "redirect", value: Self.appRedirect),
        ])
    }

    /// Trades the one-time code for this phone's tokens. The code only trades
    /// with the verifier whose challenge started the sign-in, and only once.
    public func exchange(code: String, verifier: String, deviceName: String) async throws {
        let body = try Self.json([
            "code": code,
            "verifier": verifier,
            "device_name": deviceName.isEmpty ? "iPhone" : deviceName,
            "platform": "ios",
        ])
        let data = try await send(method: "POST", path: "/api/app/exchange", body: body)
        adopt(try APIDecoding.decode(AppTokens.self, from: data))
    }

    public func logout() async {
        if tokens != nil {
            _ = try? await send(method: "POST", path: "/api/app/signout", acceptError: true)
        } else {
            _ = try? await send(method: "POST", path: "/api/logout", acceptError: true)
        }
        tokens = nil
        tokensExpire = nil
        if let host = origin?.host { cookies.clear(host: host) }
    }

    public func session() async throws -> SessionInfo {
        let data = try await send(method: "GET", path: "/api/session")
        return try APIDecoding.decode(SessionInfo.self, from: data)
    }

    // MARK: Account

    /// Turns off this person's sign-in and records a pending deletion request.
    /// Refuses a body that claims the account was deleted.
    public func requestAccountDeletion(pin: String) async throws -> DeletionReceipt {
        let body = try Self.json([
            "confirm": AIDisclosure.confirmPhrase,
            "pin": pin,
        ])
        let data = try await send(method: "POST", path: "/api/account/deletion-request", body: body)
        let receipt = try APIDecoding.decode(DeletionReceipt.self, from: data)
        if receipt.deleted || receipt.deletion != "pending" || !receipt.deactivated {
            throw JarvisError.http(502, "The server's reply did not match a pending request. Nothing here was treated as deleted.")
        }
        dropSession()
        return receipt
    }

    /// Owner-only. Describes full team deletion and does not perform it.
    public func teamDeletionImpact() async throws -> TeamDeletionImpact {
        let data = try await send(method: "GET", path: "/api/account/tenant-deletion")
        return try APIDecoding.decode(TeamDeletionImpact.self, from: data)
    }

    /// Owner-only one-time nonce. Does not delete anything.
    public func teamDeletionChallenge() async throws -> TeamDeletionImpact {
        let data = try await send(
            method: "POST", path: "/api/account/tenant-deletion/challenge", body: Data("{}".utf8),
            headers: ["X-Jarvis-Confirm": "delete-tenant"])
        return try APIDecoding.decode(TeamDeletionImpact.self, from: data)
    }

    /// Deletes this tenant's local records. Refuses a reply that does not say
    /// the local data is gone and sign-in is blocked, and keeps `stillExists`
    /// for the screen to show.
    public func deleteTeam(pin: String, phrase: String, nonce: String) async throws -> TeamDeletionReceipt {
        let body = try Self.json([
            "confirm": phrase,
            "pin": pin,
            "nonce": nonce,
        ])
        let data = try await send(
            method: "POST", path: "/api/account/tenant-deletion", body: body,
            headers: ["X-Jarvis-Confirm": "delete-tenant"])
        let receipt = try APIDecoding.decode(TeamDeletionReceipt.self, from: data)
        if receipt.action != "team-deleted" || !receipt.deletedLocal || !receipt.signInBlocked {
            throw JarvisError.http(502, "The server did not confirm that this team's local records were removed.")
        }
        dropSession()
        return receipt
    }

    // MARK: Conversations

    public func threads() async throws -> ThreadsResponse {
        let data = try await send(method: "GET", path: "/api/threads")
        return try APIDecoding.decode(ThreadsResponse.self, from: data)
    }

    public func createThread(name: String, categoryID: String?) async throws -> ThreadItem {
        let body = try Self.json(["name": name, "category_id": categoryID ?? NSNull()])
        let data = try await send(method: "POST", path: "/api/threads", body: body)
        return try Self.boxed(ThreadItem.self, "thread", from: data)
    }

    public func renameThread(id: String, name: String) async throws {
        _ = try await send(method: "PATCH", path: "/api/threads/\(Self.seg(id))", body: try Self.json(["name": name]))
    }

    public func archiveThread(id: String) async throws {
        _ = try await send(
            method: "POST", path: "/api/threads/\(Self.seg(id))/archive",
            query: [URLQueryItem(name: "archived", value: "true")])
    }

    public func setThreadCategory(id: String, categoryID: String?) async throws {
        let body = try Self.json(["category_id": categoryID ?? NSNull()])
        _ = try await send(method: "PUT", path: "/api/threads/\(Self.seg(id))/category", body: body)
    }

    public func createCategory(name: String) async throws {
        _ = try await send(method: "POST", path: "/api/categories", body: try Self.json(["name": name]))
    }

    public func renameCategory(id: String, name: String) async throws {
        _ = try await send(method: "PATCH", path: "/api/categories/\(Self.seg(id))", body: try Self.json(["name": name]))
    }

    public func deleteCategory(id: String) async throws {
        _ = try await send(method: "DELETE", path: "/api/categories/\(Self.seg(id))")
    }

    public func threadMembers(id: String) async throws -> [ThreadMember] {
        let data = try await send(method: "GET", path: "/api/threads/\(Self.seg(id))/members")
        return try Self.boxed([ThreadMember].self, "members", from: data)
    }

    public func addThreadMember(id: String, personKey: String) async throws -> [ThreadMember] {
        let data = try await send(
            method: "POST", path: "/api/threads/\(Self.seg(id))/members",
            body: try Self.json(["person_key": personKey]))
        return try Self.boxed([ThreadMember].self, "members", from: data)
    }

    public func removeThreadMember(id: String, personKey: String) async throws -> [ThreadMember] {
        let data = try await send(method: "DELETE", path: "/api/threads/\(Self.seg(id))/members/\(Self.seg(personKey))")
        return try Self.boxed([ThreadMember].self, "members", from: data)
    }

    /// Everyone on the team, for names in channels and the members sheet.
    public func directory() async throws -> [PersonChoice] {
        let data = try await send(method: "GET", path: "/api/team/directory")
        return try Self.boxed([PersonChoice].self, "people", from: data)
    }

    // MARK: Chat

    /// `after` is the newest id already on screen: only later rows come back.
    public func chatHistory(threadID: String, after: Int = 0) async throws -> ChatHistory {
        var query = [URLQueryItem(name: "thread_id", value: threadID)]
        if after > 0 { query.append(URLQueryItem(name: "after", value: String(after))) }
        let data = try await send(method: "GET", path: "/api/chat/history", query: query)
        return try APIDecoding.decode(ChatHistory.self, from: data)
    }

    /// Moves this person's read position forward; the home screen's dot goes out.
    public func markThreadRead(id: String, lastID: Int) async throws {
        _ = try await send(
            method: "POST", path: "/api/threads/\(Self.seg(id))/read",
            body: try Self.json(["last_id": lastID]))
    }

    /// Uploads one file to a thread, to be sent with the next message.
    public func uploadAttachment(threadID: String, filename: String, mime: String, data file: Data) async throws -> AttachmentItem {
        let boundary = "jarvis-\(UUID().uuidString)"
        let safeName = filename.replacingOccurrences(of: "\"", with: "'").replacingOccurrences(of: "\r", with: " ").replacingOccurrences(of: "\n", with: " ")
        var body = Data()
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"\(safeName)\"\r\n".utf8))
        body.append(Data("Content-Type: \(mime)\r\n\r\n".utf8))
        body.append(file)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        let data = try await send(
            method: "POST", path: "/api/threads/\(Self.seg(threadID))/attachments", body: body,
            contentType: "multipart/form-data; boundary=\(boundary)")
        return try Self.boxed(AttachmentItem.self, "attachment", from: data)
    }

    /// One chat turn, as it streams. Refuses before any network call when
    /// consent is off.
    public func streamChat(threadID: String, message: String, attachmentIDs: [String] = []) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    guard self.aiConsent else { throw JarvisError.consentRequired }
                    var payload: [String: Any] = ["thread_id": threadID, "message": message]
                    if !attachmentIDs.isEmpty { payload["attachments"] = attachmentIDs }
                    let body = try Self.json(payload)
                    try await self.refreshIfStale()
                    var request = try self.makeRequest(method: "POST", path: "/api/chat", body: body)
                    request.setValue("application/x-ndjson", forHTTPHeaderField: "Accept")
                    var parser = NDJSONParser()
                    for try await chunk in self.transport.stream(request) {
                        try Task.checkCancellation()
                        for event in parser.feed(chunk) { continuation.yield(event) }
                    }
                    for event in parser.finish() { continuation.yield(event) }
                    continuation.finish()
                } catch is CancellationError {
                    continuation.finish(throwing: JarvisError.cancelled)
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// The direct line, as plain text lines. Kept for callers that only want
    /// the words.
    public func sendChat(message: String, threadID: String = "direct") -> AsyncThrowingStream<ChatLine, Error> {
        let events = streamChat(threadID: threadID, message: message)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await event in events {
                        switch event {
                        case .delta(let text): continuation.yield(ChatLine(kind: .delta, text: text))
                        case .error(let message, _): continuation.yield(ChatLine(kind: .error, text: message))
                        case .done: continuation.yield(ChatLine(kind: .done, text: ""))
                        case .tool: continuation.yield(ChatLine(kind: .other, text: "tool"))
                        case .notice: continuation.yield(ChatLine(kind: .other, text: "notice"))
                        case .verified: continuation.yield(ChatLine(kind: .other, text: "verified"))
                        case .action: continuation.yield(ChatLine(kind: .other, text: "action"))
                        case .posted: continuation.yield(ChatLine(kind: .other, text: "posted"))
                        case .saved: continuation.yield(ChatLine(kind: .other, text: "saved"))
                        case .other(let type): continuation.yield(ChatLine(kind: .other, text: type))
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: Activity, approvals, inbox

    public func feed(limit: Int = 50) async throws -> [FeedEvent] {
        let data = try await send(method: "GET", path: "/api/feed", query: [URLQueryItem(name: "limit", value: String(limit))])
        return try Self.boxed([FeedEvent].self, "events", from: data)
    }

    /// Today's counts. "Today" needs a zone, and the phone holding the app is
    /// the best authority on what day it is for the person reading.
    public func feedSummary(timeZone: String?) async throws -> FeedSummary {
        var query: [URLQueryItem] = []
        if let timeZone, !timeZone.isEmpty { query.append(URLQueryItem(name: "tz", value: timeZone)) }
        let data = try await send(method: "GET", path: "/api/feed/summary", query: query)
        return try APIDecoding.decode(FeedSummary.self, from: data)
    }

    public func approvals() async throws -> [ApprovalItem] {
        let data = try await send(method: "GET", path: "/api/approvals")
        guard let rows = try JSONValue.parse(data)["approvals"]?.arrayValue else { throw JarvisError.badResponse }
        return rows.compactMap(ApprovalItem.init(json:))
    }

    /// Records a decision and returns the request as the server now has it,
    /// when it sent it back.
    @discardableResult
    public func decide(approvalID: String, approve: Bool) async throws -> ApprovalItem? {
        let word = approve ? "approved" : "rejected"
        let body = try Self.json(["decision": word])
        let data = try await send(method: "POST", path: "/api/approvals/\(Self.seg(approvalID))", body: body)
        return (try? JSONValue.parse(data))?["approval"].flatMap(ApprovalItem.init(json:))
    }

    public func inbox() async throws -> InboxResponse {
        let data = try await send(method: "GET", path: "/api/inbox")
        return try APIDecoding.decode(InboxResponse.self, from: data)
    }

    // MARK: Tasks

    public func tasks() async throws -> [TaskItem] {
        try await tasksResponse().tasks
    }

    public func tasksResponse() async throws -> TasksResponse {
        let data = try await send(method: "GET", path: "/api/tasks")
        return try APIDecoding.decode(TasksResponse.self, from: data)
    }

    public func createTask(title: String, assignee: String?, priority: String, deadline: String?) async throws {
        let body = try Self.json([
            "title": title,
            "assignee": assignee ?? NSNull(),
            "priority": priority,
            "deadline": deadline ?? NSNull(),
        ])
        _ = try await send(method: "POST", path: "/api/tasks", body: body)
    }

    public func setTaskStatus(id: String, status: String) async throws {
        _ = try await send(method: "POST", path: "/api/tasks/\(Self.seg(id))/status", body: try Self.json(["status": status]))
    }

    public func assignTask(id: String, assignee: String?) async throws {
        _ = try await send(
            method: "POST", path: "/api/tasks/\(Self.seg(id))/assign",
            body: try Self.json(["assignee": assignee ?? NSNull()]))
    }

    public func archiveTask(id: String) async throws {
        _ = try await send(method: "POST", path: "/api/tasks/\(Self.seg(id))/archive")
    }

    // MARK: Leads

    public func leads() async throws -> [LeadItem] {
        try await leadsResponse().leads
    }

    public func leadsResponse() async throws -> LeadsResponse {
        let data = try await send(method: "GET", path: "/api/leads")
        return try APIDecoding.decode(LeadsResponse.self, from: data)
    }

    public func lead(id: String) async throws -> LeadDetail {
        let data = try await send(method: "GET", path: "/api/leads/\(Self.seg(id))")
        return try APIDecoding.decode(LeadDetail.self, from: data)
    }

    public func createLead(_ draft: LeadDraft, includeOwner: Bool) async throws {
        var payload: [String: Any] = ["name": draft.name.trimmingCharacters(in: .whitespaces), "source": "Open house"]
        func put(_ key: String, _ value: String) {
            let trimmed = value.trimmingCharacters(in: .whitespaces)
            if !trimmed.isEmpty { payload[key] = trimmed }
        }
        put("phone", draft.phone)
        put("email", draft.email)
        put("property", draft.property)
        put("temperature", draft.temperature)
        put("notes", draft.notes)
        if includeOwner { put("owner_person", draft.owner) }
        _ = try await send(method: "POST", path: "/api/leads", body: try Self.json(payload))
    }

    /// One field per tap: temperature, status or pipeline.
    public func patchLead(id: String, fields: [String: String]) async throws {
        _ = try await send(method: "PATCH", path: "/api/leads/\(Self.seg(id))", body: try Self.json(fields))
    }

    public func archiveLead(id: String) async throws {
        _ = try await send(method: "POST", path: "/api/leads/\(Self.seg(id))/archive")
    }

    public func logContact(leadID: String, kind: String, note: String?, reached: Bool?, nextDate: String?) async throws -> LeadDetail {
        var payload: [String: Any] = ["kind": kind]
        if let note, !note.isEmpty { payload["note"] = note }
        if kind != "note", let reached { payload["reached"] = reached }
        if let nextDate, !nextDate.isEmpty { payload["next_touch_at"] = nextDate }
        _ = try await send(method: "POST", path: "/api/leads/\(Self.seg(leadID))/touches", body: try Self.json(payload))
        return try await lead(id: leadID)
    }

    // MARK: Settings

    public func googleConnection() async throws -> GoogleConnection {
        let data = try await send(method: "GET", path: "/api/google/connection")
        return try APIDecoding.decode(GoogleConnection.self, from: data)
    }

    public func disconnectGoogle() async throws {
        _ = try await send(method: "POST", path: "/api/google/connection/disconnect")
    }

    /// Whether Jarvis's browser is switched on for this person.
    public func browserEnabled() async throws -> Bool {
        let data = try await send(method: "GET", path: "/api/browser/status")
        struct Box: Decodable { var enabled: Bool }
        return try APIDecoding.decode(Box.self, from: data).enabled
    }

    public func websiteLogins() async throws -> [WebsiteLogin] {
        let data = try await send(method: "GET", path: "/api/browser/logins")
        return try Self.boxed([WebsiteLogin].self, "logins", from: data)
    }

    /// The password goes to the server once and is never returned.
    public func saveWebsiteLogin(site: String, username: String, password: String) async throws {
        let body = try Self.json(["site": site, "username": username, "password": password])
        _ = try await send(method: "POST", path: "/api/browser/logins", body: body)
    }

    public func deleteWebsiteLogin(site: String) async throws {
        _ = try await send(method: "DELETE", path: "/api/browser/logins/\(Self.seg(site))")
    }

    public func macStatus() async throws -> MacStatus {
        let data = try await send(method: "GET", path: "/api/imessage/status")
        return try APIDecoding.decode(MacStatus.self, from: data)
    }

    public func newMacCode() async throws -> PairingCodeInfo {
        let data = try await send(method: "POST", path: "/api/imessage/pair")
        return try APIDecoding.decode(PairingCodeInfo.self, from: data)
    }

    public func disconnectMac(deviceID: String) async throws {
        _ = try await send(method: "DELETE", path: "/api/imessage/devices/\(Self.seg(deviceID))")
    }

    /// Emails the texts still waiting for a reply. Returns how many.
    public func sendReplyReminder() async throws -> Int {
        let data = try await send(method: "POST", path: "/api/imessage/reminder/email")
        struct Box: Decodable { var waiting: Int? }
        return try APIDecoding.decode(Box.self, from: data).waiting ?? 0
    }

    public func team() async throws -> TeamResponse {
        let data = try await send(method: "GET", path: "/api/team")
        return try APIDecoding.decode(TeamResponse.self, from: data)
    }

    public func invite(name: String, email: String, role: String) async throws -> InviteResult {
        let body = try Self.json(["name": name, "email": email, "role": role])
        let data = try await send(method: "POST", path: "/api/team/invite", body: body)
        struct Box: Decodable {
            var person: TeamPerson
            var emailed: Bool?
            var emailError: String?
            enum CodingKeys: String, CodingKey {
                case person, emailed
                case emailError = "email_error"
            }
        }
        let box = try APIDecoding.decode(Box.self, from: data)
        return InviteResult(person: box.person, emailed: box.emailed == true, emailError: box.emailError)
    }

    /// `patch` holds any of role, disabled and features.
    public func patchPerson(key: String, role: String? = nil, disabled: Bool? = nil, features: [String: Bool]? = nil) async throws -> TeamPerson {
        var payload: [String: Any] = [:]
        if let role { payload["role"] = role }
        if let disabled { payload["disabled"] = disabled }
        if let features { payload["features"] = features }
        let data = try await send(method: "PATCH", path: "/api/team/\(Self.seg(key))", body: try Self.json(payload))
        return try Self.boxed(TeamPerson.self, "person", from: data)
    }

    public func channelSettings() async throws -> ChannelSettings {
        let data = try await send(method: "GET", path: "/api/channels/settings")
        return try APIDecoding.decode(ChannelSettings.self, from: data)
    }

    public func setChannelSettings(jarvisReplies: String) async throws -> ChannelSettings {
        let data = try await send(
            method: "PUT", path: "/api/channels/settings",
            body: try Self.json(["jarvis_replies": jarvisReplies]))
        return try APIDecoding.decode(ChannelSettings.self, from: data)
    }

    /// A page of the web app the phone hands to the browser, such as the
    /// Google connect flow. Nil for anything that is not a plain path.
    public func webURL(_ path: String) -> URL? {
        guard let origin, path.hasPrefix("/"), !path.contains("?"), !path.contains("#") else { return nil }
        return origin.endpoint(path)
    }

    // MARK: Plumbing

    public func cookieNamesForTests() -> [String] {
        guard let host = origin?.host else { return [] }
        return cookies.names(for: host)
    }

    private func dropSession() {
        tokens = nil
        tokensExpire = nil
        if let host = origin?.host { cookies.clear(host: host) }
    }

    private func adopt(_ next: AppTokens) {
        tokens = next
        tokensExpire = Date().addingTimeInterval(TimeInterval(next.expiresIn ?? 3600))
        // One session at a time: the phone's tokens replace a web cookie.
        if let host = origin?.host { cookies.clear(host: host) }
    }

    /// A new access token a minute before the old one runs out, so a long
    /// stream never starts on a token about to expire.
    private func refreshIfStale() async throws {
        guard tokens != nil, let tokensExpire, tokensExpire.timeIntervalSinceNow < 60 else { return }
        _ = try await refreshTokens()
    }

    /// False when there is nothing to refresh. Throws 401 when the server
    /// says this phone has to sign in again.
    private func refreshTokens() async throws -> Bool {
        guard let current = tokens else { return false }
        let body = try Self.json(["device_id": current.deviceId, "refresh_token": current.refreshToken])
        var request = try makeRequest(method: "POST", path: "/api/app/refresh", body: body)
        request.setValue(nil, forHTTPHeaderField: "Authorization")
        let (data, response) = try await transport.send(request)
        guard (200..<300).contains(response.statusCode) else {
            if response.statusCode == 401 {
                tokens = nil
                tokensExpire = nil
            }
            throw JarvisError.http(response.statusCode, APIDecoding.message(from: data, status: response.statusCode))
        }
        adopt(try APIDecoding.decode(AppTokens.self, from: data))
        return true
    }

    private func send(
        method: String, path: String, query: [URLQueryItem] = [], body: Data? = nil,
        contentType: String = "application/json", acceptError: Bool = false, headers: [String: String] = [:]
    ) async throws -> Data {
        let signingIn = path.hasPrefix("/api/app/") && path != "/api/app/signout"
        if !signingIn { try await refreshIfStale() }
        var (data, response) = try await perform(
            method: method, path: path, query: query, body: body, contentType: contentType, headers: headers)
        if response.statusCode == 401, tokens != nil, !signingIn, try await refreshTokens() {
            (data, response) = try await perform(
                method: method, path: path, query: query, body: body, contentType: contentType, headers: headers)
        }
        if !acceptError && !(200..<300).contains(response.statusCode) {
            throw JarvisError.http(response.statusCode, APIDecoding.message(from: data, status: response.statusCode))
        }
        return data
    }

    private func perform(
        method: String, path: String, query: [URLQueryItem], body: Data?, contentType: String, headers: [String: String]
    ) async throws -> (Data, HTTPURLResponse) {
        let request = try makeRequest(
            method: method, path: path, query: query, body: body, contentType: contentType, headers: headers)
        let (data, response) = try await transport.send(request)
        if let header = response.value(forHTTPHeaderField: "Set-Cookie"), let host = origin?.host {
            cookies.store(setCookieHeader: header, host: host)
        }
        return (data, response)
    }

    private func makeRequest(
        method: String, path: String, query: [URLQueryItem] = [], body: Data?,
        contentType: String = "application/json", headers: [String: String] = [:]
    ) throws -> URLRequest {
        guard let origin else { throw JarvisError.noOrigin }
        var request = URLRequest(url: origin.endpoint(path, query: query))
        request.httpMethod = method
        request.httpBody = body
        if body != nil { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        if let tokens {
            request.setValue("Bearer \(tokens.accessToken)", forHTTPHeaderField: "Authorization")
        } else if let cookie = cookies.header(for: origin.host) {
            request.setValue(cookie, forHTTPHeaderField: "Cookie")
        }
        return request
    }

    private static func json(_ object: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: object)
    }

    /// Decodes `{ "<key>": T }`, the envelope most writes answer with.
    private static func boxed<T: Decodable>(_ type: T.Type, _ key: String, from data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.userInfo[Envelope<T>.keyName] = key
        do { return try decoder.decode(Envelope<T>.self, from: data).value }
        catch { throw JarvisError.badResponse }
    }

    /// One path segment, percent-encoded.
    private static func seg(_ id: String) -> String {
        var allowed = CharacterSet.alphanumerics
        allowed.insert(charactersIn: "-._~")
        return id.addingPercentEncoding(withAllowedCharacters: allowed) ?? id
    }
}

private struct EnvelopeKey: CodingKey {
    var stringValue: String
    var intValue: Int? { nil }
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { nil }
}

private struct Envelope<T: Decodable>: Decodable {
    var value: T

    static var keyName: CodingUserInfoKey { CodingUserInfoKey(rawValue: "jarvis.envelope.key")! }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: EnvelopeKey.self)
        guard let name = decoder.userInfo[Self.keyName] as? String, let key = EnvelopeKey(stringValue: name) else {
            throw JarvisError.badResponse
        }
        value = try container.decode(T.self, forKey: key)
    }
}
