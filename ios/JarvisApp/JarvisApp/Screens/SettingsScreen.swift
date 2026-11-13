import SwiftUI
import JarvisKit

/// Settings: the signed-in person's own accounts, then the team's settings
/// for an owner or admin. Follows frontend/src/screens/Settings.tsx. The
/// API only ever reads or changes the caller's own row; hiding a section
/// here is a courtesy on top of that.
struct SettingsScreen: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase

    @State private var connection: GoogleConnection?
    @State private var problem: LoadProblem?
    @State private var phase: SettingsWording.GooglePhase = .idle
    @State private var confirm = false

    private var connected: Bool {
        connection?.status == "connected" || connection?.status == "needs_reauth"
    }

    private var googleRows: [SettingsRow] {
        guard let mailbox = connection?.googleEmail, !mailbox.isEmpty, phase != .disconnected else { return [] }
        return [SettingsRow(id: "mailbox", title: mailbox, detail: SettingsWording.googleStatus(connection, phase: phase).time)]
    }

    var body: some View {
        let role = model.role
        let manager = Roles.canManageTeam(role)
        Page(
            title: "Settings",
            subtitle: "Your own accounts. Your assistant uses them for you; nobody else on the team can."
        ) {
            if let problem {
                ErrorState(
                    label: "Settings unavailable", message: problem.message, detail: problem.detail,
                    onRetry: { Task { await load() } })
            }

            SettingsSection(
                title: "Your Google account",
                description: "Your assistant uses it to check your calendar and email.",
                status: connection != nil || phase != .idle ? SettingsWording.googleStatus(connection, phase: phase).status : nil,
                rows: googleRows,
                showsActions: phase != .disconnecting,
                actions: {
                    if !connected {
                        JMButton("Connect", kind: .primary) {
                            // The web's own connect flow, in the browser.
                            if let url = model.client.webURL("/api/google/connect/start") {
                                Platform.open(url)
                            }
                        }
                    } else {
                        JMButton(confirm ? "Confirm disconnect" : "Disconnect") {
                            Task { await disconnect() }
                        }
                        if confirm {
                            JMButton("Cancel", kind: .ghost) { confirm = false }
                        }
                    }
                })

            if role != "operator" && model.session?.allows("browser") != false {
                WebsiteLoginsSection()
            }
            if role != "operator" && model.session?.allows("imessage") != false {
                MacMessagesSection()
            }
            if manager {
                TeamSection()
                ChannelsSection()
            }

            PhoneSettings()
        }
        .task { await load() }
        .onChange(of: scenePhase) { _, next in
            // Back from the browser: the connection may have changed.
            if next == .active { Task { await load() } }
        }
    }

    private func load() async {
        do {
            let next = try await model.client.googleConnection()
            connection = next
            problem = nil
            if next.status != "disconnected" { phase = .idle }
        } catch {
            problem = model.problem(
                for: error, message: "Jarvis could not read the Google connection.",
                offline: "Something went wrong loading Settings.")
        }
    }

    private func disconnect() async {
        if !confirm {
            confirm = true
            return
        }
        phase = .disconnecting
        confirm = false
        do {
            try await model.client.disconnectGoogle()
            phase = .disconnected
            connection = GoogleConnection(status: "disconnected")
        } catch {
            phase = .idle
            problem = model.problem(for: error, message: "Could not disconnect Google.")
        }
    }
}

// MARK: - Website logins

/// Saved website logins for Jarvis's browser. The API never returns a
/// password; the browser types it into the site's own form.
private struct WebsiteLoginsSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var enabled = false
    @State private var logins: [WebsiteLogin] = []
    @State private var site = ""
    @State private var username = ""
    @State private var password = ""
    @State private var busy = false
    @State private var error: String?

    private var complete: Bool {
        !site.trimmingCharacters(in: .whitespaces).isEmpty
            && !username.trimmingCharacters(in: .whitespaces).isEmpty && !password.isEmpty
    }

    var body: some View {
        Group {
            if enabled {
                SettingsSection(
                    title: "Website logins",
                    description: "Jarvis signs in with these when you approve a task on that site. It never sees the password: the browser types it into the site’s own sign-in form.",
                    rows: logins.map { login in
                        let used = (login.lastUsedAt ?? "").isEmpty
                            ? " · not used yet" : " · last used \(JarvisTime.relative(login.lastUsedAt))"
                        return SettingsRow(
                            id: login.site, title: login.site, detail: login.username + used,
                            actionLabel: "Remove", actionDisabled: busy,
                            action: { Task { await remove(login.site) } })
                    },
                    error: error,
                    content: {
                        VStack(alignment: .leading, spacing: JM.Space.s2) {
                            JMField("Site, e.g. zillow.com", text: $site)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                                .accessibilityLabel("Site")
                            JMField("Email or username", text: $username)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                            JMField("Password", text: $password, secure: true)
                            JMButton("Save login", kind: .primary) { Task { await add() } }
                                .disabled(busy || !complete)
                        }
                    })
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            enabled = try await model.client.browserEnabled()
            if enabled { logins = try await model.client.websiteLogins() }
        } catch {
            self.error = model.words(for: error, fallback: "Could not read the saved logins.")
        }
    }

    private func add() async {
        busy = true
        error = nil
        defer { busy = false }
        do {
            try await model.client.saveWebsiteLogin(
                site: site.trimmingCharacters(in: .whitespaces),
                username: username.trimmingCharacters(in: .whitespaces), password: password)
            site = ""
            username = ""
            password = ""
            await load()
        } catch {
            self.error = model.words(for: error, fallback: "Could not save that login.")
        }
    }

    private func remove(_ site: String) async {
        busy = true
        defer { busy = false }
        do {
            try await model.client.deleteWebsiteLogin(site: site)
            await load()
        } catch {
            self.error = model.words(for: error, fallback: "Could not remove that login.")
        }
    }
}

// MARK: - iMessage

/// The read-only iMessage helper. Pairing is a one-time code the Mac claims.
private struct MacMessagesSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var status: MacStatus?
    @State private var code: PairingCodeInfo?
    @State private var busy = false
    @State private var error: String?

    var body: some View {
        Group {
            if let status {
                let devices = status.devices
                let reminder = status.reminder.flatMap { $0.enabled && !devices.isEmpty ? $0 : nil }
                SettingsSection(
                    title: "iMessage",
                    description: "A small helper on your Mac lets Jarvis read your texts from the last \(status.retentionDays ?? 0) days. Reading only: Jarvis can’t send a text. Disconnecting deletes everything it synced.",
                    status: SettingsWording.macStatus(devices),
                    rows: devices.map { device in
                        SettingsRow(
                            id: device.deviceId, title: device.name,
                            detail: "\(status.messages ?? 0) messages · \(SettingsWording.macDetail(device.lastSyncAt))",
                            actionLabel: "Disconnect", actionDisabled: busy,
                            action: { Task { await remove(device.deviceId) } })
                    },
                    error: error,
                    showsContent: code != nil || reminder != nil,
                    showsActions: code == nil,
                    content: {
                        if let code {
                            if status.macApp == true {
                                steps(code)
                            } else {
                                PairingCode(code: code.code, expires: JarvisTime.absolute(code.expiresAt))
                            }
                        }
                        if let reminder {
                            ReplyReminderNote(reminder: reminder)
                        }
                    },
                    actions: {
                        JMButton(devices.isEmpty ? "Connect a Mac" : "Connect another Mac", kind: .primary) {
                            Task { await pair() }
                        }
                        .disabled(busy)
                    })
            } else if let error {
                SettingsSection(title: "iMessage", error: error)
            }
        }
        .task { await load() }
        .task(id: code?.code) {
            // While a code is on screen, watch for the Mac claiming it.
            guard code != nil else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 4_000_000_000)
                if Task.isCancelled { break }
                await load()
            }
        }
    }

    private func steps(_ code: PairingCodeInfo) -> some View {
        VStack(alignment: .leading, spacing: JM.Space.s3) {
            step(1) {
                Text("Download Jarvis for Mac and open it. The download is in Settings on the Mac, in the web app.")
            }
            step(2) {
                Text("The first time, macOS asks whether to open it: choose Open, or go to System Settings → Privacy & Security and click Open Anyway.")
            }
            step(3) {
                Text("Type this code when Jarvis asks:")
                PairingCode(code: code.code, expires: JarvisTime.absolute(code.expiresAt), instruction: nil)
                    .padding(.top, JM.Space.s2)
            }
            step(4) {
                Text("Turn on Full Disk Access for Jarvis when it shows you where. That’s it: it keeps up in the background.")
            }
        }
    }

    private func step<Body: View>(_ number: Int, @ViewBuilder body: () -> Body) -> some View {
        HStack(alignment: .top, spacing: JM.Space.s1) {
            Text("\(number).")
                .jmText(.bodySm)
                .monospacedDigit()
                .frame(width: JM.Space.s4, alignment: .leading)
            VStack(alignment: .leading, spacing: 0) {
                body()
                    .jmText(.bodySm)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .foregroundStyle(Color.jmFgSecondary)
    }

    private func load() async {
        do {
            let next = try await model.client.macStatus()
            status = next
            if code != nil && !next.devices.isEmpty { code = nil }
        } catch {
            self.error = model.words(for: error, fallback: "Could not read the Mac connection.")
        }
    }

    private func pair() async {
        busy = true
        error = nil
        defer { busy = false }
        do {
            code = try await model.client.newMacCode()
        } catch {
            self.error = model.words(for: error, fallback: "Could not make a code.")
        }
    }

    private func remove(_ id: String) async {
        busy = true
        defer { busy = false }
        do {
            try await model.client.disconnectMac(deviceID: id)
            await load()
        } catch {
            self.error = model.words(for: error, fallback: "Could not disconnect that Mac.")
        }
    }
}

/// The twice-daily "texts waiting for your reply" email. It goes from the
/// person's own Gmail to that same address, so without one it can't go.
private struct ReplyReminderNote: View {
    @EnvironmentObject private var model: AppModel

    var reminder: ReplyReminder

    @State private var busy = false
    @State private var note: String?

    private var text: String {
        let when = SettingsWording.listLabel((reminder.timesLocal ?? []).map(SettingsWording.clockLabel))
        if reminder.sendOn != true { return "Reply reminders are paused: sending is switched off for you." }
        guard let to = reminder.to, !to.isEmpty else {
            return "Connect your Google account above and Jarvis will email you at \(when) with the texts still waiting for your reply."
        }
        return "At \(when), Jarvis emails \(to) the texts still waiting for your reply."
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s2) {
            Text(text)
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFgSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if !(reminder.to ?? "").isEmpty && reminder.sendOn == true {
                FlowLayout(spacing: JM.Space.s3, lineSpacing: JM.Space.s2) {
                    JMButton("Email me the list now") { Task { await sendNow() } }
                        .disabled(busy)
                    if let note {
                        Text(note).jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                    }
                }
            }
        }
    }

    private func sendNow() async {
        busy = true
        note = nil
        defer { busy = false }
        do {
            let waiting = try await model.client.sendReplyReminder()
            note = waiting > 0 ? "Sent: \(waiting) waiting for your reply." : "Sent: nothing is waiting for your reply."
        } catch {
            note = model.words(for: error, fallback: "The email could not be sent.")
        }
    }
}

// MARK: - PairingCode (jm/PairingCode)

struct PairingCode: View {
    var code: String
    /// A formatted time: "Works once, until …."
    var expires: String?
    /// Nil hides the instruction line.
    var instruction: String? = "On your Mac, run the Jarvis helper’s pair command with this code:"

    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s3) {
            if let instruction {
                Text(instruction)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            FlowLayout(spacing: JM.Space.s4, lineSpacing: JM.Space.s3) {
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    ForEach(Array(code).numbered) { character in
                        if character.value == "-" || character.value == " " {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color.jmBorderStrong)
                                .frame(width: 10, height: 2)
                                .frame(height: 52)
                        } else {
                            Text(String(character.value))
                                .jmFont(.pairing)
                                .monospacedDigit()
                                .foregroundStyle(Color.jmFg)
                                .frame(minWidth: 40, minHeight: 52)
                                .background(Color.jmBg, in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                                .overlay(
                                    RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                                        .strokeBorder(Color.jmBorderStrong, lineWidth: 1))
                        }
                    }
                }
                .accessibilityElement()
                .accessibilityLabel("Pairing code: \(code.map(String.init).joined(separator: " "))")
                JMButton(copied ? "Copied" : "Copy", icon: .copy, size: .sm) {
                    Platform.copy(code)
                    copied = true
                    Task {
                        try? await Task.sleep(nanoseconds: 1_600_000_000)
                        copied = false
                    }
                }
            }
            if let expires, !expires.isEmpty {
                Text("Works once, until \(expires).").jmText(.caption).foregroundStyle(Color.jmFgSecondary)
            }
            HStack(spacing: JM.Space.s2) {
                Dots()
                Text("Waiting for your Mac…").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
            }
        }
    }
}

// MARK: - Team

/// Settings → Team. Who may change whom is the API's (app/team.py); this
/// only hides controls the API would refuse.
private struct TeamSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var team: TeamResponse?
    @State private var error: String?
    @State private var busy: String?
    @State private var name = ""
    @State private var email = ""
    @State private var newRole = "member"
    @State private var notice: String?

    private static let description =
        "Invite people by email. They sign in with Google using that address. Switch a feature off for one person and both the app and Jarvis stop offering it to them."

    private var owner: Bool { model.role == "owner" }

    private func manageable(_ person: TeamPerson) -> Bool {
        person.personKey != model.personKey && person.role != "owner" && (owner || person.role == "member")
    }

    var body: some View {
        Group {
            if let team {
                SettingsSection(
                    title: "Team", description: Self.description, error: error,
                    content: {
                        VStack(alignment: .leading, spacing: JM.Space.s4) {
                            ForEach(team.people.numbered) { row in
                                person(row.value, team: team, last: row.id == team.people.count - 1)
                            }
                        }
                        if team.invitesReady == true {
                            inviteForm(team)
                        } else {
                            Text("Inviting isn’t set up on this account yet.")
                                .jmText(.bodySm)
                                .foregroundStyle(Color.jmFgSecondary)
                                .padding(.top, JM.Space.s1)
                        }
                    })
            } else if let error {
                SettingsSection(title: "Team", error: error)
            }
        }
        .task { await load() }
    }

    private func person(_ person: TeamPerson, team: TeamResponse, last: Bool) -> some View {
        let can = manageable(person)
        let off = person.disabled == true
        let signedIn: String = {
            if off { return "Access off" }
            if let seen = person.lastLogin, !seen.isEmpty { return "Signed in \(JarvisTime.relative(seen))" }
            return "Hasn’t signed in yet"
        }()
        let facts = [person.email ?? "", signedIn].filter { !$0.isEmpty }.joined(separator: " · ")
        let shown = (person.fullName ?? "").isEmpty ? person.name : (person.fullName ?? "")
        return VStack(alignment: .leading, spacing: JM.Space.s2) {
            FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                VStack(alignment: .leading, spacing: 0) {
                    Text(shown + (person.personKey == model.personKey ? " (you)" : ""))
                        .jmText(.label)
                        .foregroundStyle(Color.jmFg)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(facts)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if owner && can {
                    JMSelect(
                        label: "Role for \(person.name)", value: person.role,
                        options: [SelectOption(value: "member", label: "Member"), SelectOption(value: "admin", label: "Admin")],
                        onChange: { role in Task { await change(person, role: role) } })
                    .disabled(busy != nil)
                } else {
                    Text(Roles.label(person.role))
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .frame(minHeight: JM.Size.controlSm)
                }
                if can {
                    JMButton(off ? "Turn access back on" : "Turn off access", kind: .ghost) {
                        Task { await change(person, disabled: !off) }
                    }
                    .disabled(busy != nil)
                }
            }
            if person.role != "owner" {
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    ForEach(team.features, id: \.self) { feature in
                        let on = person.features?[feature] == true
                        let label = team.featureLabels?[feature] ?? feature
                        Button {
                            Task { await change(person, features: [feature: !on]) }
                        } label: {
                            HStack(spacing: JM.Space.s1) {
                                CheckBox(on: on)
                                Text(label).jmText(.caption).strikethrough(!on)
                            }
                            .foregroundStyle(on ? Color.jmAccentSoftText : Color.jmFgSecondary)
                            .padding(.horizontal, JM.Space.s3)
                            .padding(.vertical, 4)
                            .background(on ? Color.jmAccentSoft : Color.clear, in: Capsule())
                            .overlay(Capsule().strokeBorder(on ? Color.clear : Color.jmBorder, lineWidth: 1))
                        }
                        .buttonStyle(JMPlainStyle())
                        .accessibilityLabel("\(label) for \(person.name)")
                        .accessibilityAddTraits(on ? .isSelected : [])
                    }
                }
                .disabled(!can || busy != nil)
                .opacity(can ? 1 : JM.disabledOpacity)
            }
        }
        .padding(.bottom, last ? 0 : JM.Space.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) {
            if !last {
                Rectangle().fill(Color.jmBorder).frame(height: 1)
            }
        }
    }

    private func inviteForm(_ team: TeamResponse) -> some View {
        let admins = team.canInviteAdmins == true
        let ready = !name.trimmingCharacters(in: .whitespaces).isEmpty && !email.trimmingCharacters(in: .whitespaces).isEmpty
        return VStack(alignment: .leading, spacing: JM.Space.s2) {
            JMField("Their name", text: $name)
            JMField("Their Google email", text: $email)
                .keyboardType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            JMSelect(
                label: "Role", value: newRole,
                options: [SelectOption(value: "member", label: "Member")]
                    + (admins ? [SelectOption(value: "admin", label: "Admin")] : []),
                onChange: { newRole = $0 })
            .disabled(!admins)
            .opacity(admins ? 1 : JM.disabledOpacity)
            JMButton(busy == "invite" ? "Inviting…" : "Invite", kind: .primary) { Task { await invite() } }
                .disabled(busy != nil || !ready)
            if let notice {
                Text(notice)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.top, JM.Space.s1)
    }

    private func load() async {
        do {
            team = try await model.client.team()
        } catch {
            self.error = model.words(for: error, fallback: "Could not read the team.")
        }
    }

    private func change(_ person: TeamPerson, role: String? = nil, disabled: Bool? = nil, features: [String: Bool]? = nil) async {
        busy = person.personKey
        error = nil
        notice = nil
        defer { busy = nil }
        do {
            let next = try await model.client.patchPerson(key: person.personKey, role: role, disabled: disabled, features: features)
            if let index = team?.people.firstIndex(where: { $0.personKey == next.personKey }) {
                team?.people[index] = next
            }
        } catch {
            self.error = model.words(for: error, fallback: "That change did not save.")
        }
    }

    private func invite() async {
        busy = "invite"
        error = nil
        notice = nil
        defer { busy = nil }
        do {
            let result = try await model.client.invite(
                name: name.trimmingCharacters(in: .whitespaces),
                email: email.trimmingCharacters(in: .whitespaces), role: newRole)
            name = ""
            email = ""
            newRole = "member"
            let address = model.originText
            let mail = result.person.email ?? ""
            if result.emailed {
                notice = "An invitation was emailed to \(mail). They sign in with Google at \(address)."
            } else {
                let why = (result.emailError ?? "").isEmpty ? "." : ": \(result.emailError ?? "")"
                notice = "\(result.person.name) is on the team, but the invitation email was not sent\(why) Send them \(address) and have them sign in with Google using \(mail)."
            }
            await load()
        } catch {
            self.error = model.words(for: error, fallback: "Could not invite them.")
        }
    }
}

// MARK: - Channels

/// Settings → Channels. Owner and admins; the API refuses anyone else.
private struct ChannelsSection: View {
    @EnvironmentObject private var model: AppModel

    @State private var settings: ChannelSettings?
    @State private var busy = false
    @State private var error: String?

    private struct Choice: Identifiable {
        var id: String
        var label: String
        var detail: String
    }

    private static let choices = [
        Choice(
            id: "mention", label: "Only when someone types @jarvis",
            detail: "People talk to each other; Jarvis joins in when it’s called."),
        Choice(id: "every", label: "Every message", detail: "Jarvis answers everything anyone writes in a channel."),
    ]

    var body: some View {
        Group {
            if let settings {
                let locked = busy || settings.canChange != true
                SettingsSection(
                    title: "Channels",
                    description: "When Jarvis answers in a team channel. Your own Ask Jarvis line always answers.",
                    error: error,
                    content: {
                        VStack(spacing: JM.Space.s2) {
                            ForEach(Self.choices) { choice in
                                let on = settings.jarvisReplies == choice.id
                                Button {
                                    Task { await choose(choice.id) }
                                } label: {
                                    HStack(alignment: .top, spacing: JM.Space.s3) {
                                        RadioMark(on: on).padding(.top, 3)
                                        VStack(alignment: .leading, spacing: 0) {
                                            Text(choice.label).jmText(.label).foregroundStyle(Color.jmFg)
                                            Text(choice.detail)
                                                .jmText(.caption)
                                                .foregroundStyle(Color.jmFgSecondary)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                    }
                                    .padding(.horizontal, JM.Space.s3)
                                    .padding(.vertical, JM.Space.s2)
                                    .background(
                                        on ? Color.jmAccentSoft : Color.clear,
                                        in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                                            .strokeBorder(on ? Color.jmAccent : Color.jmBorder, lineWidth: 1))
                                    .contentShape(RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                                }
                                .buttonStyle(JMPlainStyle())
                                .accessibilityAddTraits(on ? .isSelected : [])
                            }
                        }
                        .disabled(locked)
                        .opacity(locked ? JM.disabledOpacity : 1)
                    })
            } else if let error {
                SettingsSection(title: "Channels", error: error)
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            settings = try await model.client.channelSettings()
        } catch {
            self.error = model.words(for: error, fallback: "Could not read the channel settings.")
        }
    }

    private func choose(_ value: String) async {
        guard let settings, value != settings.jarvisReplies else { return }
        busy = true
        error = nil
        defer { busy = false }
        do {
            self.settings = try await model.client.setChannelSettings(jarvisReplies: value)
        } catch {
            self.error = model.words(for: error, fallback: "That setting did not save.")
        }
    }
}

/// A radio button's mark.
struct RadioMark: View {
    var on: Bool

    var body: some View {
        ZStack {
            Circle().strokeBorder(on ? Color.jmAccent : Color.jmBorderStrong, lineWidth: 1)
            if on {
                Circle().fill(Color.jmAccent).frame(width: 7, height: 7)
            }
        }
        .frame(width: 13, height: 13)
        .accessibilityHidden(true)
    }
}
