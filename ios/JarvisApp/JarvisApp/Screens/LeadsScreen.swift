import SwiftUI
import JarvisKit

/// Leads. A sheet-backed tenant is read-only here; an app-native tenant
/// edits in place, and opens on Today: the leads whose follow-up date has
/// come. Follows frontend/src/screens/Leads.tsx.
struct LeadsScreen: View {
    @EnvironmentObject private var model: AppModel

    var people: [PersonChoice]

    private enum Filter: String {
        case due, all
        case needsTouch = "needs_touch"
        case hot, new

        var label: String {
            switch self {
            case .due: return "Today"
            case .needsTouch: return "Needs touch"
            case .hot: return "Hot"
            case .new: return "New"
            case .all: return "All"
            }
        }
    }

    @State private var data: LeadsResponse?
    @State private var problem: LoadProblem?
    @State private var filter: Filter = .due
    @State private var agent: String?
    @State private var busy = false
    @State private var composing: Bool
    @State private var draft = LeadDraft()
    @State private var open: String?

    /// Previews open with the new-lead form or one lead's sheet showing.
    init(people: [PersonChoice], composing: Bool = false, open: String? = nil) {
        self.people = people
        _composing = State(initialValue: composing)
        _open = State(initialValue: open)
    }

    // Two conditions, and they fail for different reasons. Source: writes
    // exist only against the app's own store. Person: an edit is a claim
    // about the business that needs someone to have made it.
    private var store: Bool { data?.source == "store" }
    private var canEdit: Bool { store && model.personKey != nil }
    private var manager: Bool { model.isManager || model.role == "operator" }
    private var today: String? { data?.today }

    private var active: Filter { filter == .due && !store ? .all : filter }

    private func isDue(_ lead: LeadItem) -> Bool {
        LeadDates.isDue(next: lead.nextFollowUp, today: today, status: lead.status)
    }

    private var dueCount: Int { (data?.leads ?? []).filter(isDue).count }

    /// Each agent tab with its name and how many of theirs are due.
    private var agents: [SelectOption] {
        var order: [String] = []
        var names: [String: String] = [:]
        var due: [String: Int] = [:]
        for lead in data?.leads ?? [] {
            guard let tab = lead.tab, !tab.isEmpty else { continue }
            if names[tab] == nil {
                order.append(tab)
                names[tab] = (lead.agent ?? "").isEmpty ? tab : (lead.agent ?? "")
            }
            if isDue(lead) { due[tab, default: 0] += 1 }
        }
        return order.map { SelectOption(value: $0, label: "\(names[$0] ?? $0) · \(due[$0] ?? 0)") }
    }

    private var visible: [LeadItem] {
        var all = data?.leads ?? []
        if let agent { all = all.filter { $0.tab == agent } }
        switch active {
        case .due:
            return all.filter(isDue).sorted { a, b in
                let first = a.nextFollowUp ?? ""
                let second = b.nextFollowUp ?? ""
                if first != second { return first < second }
                return (a.temperatureRank ?? 0) > (b.temperatureRank ?? 0)
            }
        case .needsTouch:
            return all.filter { $0.needsTouch == true }
        case .hot:
            return all.filter { $0.isHot == true }
        case .new:
            return all.filter { $0.isNew == true }
        case .all:
            return all
        }
    }

    /// The subtitle says where leads live, which differs per tenant.
    private var subtitle: String {
        guard let data else { return "From your open-house tracker" }
        let count = data.count ?? data.leads.count
        if store {
            return "\(count) lead\(count == 1 ? "" : "s") · \(dueCount) to follow up today"
        }
        return "\(count) from \((data.tabs ?? []).count) agent tabs · the sheet stays the place you edit"
    }

    private var showOptions: [SelectOption] {
        [Filter.due, .needsTouch, .hot, .new]
            .filter { store || $0 != .due }
            .map { SelectOption(value: $0.rawValue, label: $0 == .due ? "Today (\(dueCount))" : $0.label) }
    }

    private var emptyText: String {
        switch active {
        case .due: return "Nobody to follow up with today."
        case .all: return "No leads in the tracker yet."
        default: return "No leads match \(active.label)."
        }
    }

    var body: some View {
        Page(title: "Leads", subtitle: subtitle) {
            if canEdit {
                JMButton("New lead", icon: .plus, kind: .primary, size: .sm) { composing = true }
                    .disabled(busy)
            }
        } content: {
            if composing {
                quickAdd
            }

            if let problem {
                ErrorState(
                    label: "Leads unavailable", message: problem.message, detail: problem.detail,
                    stale: !(data?.leads ?? []).isEmpty, onRetry: { Task { await load() } })
            }

            VStack(alignment: .leading, spacing: JM.Space.s2) {
                FilterChips(label: "Show", options: showOptions, active: active == .all ? nil : active.rawValue) { value in
                    filter = value.flatMap(Filter.init(rawValue:)) ?? .all
                }
                if manager && store && agents.count > 1 {
                    FilterChips(label: "Who", options: agents, active: agent, allLabel: "All agents") { agent = $0 }
                }
            }

            if data == nil && problem == nil {
                LoadingLine()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, JM.Space.s8)
            } else if visible.isEmpty && problem == nil {
                EmptyState(icon: .leads, text: emptyText)
            } else {
                JMList(items: visible.numbered) { row in
                    let lead = row.value
                    LeadRow(
                        lead: lead,
                        due: store ? LeadDates.dueLabel(next: lead.nextFollowUp, today: today, status: lead.status) : nil,
                        canEdit: canEdit,
                        busy: busy,
                        onOpen: store && !lead.leadId.isEmpty ? { open = lead.leadId } : nil,
                        onChange: { fields in
                            Task { await mutate { try await model.client.patchLead(id: lead.leadId, fields: fields) } }
                        },
                        onArchive: {
                            Task { await mutate { try await model.client.archiveLead(id: lead.leadId) } }
                        })
                }
            }
        }
        .jmSheet(isPresented: open != nil, onClose: { open = nil }) {
            if let open {
                LeadSheet(
                    leadID: open, canEdit: canEdit,
                    onClose: { self.open = nil },
                    onChanged: { Task { await load() } })
                .id(open)
            }
        }
        .task { await load() }
    }

    private var quickAdd: some View {
        JMCard {
            JMField("Name", text: $draft.name, onSubmit: add)
            JMField("Phone", text: $draft.phone)
                .keyboardType(.phonePad)
            JMField("Email", text: $draft.email)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            JMField("Property they saw", text: $draft.property)
                .accessibilityLabel("Property")
            JMField("What they want (optional)", text: $draft.notes)
                .accessibilityLabel("Notes")
            FilterChips(
                label: "How hot",
                options: LeadWording.temperatures.map { SelectOption(value: $0, label: $0) },
                active: draft.temperature.isEmpty ? nil : draft.temperature,
                allLabel: "Not sure"
            ) { draft.temperature = $0 ?? "" }
            if manager && !people.isEmpty {
                HStack(spacing: JM.Space.s3) {
                    Text("Agent").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                    Spacer(minLength: 0)
                    JMSelect(
                        label: "Agent", value: draft.owner,
                        options: [SelectOption(value: "", label: "Me")]
                            + people
                                .filter { $0.personKey != model.personKey }
                                .map { SelectOption(value: $0.personKey, label: $0.name.isEmpty ? $0.personKey : $0.name) },
                        onChange: { draft.owner = $0 })
                }
            }
            Text(LeadDates.firstFollowUp(data?.cadence))
                .jmText(.caption)
                .foregroundStyle(Color.jmFgSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: JM.Space.s2) {
                JMButton("Add", kind: .primary, action: add)
                    .disabled(busy || draft.name.trimmingCharacters(in: .whitespaces).isEmpty)
                JMButton("Cancel", kind: .ghost) {
                    composing = false
                    draft = LeadDraft()
                }
            }
        }
    }

    private func add() {
        if draft.name.trimmingCharacters(in: .whitespaces).isEmpty { return }
        let sending = draft
        let asManager = manager
        composing = false
        draft = LeadDraft()
        Task {
            await mutate { try await model.client.createLead(sending, includeOwner: asManager) }
        }
    }

    private func load() async {
        do {
            data = try await model.client.leadsResponse()
            problem = nil
        } catch {
            // A sheet that cannot be reached says so. Never an empty table,
            // which would read as "you have no leads".
            problem = model.problem(for: error, message: "Could not load leads.")
        }
    }

    private func mutate(_ change: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do {
            try await change()
            await load()
        } catch {
            if model.words(for: error, fallback: "") != nil {
                problem = LoadProblem(message: "That change did not save.", detail: nil)
            }
        }
    }
}

// MARK: - LeadRow (jm/LeadRow)

struct LeadRow: View {
    var lead: LeadItem
    var due: DueLabel?
    var canEdit: Bool
    var busy: Bool
    /// Opens the lead and its timeline; the name becomes a button.
    var onOpen: (() -> Void)?
    /// One field per tap: temperature, status or pipeline.
    var onChange: ([String: String]) -> Void
    var onArchive: () -> Void

    private var temperature: String { lead.temperature ?? "" }
    private var status: String { lead.status ?? "" }
    private var pipeline: String { lead.pipeline ?? "" }

    private var rank: Int {
        lead.temperatureRank ?? max(0, LeadWording.temperatureCycle.firstIndex(of: temperature) ?? 0)
    }

    private var swatch: Color? {
        switch temperature {
        case "Hot": return .jmHot
        case "Warm": return .jmWarm
        case "Cold": return .jmCold
        default: return nil
        }
    }

    private var place: String {
        [lead.property, lead.price].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private var meta: [MetaItem] {
        [LeadWording.activity(lead), lead.agent ?? "", lead.date ?? ""]
            .filter { !$0.isEmpty }
            .map { MetaItem.plain($0) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s1) {
            HStack(spacing: JM.Space.s2) {
                bars
                if let swatch {
                    Circle()
                        .fill(swatch)
                        .overlay(Circle().strokeBorder(Color.black.opacity(0.1), lineWidth: 1))
                        .frame(width: 12, height: 12)
                        .accessibilityHidden(true)
                }
                name
                if lead.createdBy == "jarvis" {
                    SparkGlyph(label: "Added by Jarvis")
                }
                pipelineTag
                statusTag
            }
            if !place.isEmpty {
                Text(place).jmText(.bodySm).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
            }
            if let due {
                Text(due.text)
                    .jmText(.caption, weight: due.now ? .medium : nil)
                    .foregroundStyle(due.now ? Color.jmAccentText : Color.jmFgSecondary)
            }
            HStack(spacing: JM.Space.s2) {
                MetaLine(items: meta)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if canEdit {
                    JMButton("Archive", kind: .ghost, size: .sm, action: onArchive)
                        .disabled(busy)
                }
            }
            .frame(minHeight: JM.Size.controlSm)
        }
    }

    @ViewBuilder
    private var bars: some View {
        let label = temperature.isEmpty ? "No temperature set" : "Temperature: \(temperature)"
        if canEdit {
            let next = LeadWording.next(after: temperature, in: LeadWording.temperatureCycle)
            Button {
                onChange(["temperature": next])
            } label: {
                TempBars(rank: rank)
            }
            .buttonStyle(JMPlainStyle())
            .disabled(busy)
            .opacity(busy ? JM.disabledOpacity : 1)
            .accessibilityLabel("\(label). Change to \(next.isEmpty ? "none" : next)")
        } else {
            TempBars(rank: rank)
                .accessibilityElement()
                .accessibilityLabel(label)
        }
    }

    @ViewBuilder
    private var name: some View {
        if let onOpen {
            Button(action: onOpen) {
                Text(lead.name)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFg)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, minHeight: JM.Size.touch, alignment: .leading)
            }
            .buttonStyle(JMPlainStyle())
        } else {
            Text(lead.name)
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFg)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private var pipelineTag: some View {
        if !pipeline.isEmpty {
            if canEdit {
                let next = LeadWording.nextPipeline(after: pipeline)
                Button {
                    onChange(["pipeline": next])
                } label: {
                    Tag(pipeline, maxWidth: 144)
                }
                .buttonStyle(JMPlainStyle())
                .disabled(busy)
                .opacity(busy ? JM.disabledOpacity : 1)
                .accessibilityLabel("Pipeline: \(pipeline). Change to \(next)")
            } else {
                Tag(pipeline, maxWidth: 144)
            }
        }
    }

    /// No Response needs a follow-up: the blue pill. Responded: the check.
    @ViewBuilder
    private var statusMark: some View {
        switch status.lowercased() {
        case "no response":
            Tag(status, tone: .action, dot: false)
        case "responded":
            StatusPill(word: status) { Tick(size: 14) }
        default:
            Tag(status)
        }
    }

    @ViewBuilder
    private var statusTag: some View {
        if !status.isEmpty {
            if canEdit {
                let next = LeadWording.next(after: status, in: LeadWording.statusCycle)
                Button {
                    onChange(["status": next])
                } label: {
                    statusMark
                }
                .buttonStyle(JMPlainStyle())
                .disabled(busy)
                .opacity(busy ? JM.disabledOpacity : 1)
                .accessibilityLabel("Status: \(status). Change to \(next)")
            } else {
                statusMark
            }
        }
    }
}

// MARK: - LeadSheet (components/LeadSheet.tsx)

/// One lead: how to reach them, what has happened, and "Log contact". The
/// next follow-up is left blank on purpose: the server picks it from the
/// cadence. Choosing a date overrides it.
struct LeadSheet: View {
    @EnvironmentObject private var model: AppModel

    var leadID: String
    var canEdit: Bool
    var onClose: () -> Void
    var onChanged: () -> Void

    @State private var data: LeadDetail?
    @State private var error: String?
    @State private var busy = false
    @State private var kind = "call"
    @State private var missed = false
    @State private var note = ""
    @State private var next = ""

    private var lead: LeadItem? { data?.lead }

    private var subtitle: String? {
        let parts = [lead?.property, lead?.agent].compactMap { $0 }.filter { !$0.isEmpty }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    var body: some View {
        JMSheet(title: lead?.name ?? "Lead", subtitle: subtitle, onClose: onClose) {
            if let error {
                ErrorLine(error)
            }
            if data == nil && error == nil {
                LoadingLine()
            }
            if let data {
                VStack(alignment: .leading, spacing: JM.Space.s4) {
                    contact(data.lead, today: data.today)
                    if canEdit {
                        logForm
                    }
                    timeline(data.timeline)
                }
            }
        }
        .task { await load() }
    }

    private func contact(_ lead: LeadItem, today: String?) -> some View {
        let facts = [(lead.status ?? "").isEmpty ? "New" : (lead.status ?? ""), lead.temperature ?? "", lead.price ?? "", lead.source ?? ""]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
        let due = LeadDates.dueLabel(next: lead.nextFollowUp, today: today, status: lead.status)
        return VStack(alignment: .leading, spacing: JM.Space.s1) {
            if let phone = lead.phone, !phone.isEmpty {
                reach(phone, scheme: "tel")
            }
            if let email = lead.email, !email.isEmpty {
                reach(email, scheme: "mailto")
            }
            Text(facts).jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
            if let due {
                Text(due.text)
                    .jmText(.bodySm, weight: due.now ? .medium : nil)
                    .foregroundStyle(due.now ? Color.jmAccentText : Color.jmFgSecondary)
            }
            if let notes = lead.notes, !notes.isEmpty {
                Text(notes)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFg)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// A phone number or address that opens the phone or mail app.
    private func reach(_ value: String, scheme: String) -> some View {
        Button {
            let compact = scheme == "tel" ? value.filter { !$0.isWhitespace } : value
            if let url = URL(string: "\(scheme):\(compact)") {
                Platform.open(url)
            }
        } label: {
            Text(value).jmText(.bodySm).foregroundStyle(Color.jmAccentText)
        }
        .buttonStyle(JMPlainStyle())
    }

    private var logForm: some View {
        JMCard {
            Text("Log contact").jmText(.label).foregroundStyle(Color.jmFg)
            FilterChips(
                label: "What",
                options: LeadWording.touchKinds.map { SelectOption(value: $0.id, label: $0.label) },
                active: kind, hideAll: true
            ) { picked in
                if let picked { kind = picked }
            }
            if kind != "note" {
                Button {
                    missed.toggle()
                } label: {
                    HStack(spacing: JM.Space.s2) {
                        CheckBox(on: missed)
                        Text("Didn’t reach them").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                    }
                }
                .buttonStyle(JMPlainStyle())
                .accessibilityAddTraits(missed ? .isSelected : [])
            }
            JMField("What happened (optional)", text: $note)
                .accessibilityLabel("Note")
            if kind != "note" {
                VStack(alignment: .leading, spacing: JM.Space.s2) {
                    Text("Next follow-up").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                    JMDateField(label: "Next follow-up", day: $next)
                    Text("Leave blank and Jarvis picks it.").jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                }
            }
            JMButton(kind == "note" ? "Save note" : "Log contact", kind: .primary) {
                Task { await save() }
            }
            .disabled(busy)
        }
    }

    private func timeline(_ touches: [LeadTouchItem]) -> some View {
        VStack(alignment: .leading, spacing: JM.Space.s2) {
            Text("Timeline").jmText(.label).foregroundStyle(Color.jmFg)
            if touches.isEmpty {
                Text("Nothing logged yet.").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
            } else {
                let newestFirst = Array(touches.reversed()).numbered
                ForEach(newestFirst) { row in
                    let touch = row.value
                    let facts = [JarvisTime.stamp(touch.at), touch.byName ?? "", (touch.nextTouchAt ?? "").isEmpty ? "" : "next \(touch.nextTouchAt ?? "")"]
                        .filter { !$0.isEmpty }
                        .joined(separator: " · ")
                    VStack(alignment: .leading, spacing: 2) {
                        Text(LeadWording.touchTitle(touch)).jmText(.bodySm).foregroundStyle(Color.jmFg)
                        if let note = touch.note, !note.isEmpty {
                            Text(note)
                                .jmText(.bodySm)
                                .foregroundStyle(Color.jmFgSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Text(facts).jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                    }
                    .padding(.bottom, JM.Space.s2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .overlay(alignment: .bottom) {
                        if row.id != newestFirst.count - 1 {
                            Rectangle().fill(Color.jmBorder).frame(height: 1)
                        }
                    }
                }
            }
        }
    }

    private func load() async {
        do {
            data = try await model.client.lead(id: leadID)
            error = nil
        } catch {
            if model.words(for: error, fallback: "") != nil {
                self.error = "Could not open this lead."
            }
        }
    }

    private func save() async {
        if busy { return }
        busy = true
        defer { busy = false }
        do {
            let words = note.trimmingCharacters(in: .whitespacesAndNewlines)
            data = try await model.client.logContact(
                leadID: leadID, kind: kind, note: words.isEmpty ? nil : words,
                reached: kind == "note" ? nil : !missed, nextDate: next.isEmpty ? nil : next)
            error = nil
            note = ""
            next = ""
            missed = false
            onChanged()
        } catch {
            if model.words(for: error, fallback: "") != nil {
                self.error = "That did not save."
            }
        }
    }
}

/// A small square check, for the one checkbox the web app has.
struct CheckBox: View {
    var on: Bool

    var body: some View {
        RoundedRectangle(cornerRadius: 3, style: .continuous)
            .fill(on ? Color.jmAccent : Color.clear)
            .overlay(
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .strokeBorder(on ? Color.jmAccent : Color.jmBorderStrong, lineWidth: 1))
            .overlay {
                if on {
                    JarvisIconShape(icon: .check)
                        .stroke(Color.jmOnAccent, style: StrokeStyle(lineWidth: 1.75, lineCap: .round, lineJoin: .round))
                        .frame(width: 11, height: 11)
                }
            }
            .frame(width: 16, height: 16)
            .accessibilityHidden(true)
    }
}
