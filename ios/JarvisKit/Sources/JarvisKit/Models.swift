import Foundation

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

public struct LeadItem: Decodable, Equatable, Identifiable {
    public var leadId: String
    public var name: String
    public var property: String?
    public var price: String?
    public var status: String?
    public var pipeline: String?
    public var followUp: String?
    public var nextFollowUp: String?
    public var temperature: String?
    public var agent: String?
    public var externalRef: String?
    public var phone: String?
    public var email: String?
    public var notes: String?
    public var id: String { leadId }

    enum CodingKeys: String, CodingKey {
        case name, property, price, status, pipeline, temperature, agent, phone, email, notes
        case leadId = "lead_id"
        case followUp = "follow_up"
        case nextFollowUp = "next_follow_up"
        case externalRef = "external_ref"
    }
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

public struct TaskItem: Decodable, Equatable, Identifiable {
    public var taskId: String
    public var title: String
    public var detail: String?
    public var status: String?
    public var assigneeName: String?
    public var dueLabel: String?
    public var id: String { taskId }

    enum CodingKeys: String, CodingKey {
        case title, detail, status
        case taskId = "task_id"
        case assigneeName = "assignee_name"
        case dueLabel = "due_label"
    }
}

public struct ApprovalItem: Decodable, Equatable, Identifiable {
    public var id: String
    public var summary: String
    public var status: String
    public var skill: String?
    public var canApprove: Bool?
    public var canReject: Bool?

    enum CodingKeys: String, CodingKey {
        case id, summary, status, skill
        case canApprove = "can_approve"
        case canReject = "can_reject"
    }
}

public struct ChatLine: Equatable {
    public enum Kind: Equatable { case delta, done, error, other }
    public var kind: Kind
    public var text: String
}

public enum JarvisError: Error, Equatable {
    case noOrigin
    case consentRequired
    case http(Int, String)
    case cancelled
    case badResponse
}

/// What Ask tells the person before a turn is dispatched. Matches
/// app/model_proxy.py: the server posts the turn to Anthropic.
public enum AIDisclosure {
    public static let text = "Messages you send in Ask go to this Jarvis server, which sends them to Anthropic to write the reply. Saying yes here covers only those messages. It does not change reminders, imports, or other jobs the server already runs."
    /// site/privacy.md, published for the Yamin service. Confirmed responding
    /// when this client was written. A tenant with a different policy replaces
    /// this before release.
    public static let policyURL = URL(string: "https://yamin.io/privacy")!
}
