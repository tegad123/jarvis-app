import Foundation

// The words the screens print. Each function here is a port of one in the web
// app's frontend/src/lib, named beside it, so the phone and the browser say
// the same thing about the same row.

// MARK: - Time (lib/time.ts)

public enum JarvisTime {
    /// Reads what the server writes: Python's isoformat() on a UTC-aware
    /// datetime ('2026-08-16T15:35:35.051992+00:00'), a trailing Z, or a bare
    /// date. A time with no zone is read in the phone's own zone, a bare date
    /// as UTC midnight, as the browser reads them.
    public static func parse(_ raw: String?, timeZone: TimeZone = .current) -> Date? {
        guard let raw else { return nil }
        let text = Array(raw.trimmingCharacters(in: .whitespaces).utf8)
        var at = 0

        func digits(_ count: Int) -> Int? {
            guard at + count <= text.count else { return nil }
            var value = 0
            for byte in text[at..<(at + count)] {
                guard byte >= 0x30, byte <= 0x39 else { return nil }
                value = value * 10 + Int(byte - 0x30)
            }
            at += count
            return value
        }
        func take(_ character: Character) -> Bool {
            guard at < text.count, text[at] == character.asciiValue else { return false }
            at += 1
            return true
        }

        guard let year = digits(4), take("-"), let month = digits(2), take("-"), let day = digits(2) else { return nil }
        var parts = DateComponents()
        parts.year = year
        parts.month = month
        parts.day = day
        var zone = TimeZone(secondsFromGMT: 0)!
        var fraction = 0.0

        if at < text.count {
            guard take("T") || take(" ") || take("t") else { return nil }
            guard let hour = digits(2), take(":"), let minute = digits(2) else { return nil }
            parts.hour = hour
            parts.minute = minute
            if take(":") {
                guard let second = digits(2) else { return nil }
                parts.second = second
                if take(".") || take(",") {
                    var scale = 0.1
                    while at < text.count, text[at] >= 0x30, text[at] <= 0x39 {
                        fraction += Double(text[at] - 0x30) * scale
                        scale /= 10
                        at += 1
                    }
                }
            }
            if at == text.count {
                zone = timeZone
            } else if take("Z") || take("z") {
                guard at == text.count else { return nil }
            } else {
                let sign: Int
                if take("+") { sign = 1 } else if take("-") { sign = -1 } else { return nil }
                guard let offsetHours = digits(2) else { return nil }
                _ = take(":")
                let offsetMinutes = at < text.count ? digits(2) : 0
                guard let offsetMinutes, at == text.count,
                      let fixed = TimeZone(secondsFromGMT: sign * (offsetHours * 3600 + offsetMinutes * 60)) else { return nil }
                zone = fixed
            }
        }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        guard (1...12).contains(month), (1...31).contains(day), let date = calendar.date(from: parts) else { return nil }
        return date.addingTimeInterval(fraction)
    }

    /// "just now" / "4m" / "3h" / "2d" / then "Aug 16".
    public static func relative(_ raw: String?, now: Date = Date()) -> String {
        guard let date = parse(raw) else { return "" }
        return relative(date, now: now)
    }

    public static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = jsRound(now.timeIntervalSince(date))
        // A future timestamp is clock skew; read it as now.
        if seconds < 45 { return "just now" }
        let minutes = jsRound(Double(seconds) / 60)
        if minutes < 60 { return "\(minutes)m" }
        let hours = jsRound(Double(minutes) / 60)
        if hours < 24 { return "\(hours)h" }
        let days = jsRound(Double(hours) / 24)
        if days < 7 { return "\(days)d" }
        return JarvisFormat.monthDay(date)
    }

    /// "Sat, Aug 16, 3:35 PM", for an accessibility label.
    public static func absolute(_ raw: String?) -> String {
        guard let date = parse(raw) else { return raw ?? "" }
        return JarvisFormat.string(date, template: "EEEMMMdjmm")
    }

    /// "Aug 16, 3:35 PM": the lead timeline's stamp (LeadSheet.tsx `when`).
    public static func stamp(_ raw: String?) -> String {
        guard let date = parse(raw) else { return raw ?? "" }
        return JarvisFormat.string(date, template: "MMMdjmm")
    }

    /// "3:35 PM": until when a pairing code works.
    public static func clock(_ raw: String?) -> String {
        guard let date = parse(raw) else { return raw ?? "" }
        return JarvisFormat.string(date, template: "jmm")
    }

    /// How long a task has been sitting, in words.
    public static func durationOpen(_ days: Int?) -> String {
        guard let days, days > 0 else { return "open today" }
        if days == 1 { return "open 1 day" }
        if days < 14 { return "open \(days) days" }
        return "open \(jsRound(Double(days) / 7)) weeks"
    }

    /// True when the row's last message is older than two days.
    public static func isQuiet(_ raw: String?, now: Date = Date()) -> Bool {
        guard let date = parse(raw) else { return false }
        return now.timeIntervalSince(date) > 48 * 3600
    }

    /// Math.round: halves go up.
    static func jsRound(_ value: Double) -> Int {
        Int((value + 0.5).rounded(.down))
    }
}

/// Locale-following date text. Tests pin the locale and zone.
public enum JarvisFormat {
    nonisolated(unsafe) public static var locale: Locale = .current
    nonisolated(unsafe) public static var timeZone: TimeZone = .current

    public static func string(_ date: Date, template: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = locale
        formatter.timeZone = timeZone
        formatter.setLocalizedDateFormatFromTemplate(template)
        return formatter.string(from: date)
    }

    public static func monthDay(_ date: Date) -> String {
        string(date, template: "MMMd")
    }

    /// A YYYY-MM-DD calendar day as "Aug 16", read as a local date so it is
    /// never a day off west of Greenwich. Anything else comes back unchanged.
    public static func shortDay(_ day: String) -> String {
        guard let date = localDate(day) else { return day }
        return monthDay(date)
    }

    public static func localDate(_ day: String) -> Date? {
        let pieces = day.trimmingCharacters(in: .whitespaces).split(separator: "-").map { Int($0) }
        guard pieces.count == 3, let year = pieces[0], let month = pieces[1], let dayOfMonth = pieces[2],
              year > 0, month > 0, dayOfMonth > 0 else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar.date(from: DateComponents(year: year, month: month, day: dayOfMonth, hour: 12))
    }

    /// The YYYY-MM-DD of a date the person picked, on their own calendar.
    public static func dayString(_ date: Date) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}

// MARK: - Feed kinds (lib/kinds.ts)

public enum KindSemantic: String, Equatable {
    case info, success, warning, alert, neutral

    /// "A human still has to do something about this."
    public var needsYou: Bool { self == .warning || self == .alert }
}

public struct KindStyle: Equatable {
    public var semantic: KindSemantic
    public var word: String

    private static let exact: [String: KindStyle] = [
        "email.classified": KindStyle(semantic: .info, word: "Email filed"),
        "task.created": KindStyle(semantic: .success, word: "Task created"),
        "task.completed": KindStyle(semantic: .neutral, word: "Done"),
        "task.assigned": KindStyle(semantic: .info, word: "Assigned"),
        "task.stale": KindStyle(semantic: .alert, word: "Going quiet"),
        "lead.quiet": KindStyle(semantic: .alert, word: "Going quiet"),
        "meeting.filed": KindStyle(semantic: .info, word: "Meeting filed"),
        "digest.sent": KindStyle(semantic: .info, word: "Digest sent"),
        "operator.inject": KindStyle(semantic: .info, word: "Test input"),
        "info": KindStyle(semantic: .info, word: "Update"),
        "warning": KindStyle(semantic: .warning, word: "Warning"),
        "alert": KindStyle(semantic: .alert, word: "Alert"),
        "error": KindStyle(semantic: .alert, word: "Alert"),
    ]

    /// An unknown kind is shown with a readable name, never hidden.
    public static func of(_ rawKind: String?) -> KindStyle {
        let kind = (rawKind ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if kind.isEmpty { return KindStyle(semantic: .info, word: "Update") }
        let lower = kind.lowercased()
        if let known = exact[lower] { return known }
        let word = Wording.prettify(kind, empty: "Update")
        if lower.contains("approval") || lower.contains("needs_") { return KindStyle(semantic: .warning, word: word) }
        if lower.hasSuffix(".failed") || lower.contains("alert") || lower.contains("error") {
            return KindStyle(semantic: .alert, word: word)
        }
        if lower.contains("warning") { return KindStyle(semantic: .warning, word: word) }
        if lower.hasSuffix(".completed") || lower.hasSuffix(".done") { return KindStyle(semantic: .neutral, word: word) }
        if lower.hasSuffix(".created") { return KindStyle(semantic: .success, word: word) }
        return KindStyle(semantic: .info, word: word)
    }
}

// MARK: - Today (lib/summary.ts)

public struct DaySummary: Equatable {
    public var todayCount: Int
    public var needsYouCount: Int
    /// "3 emails filed", most frequent first.
    public var items: [String]

    public init(counts: [String: Int]) {
        var byWord: [String: Int] = [:]
        var needs = 0
        var total = 0
        for (kind, n) in counts {
            let style = KindStyle.of(kind)
            byWord[style.word, default: 0] += n
            if style.semantic.needsYou { needs += n }
            total += n
        }
        todayCount = total
        needsYouCount = needs
        items = byWord
            .sorted { a, b in a.value != b.value ? a.value > b.value : a.key.localizedCompare(b.key) == .orderedAscending }
            .map { "\($0.value) \(DaySummary.pluralise($0.key, $0.value))" }
    }

    static func pluralise(_ word: String, _ n: Int) -> String {
        let lower = word.lowercased()
        if n == 1 || lower == "done" { return lower }
        var words = lower.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        guard let noun = words.first else { return lower }
        let hissing = ["s", "x", "z", "ch", "sh"].contains { noun.hasSuffix($0) }
        words[0] = noun + (hissing ? "es" : "s")
        return words.joined(separator: " ")
    }
}

// MARK: - Steps (lib/steps.ts)

public enum StepWording {
    public static let thinking = "Thinking…"

    private static let verbs: [String: String] = [
        "terminal": "Running a command",
        "web_search": "Searching the web",
        "web_fetch": "Reading a web page",
        "read_file": "Looking through files",
        "write_file": "Writing a file",
        "edit_file": "Editing a file",
        "search_files": "Looking through files",
        "list_files": "Looking through files",
        "skill_view": "Using a skill",
        "skills_list": "Using a skill",
        "skill_manage": "Using a skill",
        "delegate_task": "Working on a subtask",
        "memory_search": "Checking what it remembers",
        "memory_write": "Making a note",
    ]

    /// A readable name for a running tool. Never blank.
    public static func name(_ tool: String?) -> String {
        let key = (tool ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if key.isEmpty { return thinking }
        return verbs[key] ?? Wording.prettify(key, empty: thinking)
    }

    /// Labels can be whole shell commands; keep the step to one line.
    public static func truncate(_ label: String?, max: Int = 48) -> String {
        guard let label, !label.isEmpty else { return "" }
        let clean = label.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        return clean.count <= max ? clean : String(clean.prefix(max - 1)) + "…"
    }
}

// MARK: - Roles (lib/roles.ts)

public enum Roles {
    public static func canSeeSettings(_ role: String?) -> Bool {
        role == "owner" || role == "admin" || role == "member"
    }

    public static func canManageTeam(_ role: String?) -> Bool {
        role == "owner" || role == "admin"
    }

    public static func label(_ role: String?) -> String {
        switch role {
        case "owner": return "Owner"
        case "admin": return "Admin"
        case "member": return "Member"
        case "operator": return "Operator access"
        default: return ""
        }
    }
}

// MARK: - Lead dates (lib/leadDates.ts)

public struct DueLabel: Equatable {
    public var text: String
    /// Today or overdue: drawn in the accent colour.
    public var now: Bool
}

public enum LeadDates {
    public static func isDue(next: String?, today: String?, status: String?) -> Bool {
        guard let next, !next.isEmpty, let today, !today.isEmpty, status != "Not Interested" else { return false }
        return String(next.prefix(10)) <= today
    }

    public static func dueLabel(next: String?, today: String?, status: String?) -> DueLabel? {
        guard let next, !next.isEmpty, status != "Not Interested" else { return nil }
        let day = String(next.prefix(10))
        guard let today, !today.isEmpty else {
            return DueLabel(text: "Follow up \(JarvisFormat.shortDay(day))", now: false)
        }
        if day < today { return DueLabel(text: "Overdue · was due \(JarvisFormat.shortDay(day))", now: true) }
        if day == today { return DueLabel(text: "Follow up today", now: true) }
        return DueLabel(text: "Next follow-up · \(JarvisFormat.shortDay(day))", now: false)
    }

    /// The sentence under the quick-add form (Leads.tsx `firstFollowUp`).
    public static func firstFollowUp(_ cadence: LeadCadence?) -> String {
        func day(_ n: Int) -> String {
            n == 1 ? "tomorrow" : n == 2 ? "in two days" : "in \(n) days"
        }
        return "Jarvis sets the first follow-up: \(day(cadence?.hot ?? 1)) if hot, \(day(cadence?.warm ?? 2)) if warm, otherwise \(day(cadence?.other ?? 3))."
    }
}

// MARK: - Lead and task rows (jm/LeadRow, jm/TaskRow, LeadSheet.tsx)

public enum LeadWording {
    public static let temperatures = ["Hot", "Warm", "Cold"]
    public static let temperatureCycle = ["", "Cold", "Warm", "Hot"]
    public static let pipelineCycle = ["New", "Contacted", "Touring", "Offer Made", "Closed", "Lost"]
    public static let statusCycle = ["No Response", "Responded", "Scheduled", "Not Interested", "Closed", "Dead?", "Dead", "Won", "Lost"]

    public static let touchKinds: [(id: String, label: String)] = [
        ("call", "Call"), ("text", "Text"), ("email", "Email"), ("met", "Met"), ("showing", "Showing"), ("note", "Note"),
    ]

    private static let kindWords: [String: String] = [
        "call": "Called",
        "text": "Texted",
        "email": "Emailed",
        "met": "Met",
        "showing": "Showed a property",
        "note": "Note",
        "created": "Added",
        "imported": "Imported from the spreadsheet",
    ]

    /// "Called · no answer"
    public static func touchTitle(_ touch: LeadTouchItem) -> String {
        let word = kindWords[touch.kind] ?? touch.kind
        return touch.reached == false ? "\(word) · no answer" : word
    }

    /// The value after `current` in a tap-to-cycle list; the first when the
    /// current value is not in it.
    public static func next(after current: String?, in cycle: [String]) -> String {
        guard let index = cycle.firstIndex(of: current ?? "") else { return cycle.first ?? "" }
        return cycle[(index + 1) % cycle.count]
    }

    /// Bars lit for a temperature: Hot 3, Warm 2, Cold 1.
    public static func bars(_ temperature: String?) -> Int {
        switch temperature {
        case "Hot": return 3
        case "Warm": return 2
        case "Cold": return 1
        default: return 0
        }
    }

    /// The last thing that happened, for the row's meta line.
    public static func activity(_ lead: LeadItem) -> String {
        if let followUp = lead.followUp, !followUp.isEmpty, followUp != lead.pipeline { return followUp }
        if let nudge = lead.lastNudge, !nudge.isEmpty { return "Nudged \(nudge)" }
        return "No activity yet"
    }
}

public enum TaskWording {
    public static let statusCycle = ["open", "in_progress", "done"]

    public static func statusLabel(_ status: String?) -> String {
        switch status {
        case "in_progress": return "In progress"
        case "done": return "Done"
        default: return "Open"
        }
    }

    public static func priorityLabel(_ priority: String?) -> String {
        switch priority {
        case "high": return "High"
        case "low": return "Low"
        default: return "Medium"
        }
    }
}

// MARK: - Settings (screens/Settings.tsx)

public enum StatusTone: Equatable {
    case neutral, done, action, busy
}

public struct StatusCopy: Equatable {
    public var tone: StatusTone
    public var label: String
}

public enum SettingsWording {
    /// The Mac syncs every two minutes; well past that, it has stopped.
    static let macStale: TimeInterval = 10 * 60

    public static func macSyncing(_ lastSync: String?, now: Date = Date()) -> Bool {
        guard let date = JarvisTime.parse(lastSync) else { return false }
        return now.timeIntervalSince(date) < macStale
    }

    public static func macStatus(_ devices: [MacDevice], now: Date = Date()) -> StatusCopy {
        if devices.isEmpty { return StatusCopy(tone: .neutral, label: "Not connected") }
        if devices.contains(where: { macSyncing($0.lastSyncAt, now: now) }) { return StatusCopy(tone: .done, label: "Syncing") }
        if devices.allSatisfy({ ($0.lastSyncAt ?? "").isEmpty }) { return StatusCopy(tone: .action, label: "Waiting for the Mac") }
        return StatusCopy(tone: .action, label: "Not syncing")
    }

    public static func macDetail(_ lastSync: String?, now: Date = Date()) -> String {
        guard let lastSync, !lastSync.isEmpty else {
            return "paired, but the Mac hasn’t synced yet: open Jarvis on it and turn on Full Disk Access"
        }
        if macSyncing(lastSync, now: now) { return "synced \(JarvisTime.relative(lastSync, now: now))" }
        return "last synced \(JarvisTime.relative(lastSync, now: now)): open Jarvis on the Mac to see what’s wrong"
    }

    /// "09:00" → "9 AM", "16:30" → "4:30 PM"
    public static func clockLabel(_ hhmm: String) -> String {
        let parts = hhmm.split(separator: ":").map { Int($0) ?? 0 }
        let hour = parts.first ?? 0
        let minute = parts.count > 1 ? parts[1] : 0
        let shown = hour % 12 == 0 ? 12 : hour % 12
        let minutes = minute == 0 ? "" : String(format: ":%02d", minute)
        return "\(shown)\(minutes) \(hour < 12 ? "AM" : "PM")"
    }

    /// "a", "a and b", "a, b and c"
    public static func listLabel(_ items: [String]) -> String {
        guard items.count > 1, let last = items.last else { return items.joined() }
        return "\(items.dropLast().joined(separator: ", ")) and \(last)"
    }

    public enum GooglePhase: Equatable {
        case idle, disconnecting, disconnected
    }

    public static func googleStatus(_ connection: GoogleConnection?, phase: GooglePhase, now: Date = Date()) -> (status: StatusCopy, time: String?) {
        if phase == .disconnecting { return (StatusCopy(tone: .busy, label: "Disconnecting…"), nil) }
        if phase == .disconnected || connection?.status == "disconnected" {
            return (StatusCopy(tone: .neutral, label: phase == .disconnected ? "Disconnected" : "Never connected"), nil)
        }
        if connection?.status == "needs_reauth" { return (StatusCopy(tone: .action, label: "Needs reauth"), nil) }
        if connection?.status == "connected" {
            let done = StatusCopy(tone: .done, label: "Connected")
            if let refreshed = connection?.refreshedAt, !refreshed.isEmpty {
                return (done, "Last refreshed \(JarvisTime.relative(refreshed, now: now))")
            }
            if let connected = connection?.connectedAt, !connected.isEmpty {
                return (done, "Connected \(JarvisTime.relative(connected, now: now))")
            }
            return (done, nil)
        }
        return (StatusCopy(tone: .neutral, label: "Never connected"), nil)
    }
}

// MARK: - Activity rows (screens/Feed.tsx)

public enum FeedWording {
    private static func attachmentLabel(_ attachment: JSONValue) -> String? {
        for key in ["title", "subject", "task_id", "message_id", "name"] {
            if let value = attachment[key]?.stringValue, !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                return value
            }
        }
        return attachment["type"]?.stringValue
    }

    /// Attachment labels worth a chip: not ones the headline already says.
    public static func chips(_ event: FeedEvent) -> [String] {
        let headline = event.headline ?? ""
        let body = (headline.isEmpty ? (event.message ?? "") : headline).lowercased()
        let subject = event.subject?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() ?? ""
        return (event.attachments ?? []).compactMap(attachmentLabel).filter { label in
            let text = label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
            if !headline.isEmpty, !subject.isEmpty, text == subject { return false }
            return !body.contains(String(text.prefix(40)))
        }
    }

    public static func headline(_ event: FeedEvent) -> String {
        if let headline = event.headline, !headline.isEmpty { return headline }
        return event.message ?? ""
    }
}

// MARK: - Approvals (jm/ApprovalCard)

public enum ApprovalWording {
    /// "reply_to" → "Reply to"
    public static func fieldLabel(_ key: String) -> String {
        let spaced = key.replacingOccurrences(of: "_", with: " ")
        return spaced.prefix(1).uppercased() + spaced.dropFirst()
    }

    public static func approveLabel(_ approval: ApprovalItem) -> String {
        approval.isBrowserTask ? "Approve for 30 min" : "Approve"
    }

    /// Why a button is missing, when one is.
    public static func note(_ approval: ApprovalItem) -> String? {
        let canApprove = approval.canApprove != false
        let canReject = approval.canReject != false
        if canApprove { return nil }
        if canReject {
            if approval.isBrowserTask, let person = approval.forPerson, !person.isEmpty {
                return "For \(person)’s browser. Only they can approve it; you can reject it."
            }
            return "Someone else approves this one; you can reject it."
        }
        return "You can’t decide this one."
    }

    public static func decided(_ approval: ApprovalItem, now: Date = Date()) -> String {
        var text = approval.status == "approved" ? "Approved" : "Rejected"
        if let by = approval.decidedBy, !by.isEmpty { text += " by \(by)" }
        let when = JarvisTime.relative(approval.decidedAt, now: now)
        if !when.isEmpty { text += " · \(when)" }
        return text
    }
}

// MARK: - Small things (jm/shared/primitives.tsx, SignIn.tsx, Composer.tsx)

public enum Wording {
    /// 'search_files' → 'Search Files'
    public static func prettify(_ raw: String, empty: String) -> String {
        let words = raw.split(whereSeparator: { $0 == "." || $0 == "_" || $0 == "-" || $0.isWhitespace })
        if words.isEmpty { return empty }
        return words.map { $0.prefix(1).uppercased() + $0.dropFirst().lowercased() }.joined(separator: " ")
    }

    /// First letters of the first two words. "J" for nobody.
    public static func initials(_ name: String?) -> String {
        let words = (name ?? "").split(whereSeparator: { $0.isWhitespace })
        let letters = words.prefix(2).compactMap(\.first).map { String($0).uppercased() }.joined()
        return letters.isEmpty ? "J" : letters
    }

    /// "Broker (owner)" → "Broker": the sign-in row's second line.
    public static func titleOnly(_ title: String?) -> String {
        guard let title else { return "" }
        if let cut = title.range(of: " (") { return String(title[..<cut.lowerBound]) }
        return title
    }

    public static func firstName(_ name: String) -> String {
        name.split(whereSeparator: { $0.isWhitespace }).first.map(String.init) ?? name
    }

    public static func humanSize(_ bytes: Int) -> String {
        if bytes >= 1024 * 1024 { return String(format: "%.1f MB", Double(bytes) / (1024 * 1024)) }
        return "\(max(1, JarvisTime.jsRound(Double(bytes) / 1024))) KB"
    }
}

public enum PinFormat {
    /// A whole PIN is twelve characters in three groups.
    public static let completeLength = 14

    /// Lowercase letters and digits in groups of four: "abcd-efgh-ijkl".
    public static func format(_ raw: String) -> String {
        let kept = raw.lowercased().unicodeScalars.filter { scalar in
            (scalar.value >= 0x30 && scalar.value <= 0x39) || (scalar.value >= 0x61 && scalar.value <= 0x7A)
        }
        let clean = Array(String(String.UnicodeScalarView(kept)).prefix(12))
        var groups: [String] = []
        var index = 0
        while index < clean.count {
            groups.append(String(clean[index..<min(index + 4, clean.count)]))
            index += 4
        }
        return groups.joined(separator: "-")
    }

    public static func isComplete(_ formatted: String) -> Bool {
        formatted.count == completeLength
    }
}

/// A run of message text: plain words, or an @mention.
public struct MentionRun: Equatable {
    public var text: String
    public var isMention: Bool
    /// The mention names the person reading.
    public var isSelf: Bool
}

public enum Mentions {
    /// Splits text on @Name for the names given. A name only counts when it
    /// is not part of an address or a longer word, as on the web.
    public static func runs(_ text: String, names: [String], selfNames: [String] = []) -> [MentionRun] {
        let wanted = Array(Set(names.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }))
            .sorted { $0.count > $1.count }
        guard !wanted.isEmpty, text.contains("@") else { return [MentionRun(text: text, isMention: false, isSelf: false)] }
        let alternatives = wanted.map(NSRegularExpression.escapedPattern(for:)).joined(separator: "|")
        guard let pattern = try? NSRegularExpression(
            pattern: "(?<![\\w@.])@(\(alternatives))(?![\\w-])", options: [.caseInsensitive]) else {
            return [MentionRun(text: text, isMention: false, isSelf: false)]
        }
        let own = Set(selfNames.map { $0.lowercased() })
        let whole = text as NSString
        var runs: [MentionRun] = []
        var at = 0
        for match in pattern.matches(in: text, range: NSRange(location: 0, length: whole.length)) {
            if match.range.location > at {
                runs.append(MentionRun(
                    text: whole.substring(with: NSRange(location: at, length: match.range.location - at)),
                    isMention: false, isSelf: false))
            }
            let name = whole.substring(with: match.range(at: 1)).lowercased()
            runs.append(MentionRun(text: whole.substring(with: match.range), isMention: true, isSelf: own.contains(name)))
            at = match.range.location + match.range.length
        }
        if at < whole.length {
            runs.append(MentionRun(text: whole.substring(from: at), isMention: false, isSelf: false))
        }
        return runs
    }

    /// The @word being typed at the end of the draft, if there is one:
    /// "ask @da" → "da". Nil when the draft does not end in a mention.
    public static func query(in draft: String) -> String? {
        guard let at = draft.lastIndex(of: "@") else { return nil }
        let word = draft[draft.index(after: at)...]
        if word.contains(where: { $0.isWhitespace || $0 == "@" }) { return nil }
        if at != draft.startIndex {
            let before = draft[draft.index(before: at)]
            guard before.isWhitespace || before == "(" else { return nil }
        }
        return String(word)
    }

    /// The draft with the @word being typed replaced by "@Name ".
    public static func complete(_ draft: String, with name: String) -> String {
        guard query(in: draft) != nil, let at = draft.lastIndex(of: "@") else { return draft }
        return String(draft[..<at]) + "@\(name) "
    }

    /// Up to six names any of whose words starts with the query.
    public static func matches<T>(_ query: String, in people: [T], name: (T) -> String) -> [T] {
        let wanted = query.lowercased()
        return Array(people.filter { person in
            let full = name(person).lowercased()
            if wanted.isEmpty { return true }
            return full.hasPrefix(wanted) || full.split(whereSeparator: { $0.isWhitespace }).contains { $0.hasPrefix(wanted) }
        }.prefix(6))
    }
}
