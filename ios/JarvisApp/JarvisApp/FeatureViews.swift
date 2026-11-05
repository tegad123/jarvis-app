import SwiftUI
import JarvisKit

struct AskView: View {
    @EnvironmentObject private var model: AppModel
    @State private var draft = ""
    @State private var transcript = ""
    @State private var busy = false
    @State private var error: String?
    @State private var task: Task<Void, Never>?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Text(AIDisclosure.text).font(.footnote).foregroundStyle(.secondary)
                Toggle("Send Ask messages to the model", isOn: consent)
                    .accessibilityHint("Off means nothing is sent")
                ScrollView {
                    Text(transcript.isEmpty ? "No messages yet." : transcript)
                        .font(.body)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .accessibilityLabel("Conversation")
                if let error { Text(error).foregroundStyle(.red).font(.footnote) }
                if model.session?.allows("ask") == false {
                    Text("Ask is switched off for you. The server enforces that.")
                        .font(.footnote)
                }
                TextField("Message", text: $draft, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityLabel("Message")
                HStack {
                    Button(busy ? "Sending…" : "Send") { start() }
                        .disabled(busy || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !model.consent)
                    if busy { Button("Stop") { task?.cancel() } }
                }
            }
            .padding()
            .navigationTitle("Ask")
        }
    }

    private var consent: Binding<Bool> {
        Binding(get: { model.consent }, set: { model.consent = $0 })
    }

    private func start() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        transcript += (transcript.isEmpty ? "" : "\n\n") + "You: \(text)\nJarvis: "
        error = nil
        busy = true
        task = Task {
            var reply = ""
            do {
                for try await line in model.client.sendChat(message: text) {
                    switch line.kind {
                    case .delta:
                        reply += line.text
                        transcript += line.text
                    case .error:
                        error = line.text
                    case .done, .other:
                        break
                    }
                }
                if reply.isEmpty && error == nil { transcript += "(no reply)" }
            } catch JarvisError.consentRequired {
                error = "Turn on sending before this message goes to the model."
            } catch JarvisError.cancelled {
                error = "Stopped."
            } catch JarvisError.http(_, let message) {
                error = message
            } catch {
                error = "The reply did not finish."
            }
            busy = false
        }
    }
}

struct LeadsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var leads: [LeadItem] = []
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if loading { ProgressView("Loading leads") }
                else if let error { ErrorRetry(message: error) { await load() } }
                else if leads.isEmpty { Text("No leads.").font(.body) }
                else {
                    List(leads) { lead in
                        NavigationLink(lead.name) { LeadDetailView(leadID: lead.leadId) }
                            .accessibilityLabel(leadLabel(lead))
                    }
                }
            }
            .navigationTitle("Leads")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private func leadLabel(_ lead: LeadItem) -> String {
        [lead.name, lead.pipeline, lead.status].compactMap { $0 }.joined(separator: ", ")
    }

    private func load() async {
        loading = leads.isEmpty
        defer { loading = false }
        do { leads = try await model.client.leads(); error = nil }
        catch JarvisError.http(_, let message) { error = message }
        catch { error = "Could not load leads." }
    }
}

struct LeadDetailView: View {
    @EnvironmentObject private var model: AppModel
    let leadID: String
    @State private var detail: LeadDetail?
    @State private var error: String?
    @State private var kind = "call"
    @State private var note = ""
    @State private var missed = false
    @State private var busy = false

    private let kinds = ["call", "text", "email", "met", "showing", "note"]

    var body: some View {
        Group {
            if let detail {
                List {
                    Section(detail.lead.name) {
                        labeled("Status", detail.lead.status)
                        labeled("Pipeline", detail.lead.pipeline)
                        labeled("Follow-up", detail.lead.followUp)
                        labeled("Next", detail.lead.nextFollowUp)
                        labeled("Property", detail.lead.property)
                        labeled("Phone", detail.lead.phone)
                        labeled("Email", detail.lead.email)
                        labeled("Id", detail.lead.externalRef)
                        if let notes = detail.lead.notes, !notes.isEmpty {
                            Text(notes).font(.body)
                        }
                    }
                    Section("Log contact") {
                        Picker("What", selection: $kind) {
                            ForEach(kinds, id: \.self) { Text($0).tag($0) }
                        }
                        if kind != "note" {
                            Toggle("Didn't reach them", isOn: $missed)
                        }
                        TextField("What happened", text: $note, axis: .vertical)
                            .accessibilityLabel("Note")
                        Button(busy ? "Saving…" : "Log contact") { Task { await save() } }
                            .disabled(busy)
                    }
                    Section("Timeline") {
                        if detail.timeline.isEmpty { Text("Nothing logged yet.") }
                        ForEach(detail.timeline) { touch in
                            VStack(alignment: .leading) {
                                Text(touch.kind).font(.body)
                                if let note = touch.note { Text(note).font(.footnote) }
                                if touch.reached == false { Text("No answer").font(.footnote) }
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }
                }
            } else if let error {
                ErrorRetry(message: error) { await load() }
            } else {
                ProgressView("Opening lead")
            }
        }
        .navigationTitle("Lead")
        .task { await load() }
    }

    private func labeled(_ title: String, _ value: String?) -> some View {
        HStack {
            Text(title).foregroundStyle(.secondary)
            Spacer()
            Text(value?.isEmpty == false ? value! : "—")
        }
        .accessibilityElement(children: .combine)
    }

    private func load() async {
        do { detail = try await model.client.lead(id: leadID); error = nil }
        catch JarvisError.http(_, let message) { error = message }
        catch { error = "Could not open this lead." }
    }

    private func save() async {
        busy = true
        defer { busy = false }
        do {
            detail = try await model.client.logContact(
                leadID: leadID,
                kind: kind,
                note: note,
                reached: kind == "note" ? nil : !missed,
                nextDate: nil
            )
            note = ""
            missed = false
            error = nil
        } catch JarvisError.http(_, let message) {
            error = message
        } catch {
            error = "That did not save."
        }
    }
}

struct TasksView: View {
    @EnvironmentObject private var model: AppModel
    @State private var tasks: [TaskItem] = []
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if loading { ProgressView("Loading tasks") }
                else if let error { ErrorRetry(message: error) { await load() } }
                else if tasks.isEmpty { Text("No tasks.").font(.body) }
                else {
                    List(tasks) { task in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(task.title).font(.body)
                            Text([task.status, task.assigneeName, task.dueLabel].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                            if let detail = task.detail, !detail.isEmpty {
                                Text(detail).font(.footnote)
                            }
                        }
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            .navigationTitle("Tasks")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private func load() async {
        loading = tasks.isEmpty
        defer { loading = false }
        do { tasks = try await model.client.tasks(); error = nil }
        catch JarvisError.http(_, let message) { error = message }
        catch { error = "Could not load tasks." }
    }
}

struct ApprovalsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var items: [ApprovalItem] = []
    @State private var loading = true
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Group {
                if loading { ProgressView("Loading approvals") }
                else if let error { ErrorRetry(message: error) { await load() } }
                else if items.isEmpty { Text("Nothing waiting.").font(.body) }
                else {
                    List(items) { item in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(item.summary).font(.body)
                            Text(item.status).font(.footnote).foregroundStyle(.secondary)
                            if item.status == "pending" {
                                HStack {
                                    if item.canApprove == true {
                                        Button("Approve") { Task { await decide(item, approve: true) } }
                                    }
                                    if item.canReject == true {
                                        Button("Reject") { Task { await decide(item, approve: false) } }
                                    }
                                    if item.canApprove != true && item.canReject != true {
                                        Text("You can't decide this one.").font(.footnote)
                                    }
                                }
                            }
                        }
                        .accessibilityElement(children: .contain)
                    }
                }
            }
            .navigationTitle("Approvals")
            .task { await load() }
            .refreshable { await load() }
        }
    }

    private func load() async {
        loading = items.isEmpty
        defer { loading = false }
        do { items = try await model.client.approvals(); error = nil }
        catch JarvisError.http(_, let message) { error = message }
        catch { error = "Could not load approvals." }
    }

    private func decide(_ item: ApprovalItem, approve: Bool) async {
        do {
            try await model.client.decide(approvalID: item.id, approve: approve)
            await load()
        } catch JarvisError.http(_, let message) {
            error = message
        } catch {
            error = "That decision did not save."
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var closurePIN = ""
    @State private var closureBusy = false
    @State private var closureNote: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("Signed in") {
                    Text(model.session?.displayName ?? model.session?.personKey ?? "Signed in")
                    Text(model.session?.role ?? "").foregroundStyle(.secondary)
                    Button("Sign out") {
                        Task {
                            await model.client.logout()
                            model.session = nil
                        }
                    }
                }
                Section("Privacy") {
                    Text(AIDisclosure.text).font(.footnote)
                    Text("The written policy in the repo (site/privacy.md) describes the Yamin service at yamin.io. It is not automatically the policy of the tenant address above, so this screen does not link there.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("My sign-in") {
                    Text("This turns off your sign-in and records a deletion request. It does not delete leads, tasks, or messages, and it does not delete the team. The only owner cannot do this to themselves.")
                        .font(.footnote)
                    SecureField("PIN again", text: $closurePIN)
                        .accessibilityLabel("PIN to confirm turning off sign-in")
                    Button("Turn off my sign-in") { Task { await requestClosure() } }
                        .disabled(closurePIN.isEmpty || closureBusy)
                    if let closureNote {
                        Text(closureNote).font(.footnote)
                    }
                }
                if model.session?.role == "owner" {
                    TeamDeletionSection()
                }
                Section("Store login") {
                    Text("This build signs in with a PIN only. Google sign-in is still how some tenants sign in on the web. Leaving it off this screen does not, by itself, settle App Store guideline 4.8.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section {
                    Button("Forget this tenant") { Task { await model.forgetOrigin() } }
                }
            }
            .navigationTitle("Settings")
        }
    }

    private func requestClosure() async {
        closureBusy = true
        defer { closureBusy = false; closurePIN = "" }
        do {
            let receipt = try await model.client.requestAccountDeletion(pin: closurePIN)
            closureNote = receipt.detail
            model.session = nil
        } catch JarvisError.http(_, let message) {
            closureNote = message
        } catch {
            closureNote = "That request did not go through."
        }
    }
}

struct TeamDeletionSection: View {
    @EnvironmentObject private var model: AppModel
    @State private var phraseRequired = ""
    @State private var nonce = ""
    @State private var removes: [String] = []
    @State private var limits: [String] = []
    @State private var typed = ""
    @State private var pin = ""
    @State private var busy = false
    @State private var note: String?

    var body: some View {
        Section("Delete this team") {
            Text("This permanently deletes this team's records stored in Jarvis: every member's sign-in, leads, tasks, chats, and messages. It is not the same as turning off your own sign-in.")
                .font(.footnote)
            ForEach(removes, id: \.self) { line in
                Text(line).font(.footnote)
            }
            ForEach(limits, id: \.self) { line in
                Text(line).font(.footnote).foregroundStyle(.secondary)
            }
            if phraseRequired.isEmpty {
                Text(note ?? "Loading what this will do.").font(.footnote)
            } else {
                Text("Type \(phraseRequired) to confirm. There is no one-tap delete.")
                    .font(.footnote)
                    .accessibilityLabel("Type \(phraseRequired) to confirm")
                TextField(phraseRequired, text: $typed)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .accessibilityLabel("Confirmation phrase")
                SecureField("Owner PIN", text: $pin)
                    .accessibilityLabel("Owner PIN")
                Button("Delete this team") { Task { await deleteTeam() } }
                    .disabled(busy || nonce.isEmpty || pin.isEmpty || typed != phraseRequired)
                if let note {
                    Text(note).font(.footnote)
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            let impact = try await model.client.teamDeletionChallenge()
            phraseRequired = impact.phrase
            nonce = impact.nonce ?? ""
            removes = impact.removes
            limits = impact.notRemoved
            note = nil
        } catch JarvisError.http(_, let message) {
            note = message
        } catch {
            note = "Could not load team deletion."
        }
    }

    private func deleteTeam() async {
        busy = true
        defer { busy = false; pin = "" }
        do {
            let receipt = try await model.client.deleteTeam(pin: pin, phrase: typed, nonce: nonce)
            let limits = receipt.stillExists.joined(separator: " ")
            model.status = receipt.detail + " " + limits
            model.session = nil
        } catch JarvisError.http(_, let message) {
            note = message
            nonce = ""
            await load()
        } catch {
            note = "That request did not go through. Nothing here was treated as deleted."
        }
    }
}

struct ErrorRetry: View {
    var message: String
    var retry: () async -> Void

    var body: some View {
        VStack(spacing: 12) {
            Text(message).font(.body).multilineTextAlignment(.center)
            Button("Try again") { Task { await retry() } }
        }
        .padding()
        .accessibilityElement(children: .contain)
    }
}
