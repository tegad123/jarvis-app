import XCTest
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
@testable import JarvisKit

/// A transport that records whole requests, for the routes whose query,
/// headers or order matter.
final class RecordingTransport: HTTPTransport {
    var requests: [URLRequest] = []
    var handler: (URLRequest) -> (Int, String) = { _ in (200, "{}") }
    var streamBody = ""

    func send(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        let (status, body) = handler(request)
        return (Data(body.utf8), HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: [:])!)
    }

    func stream(_ request: URLRequest) -> AsyncThrowingStream<Data, Error> {
        requests.append(request)
        let body = Data(streamBody.utf8)
        return AsyncThrowingStream { continuation in
            continuation.yield(body)
            continuation.finish()
        }
    }

    func body(_ index: Int) -> [String: Any] {
        guard let data = requests[index].httpBody,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return object
    }
}

private let tokenJSON = """
{"access_token":"ja1.first","refresh_token":"r1","device_id":"dev_1","expires_in":3600,"token_type":"Bearer","person_key":"dana","role":"owner","tenant":"t"}
"""

final class WebParityTests: XCTestCase {
    private let utc = TimeZone(secondsFromGMT: 0)!

    override func setUp() {
        JarvisFormat.locale = Locale(identifier: "en_US")
        JarvisFormat.timeZone = utc
    }

    private func client(_ transport: HTTPTransport) throws -> JarvisClient {
        let client = JarvisClient(transport: transport, aiConsent: true)
        try client.useOrigin("https://team.example.com")
        return client
    }

    // MARK: Time

    func testServerTimestampsParse() {
        let micro = JarvisTime.parse("2026-08-16T15:35:35.051992+00:00")
        XCTAssertEqual(micro?.timeIntervalSince1970 ?? 0, 1_786_894_535.051992, accuracy: 0.001)
        XCTAssertEqual(JarvisTime.parse("2026-08-16T15:35:35Z"), JarvisTime.parse("2026-08-16T10:35:35-05:00"))
        XCTAssertEqual(JarvisTime.parse("2026-08-16")?.timeIntervalSince1970, 1_786_838_400)
        XCTAssertEqual(
            JarvisTime.parse("2026-08-16T15:35:35", timeZone: TimeZone(secondsFromGMT: 3600)!),
            JarvisTime.parse("2026-08-16T14:35:35Z"))
        XCTAssertNil(JarvisTime.parse("yesterday"))
        XCTAssertNil(JarvisTime.parse("2026-13-40"))
        XCTAssertNil(JarvisTime.parse(nil))
    }

    func testRelativeTimeMatchesTheWebsBoundaries() {
        let now = Date(timeIntervalSince1970: 1_786_894_535)
        func ago(_ seconds: Double) -> String { JarvisTime.relative(now.addingTimeInterval(-seconds), now: now) }
        XCTAssertEqual(ago(-30), "just now")
        XCTAssertEqual(ago(44), "just now")
        XCTAssertEqual(ago(45), "1m")
        XCTAssertEqual(ago(89), "1m")
        XCTAssertEqual(ago(90), "2m")
        XCTAssertEqual(ago(59 * 60), "59m")
        XCTAssertEqual(ago(59.5 * 60), "1h")
        XCTAssertEqual(ago(23 * 3600), "23h")
        XCTAssertEqual(ago(23.5 * 3600), "1d")
        XCTAssertEqual(ago(6 * 86_400), "6d")
        XCTAssertEqual(ago(7 * 86_400), "Aug 9")
        XCTAssertEqual(JarvisTime.relative("not a time"), "")
    }

    func testDurationAndQuiet() {
        XCTAssertEqual(JarvisTime.durationOpen(0), "open today")
        XCTAssertEqual(JarvisTime.durationOpen(nil), "open today")
        XCTAssertEqual(JarvisTime.durationOpen(1), "open 1 day")
        XCTAssertEqual(JarvisTime.durationOpen(13), "open 13 days")
        XCTAssertEqual(JarvisTime.durationOpen(18), "open 3 weeks")
        let now = Date(timeIntervalSince1970: 1_786_894_535)
        XCTAssertFalse(JarvisTime.isQuiet("2026-08-15T15:35:35+00:00", now: now))
        XCTAssertTrue(JarvisTime.isQuiet("2026-08-14T15:35:34+00:00", now: now))
    }

    // MARK: Wording

    func testKindStylesFollowTheWebTable() {
        XCTAssertEqual(KindStyle.of("email.classified"), KindStyle(semantic: .info, word: "Email filed"))
        XCTAssertEqual(KindStyle.of("task.completed"), KindStyle(semantic: .neutral, word: "Done"))
        XCTAssertEqual(KindStyle.of("lead.quiet"), KindStyle(semantic: .alert, word: "Going quiet"))
        XCTAssertEqual(KindStyle.of("email.needs_approval"), KindStyle(semantic: .warning, word: "Email Needs Approval"))
        XCTAssertEqual(KindStyle.of("sync.failed"), KindStyle(semantic: .alert, word: "Sync Failed"))
        XCTAssertEqual(KindStyle.of("deal.channel_created"), KindStyle(semantic: .info, word: "Deal Channel Created"))
        XCTAssertEqual(KindStyle.of("deal.created"), KindStyle(semantic: .success, word: "Deal Created"))
        XCTAssertEqual(KindStyle.of("  "), KindStyle(semantic: .info, word: "Update"))
        XCTAssertEqual(KindStyle.of(nil), KindStyle(semantic: .info, word: "Update"))
    }

    func testDaySummaryCountsAndPlurals() {
        let summary = DaySummary(counts: [
            "email.classified": 3, "task.completed": 2, "task.stale": 1, "lead.quiet": 1, "task.created": 1, "watch.done": 2,
        ])
        XCTAssertEqual(summary.todayCount, 10)
        XCTAssertEqual(summary.needsYouCount, 2)
        XCTAssertEqual(summary.items, ["3 emails filed", "2 done", "2 goings quiet", "2 watches done", "1 task created"])
        XCTAssertEqual(DaySummary(counts: [:]).items, [])
    }

    func testStepNames() {
        XCTAssertEqual(StepWording.name("web_search"), "Searching the web")
        XCTAssertEqual(StepWording.name("calendar_lookup"), "Calendar Lookup")
        XCTAssertEqual(StepWording.name(""), "Thinking…")
        XCTAssertEqual(StepWording.name(nil), "Thinking…")
        XCTAssertEqual(StepWording.truncate("ls   -la\n /tmp"), "ls -la /tmp")
        let long = StepWording.truncate(String(repeating: "a", count: 60))
        XCTAssertEqual(long.count, 48)
        XCTAssertTrue(long.hasSuffix("…"))
    }

    func testLeadDueLabels() {
        XCTAssertEqual(
            LeadDates.dueLabel(next: "2026-08-14", today: "2026-08-16", status: nil),
            DueLabel(text: "Overdue · was due Aug 14", now: true))
        XCTAssertEqual(
            LeadDates.dueLabel(next: "2026-08-16T00:00:00", today: "2026-08-16", status: nil),
            DueLabel(text: "Follow up today", now: true))
        XCTAssertEqual(
            LeadDates.dueLabel(next: "2026-08-20", today: "2026-08-16", status: nil),
            DueLabel(text: "Next follow-up · Aug 20", now: false))
        XCTAssertEqual(
            LeadDates.dueLabel(next: "2026-08-20", today: nil, status: nil),
            DueLabel(text: "Follow up Aug 20", now: false))
        XCTAssertNil(LeadDates.dueLabel(next: "2026-08-14", today: "2026-08-16", status: "Not Interested"))
        XCTAssertNil(LeadDates.dueLabel(next: nil, today: "2026-08-16", status: nil))
        XCTAssertTrue(LeadDates.isDue(next: "2026-08-16", today: "2026-08-16", status: "Responded"))
        XCTAssertFalse(LeadDates.isDue(next: "2026-08-17", today: "2026-08-16", status: nil))
        XCTAssertFalse(LeadDates.isDue(next: "2026-08-10", today: "2026-08-16", status: "Not Interested"))
        XCTAssertEqual(
            LeadDates.firstFollowUp(LeadCadence(hot: 1, warm: 2, other: 5)),
            "Jarvis sets the first follow-up: tomorrow if hot, in two days if warm, otherwise in 5 days.")
    }

    func testCyclesAndSmallWords() {
        XCTAssertEqual(LeadWording.next(after: "", in: LeadWording.temperatureCycle), "Cold")
        XCTAssertEqual(LeadWording.next(after: "Hot", in: LeadWording.temperatureCycle), "")
        XCTAssertEqual(LeadWording.next(after: "Lost", in: LeadWording.pipelineCycle), "New")
        XCTAssertEqual(LeadWording.next(after: "something else", in: LeadWording.statusCycle), "No Response")
        XCTAssertEqual(LeadWording.next(after: "done", in: TaskWording.statusCycle), "open")
        // The web's stage button: an unknown stage counts as the first one.
        XCTAssertEqual(LeadWording.nextPipeline(after: LeadWording.pipelineCycle[0]), LeadWording.pipelineCycle[1])
        XCTAssertEqual(LeadWording.nextPipeline(after: "not a stage"), LeadWording.pipelineCycle[1])
        XCTAssertEqual(LeadWording.nextPipeline(after: LeadWording.pipelineCycle.last), LeadWording.pipelineCycle[0])
        XCTAssertTrue(LeadWording.statusCycle.contains("Dead? (confirm)"))
        XCTAssertEqual(Wording.initials("Dana Whitfield"), "DW")
        XCTAssertEqual(Wording.initials("priya"), "P")
        XCTAssertEqual(Wording.initials(""), "J")
        XCTAssertEqual(Wording.titleOnly("Broker (owner)"), "Broker")
        XCTAssertEqual(Wording.humanSize(300), "1 KB")
        XCTAssertEqual(Wording.humanSize(1_572_864), "1.5 MB")
        XCTAssertEqual(SettingsWording.clockLabel("09:00"), "9 AM")
        XCTAssertEqual(SettingsWording.clockLabel("16:30"), "4:30 PM")
        XCTAssertEqual(SettingsWording.clockLabel("00:05"), "12:05 AM")
        XCTAssertEqual(SettingsWording.listLabel(["9 AM", "4:30 PM"]), "9 AM and 4:30 PM")
        XCTAssertEqual(SettingsWording.listLabel(["a", "b", "c"]), "a, b and c")
        XCTAssertEqual(ApprovalWording.fieldLabel("reply_to"), "Reply to")
    }

    func testPinFormat() {
        XCTAssertEqual(PinFormat.format("ABCD efgh-1234 zz"), "abcd-efgh-1234")
        XCTAssertEqual(PinFormat.format("abcde"), "abcd-e")
        XCTAssertEqual(PinFormat.format(""), "")
        XCTAssertTrue(PinFormat.isComplete("abcd-efgh-1234"))
        XCTAssertFalse(PinFormat.isComplete("abcd-efgh-123"))
    }

    func testMentionsOnlyCountWholeNames() {
        let runs = Mentions.runs("@jarvis ask @Dana, not dana@mail.example or @danab", names: ["jarvis", "Dana"], selfNames: ["dana"])
        XCTAssertEqual(runs.filter(\.isMention).map(\.text), ["@jarvis", "@Dana"])
        XCTAssertEqual(runs.filter(\.isMention).map(\.isSelf), [false, true])
        XCTAssertEqual(runs.map(\.text).joined(), "@jarvis ask @Dana, not dana@mail.example or @danab")
        XCTAssertEqual(Mentions.runs("no mention here", names: ["jarvis"]).count, 1)
        // The longer name wins over its own first word.
        XCTAssertEqual(
            Mentions.runs("hi @Dana Whitfield", names: ["Dana", "Dana Whitfield"]).last?.text, "@Dana Whitfield")
    }

    func testMentionQueryAndCompletion() {
        XCTAssertEqual(Mentions.query(in: "ask @da"), "da")
        XCTAssertEqual(Mentions.query(in: "@"), "")
        XCTAssertEqual(Mentions.query(in: "(@pr"), "pr")
        XCTAssertNil(Mentions.query(in: "mail me at dana@ma"))
        XCTAssertNil(Mentions.query(in: "@dana hello"))
        XCTAssertNil(Mentions.query(in: "nothing"))
        XCTAssertEqual(Mentions.complete("ask @da", with: "Dana Whitfield"), "ask @Dana Whitfield ")
        let people = ["Jarvis", "Priya Raman", "Omar Haddad"]
        XCTAssertEqual(Mentions.matches("ra", in: people) { $0 }, ["Priya Raman"])
        XCTAssertEqual(Mentions.matches("", in: people) { $0 }.count, 3)
    }

    func testMacAndGoogleStatusCopy() {
        let now = Date(timeIntervalSince1970: 1_786_894_535)
        func device(_ last: String?) -> MacDevice { MacDevice(deviceId: "d", name: "Mac", createdAt: nil, lastSyncAt: last) }
        XCTAssertEqual(SettingsWording.macStatus([], now: now).label, "Not connected")
        XCTAssertEqual(SettingsWording.macStatus([device("2026-08-16T15:30:35+00:00")], now: now).label, "Syncing")
        XCTAssertEqual(SettingsWording.macStatus([device(nil)], now: now).label, "Waiting for the Mac")
        XCTAssertEqual(SettingsWording.macStatus([device("2026-08-16T12:35:35+00:00")], now: now).label, "Not syncing")
        XCTAssertEqual(SettingsWording.macDetail("2026-08-16T15:30:35+00:00", now: now), "synced 5m")
        XCTAssertEqual(
            SettingsWording.macDetail("2026-08-16T12:35:35+00:00", now: now),
            "last synced 3h: open Jarvis on the Mac to see what’s wrong")

        let connected = GoogleConnection(status: "connected", googleEmail: "a@b.example", connectedAt: "2026-08-10T15:35:35+00:00", refreshedAt: "2026-08-16T14:35:35+00:00")
        let copy = SettingsWording.googleStatus(connected, phase: .idle, now: now)
        XCTAssertEqual(copy.status, StatusCopy(tone: .done, label: "Connected"))
        XCTAssertEqual(copy.time, "Last refreshed 1h")
        XCTAssertEqual(SettingsWording.googleStatus(nil, phase: .idle).status.label, "Never connected")
        XCTAssertEqual(SettingsWording.googleStatus(connected, phase: .disconnected).status.label, "Disconnected")
        XCTAssertEqual(SettingsWording.googleStatus(GoogleConnection(status: "needs_reauth"), phase: .idle).status.tone, .action)
    }

    // MARK: JSON order, approvals, chat lines

    func testJSONKeepsTheServersKeyOrder() throws {
        let value = try JSONValue.parse(Data(#"{"to":"a@b.example","subject":"Hi \"there\" \u00e9\ud83d\ude00","message":"x\ny","n":[1,2.5,-3e2,true,null]}"#.utf8))
        XCTAssertEqual(value.objectValue?.keys, ["to", "subject", "message", "n"])
        XCTAssertEqual(value["subject"]?.stringValue, "Hi \"there\" é😀")
        XCTAssertEqual(value["message"]?.stringValue, "x\ny")
        XCTAssertEqual(value["n"]?.display, "1, 2.5, -300, true, ")
        XCTAssertThrowsError(try JSONValue.parse(Data(#"{"a":1,}"#.utf8)))
        XCTAssertThrowsError(try JSONValue.parse(Data(#"{"a":1} trailing"#.utf8)))
    }

    func testApprovalFieldsAndConfigChanges() async throws {
        let fake = RecordingTransport()
        fake.handler = { _ in (200, """
            {"approvals":[
             {"id":"a1","summary":"Send","status":"pending","kind":"action","can_approve":false,"can_reject":true,
              "payload":{"type":"send_email","zeta":"last"},
              "display":{"to":"x@y.example","subject":"Hello","cc":null,"note":"","message":"Body"}},
             {"id":"a2","summary":"Change","status":"pending","kind":"config-change",
              "payload":{"changes":[{"key":"digest.times","label":"Digest times","current":["07:30"],"proposed":["07:30","17:00"]}]}},
             {"id":"a3","summary":"Browse","status":"approved","decided_by":"Dana","decided_at":"2020-01-01T00:00:00+00:00",
              "for_person":"Omar","can_approve":false,"can_reject":true,"payload":{"type":"browser_task","site":"zillow.com"}}
            ]}
            """) }
        let approvals = try await client(fake).approvals()
        XCTAssertEqual(approvals.map(\.id), ["a1", "a2", "a3"])
        XCTAssertEqual(approvals[0].fields.map(\.key), ["to", "subject", "message"])
        XCTAssertNil(approvals[0].changes)
        XCTAssertEqual(ApprovalWording.note(approvals[0]), "Someone else approves this one; you can reject it.")
        XCTAssertEqual(approvals[1].changes?.first?.label, "Digest times")
        XCTAssertEqual(approvals[1].changes?.first?.current.display, "07:30")
        XCTAssertEqual(approvals[1].changes?.first?.proposed.display, "07:30, 17:00")
        XCTAssertTrue(approvals[1].fields.isEmpty)
        XCTAssertNil(ApprovalWording.note(approvals[1]))
        XCTAssertEqual(approvals[2].fields.map(\.key), ["type", "site"])
        XCTAssertEqual(ApprovalWording.approveLabel(approvals[2]), "Approve for 30 min")
        XCTAssertEqual(ApprovalWording.note(approvals[2]), "For Omar’s browser. Only they can approve it; you can reject it.")
        XCTAssertEqual(ApprovalWording.decided(approvals[2], now: Date(timeIntervalSince1970: 1_577_836_860)), "Approved by Dana · 1m")
    }

    func testChatEventsParseEveryLineKind() {
        var parser = NDJSONParser()
        let text = """
        {"type":"posted","message":{"id":7,"role":"user","author":"dana","author_name":"Dana","content":"hi","ts":12.5},"jarvis":true}
        {"type":"tool","status":"running","tool":"web_search","label":"homes","toolCallId":"c1"}
        {"type":"tool","status":"completed","tool":"web_search","toolCallId":"c1"}
        {"type":"notice","message":"One file was not read."}
        {"type":"delta","text":"Hel"}
        not json at all
        {"type":"verified","ok":false,"message":"Nothing was saved."}
        {"type":"action","action":"connect_google","label":"Connect"}
        {"type":"saved","ids":[7,8]}
        {"type":"error","message":"Stopped.","partial":true}
        {"type":"future","x":1}
        {"type":"done"}
        """
        let split = text.index(text.startIndex, offsetBy: 150)
        var events = parser.feed(Data(text[..<split].utf8))
        events += parser.feed(Data(text[split...].utf8))
        events += parser.finish()
        XCTAssertEqual(events.count, 11)
        guard case .posted(let message, let jarvis) = events[0] else { return XCTFail("posted") }
        XCTAssertEqual(message?.serverId, 7)
        XCTAssertEqual(message?.authorName, "Dana")
        XCTAssertEqual(message?.id, "s7")
        XCTAssertTrue(jarvis)
        XCTAssertEqual(events[1], .tool(running: true, tool: "web_search", label: "homes", callId: "c1"))
        XCTAssertEqual(events[2], .tool(running: false, tool: "web_search", label: nil, callId: "c1"))
        XCTAssertEqual(events[3], .notice("One file was not read."))
        XCTAssertEqual(events[4], .delta("Hel"))
        XCTAssertEqual(events[5], .verified(ok: false, message: "Nothing was saved."))
        XCTAssertEqual(events[6], .action("connect_google"))
        XCTAssertEqual(events[7], .saved([7, 8]))
        XCTAssertEqual(events[8], .error(message: "Stopped.", partial: true))
        XCTAssertEqual(events[9], .other("future"))
        XCTAssertEqual(events[10], .done)
    }

    // MARK: Requests

    func testRoutesMatchTheWebClient() async throws {
        let fake = RecordingTransport()
        fake.handler = { request in
            switch request.url?.path {
            case "/api/chat/history": return (200, #"{"messages":[],"last_id":0}"#)
            case "/api/feed": return (200, #"{"events":[]}"#)
            case "/api/feed/summary": return (200, #"{"counts":{}}"#)
            case "/api/threads": return (200, #"{"thread":{"thread_id":"ch 1","name":"A"}}"#)
            default: return (200, #"{"members":[],"ok":true}"#)
            }
        }
        let client = try client(fake)
        _ = try await client.chatHistory(threadID: "ch-alder", after: 204)
        _ = try await client.chatHistory(threadID: "direct")
        _ = try await client.feed()
        _ = try await client.feedSummary(timeZone: "America/Chicago")
        try await client.archiveThread(id: "ch 1/x")
        let created = try await client.createThread(name: "128 Alder Ave", categoryID: nil)
        _ = try await client.removeThreadMember(id: "ch-1", personKey: "omar")
        try await client.setTaskStatus(id: "t1", status: "in_progress")
        try await client.patchLead(id: "ld-1", fields: ["temperature": "Hot"])
        try await client.deleteWebsiteLogin(site: "zillow.com")

        let urls = fake.requests.map { $0.url!.absoluteString }
        XCTAssertEqual(urls[0], "https://team.example.com/api/chat/history?thread_id=ch-alder&after=204")
        XCTAssertEqual(urls[1], "https://team.example.com/api/chat/history?thread_id=direct")
        XCTAssertEqual(urls[2], "https://team.example.com/api/feed?limit=50")
        XCTAssertEqual(urls[3], "https://team.example.com/api/feed/summary?tz=America/Chicago")
        XCTAssertEqual(urls[4], "https://team.example.com/api/threads/ch%201%2Fx/archive?archived=true")
        XCTAssertEqual(fake.requests[4].httpMethod, "POST")
        XCTAssertEqual(created.threadId, "ch 1")
        XCTAssertEqual(fake.body(5)["name"] as? String, "128 Alder Ave")
        XCTAssertTrue(fake.body(5)["category_id"] is NSNull)
        XCTAssertEqual(fake.requests[6].httpMethod, "DELETE")
        XCTAssertEqual(urls[6], "https://team.example.com/api/threads/ch-1/members/omar")
        XCTAssertEqual(urls[7], "https://team.example.com/api/tasks/t1/status")
        XCTAssertEqual(fake.body(7)["status"] as? String, "in_progress")
        XCTAssertEqual(fake.requests[8].httpMethod, "PATCH")
        XCTAssertEqual(fake.body(8)["temperature"] as? String, "Hot")
        XCTAssertEqual(urls[9], "https://team.example.com/api/browser/logins/zillow.com")
    }

    func testLeadDraftLeavesOutEmptyFieldsAndAMembersOwner() async throws {
        let fake = RecordingTransport()
        var draft = LeadDraft()
        draft.name = "  Renee Castillo "
        draft.phone = "(555) 010-0142"
        draft.owner = "omar"
        try await client(fake).createLead(draft, includeOwner: false)
        let body = fake.body(0)
        XCTAssertEqual(body["name"] as? String, "Renee Castillo")
        XCTAssertEqual(body["source"] as? String, "Open house")
        XCTAssertEqual(body["phone"] as? String, "(555) 010-0142")
        XCTAssertEqual(Set(body.keys), ["name", "source", "phone"])
    }

    func testChatStreamSendsAttachmentsAndAsksForNDJSON() async throws {
        let fake = RecordingTransport()
        fake.streamBody = "{\"type\":\"delta\",\"text\":\"ok\"}\n{\"type\":\"done\"}"
        var seen: [ChatEvent] = []
        for try await event in try client(fake).streamChat(threadID: "ch-1", message: "look", attachmentIDs: ["att_1"]) {
            seen.append(event)
        }
        XCTAssertEqual(seen, [.delta("ok"), .done])
        XCTAssertEqual(fake.requests[0].value(forHTTPHeaderField: "Accept"), "application/x-ndjson")
        XCTAssertEqual(fake.body(0)["thread_id"] as? String, "ch-1")
        XCTAssertEqual(fake.body(0)["attachments"] as? [String], ["att_1"])
    }

    func testUploadIsMultipartWithTheFileField() async throws {
        let fake = RecordingTransport()
        fake.handler = { _ in (201, #"{"attachment":{"id":"att_1","filename":"a.jpg","is_image":true}}"#) }
        let saved = try await client(fake).uploadAttachment(threadID: "ch-1", filename: "a\".jpg", mime: "image/jpeg", data: Data([1, 2, 3]))
        XCTAssertEqual(saved.id, "att_1")
        let request = fake.requests[0]
        XCTAssertEqual(request.url?.path, "/api/threads/ch-1/attachments")
        let type = request.value(forHTTPHeaderField: "Content-Type") ?? ""
        XCTAssertTrue(type.hasPrefix("multipart/form-data; boundary=jarvis-"))
        let text = String(decoding: request.httpBody ?? Data(), as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"file\"; filename=\"a'.jpg\""))
        XCTAssertTrue(text.contains("Content-Type: image/jpeg"))
    }

    // MARK: Phone sign-in

    func testPKCEMatchesTheRFCVector() {
        // RFC 7636, appendix B.
        let pkce = PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        XCTAssertEqual(pkce.challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        XCTAssertEqual(
            SHA256.hash(Array("abc".utf8)).map { String(format: "%02x", $0) }.joined(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        XCTAssertEqual(
            SHA256.hash(Array(String(repeating: "a", count: 119).utf8)).count, 32)
        let fresh = PKCE()
        XCTAssertEqual(fresh.verifier.count, 43)
        XCTAssertEqual(fresh.challenge.count, 43)
        XCTAssertNotEqual(fresh.verifier, PKCE().verifier)
        XCTAssertEqual(PKCE.code(fromCallback: URL(string: "jarvis://auth?code=abc123&tenant=t")!), "abc123")
        XCTAssertNil(PKCE.code(fromCallback: URL(string: "https://evil.example/auth?code=abc123")!))
        XCTAssertNil(PKCE.code(fromCallback: URL(string: "jarvis://auth?error=denied")!))
    }

    func testGoogleSignInStartsExchangesAndUsesTheBearer() async throws {
        let fake = RecordingTransport()
        fake.handler = { request in
            request.url?.path == "/api/app/exchange" ? (200, tokenJSON) : (200, #"{"leads":[]}"#)
        }
        let client = try client(fake)
        let start = try client.appStartURL(challenge: "CHALLENGE")
        XCTAssertEqual(start.absoluteString, "https://team.example.com/app/start?challenge=CHALLENGE&redirect=jarvis://auth")
        try await client.exchange(code: "one-time", verifier: "VERIFIER", deviceName: "")
        XCTAssertEqual(fake.body(0)["code"] as? String, "one-time")
        XCTAssertEqual(fake.body(0)["verifier"] as? String, "VERIFIER")
        XCTAssertEqual(fake.body(0)["platform"] as? String, "ios")
        XCTAssertEqual(fake.body(0)["device_name"] as? String, "iPhone")
        XCTAssertNil(fake.requests[0].value(forHTTPHeaderField: "Authorization"))
        XCTAssertTrue(client.hasDeviceSession)

        _ = try await client.leads()
        XCTAssertEqual(fake.requests[1].value(forHTTPHeaderField: "Authorization"), "Bearer ja1.first")
        XCTAssertNil(fake.requests[1].value(forHTTPHeaderField: "Cookie"))

        await client.logout()
        XCTAssertEqual(fake.requests[2].url?.path, "/api/app/signout")
        XCTAssertEqual(fake.requests[2].value(forHTTPHeaderField: "Authorization"), "Bearer ja1.first")
        XCTAssertFalse(client.hasDeviceSession)
        _ = try await client.leads()
        XCTAssertNil(fake.requests[3].value(forHTTPHeaderField: "Authorization"))
    }

    func testAnExpiredBearerIsRefreshedOnceAndRetried() async throws {
        let fake = RecordingTransport()
        var leadCalls = 0
        fake.handler = { request in
            switch request.url?.path {
            case "/api/app/exchange": return (200, tokenJSON)
            case "/api/app/refresh":
                return (200, tokenJSON.replacingOccurrences(of: "ja1.first", with: "ja1.second").replacingOccurrences(of: "\"r1\"", with: "\"r2\""))
            default:
                leadCalls += 1
                return leadCalls == 1 ? (401, #"{"detail":"Not authenticated."}"#) : (200, #"{"leads":[]}"#)
            }
        }
        let client = try client(fake)
        try await client.exchange(code: "c", verifier: "v", deviceName: "Tega's iPhone")
        _ = try await client.leads()
        XCTAssertEqual(fake.requests.map { $0.url!.path }, ["/api/app/exchange", "/api/leads", "/api/app/refresh", "/api/leads"])
        XCTAssertEqual(fake.body(2)["refresh_token"] as? String, "r1")
        XCTAssertEqual(fake.body(2)["device_id"] as? String, "dev_1")
        XCTAssertNil(fake.requests[2].value(forHTTPHeaderField: "Authorization"))
        XCTAssertEqual(fake.requests[3].value(forHTTPHeaderField: "Authorization"), "Bearer ja1.second")
    }

    func testARefusedRefreshSignsThePhoneOut() async throws {
        let fake = RecordingTransport()
        fake.handler = { request in
            switch request.url?.path {
            case "/api/app/exchange": return (200, tokenJSON)
            case "/api/app/refresh": return (401, #"{"detail":"Sign in again on this phone."}"#)
            default: return (401, #"{"detail":"Not authenticated."}"#)
            }
        }
        let client = try client(fake)
        try await client.exchange(code: "c", verifier: "v", deviceName: "iPhone")
        do {
            _ = try await client.leads()
            XCTFail("expected 401")
        } catch let error as JarvisError {
            XCTAssertTrue(error.isUnauthorized)
            XCTAssertEqual(error.serverMessage, "Sign in again on this phone.")
        }
        XCTAssertFalse(client.hasDeviceSession)
        // A canary for the assertion above: a cookie session has no refresh to try.
        XCTAssertEqual(fake.requests.filter { $0.url?.path == "/api/app/refresh" }.count, 1)
    }

    func testAuthMethodsFallBackToEverythingOnAnOlderServer() async throws {
        let fake = RecordingTransport()
        fake.handler = { _ in (404, #"{"detail":"Not Found"}"#) }
        let older = try await client(fake).authMethods()
        XCTAssertEqual(older, AuthMethods(pin: true, passphrase: true, google: true))
        fake.handler = { _ in (200, #"{"pin":false,"passphrase":false,"google":true}"#) }
        let googleOnly = try await client(fake).authMethods()
        XCTAssertTrue(googleOnly.googleOnly)
    }

    // MARK: Icons

    func testSVGPathReadsCompactNumbersAndImplicitRepeats() {
        XCTAssertEqual(SVGPath.steps("M12 5v14M5 12h14"), [
            .move(x: 12, y: 5), .line(x: 12, y: 19), .move(x: 5, y: 12), .line(x: 19, y: 12),
        ])
        // An implicit line after a move, relative and absolute.
        XCTAssertEqual(SVGPath.steps("M11 5 6.5 9H3v6"), [
            .move(x: 11, y: 5), .line(x: 6.5, y: 9), .line(x: 3, y: 9), .line(x: 3, y: 15),
        ])
        XCTAssertEqual(SVGPath.steps("M5.5 12h.01"), [.move(x: 5.5, y: 12), .line(x: 5.51, y: 12)])
        XCTAssertEqual(SVGPath.steps("M1 1l-.5.5-1-1z"), [
            .move(x: 1, y: 1), .line(x: 0.5, y: 1.5), .line(x: -0.5, y: 0.5), .close,
        ])
    }

    func testArcsBecomeCurvesThatStayOnTheCircle() {
        // The alert icon's ring: a full circle of radius 9 about (12, 12).
        let steps = SVGPath.steps("M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z")
        XCTAssertEqual(steps.first, .move(x: 12, y: 3))
        XCTAssertEqual(steps.last, .close)
        var x = 12.0, y = 3.0
        var curves = 0
        for step in steps {
            guard case .curve(let toX, let toY, let c1x, let c1y, let c2x, let c2y) = step else { continue }
            curves += 1
            // Sample the cubic's midpoint: it must sit on the circle too.
            let mx = (x + 3 * c1x + 3 * c2x + toX) / 8
            let my = (y + 3 * c1y + 3 * c2y + toY) / 8
            XCTAssertEqual(((mx - 12) * (mx - 12) + (my - 12) * (my - 12)).squareRoot(), 9, accuracy: 0.01)
            XCTAssertEqual(((toX - 12) * (toX - 12) + (toY - 12) * (toY - 12)).squareRoot(), 9, accuracy: 1e-9)
            x = toX
            y = toY
        }
        XCTAssertEqual(curves, 4)
        XCTAssertEqual(x, 12, accuracy: 1e-9)
        XCTAssertEqual(y, 3, accuracy: 1e-9)
        // Sweep direction: the first half goes down the left side (sweep 0).
        guard case .curve(let firstX, _, _, _, _, _) = steps[1] else { return XCTFail("curve") }
        XCTAssertEqual(firstX, 3, accuracy: 1e-9)
    }

    func testEveryIconMatchesTheWebSourceAndStaysInItsGrid() throws {
        let web = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("frontend/src/jm/shared/Icon.tsx")
        let source = try String(contentsOf: web, encoding: .utf8)
        var checked = 0
        for icon in JarvisIcon.allCases where icon != .stop {
            let quoted = icon.rawValue.contains("-") ? "'\(icon.rawValue)'" : icon.rawValue
            XCTAssertTrue(source.contains("  \(quoted): '\(icon.path)',"), "\(icon.rawValue) differs from Icon.tsx")
            checked += 1
        }
        XCTAssertEqual(checked, 32)
        // The reverse: no web icon is missing here.
        let webNames = source.components(separatedBy: "\n").filter { $0.hasPrefix("  ") && $0.contains(": 'M") }
        XCTAssertEqual(webNames.count, 32)

        for icon in JarvisIcon.allCases {
            let steps = icon.steps
            XCTAssertFalse(steps.isEmpty, icon.rawValue)
            guard case .move = steps[0] else { return XCTFail("\(icon.rawValue) does not start with a move") }
            for step in steps {
                let points: [Double]
                switch step {
                case .move(let x, let y), .line(let x, let y): points = [x, y]
                case .curve(let x, let y, let a, let b, let c, let d): points = [x, y, a, b, c, d]
                case .close: points = []
                }
                for value in points {
                    XCTAssertTrue(value.isFinite && value >= -0.5 && value <= 24.5, "\(icon.rawValue) leaves the grid: \(value)")
                }
            }
        }
    }

    // MARK: The preview fixtures decode through the real client

    func testEveryFixtureDecodesThroughTheClient() async throws {
        let client = PreviewTransport.client(.owner)
        let session = try await client.session()
        XCTAssertEqual(session.role, "owner")
        XCTAssertEqual(session.displayName, "Dana Whitfield")
        XCTAssertTrue(session.allows("leads"))

        let people = try await client.people()
        XCTAssertEqual(people.map(\.personKey), ["dana", "priya", "omar"])
        let directory = try await client.directory()
        XCTAssertEqual(directory.count, 3)

        let threads = try await client.threads()
        XCTAssertEqual(threads.threads.count, 5)
        XCTAssertEqual(threads.threads.filter(\.direct).count, 1)
        XCTAssertEqual(threads.categories?.map(\.name), ["Listings", "Buyers"])
        XCTAssertEqual(threads.jarvisReplies, "mention")
        XCTAssertEqual(threads.threads.first { $0.threadId == "ch-alder" }?.mentions, 1)

        for thread in threads.threads {
            let history = try await client.chatHistory(threadID: thread.threadId)
            XCTAssertNotNil(history.lastId, thread.threadId)
            if !thread.direct {
                let members = try await client.threadMembers(id: thread.threadId)
                XCTAssertEqual(members.map(\.personKey), thread.members ?? [], thread.threadId)
            }
        }
        let direct = try await client.chatHistory(threadID: "direct-dana")
        XCTAssertEqual(direct.messages.map(\.serverId), [101, 102, 103, 104])
        XCTAssertEqual(direct.messages.map(\.isAssistant), [false, true, false, true])
        let sent = try XCTUnwrap(direct.messages.first?.ts)
        XCTAssertEqual(Date().timeIntervalSince1970 - sent, 14 * 60, accuracy: 5)

        let feed = try await client.feed()
        XCTAssertFalse(feed.isEmpty)
        XCTAssertFalse(JarvisTime.relative(feed[0].ts).isEmpty)
        let summary = try await client.feedSummary(timeZone: "UTC")
        XCTAssertFalse(DaySummary(counts: summary.counts).items.isEmpty)

        let approvals = try await client.approvals()
        XCTAssertEqual(approvals.map(\.id), ["apr-1", "apr-2", "apr-3"])
        XCTAssertEqual(approvals[0].fields.map(\.key), ["to", "subject", "message"])
        XCTAssertEqual(approvals[1].changes?.count, 1)
        XCTAssertFalse(approvals[2].isPending)

        let inbox = try await client.inbox()
        XCTAssertEqual(inbox.status, "connected")
        XCTAssertFalse(inbox.messages.isEmpty)

        let tasks = try await client.tasksResponse()
        XCTAssertEqual(tasks.tasks.map(\.taskId), ["t1", "t2", "t3", "t4"])
        XCTAssertEqual(tasks.source, "store")

        let leads = try await client.leadsResponse()
        XCTAssertEqual(leads.leads.map(\.leadId), ["ld-1", "ld-2", "ld-3", "ld-4"])
        XCTAssertEqual(leads.today, JarvisFormat.dayString(Date()))
        XCTAssertEqual(leads.cadence, LeadCadence(hot: 1, warm: 2, other: 3))
        for lead in leads.leads {
            let detail = try await client.lead(id: lead.leadId)
            XCTAssertEqual(detail.timeline.count, 3, lead.leadId)
        }

        let google = try await client.googleConnection()
        XCTAssertEqual(google.status, "connected")
        let enabled = try await client.browserEnabled()
        XCTAssertTrue(enabled)
        let logins = try await client.websiteLogins()
        XCTAssertFalse(logins.isEmpty)
        let mac = try await client.macStatus()
        XCTAssertFalse(mac.devices.isEmpty)
        XCTAssertNotNil(mac.reminder)
        let team = try await client.team()
        XCTAssertEqual(team.people.count, 3)
        XCTAssertFalse(team.features.isEmpty)
        let channels = try await client.channelSettings()
        XCTAssertEqual(channels.jarvisReplies, "mention")

        var events: [ChatEvent] = []
        for try await event in client.streamChat(threadID: "direct-dana", message: "What's on this week?") {
            events.append(event)
        }
        XCTAssertEqual(events.first, .tool(running: true, tool: "jarvis", label: "Checking your calendar", callId: "s1"))
        XCTAssertEqual(events.last, .done)
    }

    func testAttachmentsAreFetchedByIDWithTheSession() async throws {
        let fake = RecordingTransport()
        fake.handler = { _ in (200, "bytes") }
        let jarvis = try client(fake)
        let data = try await jarvis.attachmentData(id: "att 1/x")
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "bytes")
        XCTAssertEqual(fake.requests.last?.httpMethod, "GET")
        // One path segment: an id can never reach another route.
        XCTAssertEqual(fake.requests.last?.url?.absoluteString, "https://team.example.com/api/attachments/att%201%2Fx")
    }

    func testPreviewSessionsFollowTheScenario() {
        XCTAssertEqual(PreviewTransport.session(.owner)?.role, "owner")
        XCTAssertEqual(PreviewTransport.session(.empty)?.personKey, "dana")
        XCTAssertEqual(PreviewTransport.session(.member)?.role, "member")
        XCTAssertNil(PreviewTransport.session(.signedOut))
    }

    func testPreviewScenariosDiffer() async throws {
        let member = PreviewTransport.client(.member)
        let memberSession = try await member.session()
        XCTAssertEqual(memberSession.role, "member")
        let memberLeads = try await member.leads()
        XCTAssertEqual(memberLeads.count, 2)
        let memberThreads = try await member.threads()
        XCTAssertLessThan(memberThreads.threads.count, 5)

        let empty = PreviewTransport.client(.empty)
        let emptyTasks = try await empty.tasks()
        XCTAssertTrue(emptyTasks.isEmpty)
        let emptyThreads = try await empty.threads()
        XCTAssertEqual(emptyThreads.threads.count, 1)
        let emptyHistory = try await empty.chatHistory(threadID: "direct-dana")
        XCTAssertTrue(emptyHistory.messages.isEmpty)
        let emptyLeads = try await empty.leadsResponse()
        XCTAssertEqual(emptyLeads.today, JarvisFormat.dayString(Date()))

        let signedOut = PreviewTransport.client(.signedOut)
        let people = try await signedOut.people()
        XCTAssertEqual(people.count, 3)
        do {
            _ = try await signedOut.session()
            XCTFail("expected 401")
        } catch let error as JarvisError {
            XCTAssertTrue(error.isUnauthorized)
        }
    }

    func testNoFixturePlaceholderSurvives() {
        let transport = PreviewTransport(.owner, now: Date(timeIntervalSince1970: 1_786_894_535))
        for (path, body) in PreviewFixtures.owner.merging(PreviewFixtures.member, uniquingKeysWith: { a, _ in a }) {
            XCTAssertFalse(transport.resolve(body).contains("@@"), path)
        }
        // The canary: the raw fixtures do carry placeholders, so the check above can fail.
        XCTAssertTrue(PreviewFixtures.owner["/api/threads"]?.contains("@@AGO:") == true)
        XCTAssertEqual(transport.resolve(#"{"ts": "@@TS:60@@", "at": "@@AGO:1@@", "day": "@@DAY:-1@@"}"#),
                       #"{"ts": 1786894475, "at": "2026-08-16T15:34:35.000000+00:00", "day": "2026-08-15"}"#)
    }
}
