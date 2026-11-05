import XCTest
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import JarvisKit

final class FakeTransport: HTTPTransport {
    struct Call {
        var method: String
        var path: String
        var body: Data?
        var cookie: String?
    }
    var calls: [Call] = []
    var handler: ((URLRequest) throws -> (Int, String, Data))?
    var streamBody: Data?

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        calls.append(Call(
            method: request.httpMethod ?? "",
            path: request.url?.path ?? "",
            body: request.httpBody,
            cookie: request.value(forHTTPHeaderField: "Cookie")
        ))
        let (status, header, data) = try handler?(request) ?? (200, "", Data("{}".utf8))
        let url = request.url!
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: header.isEmpty ? [:] : ["Set-Cookie": header])!
        return (data, response)
    }

    func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        calls.append(Call(
            method: request.httpMethod ?? "",
            path: request.url?.path ?? "",
            body: request.httpBody,
            cookie: request.value(forHTTPHeaderField: "Cookie")
        ))
        let body = streamBody ?? Data()
        return AsyncThrowingStream { continuation in
            continuation.yield(body)
            continuation.finish()
        }
    }
}

final class Box { var n = 0 }

final class JarvisKitTests: XCTestCase {
    func testOriginRejectsHTTPAndCredentials() {
        XCTAssertEqual(TenantOrigin.parse("http://example.com").map(\.host), .failure(.notHTTPS))
        XCTAssertEqual(TenantOrigin.parse("https://user:secret@example.com").map(\.host), .failure(.credentialsInURL))
        XCTAssertEqual(TenantOrigin.parse("  https://team.example.com/app  ").map(\.host), .success("team.example.com"))
    }

    func testLoginCookieIsScopedAndCleared() async throws {
        let fake = FakeTransport()
        let leadsSeen = Box()
        fake.handler = { request in
            if request.url?.path == "/api/login" {
                let body = try JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? [String: Any]
                XCTAssertEqual(body?["person_key"] as? String, "omar")
                XCTAssertNil(body?["passphrase"])
                return (200, "jarvis_session=abc; HttpOnly; Path=/; SameSite=lax", Data("{\"ok\":true,\"person_key\":\"omar\",\"role\":\"member\"}".utf8))
            }
            if request.url?.path == "/api/leads" {
                leadsSeen.n += 1
                if leadsSeen.n == 1 {
                    XCTAssertEqual(request.value(forHTTPHeaderField: "Cookie"), "jarvis_session=abc")
                } else {
                    XCTAssertNil(request.value(forHTTPHeaderField: "Cookie"))
                }
                return (200, "", Data("{\"leads\":[]}".utf8))
            }
            return (200, "", Data("{\"ok\":true}".utf8))
        }
        let client = JarvisClient(transport: fake)
        try client.useOrigin("https://team.example.com")
        _ = try await client.login(personKey: "omar", pin: "pin-not-stored")
        XCTAssertEqual(client.cookieNamesForTests(), ["jarvis_session"])
        _ = try await client.leads()
        await client.logout()
        XCTAssertEqual(client.cookieNamesForTests(), [])
        _ = try await client.leads()
        XCTAssertEqual(leadsSeen.n, 2)
    }

    func testDeniedConsentDoesNotDispatchChat() async throws {
        let fake = FakeTransport()
        let client = JarvisClient(transport: fake, aiConsent: false)
        try client.useOrigin("https://team.example.com")
        var iterator = client.sendChat(message: "hello").makeAsyncIterator()
        do {
            _ = try await iterator.next()
            XCTFail("expected consent refusal")
        } catch JarvisError.consentRequired {
            XCTAssertTrue(fake.calls.isEmpty)
        }
    }

    func testConsentedChatParsesDeltasAndPostsDirectThread() async throws {
        let fake = FakeTransport()
        fake.streamBody = Data("{\"type\":\"delta\",\"text\":\"Hi\"}\n{\"type\":\"done\"}\n".utf8)
        let client = JarvisClient(transport: fake, aiConsent: true)
        try client.useOrigin("https://team.example.com")
        var texts: [ChatLine] = []
        for try await line in client.sendChat(message: "hello") { texts.append(line) }
        XCTAssertEqual(texts.map(\.kind), [.delta, .done])
        XCTAssertEqual(texts.first?.text, "Hi")
        let body = try JSONSerialization.jsonObject(with: fake.calls[0].body!) as? [String: Any]
        XCTAssertEqual(body?["thread_id"] as? String, "direct")
        XCTAssertEqual(body?["message"] as? String, "hello")
        XCTAssertEqual(fake.calls[0].path, "/api/chat")
    }

    func testServerErrorIsTheDetailNotTheBodyDump() async throws {
        let fake = FakeTransport()
        fake.handler = { _ in (403, "", Data("{\"detail\":\"Ask is switched off for this person.\"}".utf8)) }
        let client = JarvisClient(transport: fake)
        try client.useOrigin("https://team.example.com")
        do {
            _ = try await client.leads()
            XCTFail("expected http")
        } catch JarvisError.http(let code, let message) {
            XCTAssertEqual(code, 403)
            XCTAssertEqual(message, "Ask is switched off for this person.")
        }
    }

    func testLeadTouchAndApprovalUseTheRealPaths() async throws {
        let fake = FakeTransport()
        fake.handler = { request in
            if request.url?.path == "/api/leads/ld_1" {
                return (200, "", Data("{\"lead\":{\"lead_id\":\"ld_1\",\"name\":\"Ada\",\"status\":\"No Response\",\"pipeline\":\"New\"},\"timeline\":[{\"kind\":\"call\",\"note\":\"left a voicemail\",\"reached\":false}]}".utf8))
            }
            return (200, "", Data("{\"ok\":true}".utf8))
        }
        let client = JarvisClient(transport: fake)
        try client.useOrigin("https://team.example.com")
        let detail = try await client.logContact(leadID: "ld_1", kind: "call", note: "left a voicemail", reached: false, nextDate: nil)
        XCTAssertEqual(detail.lead.pipeline, "New")
        XCTAssertEqual(detail.timeline.first?.reached, false)
        let touch = fake.calls[0]
        XCTAssertEqual(touch.path, "/api/leads/ld_1/touches")
        let touchBody = try JSONSerialization.jsonObject(with: touch.body!) as? [String: Any]
        XCTAssertEqual(touchBody?["kind"] as? String, "call")
        XCTAssertEqual(touchBody?["reached"] as? Bool, false)

        try await client.decide(approvalID: "apr_1", approve: true)
        XCTAssertEqual(fake.calls[2].path, "/api/approvals/apr_1")
        let decision = try JSONSerialization.jsonObject(with: fake.calls[2].body!) as? [String: Any]
        XCTAssertEqual(decision?["decision"] as? String, "approved")
    }

    func testFeatureFlagDoesNotStopTheRequest() async throws {
        let fake = FakeTransport()
        fake.handler = { _ in (200, "", Data("{\"tasks\":[{\"task_id\":\"t1\",\"title\":\"Call back\"}]}".utf8)) }
        let client = JarvisClient(transport: fake)
        try client.useOrigin("https://team.example.com")
        let tasks = try await client.tasks()
        XCTAssertEqual(tasks.first?.title, "Call back")
        let session = SessionInfo(ok: true, authenticated: true, personKey: "omar", role: "member", displayName: "Omar", features: ["ask": false, "leads": true])
        XCTAssertFalse(session.allows("ask"))
        XCTAssertTrue(session.allows("leads"))
        XCTAssertTrue(session.allows("tasks"))
    }
}

final class CookieScopeTests: XCTestCase {
    func testSwitchingHostDropsCookies() async throws {
        let fake = FakeTransport()
        fake.handler = { request in
            if request.url?.host == "a.example.com" {
                return (200, "jarvis_session=from-a; Path=/", Data("{\"ok\":true}".utf8))
            }
            XCTAssertNil(request.value(forHTTPHeaderField: "Cookie"))
            return (200, "", Data("{\"leads\":[]}".utf8))
        }
        let client = JarvisClient(transport: fake)
        try client.useOrigin("https://a.example.com")
        _ = try await client.login(personKey: "omar", pin: "x")
        XCTAssertEqual(client.cookieNamesForTests(), ["jarvis_session"])
        try client.useOrigin("https://b.example.com")
        XCTAssertEqual(client.cookieNamesForTests(), [])
        _ = try await client.leads()
    }
}
