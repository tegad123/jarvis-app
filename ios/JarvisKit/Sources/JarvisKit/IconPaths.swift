import Foundation

/// The Jarvis Mono icon set, copied from frontend/src/jm/shared/Icon.tsx:
/// a 24-unit grid, 1.75 stroke, round caps and joins, no fills except the
/// stop square. JarvisKitTests checks every path here against that file.
public enum JarvisIcon: String, CaseIterable, Sendable {
    case plus
    case mic
    case send
    case check
    case close
    case chevron
    case more
    case speaker
    case speakerOff = "speaker-off"
    case file
    case compose
    case globe
    case expand
    case shrink
    case copy
    case alert
    case sun
    case moon
    case signout
    case spark
    case chat
    case activity
    case approvals
    case tasks
    case leads
    case settings
    case back
    case members
    case eye
    case eyeOff = "eye-off"
    case inbox
    case refresh
    case stop

    /// The grid every path is drawn in.
    public static let grid = 24.0

    /// The stroke, in grid units. "more" is three dots made of a fat stroke.
    public var strokeWidth: Double {
        self == .more ? 3 : 1.75
    }

    /// Stop is the one filled glyph: a rounded square.
    public var isFilled: Bool { self == .stop }

    public var path: String {
        switch self {
        case .plus: return "M12 5v14M5 12h14"
        case .mic: return "M12 3a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"
        case .send: return "M12 19V5M6 11l6-6 6 6"
        case .check: return "M5 12.5l4.5 4.5L19 7.5"
        case .close: return "M6 6l12 12M18 6L6 18"
        case .chevron: return "M9 6l6 6-6 6"
        case .more: return "M5.5 12h.01M12 12h.01M18.5 12h.01"
        case .speaker: return "M11 5 6.5 9H3v6h3.5L11 19V5ZM15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"
        case .speakerOff: return "M11 5 6.5 9H3v6h3.5L11 19V5ZM16 9.5l5 5M21 9.5l-5 5"
        case .file: return "M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5L14 3ZM13.5 3.2V8h4.8"
        case .compose: return "M12 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6M17.5 3.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4 8.5-8.5Z"
        case .globe: return "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18"
        case .expand: return "M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7"
        case .shrink: return "M20 10h-6V4M14 10l7-7M4 14h6v6M10 14l-7 7"
        case .copy: return "M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1ZM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"
        case .alert: return "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7.5v5.5M12 16.5h.01"
        case .sun: return "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2.5V4M12 20v1.5M2.5 12H4M20 12h1.5M5.3 5.3l1 1M17.7 17.7l1 1M5.3 18.7l1-1M17.7 6.3l1-1"
        case .moon: return "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z"
        case .signout: return "M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h10"
        case .spark: return "M12 3.5c.6 4.6 2.3 6.4 6.5 7.5-4.2 1.1-5.9 2.9-6.5 7.5-.6-4.6-2.3-6.4-6.5-7.5 4.2-1.1 5.9-2.9 6.5-7.5Z"
        case .chat: return "M20 12a8 8 0 0 1-8 8H4l1.6-3.2A8 8 0 1 1 20 12Z"
        case .activity: return "M4 6h16M4 12h16M4 18h10"
        case .approvals: return "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM8 12.5l2.7 2.7L16 9.8"
        case .tasks: return "M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1ZM8.5 12.2l2.3 2.3 4.7-4.9"
        case .leads: return "M12 4a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM5 20a7 7 0 0 1 14 0"
        case .settings: return "M4 6h16M4 12h16M4 18h16M15 4v4M8 10v4M17 16v4"
        case .back: return "M15 5l-7 7 7 7"
        case .members: return "M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 3.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM17 11a3 3 0 1 0 0-6M21 19v-1a3.6 3.6 0 0 0-2.5-3.4"
        case .eye: return "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z"
        case .eyeOff: return "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM4 20 20 4"
        case .inbox: return "M4 13h4l1.5 3h5l1.5-3h4M5.5 5h13L21 13v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5l2.5-8Z"
        case .refresh: return "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"
        case .stop: return "M9 7h6a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z"
        }
    }

    public var steps: [PathStep] { SVGPath.steps(path) }
}
