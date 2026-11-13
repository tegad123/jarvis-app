import Foundation

// The shapes the Jarvis server answers with. They follow
// frontend/src/lib/types.ts, the web app's own contract. Anything the server
// may leave out is optional here, so an older server narrows a screen rather
// than failing to decode.

public struct SessionInfo: Decodable, Equatable {
    public var ok: Bool?
    public var authenticated: Bool?
    public var personKey: String?
    public var role: String?
    public var displayName: String?
    public var features: [String: Bool]?

    enum CodingKeys: String, CodingKey {
        case ok, authenticated, role, features
        case personKey = "person_key"
        case displayName = "display_name"
    }

    public func allows(_ feature: String) -> Bool {
        features?[feature] != false
    }
}

public struct PersonChoice: Decodable, Equatable, Identifiable {
    public var personKey: String
    public var name: String
    public var title: String?
    public var id: String { personKey }

    enum CodingKeys: String, CodingKey {
        case name, title
        case personKey = "person_key"
    }
}

/// GET /api/auth/methods: what this address offers on its sign-in screen.
public struct AuthMethods: Decodable, Equatable {
    public var pin: Bool
    public var passphrase: Bool
    public var google: Bool

    public init(pin: Bool = true, passphrase: Bool = true, google: Bool = true) {
        self.pin = pin
        self.passphrase = passphrase
        self.google = google
    }

    /// The public address of a Google-only tenant: no PIN is offered.
    public var googleOnly: Bool { !pin && !passphrase }
}

/// What the phone sign-in path returns (app/devices.py). Held in memory.
public struct AppTokens: Decodable, Equatable {
    public var accessToken: String
    public var refreshToken: String
    public var deviceId: String
    public var expiresIn: Int?
    public var personKey: String?
    public var role: String?
    public var tenant: String?

    enum CodingKeys: String, CodingKey {
        case role, tenant
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case deviceId = "device_id"
        case expiresIn = "expires_in"
        case personKey = "person_key"
    }
}

// MARK: - Conversations

public struct ThreadItem: Decodable, Equatable, Identifiable {
    public var threadId: String
    public var name: String
    public var createdBy: String?
    public var createdAt: String?
    public var lastMessageAt: String?
    public var archived: Bool?
    public var isDirect: Bool?
    public var preview: String?
    public var previewRole: String?
    public var unread: Bool?
    public var unreadCount: Int?
    public var mentions: Int?
    public var previewMine: Bool?
    public var previewAuthorName: String?
    public var members: [String]?
    public var categoryId: String?
    public var id: String { threadId }

    public var direct: Bool { isDirect == true }

    enum CodingKeys: String, CodingKey {
        case name, archived, preview, unread, mentions, members
        case threadId = "thread_id"
        case createdBy = "created_by"
        case createdAt = "created_at"
        case lastMessageAt = "last_message_at"
        case isDirect = "is_direct"
        case previewRole = "preview_role"
        case unreadCount = "unread_count"
        case previewMine = "preview_mine"
        case previewAuthorName = "preview_author_name"
        case categoryId = "category_id"
    }
}

public struct CategoryItem: Decodable, Equatable, Identifiable {
    public var categoryId: String
    public var name: String
    public var position: Int?
    public var id: String { categoryId }

    enum CodingKeys: String, CodingKey {
        case name, position
        case categoryId = "category_id"
    }
}

public struct ThreadsResponse: Decodable, Equatable {
    public var threads: [ThreadItem]
    public var categories: [CategoryItem]?
    /// "mention" or "every": when Jarvis answers in a channel.
    public var jarvisReplies: String?
    public var bodiesVisible: Bool?

    enum CodingKeys: String, CodingKey {
        case threads, categories
        case jarvisReplies = "jarvis_replies"
        case bodiesVisible = "bodies_visible"
    }
}

public struct ThreadMember: Decodable, Equatable, Identifiable {
    public var personKey: String
    public var addedBy: String?
    public var addedAt: String?
    public var id: String { personKey }

    enum CodingKeys: String, CodingKey {
        case personKey = "person_key"
        case addedBy = "added_by"
        case addedAt = "added_at"
    }
}

public struct ChannelSettings: Decodable, Equatable {
    public var jarvisReplies: String
    public var updatedBy: String?
    public var updatedAt: String?
    public var canChange: Bool?

    enum CodingKeys: String, CodingKey {
        case jarvisReplies = "jarvis_replies"
        case updatedBy = "updated_by"
        case updatedAt = "updated_at"
        case canChange = "can_change"
    }
}

// MARK: - Chat

public struct AttachmentItem: Decodable, Equatable, Identifiable {
    public var id: String
    public var threadId: String?
    public var filename: String
    public var mime: String?
    public var size: Int?
    public var sizeHuman: String?
    public var isImage: Bool?
    /// False for documents: stored, but the model never reads them.
    public var readable: Bool?
    public var url: String?

    enum CodingKeys: String, CodingKey {
        case id, filename, mime, size, readable, url
        case threadId = "thread_id"
        case sizeHuman = "size_human"
        case isImage = "is_image"
    }
}

public struct SavedChatAction: Decodable, Equatable {
    public var action: String
    public var label: String?
}

public struct ChatMessage: Decodable, Equatable, Identifiable {
    /// The stored row's id. Nil until the server has saved it.
    public var serverId: Int?
    /// This screen's own handle for a message it is still sending.
    public var localId: String?
    public var role: String
    public var content: String
    public var author: String?
    public var authorName: String?
    public var ts: Double?
    public var partial: Bool?
    public var attachments: [AttachmentItem]?
    public var actions: [SavedChatAction]?

    public var id: String {
        if let serverId { return "s\(serverId)" }
        return localId ?? "m-\(role)-\(ts ?? 0)"
    }

    public var isAssistant: Bool { role == "assistant" }

    public init(localId: String, role: String, content: String, author: String? = nil,
                ts: Double? = nil, attachments: [AttachmentItem]? = nil) {
        self.localId = localId
        self.role = role
        self.content = content
        self.author = author
        self.ts = ts
        self.attachments = attachments
    }

    enum CodingKeys: String, CodingKey {
        case role, content, author, ts, partial, attachments, actions
        case serverId = "id"
        case authorName = "author_name"
    }
}

public struct ChatHistory: Decodable, Equatable {
    public var threadId: String?
    public var messages: [ChatMessage]
    public var lastId: Int?
    /// Channels: whom Jarvis is answering right now, by name.
    public var jarvisAnswering: [String]?

    enum CodingKeys: String, CodingKey {
        case messages
        case threadId = "thread_id"
        case lastId = "last_id"
        case jarvisAnswering = "jarvis_answering"
    }
}

/// One NDJSON line of POST /api/chat.
public enum ChatEvent: Equatable {
    case delta(String)
    /// A step the agent is running. `label` arrives only with `running`.
    case tool(running: Bool, tool: String?, label: String?, callId: String?)
    case done
    case error(message: String, partial: Bool)
    /// Something was deliberately withheld from the model.
    case notice(String)
    /// The turn checked itself against the store.
    case verified(ok: Bool, message: String)
    /// One of the app's own controls, asked for by the agent.
    case action(String)
    /// Channels: the person's message is saved, and whether Jarvis answers it.
    case posted(message: ChatMessage?, jarvis: Bool)
    /// Channels: the rows this turn wrote.
    case saved([Int])
    case other(String)

    static func parse(_ raw: String) -> ChatEvent? {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let obj = try? JSONValue.parse(Data(trimmed.utf8)),
              let type = obj["type"]?.stringValue else { return nil }
        switch type {
        case "delta":
            return .delta(obj["text"]?.stringValue ?? "")
        case "tool":
            return .tool(
                running: obj["status"]?.stringValue == "running",
                tool: obj["tool"]?.stringValue,
                label: obj["label"]?.stringValue,
                callId: obj["toolCallId"]?.stringValue)
        case "done":
            return .done
        case "error":
            return .error(
                message: obj["message"]?.stringValue ?? "The reply stopped.",
                partial: obj["partial"]?.boolValue ?? false)
        case "notice":
            return .notice(obj["message"]?.stringValue ?? "")
        case "verified":
            return .verified(ok: obj["ok"]?.boolValue ?? false, message: obj["message"]?.stringValue ?? "")
        case "action":
            return .action(obj["action"]?.stringValue ?? "")
        case "posted":
            return .posted(message: obj["message"].flatMap(ChatMessage.init(json:)), jarvis: obj["jarvis"]?.boolValue ?? false)
        case "saved":
            return .saved((obj["ids"]?.arrayValue ?? []).compactMap { $0.numberValue.map { Int($0) } })
        default:
            return .other(type)
        }
    }
}

extension ChatMessage {
    /// The saved row a `posted` line carries: who wrote it and its id.
    init?(json: JSONValue) {
        guard let object = json.objectValue else { return nil }
        self.init(
            localId: "",
            role: object["role"]?.stringValue ?? "user",
            content: object["content"]?.stringValue ?? "",
            author: object["author"]?.stringValue,
            ts: object["ts"]?.numberValue)
        localId = nil
        serverId = object["id"]?.numberValue.map { Int($0) }
        authorName = object["author_name"]?.stringValue
    }
}

public struct ChatLine: Equatable {
    public enum Kind: Equatable { case delta, done, error, other }
    public var kind: Kind
    public var text: String
}

// MARK: - Activity

public struct FeedEvent: Decodable, Equatable, Identifiable {
    public var ts: String
    public var skill: String
    public var kind: String
    public var message: String?
    public var headline: String?
    public var detail: String?
    public var person: String?
    public var subject: String?
    public var attachments: [JSONValue]?
    public var id: String { "\(ts)-\(skill)-\(kind)" }
}

public struct FeedSummary: Decodable, Equatable {
    public var date: String?
    public var timezone: String?
    public var total: Int?
    /// Keyed by the raw kind, for the whole day.
    public var counts: [String: Int]
}

// MARK: - Approvals

public struct ConfigChange: Equatable, Identifiable {
    public var key: String
    public var label: String?
    public var current: JSONValue
    public var proposed: JSONValue
    public var id: String { key }
}

public struct ApprovalItem: Equatable, Identifiable {
    public var id: String
    public var summary: String
    public var status: String
    public var skill: String?
    public var kind: String?
    public var ts: String?
    public var canApprove: Bool?
    public var canReject: Bool?
    public var forPerson: String?
    public var decidedBy: String?
    public var decidedAt: String?
    /// What will happen, in readable fields, in the server's order.
    public var display: JSONObject?
    public var payload: JSONObject?

    public var isPending: Bool { status == "pending" }

    /// A config change is drawn as a before/after list, not as fields.
    public var changes: [ConfigChange]? {
        guard display == nil, kind == "config-change", let rows = payload?["changes"]?.arrayValue else { return nil }
        return rows.compactMap { row in
            guard let object = row.objectValue, let key = object["key"]?.stringValue else { return nil }
            return ConfigChange(
                key: key,
                label: object["label"]?.stringValue,
                current: object["current"] ?? .null,
                proposed: object["proposed"] ?? .null)
        }
    }

    /// The field list under the summary: `display`, else the raw payload,
    /// without the empty ones.
    public var fields: [(key: String, value: JSONValue)] {
        if changes != nil { return [] }
        let source = display ?? payload ?? JSONObject()
        return source.pairs.filter { pair in
            if pair.value.isNull { return false }
            if case .string(let s) = pair.value, s.isEmpty { return false }
            return true
        }
    }

    public var isBrowserTask: Bool { payload?["type"]?.stringValue == "browser_task" }

    init?(json: JSONValue) {
        guard let object = json.objectValue,
              let id = object["id"]?.stringValue else { return nil }
        self.id = id
        summary = object["summary"]?.stringValue ?? ""
        status = object["status"]?.stringValue ?? "pending"
        skill = object["skill"]?.stringValue
        kind = object["kind"]?.stringValue
        ts = object["ts"]?.stringValue
        canApprove = object["can_approve"]?.boolValue
        canReject = object["can_reject"]?.boolValue
        forPerson = object["for_person"]?.stringValue
        decidedBy = object["decided_by"]?.stringValue
        decidedAt = object["decided_at"]?.stringValue
        display = object["display"]?.objectValue
        payload = object["payload"]?.objectValue
    }

    public static func == (a: ApprovalItem, b: ApprovalItem) -> Bool {
        a.id == b.id && a.summary == b.summary && a.status == b.status && a.skill == b.skill
            && a.kind == b.kind && a.ts == b.ts && a.canApprove == b.canApprove && a.canReject == b.canReject
            && a.forPerson == b.forPerson && a.decidedBy == b.decidedBy && a.decidedAt == b.decidedAt
            && a.display == b.display && a.payload == b.payload
    }
}

// MARK: - Inbox

public struct InboxMessage: Decodable, Equatable, Identifiable {
    public var id: String
    public var from: String?
    public var subject: String?
    public var date: String?
    public var unread: Bool?
    public var snippet: String?
    /// What Jarvis did with it, once the watcher has handled it.
    public var handling: String?
}

public struct InboxResponse: Decodable, Equatable {
    /// connected, disconnected, needs_reauth or unavailable.
    public var status: String
    public var mailbox: String?
    public var unread: Int?
    public var messages: [InboxMessage]
    public var notice: String?

    public var needsConnect: Bool { status == "disconnected" || status == "needs_reauth" }
    public var waiting: Int { status == "connected" ? (unread ?? 0) : 0 }
}

// MARK: - Tasks

public struct TaskItem: Decodable, Equatable, Identifiable {
    public var taskId: String
    public var title: String
    public var detail: String?
    public var status: String?
    public var assignee: String?
    public var assigneeName: String?
    public var createdBy: String?
    public var priority: String?
    public var deadline: String?
    public var stale: Bool?
    public var isOverdue: Bool?
    public var daysOpen: Int?
    public var dueLabel: String?
    public var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case title, detail, status, assignee, priority, deadline, stale
        case taskId = "task_id"
        case assigneeName = "assignee_name"
        case createdBy = "created_by"
        case isOverdue = "is_overdue"
        case daysOpen = "days_open"
        case dueLabel = "due_label"
    }
}

public struct TaskCounts: Decodable, Equatable {
    public var open: Int?
    public var inProgress: Int?
    public var done: Int?
    public var stale: Int?
    public var overdue: Int?

    enum CodingKeys: String, CodingKey {
        case open, done, stale, overdue
        case inProgress = "in_progress"
    }
}

public struct TasksResponse: Decodable, Equatable {
    public var tasks: [TaskItem]
    public var counts: TaskCounts?
    /// "store" accepts writes; "legacy" is a sentence, not a list.
    public var source: String?
    public var bodiesVisible: Bool?
    public var notice: String?

    enum CodingKeys: String, CodingKey {
        case tasks, counts, source, notice
        case bodiesVisible = "bodies_visible"
    }
}

// MARK: - Leads

public struct LeadItem: Decodable, Equatable, Identifiable {
    public var leadId: String
    public var name: String
    public var property: String?
    public var price: String?
    public var date: String?
    public var status: String?
    public var pipeline: String?
    public var followUp: String?
    public var nextFollowUp: String?
    public var lastNudge: String?
    public var temperature: String?
    public var temperatureRank: Int?
    public var agent: String?
    public var tab: String?
    public var isNew: Bool?
    public var isHot: Bool?
    public var needsTouch: Bool?
    public var createdBy: String?
    public var externalRef: String?
    public var phone: String?
    public var email: String?
    public var notes: String?
    public var source: String?
    public var id: String { leadId }

    enum CodingKeys: String, CodingKey {
        case name, property, price, date, status, pipeline, temperature, agent, tab, phone, email, notes, source
        case leadId = "lead_id"
        case followUp = "follow_up"
        case nextFollowUp = "next_follow_up"
        case lastNudge = "last_nudge"
        case temperatureRank = "temperature_rank"
        case isNew = "is_new"
        case isHot = "is_hot"
        case needsTouch = "needs_touch"
        case createdBy = "created_by"
        case externalRef = "external_ref"
    }
}

public struct LeadCadence: Decodable, Equatable {
    public var hot: Int?
    public var warm: Int?
    public var other: Int?
}

public struct LeadsResponse: Decodable, Equatable {
    public var leads: [LeadItem]
    public var count: Int?
    /// "sheet" is read-only; only "store" accepts writes.
    public var source: String?
    public var tabs: [String]?
    /// YYYY-MM-DD in the tenant's zone: what "due today" means.
    public var today: String?
    public var cadence: LeadCadence?
}

public struct LeadTouchItem: Decodable, Equatable, Identifiable {
    public var touchId: String?
    public var kind: String
    public var note: String?
    public var reached: Bool?
    public var at: String?
    public var byName: String?
    public var nextTouchAt: String?
    public var id: String { touchId ?? "\(kind)-\(at ?? "")-\(note ?? "")" }

    enum CodingKeys: String, CodingKey {
        case kind, note, reached, at
        case touchId = "touch_id"
        case byName = "by_name"
        case nextTouchAt = "next_touch_at"
    }
}

public struct LeadDetail: Decodable, Equatable {
    public var lead: LeadItem
    public var timeline: [LeadTouchItem]
    public var today: String?
}

/// What the quick-add form sends. Empty fields are left out.
public struct LeadDraft: Equatable {
    public var name = ""
    public var phone = ""
    public var email = ""
    public var property = ""
    public var temperature = ""
    public var notes = ""
    /// Owner or admin only; the API refuses it from a member.
    public var owner = ""

    public init() {}
}

// MARK: - Settings

public struct GoogleConnection: Decodable, Equatable {
    /// connected, needs_reauth or disconnected.
    public var status: String
    public var googleEmail: String?
    public var connectedAt: String?
    public var refreshedAt: String?

    public init(status: String, googleEmail: String? = nil, connectedAt: String? = nil, refreshedAt: String? = nil) {
        self.status = status
        self.googleEmail = googleEmail
        self.connectedAt = connectedAt
        self.refreshedAt = refreshedAt
    }

    enum CodingKeys: String, CodingKey {
        case status
        case googleEmail = "google_email"
        case connectedAt = "connected_at"
        case refreshedAt = "refreshed_at"
    }
}

public struct WebsiteLogin: Decodable, Equatable, Identifiable {
    public var site: String
    public var username: String
    public var updatedAt: String?
    public var lastUsedAt: String?
    public var id: String { site }

    enum CodingKeys: String, CodingKey {
        case site, username
        case updatedAt = "updated_at"
        case lastUsedAt = "last_used_at"
    }
}

public struct MacDevice: Decodable, Equatable, Identifiable {
    public var deviceId: String
    public var name: String
    public var createdAt: String?
    public var lastSyncAt: String?
    public var id: String { deviceId }

    enum CodingKeys: String, CodingKey {
        case name
        case deviceId = "device_id"
        case createdAt = "created_at"
        case lastSyncAt = "last_sync_at"
    }
}

public struct ReplyReminder: Decodable, Equatable {
    public var enabled: Bool
    public var timesLocal: [String]?
    public var to: String?
    public var sendOn: Bool?

    enum CodingKeys: String, CodingKey {
        case enabled, to
        case timesLocal = "times_local"
        case sendOn = "send_on"
    }
}

public struct MacStatus: Decodable, Equatable {
    public var devices: [MacDevice]
    public var messages: Int?
    public var retentionDays: Int?
    public var macApp: Bool?
    public var reminder: ReplyReminder?

    enum CodingKeys: String, CodingKey {
        case devices, messages, reminder
        case retentionDays = "retention_days"
        case macApp = "mac_app"
    }
}

public struct PairingCodeInfo: Decodable, Equatable {
    public var code: String
    public var expiresAt: String?

    enum CodingKeys: String, CodingKey {
        case code
        case expiresAt = "expires_at"
    }
}

public struct TeamPerson: Decodable, Equatable, Identifiable {
    public var personKey: String
    public var name: String
    public var fullName: String?
    public var title: String?
    public var role: String
    public var email: String?
    public var disabled: Bool?
    public var lastLogin: String?
    public var features: [String: Bool]?
    public var id: String { personKey }

    enum CodingKeys: String, CodingKey {
        case name, title, role, email, disabled, features
        case personKey = "person_key"
        case fullName = "full_name"
        case lastLogin = "last_login"
    }
}

public struct TeamResponse: Decodable, Equatable {
    public var people: [TeamPerson]
    public var features: [String]
    public var featureLabels: [String: String]?
    public var canInviteAdmins: Bool?
    public var invitesReady: Bool?

    enum CodingKeys: String, CodingKey {
        case people, features
        case featureLabels = "feature_labels"
        case canInviteAdmins = "can_invite_admins"
        case invitesReady = "invites_ready"
    }
}

public struct InviteResult: Equatable {
    public var person: TeamPerson
    public var emailed: Bool
    public var emailError: String?
}

// MARK: - Errors and account

public enum JarvisError: Error, Equatable {
    case noOrigin
    case consentRequired
    case http(Int, String)
    case cancelled
    case badResponse

    /// The single signed-out signal: the session is gone, show sign-in.
    public var isUnauthorized: Bool {
        if case .http(401, _) = self { return true }
        return false
    }

    public var status: Int? {
        if case .http(let code, _) = self { return code }
        return nil
    }

    /// The server's own sentence, when it sent one.
    public var serverMessage: String? {
        if case .http(_, let message) = self { return message }
        return nil
    }
}

/// What Ask tells the person before a turn is dispatched. Matches
/// app/model_proxy.py: the server posts the turn to Anthropic.
public enum AIDisclosure {
    public static let text = "Messages you send in Ask go to this Jarvis server, which sends them to Anthropic to write the reply. Saying yes here covers only those messages. It does not change reminders, imports, or other jobs the server already runs."
    public static let confirmPhrase = "request-account-deletion"
}

public struct TeamDeletionImpact: Decodable, Equatable {
    public var action: String
    public var slug: String
    public var phrase: String
    public var removes: [String]
    public var notRemoved: [String]
    public var nonce: String?
    public var expiresIn: Int?

    enum CodingKeys: String, CodingKey {
        case action, slug, phrase, removes, nonce
        case notRemoved = "not_removed"
        case expiresIn = "expires_in"
    }
}

public struct TeamDeletionReceipt: Decodable, Equatable {
    public var ok: Bool
    public var action: String
    public var deletedLocal: Bool
    public var signInBlocked: Bool
    public var removed: [String]
    public var stillExists: [String]
    public var detail: String

    enum CodingKeys: String, CodingKey {
        case ok, action, removed, detail
        case deletedLocal = "deleted_local"
        case signInBlocked = "sign_in_blocked"
        case stillExists = "still_exists"
    }
}

public struct DeletionReceipt: Decodable, Equatable {
    public var ok: Bool
    public var deleted: Bool
    public var deactivated: Bool
    public var deletion: String
    public var personKey: String
    public var detail: String

    enum CodingKeys: String, CodingKey {
        case ok, deleted, deactivated, deletion, detail
        case personKey = "person_key"
    }
}
