#if DEBUG
import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Answers the client from the invented fixtures, with no network: SwiftUI
/// previews run the real client and the real screens against the same rows
/// the web reference screenshots show (ios/reference/web). Debug builds only.
public final class PreviewTransport: HTTPTransport, @unchecked Sendable {
    public enum Scenario: Sendable {
        case owner
        case member
        /// Signed in, with nothing anywhere: every list and thread is empty.
        case empty
        /// Not signed in: every signed-in route answers 401.
        case signedOut
    }

    private let scenario: Scenario
    private let now: Date

    public init(_ scenario: Scenario = .owner, now: Date = Date()) {
        self.scenario = scenario
        self.now = now
    }

    /// A client already pointed at a made-up address, with Ask allowed.
    public static func client(_ scenario: Scenario = .owner) -> JarvisClient {
        let client = JarvisClient(transport: PreviewTransport(scenario), aiConsent: true)
        try? client.useOrigin("https://jarvis.example")
        return client
    }

    /// Who is signed in under a scenario, as /api/session answers it.
    public static func session(_ scenario: Scenario = .owner) -> SessionInfo? {
        let source: String?
        switch scenario {
        case .signedOut: source = nil
        case .member: source = PreviewFixtures.member["/api/session"]
        case .owner, .empty: source = PreviewFixtures.owner["/api/session"]
        }
        return source.flatMap { try? JSONDecoder().decode(SessionInfo.self, from: Data($0.utf8)) }
    }

    public func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let method = request.httpMethod ?? "GET"
        let path = request.url?.path ?? "/"
        let key = request.url?.query.map { "\(path)?\($0)" } ?? path

        if method == "GET", let body = answer(key: key, path: path) {
            return (Data(resolve(body).utf8), response(request, 200))
        }
        if method == "GET", scenario == .signedOut {
            return (Data(#"{"detail":"Not authenticated."}"#.utf8), response(request, 401))
        }
        if method == "POST", path.hasSuffix("/read") {
            return (Data(#"{"ok":true}"#.utf8), response(request, 200))
        }
        return (Data(#"{"detail":"The preview has no \#(method) \#(path)."}"#.utf8), response(request, 404))
    }

    public func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                for line in PreviewFixtures.chat.split(separator: "\n") {
                    try? await Task.sleep(nanoseconds: 150_000_000)
                    if Task.isCancelled { break }
                    continuation.yield(Data((line + "\n").utf8))
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static let open = ["/api/auth/methods", "/api/people"]

    private func answer(key: String, path: String) -> String? {
        switch scenario {
        case .signedOut:
            return Self.open.contains(path) ? PreviewFixtures.owner[path] : nil
        case .owner:
            return PreviewFixtures.owner[key] ?? PreviewFixtures.owner[path]
        case .member:
            return PreviewFixtures.member[key] ?? PreviewFixtures.member[path]
                ?? PreviewFixtures.owner[key] ?? PreviewFixtures.owner[path]
        case .empty:
            return Self.empty[path] ?? (path == "/api/chat/history" ? Self.emptyHistory : nil)
                ?? PreviewFixtures.owner[key] ?? PreviewFixtures.owner[path]
        }
    }

    private static let emptyHistory = #"{"messages":[],"count":0,"last_id":0,"jarvis_answering":[]}"#

    private static let empty: [String: String] = [
        "/api/threads": #"{"threads":[{"thread_id":"direct-dana","name":"Ask Jarvis","is_direct":true,"archived":false}],"categories":[],"jarvis_replies":"mention","bodies_visible":true}"#,
        "/api/feed": #"{"events":[]}"#,
        "/api/feed/summary": #"{"total":0,"counts":{}}"#,
        "/api/approvals": #"{"approvals":[]}"#,
        "/api/inbox": #"{"status":"connected","mailbox":"jarvis@whitfield.example","unread":0,"messages":[]}"#,
        "/api/tasks": #"{"tasks":[],"counts":{"open":0,"in_progress":0,"done":0,"stale":0,"overdue":0},"source":"store","bodies_visible":true}"#,
        "/api/leads": #"{"leads":[],"count":0,"source":"store","tabs":[],"today":"@@DAY:0@@"}"#,
        "/api/google/connection": #"{"status":"disconnected"}"#,
        "/api/browser/logins": #"{"logins":[]}"#,
        "/api/imessage/status": #"{"devices":[],"messages":0,"retention_days":30,"mac_app":true}"#,
    ]

    private func response(_ request: URLRequest, _ status: Int) -> HTTPURLResponse {
        HTTPURLResponse(
            url: request.url ?? URL(string: "https://jarvis.example/")!, statusCode: status,
            httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
    }

    /// Fills in the fixture clock: @@AGO:minutes@@ is an ISO time that long
    /// ago, @@DAY:offset@@ a calendar day, and "@@TS:seconds@@" (with its
    /// quotes) a Unix time.
    func resolve(_ text: String) -> String {
        var out = ""
        var rest = Substring(text)
        while let open = rest.range(of: "@@") {
            guard let close = rest[open.upperBound...].range(of: "@@") else { break }
            let token = rest[open.upperBound..<close.lowerBound]
            let pieces = token.split(separator: ":", maxSplits: 1)
            guard pieces.count == 2, let value = Double(pieces[1]) else {
                out += rest[..<open.upperBound]
                rest = rest[open.upperBound...]
                continue
            }
            out += rest[..<open.lowerBound]
            rest = rest[close.upperBound...]
            switch pieces[0] {
            case "AGO":
                out += Self.iso(now.addingTimeInterval(-value * 60))
            case "DAY":
                out += JarvisFormat.dayString(now.addingTimeInterval(value * 86_400))
            case "TS":
                if out.hasSuffix("\""), rest.hasPrefix("\"") {
                    out.removeLast()
                    rest = rest.dropFirst()
                }
                out += String(Int(now.timeIntervalSince1970 - value))
            default:
                out += "@@\(token)@@"
            }
        }
        return out + rest
    }

    private static func iso(_ date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        let p = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        return String(
            format: "%04d-%02d-%02dT%02d:%02d:%02d.000000+00:00",
            p.year ?? 0, p.month ?? 0, p.day ?? 0, p.hour ?? 0, p.minute ?? 0, p.second ?? 0)
    }
}
#endif
