import SwiftUI
import JarvisKit

/// One conversation: the direct line with Jarvis, or a team channel.
/// Follows frontend/src/screens/Ask.tsx.
struct ChatScreen: View {
    @EnvironmentObject private var model: AppModel

    var thread: ThreadItem
    var people: [PersonChoice]
    /// Settings → Channels: "mention" or "always".
    var jarvisReplies: String
    var onRead: () -> Void
    var onThreadsChanged: () -> Void

    private struct Turn: Equatable {
        var question: String
        var answer: String
        var jarvis: Bool
    }

    private struct Verified: Equatable {
        var ok: Bool
        var message: String
    }

    @State private var messages: [ChatMessage] = []
    @State private var draft = ""
    @State private var streaming = false
    @State private var steps: [ChatStep] = []
    @State private var stepsOpen = true
    @State private var turn: Turn?
    @State private var stoppedKey: String?
    @State private var stopped = false
    @State private var answering: [String] = []
    @State private var error: String?
    @State private var notice: String?
    @State private var pending: [AttachmentItem] = []
    @State private var uploading = false
    @State private var verified: Verified?
    @State private var chatActions: [String] = []
    @State private var members: [ThreadMember]?
    @State private var membersOpen = false
    @State private var membersBusy = false
    @State private var membersError: String?
    /// A message held back until the person allows Ask.
    @State private var consentDraft: String?

    @State private var lastID = 0
    @State private var readUpTo = 0
    @State private var shownElsewhere: Set<Int> = []
    @State private var streamTask: Task<Void, Never>?

    @State private var pinned = true
    @State private var seen = ChatGeometry(bottom: 0, height: 0)
    @State private var seenViewport: CGFloat = 0

    private static let examplePrompts = [
        "Which emails need me today?", "What's on the calendar this week?", "Any warm leads I should chase?",
    ]
    private static let end = "chat-end"

    private var channel: Bool { !thread.direct }
    private var structureOnly: Bool { model.role == "operator" }
    private var canManageMembers: Bool { model.isManager }

    private var hasReply: Bool {
        guard let turn else { return false }
        return messages.contains { $0.localId == turn.answer }
    }

    /// Dots while Jarvis works on this person's turn; in a channel only once
    /// the server has said Jarvis is answering it.
    private var waiting: Bool {
        streaming && turn?.jarvis == true && !hasReply
    }

    private var lastAssistant: Int? {
        messages.lastIndex { $0.isAssistant }
    }

    private var named: [String: PersonChoice] {
        Dictionary(people.map { ($0.personKey, $0) }, uniquingKeysWith: { first, _ in first })
    }

    private func names(of name: String?) -> [String] {
        guard let name, !name.isEmpty else { return [] }
        return [name, Wording.firstName(name)]
    }

    private var mentionNames: MentionNames {
        let known = named
        let inChannel = (members ?? []).flatMap { names(of: known[$0.personKey]?.name) }
        let me = model.personKey.flatMap { known[$0] }
        return MentionNames(names: ["jarvis", "assistant"] + inChannel, own: names(of: me?.name))
    }

    private var mentionables: [Mentionable] {
        guard channel else { return [] }
        let known = named
        let others = (members ?? [])
            .filter { $0.personKey != model.personKey }
            .compactMap { member -> Mentionable? in
                guard let person = known[member.personKey] else { return nil }
                let hint = (person.title ?? "").isEmpty ? nil : person.title
                return Mentionable(id: member.personKey, name: person.name, hint: hint)
            }
        return [Mentionable(id: "jarvis", name: "jarvis", hint: "Ask Jarvis")] + others
    }

    private var placeholder: String {
        if !channel { return "Ask Jarvis…" }
        return jarvisReplies == "mention" ? "Message \(thread.name) · @jarvis to ask Jarvis" : "Message \(thread.name)"
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            transcript
            if !structureOnly {
                Composer(
                    text: $draft,
                    placeholder: placeholder,
                    label: channel ? "Message \(thread.name)" : "Message Jarvis",
                    attachments: pending,
                    uploading: uploading,
                    streaming: streaming,
                    mentionables: mentionables,
                    onSubmit: send,
                    onStop: stop,
                    onPick: { file in Task { await upload(file) } },
                    onProblem: { error = $0 },
                    onRemove: { removed in pending.removeAll { $0.id == removed.id } }
                )
                .frame(maxWidth: JM.Size.chat)
                .padding(.horizontal, JM.Space.s4)
                .padding(.top, JM.Space.s2)
                .padding(.bottom, JM.Space.s4)
                .frame(maxWidth: .infinity)
            }
        }
        .background(Color.jmBg.ignoresSafeArea())
        .jmSheet(isPresented: membersOpen, onClose: closeMembers) {
            MembersSheet(
                channelName: thread.name,
                members: (members ?? []).map(\.personKey),
                people: people,
                personKey: model.personKey,
                canManage: canManageMembers,
                busy: membersBusy,
                error: membersError,
                onAdd: { key in
                    Task {
                        await changeMembers("Could not add them.") {
                            try await model.client.addThreadMember(id: thread.threadId, personKey: key)
                        }
                    }
                },
                onRemove: { key in
                    Task {
                        await changeMembers("Could not remove them.") {
                            try await model.client.removeThreadMember(id: thread.threadId, personKey: key)
                        }
                    }
                },
                onClose: closeMembers)
        }
        .jmSheet(isPresented: consentDraft != nil, onClose: { consentDraft = nil }) {
            ConsentSheet(
                onAllow: {
                    let held = consentDraft ?? ""
                    consentDraft = nil
                    model.consent = true
                    send(held)
                },
                onClose: { consentDraft = nil })
        }
        .task { await load() }
        .task {
            guard channel, !structureOnly else { return }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                if Task.isCancelled { break }
                await poll()
            }
        }
        .onChange(of: messages) { _, _ in markRead() }
        .onDisappear { streamTask?.cancel() }
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: JM.Space.s2) {
            BackButton(label: "Back to conversations")
            Text(thread.name)
                .jmText(.heading)
                .foregroundStyle(Color.jmFg)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            if channel {
                JMButton(
                    members.map { "\($0.count)" } ?? "—", icon: .members, kind: .ghost, size: .sm,
                    label: "Members of \(thread.name)"
                ) {
                    membersOpen = true
                }
            }
        }
        .padding(.horizontal, JM.Space.s4)
        .frame(minHeight: 56)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Color.jmBorder).frame(height: 1)
        }
    }

    // MARK: Transcript

    private var transcript: some View {
        GeometryReader { outer in
            let column = min(outer.size.width, JM.Size.chat) - JM.Space.s4 * 2
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: JM.Space.s5) {
                        conversation(width: column)
                        if let error {
                            SystemLine(tone: .error, text: error)
                        }
                        if let notice {
                            SystemLine(text: notice)
                        }
                        if let verified {
                            SystemLine(tone: verified.ok ? .done : .error, text: verified.message)
                        }
                        if lastAssistant == nil {
                            SavedActions(saved: [], live: chatActions)
                        }
                    }
                    .padding(.horizontal, JM.Space.s4)
                    .padding(.vertical, JM.Space.s6)
                    .frame(maxWidth: JM.Size.chat, alignment: .leading)
                    .frame(maxWidth: .infinity)
                    .background(GeometryReader { inner in
                        let frame = inner.frame(in: .named("chat"))
                        Color.clear.preference(
                            key: ChatGeometryKey.self, value: ChatGeometry(bottom: frame.maxY, height: frame.height))
                    })
                    Color.clear.frame(height: 1).id(Self.end)
                }
                .coordinateSpace(name: "chat")
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
                .onPreferenceChange(ChatGeometryKey.self) { next in
                    follow(next, viewport: outer.size.height, proxy: proxy)
                }
            }
        }
    }

    /// Follows the bottom whenever the transcript grows, unless the person
    /// has scrolled up to read. A change in height is growth; a change in
    /// position alone is the person scrolling.
    private func follow(_ next: ChatGeometry, viewport: CGFloat, proxy: ScrollViewProxy) {
        if next.height != seen.height || viewport != seenViewport {
            if pinned { proxy.scrollTo(Self.end, anchor: .bottom) }
        } else {
            pinned = next.bottom - viewport < 48
        }
        seen = next
        seenViewport = viewport
    }

    @ViewBuilder
    private func conversation(width: CGFloat) -> some View {
        if structureOnly {
            SystemLine(
                text: "Operator access shows this channel and who is in it — not what was said in it.",
                detail: "Reading messages needs the account owner to sign in. If that is genuinely required, reset their PIN with them on the call.",
                actionLabel: channel ? "See who is in this channel" : nil,
                action: { membersOpen = true })
        } else if messages.isEmpty && !streaming && channel {
            VStack(alignment: .leading, spacing: JM.Space.s2) {
                Text("This is the start of \(thread.name).")
                    .jmText(.title)
                    .foregroundStyle(Color.jmFg)
                    .fixedSize(horizontal: false, vertical: true)
                Text(
                    "Everyone in this channel reads what is written here. "
                        + (jarvisReplies == "mention"
                            ? "Type @jarvis when you want Jarvis to answer." : "Jarvis answers every message here.")
                )
                .jmText(.body)
                .foregroundStyle(Color.jmFgSecondary)
                .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.vertical, JM.Space.s8)
        } else if messages.isEmpty && !streaming {
            VStack(alignment: .leading, spacing: JM.Space.s4) {
                Text("Ask Jarvis anything about your leads, email, or schedule.")
                    .jmText(.title)
                    .foregroundStyle(Color.jmFg)
                    .fixedSize(horizontal: false, vertical: true)
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    ForEach(Self.examplePrompts, id: \.self) { prompt in
                        JMButton(prompt) { send(prompt) }
                    }
                }
            }
            .padding(.vertical, JM.Space.s8)
        } else {
            let names = mentionNames
            let last = lastAssistant
            ForEach(Array(messages.enumerated()), id: \.element.id) { index, message in
                let live = turn != nil && message.localId == turn?.answer
                VStack(alignment: .leading, spacing: JM.Space.s3) {
                    if live && !steps.isEmpty {
                        stepList(width: width)
                    }
                    if message.isAssistant {
                        let wasStopped = stoppedKey != nil && message.localId == stoppedKey
                        MessageBubble(
                            mine: false,
                            text: message.content,
                            partial: message.partial == true || wasStopped,
                            partialLabel: wasStopped ? "You stopped this answer." : nil,
                            from: channel ? "Jarvis" : nil,
                            width: width)
                        SavedActions(saved: message.actions ?? [], live: index == last ? chatActions : [])
                    } else {
                        let mine = !channel || message.author == model.personKey
                        let author = (message.authorName ?? "").isEmpty ? "Earlier message" : (message.authorName ?? "")
                        MessageBubble(
                            mine: mine,
                            text: message.content,
                            attachments: message.attachments ?? [],
                            from: mine ? nil : author,
                            mentions: channel ? names : nil,
                            width: width)
                    }
                }
            }
            if waiting {
                if steps.isEmpty {
                    MessageBubble(mine: false, typing: true, width: width)
                } else {
                    stepList(width: width)
                }
            }
            if !streaming && !waiting && turn != nil && !hasReply && !steps.isEmpty {
                stepList(width: width)
            }
            if channel && !streaming && !answering.isEmpty {
                MessageBubble(
                    mine: false, typing: true,
                    typingLabel: "Jarvis is answering \(answering.joined(separator: " and "))",
                    from: "Jarvis", width: width)
            }
        }
    }

    private func stepList(width: CGFloat) -> some View {
        StepList(steps: steps, open: stepsOpen, onToggle: { stepsOpen.toggle() })
            .frame(maxWidth: width * JM.Size.bubble, alignment: .leading)
    }

    // MARK: Loading

    private func load() async {
        if channel {
            do {
                members = try await model.client.threadMembers(id: thread.threadId)
            } catch {
                _ = model.words(for: error, fallback: "")
                members = []
            }
        }
        // The one thing an operator does not fetch.
        if structureOnly { return }
        do {
            let history = try await model.client.chatHistory(threadID: thread.threadId)
            lastID = history.lastId ?? 0
            answering = history.jarvisAnswering ?? []
            // A turn started before the history arrived stays on screen.
            messages = Self.merge(history.messages.filter { $0.serverId != nil }, into: messages)
        } catch {
            // An empty thread is the normal case; only a 401 matters here.
            _ = model.words(for: error, fallback: "")
        }
    }

    /// Channels: what the others wrote, every few seconds. While this
    /// person's own turn streams, its reply and their own message come
    /// through the stream; the poll still brings everyone else's.
    private func poll() async {
        do {
            let history = try await model.client.chatHistory(threadID: thread.threadId, after: lastID)
            let fresh = history.messages.filter { message in
                guard let id = message.serverId, !shownElsewhere.contains(id) else { return false }
                return !streaming || (message.role == "user" && message.author != model.personKey)
            }
            if !streaming { lastID = max(lastID, history.lastId ?? 0) }
            answering = history.jarvisAnswering ?? []
            if !fresh.isEmpty { messages = Self.merge(fresh, into: messages) }
        } catch {
            _ = model.words(for: error, fallback: "")
        }
    }

    /// Server rows into the list by id: new ones in id order, nothing twice,
    /// and what this screen is still sending (no id yet) left where it is.
    private static func merge(_ incoming: [ChatMessage], into current: [ChatMessage]) -> [ChatMessage] {
        let have = Set(current.compactMap(\.serverId))
        let add = incoming
            .filter { $0.serverId.map { !have.contains($0) } ?? false }
            .sorted { ($0.serverId ?? 0) < ($1.serverId ?? 0) }
        if add.isEmpty { return current }
        var out = current
        for message in add {
            let id = message.serverId ?? 0
            if let at = out.firstIndex(where: { ($0.serverId ?? Int.min) > id }) {
                out.insert(message, at: at)
            } else {
                out.append(message)
            }
        }
        return out
    }

    private func markRead() {
        if structureOnly { return }
        let newest = messages.compactMap(\.serverId).max() ?? 0
        guard newest > readUpTo else { return }
        readUpTo = newest
        Task {
            do {
                try await model.client.markThreadRead(id: thread.threadId, lastID: newest)
                onRead()
            } catch {
                _ = model.words(for: error, fallback: "")
            }
        }
    }

    // MARK: Members

    private func closeMembers() {
        membersOpen = false
        membersError = nil
    }

    private func changeMembers(_ failure: String, _ change: () async throws -> [ThreadMember]) async {
        membersBusy = true
        membersError = nil
        defer { membersBusy = false }
        do {
            members = try await change()
            onThreadsChanged()
        } catch {
            membersError = model.words(for: error, fallback: failure)
        }
    }

    // MARK: Sending

    private func send(_ text: String) {
        let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        // An attachment with no caption is a legitimate turn.
        if (message.isEmpty && pending.isEmpty) || streaming { return }
        guard model.consent else {
            consentDraft = message
            return
        }
        streamTask = Task { await run(message) }
    }

    private func stop() {
        stopped = true
        streamTask?.cancel()
    }

    private func settle(_ status: ChatStep.Status) {
        for index in steps.indices where steps[index].status == .running {
            steps[index].status = status
        }
    }

    private func run(_ message: String) async {
        let sending = pending
        error = nil
        notice = nil
        draft = ""
        pending = []
        streaming = true
        steps = []
        stepsOpen = true
        stoppedKey = nil
        stopped = false
        pinned = true
        verified = nil
        chatActions = []

        // This turn's question and reply, by handle. In a channel Jarvis may
        // not be asked at all; the server says so in its first line.
        let stamp = Int(Date().timeIntervalSince1970 * 1000)
        let question = "q-\(stamp)"
        let answer = "a-\(stamp)"
        messages.append(ChatMessage(
            localId: question, role: "user", content: message, author: model.personKey,
            ts: Date().timeIntervalSince1970, attachments: sending))
        turn = Turn(question: question, answer: answer, jarvis: !channel)

        var assistant = ""
        func showAssistant() {
            if let index = messages.firstIndex(where: { $0.localId == answer }) {
                messages[index].content = assistant
                return
            }
            // Right under its question, even if someone else wrote since.
            let reply = ChatMessage(localId: answer, role: "assistant", content: assistant, ts: Date().timeIntervalSince1970)
            if let at = messages.firstIndex(where: { $0.localId == question }) {
                messages.insert(reply, at: at + 1)
            } else {
                messages.append(reply)
            }
        }
        func afterStop() {
            settle(.error)
            if assistant.isEmpty {
                notice = "You stopped this answer."
            } else {
                stoppedKey = answer
            }
        }

        do {
            let events = model.client.streamChat(
                threadID: thread.threadId, message: message, attachmentIDs: sending.map(\.id))
            for try await event in events {
                switch event {
                case .delta(let text):
                    assistant += text
                    showAssistant()
                case .tool(let running, let tool, let label, let callID):
                    if running {
                        let id = callID ?? "step-\(Int(Date().timeIntervalSince1970 * 1000))"
                        // The server already turns the agent's tool into a plain step.
                        let own = tool == "jarvis"
                        let name = own && !(label ?? "").isEmpty ? (label ?? "") : StepWording.name(tool)
                        let detail = own ? "" : StepWording.truncate(label)
                        steps.removeAll { $0.id == id }
                        steps.append(ChatStep(id: id, label: name, detail: detail.isEmpty ? nil : detail, status: .running))
                    } else {
                        let index = callID != nil
                            ? steps.firstIndex { $0.id == callID }
                            : steps.lastIndex { $0.status == .running }
                        if let index { steps[index].status = .done }
                    }
                case .verified(let ok, let words):
                    verified = Verified(ok: ok, message: words)
                case .action(let action):
                    if SavedActions.labels[action] != nil && !chatActions.contains(action) {
                        chatActions.append(action)
                    }
                case .done:
                    settle(.done)
                    stepsOpen = false
                case .error(let words, _):
                    error = words
                    settle(.error)
                    if !assistant.isEmpty { showAssistant() }
                case .notice(let words):
                    notice = words
                case .posted(let saved, let jarvis):
                    // Channels: the message is saved and the others can see it.
                    if let saved, let index = messages.firstIndex(where: { $0.localId == question }) {
                        messages[index].serverId = saved.serverId
                        messages[index].author = saved.author
                        messages[index].authorName = saved.authorName
                    }
                    if turn?.question == question { turn?.jarvis = jarvis }
                case .saved(let ids):
                    // The rows this turn wrote: the reply gets its id, and a
                    // receipt (already on screen as the verified line) is not
                    // merged in again.
                    if let first = ids.first, let index = messages.firstIndex(where: { $0.localId == answer }) {
                        messages[index].serverId = first
                    }
                    shownElsewhere.formUnion(ids.dropFirst())
                case .other:
                    break
                }
            }
            if stopped { afterStop() }
        } catch {
            if stopped {
                afterStop()
            } else if let known = error as? JarvisError {
                if known.isUnauthorized {
                    _ = model.words(for: known, fallback: "")
                } else if known == .cancelled {
                    // The screen went away mid-answer: nothing to say.
                    settle(.error)
                } else {
                    settle(.error)
                    self.error = "Something went wrong sending that."
                    restore(message, sending)
                }
            } else {
                settle(.error)
                self.error = "Can't reach Jarvis right now."
                restore(message, sending)
            }
        }
        streaming = false
        streamTask = nil
        // Whatever the poll held back while this turn ran comes in now.
        if channel { await poll() }
    }

    /// Puts the words and the attachments back so a retry does not lose them.
    private func restore(_ message: String, _ sending: [AttachmentItem]) {
        draft = message
        pending = sending
    }

    private func upload(_ file: PickedFile) async {
        error = nil
        notice = nil
        uploading = true
        defer { uploading = false }
        do {
            let stored = try await model.client.uploadAttachment(
                threadID: thread.threadId, filename: file.filename, mime: file.mime, data: file.data)
            pending.append(stored)
        } catch {
            // Server wording is written to be shown as it is. Never a silent drop.
            if let words = model.words(for: error, fallback: "\(file.filename) could not be uploaded.") {
                self.error = words
            }
        }
    }
}

private struct ChatGeometry: Equatable {
    var bottom: CGFloat
    var height: CGFloat
}

private struct ChatGeometryKey: PreferenceKey {
    static let defaultValue = ChatGeometry(bottom: 0, height: 0)
    static func reduce(value: inout ChatGeometry, nextValue: () -> ChatGeometry) {
        value = nextValue()
    }
}

// MARK: - MessageBubble (jm/MessageBubble)

/// Names an @ can reach in a channel, and which of them are the reader's.
struct MentionNames {
    var names: [String]
    var own: [String]
}

struct MessageBubble: View {
    /// On the right, in the blue bubble: the reader's own message.
    var mine: Bool
    var text: String?
    var attachments: [AttachmentItem] = []
    /// Dots, before the first word.
    var typing = false
    var typingLabel: String?
    /// The turn ended before the model finished.
    var partial = false
    var partialLabel: String?
    /// Channels: who wrote it, above the bubble.
    var from: String?
    var mentions: MentionNames?
    /// The width of the column; a bubble takes at most 80% of it.
    var width: CGFloat

    private var limit: CGFloat { max(0, width * JM.Size.bubble) }
    private var side: Alignment { mine ? .trailing : .leading }

    private var shape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: JM.Radius.xl,
            bottomLeadingRadius: mine ? JM.Radius.xl : JM.Radius.tail,
            bottomTrailingRadius: mine ? JM.Radius.tail : JM.Radius.xl,
            topTrailingRadius: JM.Radius.xl,
            style: .continuous)
    }

    /// The words, with each @name marked. Inside the reader's own blue
    /// bubble a tinted chip would vanish: weight and an underline instead.
    private func marked(_ text: String, _ mentions: MentionNames) -> AttributedString {
        var out = AttributedString()
        for run in Mentions.runs(text, names: mentions.names + mentions.own, selfNames: mentions.own) {
            var part = AttributedString(run.text)
            if run.isMention {
                if mine {
                    part.swiftUI.font = JMTextStyle.body.font(.semibold)
                    part.swiftUI.underlineStyle = Text.LineStyle.single
                } else if run.isSelf {
                    part.swiftUI.backgroundColor = Color.jmAccent
                    part.swiftUI.foregroundColor = Color.jmOnAccent
                } else {
                    part.swiftUI.backgroundColor = Color.jmAccentSoft
                    part.swiftUI.foregroundColor = Color.jmAccentSoftText
                }
            }
            out += part
        }
        return out
    }

    var body: some View {
        VStack(alignment: mine ? .trailing : .leading, spacing: JM.Space.s2) {
            if let from {
                Text(from)
                    .jmText(.caption, weight: .semibold)
                    .foregroundStyle(Color.jmFgSecondary)
                    .padding(.horizontal, JM.Space.s1)
                    .padding(.bottom, -JM.Space.s1)
            }
            if !attachments.isEmpty {
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    ForEach(attachments) { attachment in
                        AttachmentChip(attachment: attachment)
                    }
                }
                .frame(maxWidth: limit, alignment: side)
            }
            if !mine && typing {
                Dots()
                    .padding(.horizontal, JM.Space.s4)
                    .frame(minHeight: 46)
                    .background(Color.jmPanel, in: shape)
                    .accessibilityLabel(typingLabel ?? "Jarvis is thinking")
            } else if let text, !text.isEmpty {
                Group {
                    if let mentions {
                        Text(marked(text, mentions))
                    } else {
                        Text(text)
                    }
                }
                .jmText(.body)
                .foregroundStyle(mine ? Color.jmOnAccent : Color.jmFg)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, JM.Space.s4)
                .padding(.vertical, JM.Space.s3)
                .background(mine ? Color.jmAccent : Color.jmPanel, in: shape)
                .frame(maxWidth: limit, alignment: side)
            }
            if partial {
                Text(partialLabel ?? "This answer was cut short.")
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .padding(.horizontal, JM.Space.s1)
            }
        }
        .frame(maxWidth: .infinity, alignment: side)
    }
}

/// The app's own buttons under a reply. Only a known action draws, and where
/// it goes is fixed here, never taken from the model (components/ChatMessage.tsx).
struct SavedActions: View {
    @EnvironmentObject private var model: AppModel

    var saved: [SavedChatAction]
    var live: [String]

    static let labels = ["connect_google": "Connect your Google account"]
    static let targets = ["connect_google": "/api/google/connect/start"]

    private var known: [String] {
        var seen: [String] = []
        for action in live + saved.map(\.action) where Self.labels[action] != nil && !seen.contains(action) {
            seen.append(action)
        }
        return seen
    }

    var body: some View {
        if !known.isEmpty {
            FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                ForEach(known, id: \.self) { action in
                    JMButton(Self.labels[action] ?? "", kind: .primary) {
                        if let path = Self.targets[action], let url = model.client.webURL(path) {
                            Platform.open(url)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - AttachmentChip (jm/shared/primitives.tsx)

struct AttachmentChip: View {
    @EnvironmentObject private var model: AppModel

    var attachment: AttachmentItem
    var onRemove: (() -> Void)?

    @State private var data: Data?
    @State private var preview: URL?
    @State private var opening = false

    private var meta: String {
        [attachment.sizeHuman, attachment.readable == false ? "saved, not readable yet" : nil]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    var body: some View {
        Button {
            Task { await open() }
        } label: {
            if attachment.isImage == true {
                ZStack {
                    Color.jmPanel
                    if let data {
                        DataImage(data: data)
                    }
                }
                .frame(width: JM.Size.thumb, height: JM.Size.thumb)
                .clipShape(RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                        .strokeBorder(Color.jmBorder, lineWidth: 1))
                .accessibilityLabel(attachment.filename)
            } else {
                HugLayout(maxWidth: 280) {
                    HStack(spacing: JM.Space.s2) {
                        Icon(.file, size: 18).foregroundStyle(Color.jmFgSecondary)
                        VStack(alignment: .leading, spacing: 0) {
                            Text(attachment.filename).jmText(.bodySm).foregroundStyle(Color.jmFg).lineLimit(1)
                            if !meta.isEmpty {
                                Text(meta).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                            }
                        }
                    }
                    .padding(.horizontal, JM.Space.s3)
                    .padding(.vertical, JM.Space.s2)
                    .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                            .strokeBorder(Color.jmBorder, lineWidth: 1))
                }
            }
        }
        .buttonStyle(JMPlainStyle())
        .disabled(opening)
        .overlay(alignment: .topTrailing) {
            if let onRemove {
                Button(action: onRemove) {
                    Icon(.close, size: 12)
                        .foregroundStyle(Color.jmOnFg)
                        .frame(width: 20, height: 20)
                        .background(Color.jmFg, in: Circle())
                }
                .buttonStyle(JMPlainStyle())
                .offset(x: 6, y: -6)
                .accessibilityLabel("Remove \(attachment.filename)")
            }
        }
        .task(id: attachment.id) {
            guard attachment.isImage == true, data == nil else { return }
            data = try? await model.client.attachmentData(id: attachment.id)
        }
        .jmFilePreview($preview)
    }

    /// The web opens an attachment in a new tab; the phone reads it with
    /// this session and shows it full screen.
    private func open() async {
        opening = true
        defer { opening = false }
        var bytes = data
        if bytes == nil {
            bytes = try? await model.client.attachmentData(id: attachment.id)
        }
        guard let bytes else { return }
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("jarvis-\(attachment.id)", isDirectory: true)
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let name = attachment.filename.replacingOccurrences(of: "/", with: "-")
        let file = folder.appendingPathComponent(name.isEmpty ? "attachment" : name)
        do {
            try bytes.write(to: file, options: .atomic)
            preview = file
        } catch {
            preview = nil
        }
    }
}

// MARK: - StepList (jm/StepList)

struct ChatStep: Identifiable, Equatable {
    enum Status: Equatable {
        case waiting, running, done, error
    }

    var id: String
    var label: String
    var detail: String?
    var status: Status = .waiting
}

/// What Jarvis is doing for this turn, above its reply.
struct StepList: View {
    var steps: [ChatStep]
    var open: Bool
    var onToggle: () -> Void

    private var running: Bool { steps.contains { $0.status == .running } }
    private var failed: Bool { steps.contains { $0.status == .error } }

    private var heading: String {
        if running { return "Working" }
        if failed { return "Stopped" }
        let done = steps.filter { $0.status == .done }.count
        return "Done · \(done) \(done == 1 ? "step" : "steps")"
    }

    private static func spoken(_ status: ChatStep.Status) -> String {
        switch status {
        case .done: return "done"
        case .running: return "in progress"
        case .error: return "failed"
        case .waiting: return "waiting"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s3) {
            Button(action: onToggle) {
                HStack(spacing: JM.Space.s2) {
                    if running {
                        Dots()
                    } else if failed {
                        Icon(.alert, size: 16).foregroundStyle(Color.jmError)
                    } else {
                        Tick()
                    }
                    Text(heading)
                        .jmText(.label)
                        .foregroundStyle(Color.jmFg)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Icon(.chevron, size: 16)
                        .foregroundStyle(Color.jmFgSecondary)
                        .rotationEffect(.degrees(open ? 90 : 0))
                        .animation(JM.Motion.easeOut(JM.Motion.press), value: open)
                }
            }
            .buttonStyle(JMPlainStyle())
            .accessibilityValue(open ? "Expanded" : "Collapsed")

            if open {
                VStack(alignment: .leading, spacing: JM.Space.s2) {
                    ForEach(steps) { step in
                        HStack(alignment: .top, spacing: JM.Space.s3) {
                            mark(step.status)
                                .frame(width: 16, height: 20)
                                .accessibilityHidden(true)
                            VStack(alignment: .leading, spacing: 0) {
                                Text(step.label)
                                    .jmText(.bodySm)
                                    .foregroundStyle(
                                        step.status == .done || step.status == .waiting ? Color.jmFgSecondary : Color.jmFg)
                                    .fixedSize(horizontal: false, vertical: true)
                                if let detail = step.detail {
                                    Text(detail).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityValue(Self.spoken(step.status))
                    }
                }
            }
        }
        .padding(.horizontal, JM.Space.s4)
        .padding(.vertical, JM.Space.s3)
        .overlay(
            RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous)
                .strokeBorder(Color.jmBorder, lineWidth: 1))
    }

    @ViewBuilder
    private func mark(_ status: ChatStep.Status) -> some View {
        switch status {
        case .done:
            Tick()
        case .error:
            Icon(.alert, size: 16).foregroundStyle(Color.jmError)
        case .running:
            LiveDot()
        case .waiting:
            Circle().strokeBorder(Color.jmBorderStrong, lineWidth: 1.5).frame(width: 10, height: 10)
        }
    }
}

// MARK: - Composer (jm/Composer)

struct Mentionable: Identifiable, Equatable {
    var id: String
    /// What gets typed after the @.
    var name: String
    /// A second line: a title, or what picking it does.
    var hint: String?
}

struct Composer: View {
    @Binding var text: String
    var placeholder: String
    var label: String
    var attachments: [AttachmentItem]
    var uploading: Bool
    /// While a reply streams the send button becomes stop.
    var streaming: Bool
    /// Channels: who an @ can reach. Typing @ opens the list.
    var mentionables: [Mentionable]
    var onSubmit: (String) -> Void
    var onStop: () -> Void
    var onPick: (PickedFile) -> Void
    var onProblem: (String) -> Void
    var onRemove: (AttachmentItem) -> Void

    @FocusState private var focused: Bool

    private var canSend: Bool {
        !uploading && !streaming
            && (!text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !attachments.isEmpty)
    }

    private var options: [Mentionable] {
        guard !mentionables.isEmpty, focused, let query = Mentions.query(in: text) else { return [] }
        return Mentions.matches(query, in: mentionables) { $0.name }
    }

    private var box: RoundedRectangle {
        RoundedRectangle(cornerRadius: JM.Radius.composer, style: .continuous)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s2) {
            if !attachments.isEmpty || uploading {
                FlowLayout(spacing: JM.Space.s3, lineSpacing: JM.Space.s3) {
                    ForEach(attachments) { attachment in
                        AttachmentChip(attachment: attachment, onRemove: { onRemove(attachment) })
                    }
                    if uploading {
                        HStack(spacing: JM.Space.s2) {
                            Dots()
                            Text("Uploading…").jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                        }
                        .frame(minHeight: 20)
                    }
                }
                .padding(.horizontal, JM.Space.s2)
                .padding(.top, JM.Space.s2)
            }
            HStack(spacing: JM.Space.s1) {
                AttachmentPicker(disabled: streaming || uploading, onPick: onPick, onProblem: onProblem) {
                    Icon(.plus, size: 18)
                        .foregroundStyle(Color.jmFgSecondary)
                        .frame(width: JM.Size.control, height: JM.Size.control)
                        .contentShape(Circle())
                        .opacity(streaming || uploading ? JM.disabledOpacity : 1)
                }
                TextField("", text: $text, prompt: Text(placeholder).foregroundColor(.jmFgSecondary))
                    .font(JMTextStyle.body.font())
                    .foregroundStyle(Color.jmFg)
                    .focused($focused)
                    .submitLabel(.send)
                    .onSubmit(submit)
                    .disabled(streaming)
                    .padding(.horizontal, JM.Space.s2)
                    .frame(height: JM.Size.control)
                    .frame(maxWidth: .infinity)
                    .accessibilityLabel(label)
                if streaming {
                    JMButton(icon: .stop, kind: .primary, label: "Stop the answer", action: onStop)
                } else {
                    JMButton(icon: .send, kind: .primary, label: "Send", action: submit)
                        .disabled(!canSend)
                }
            }
        }
        .padding(JM.Space.s2)
        .frame(minHeight: JM.Size.composer)
        .background(Color.jmPanel, in: box)
        .overlay(box.strokeBorder(focused ? Color.jmAccent : Color.jmBorder, lineWidth: 1))
        .animation(JM.Motion.easeOut(JM.Motion.press), value: focused)
        .overlay(alignment: .topLeading) {
            if !options.isEmpty {
                mentionList
                    .alignmentGuide(.top) { $0[.bottom] + JM.Space.s2 }
            }
        }
    }

    private var mentionList: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(options.enumerated()), id: \.element.id) { index, option in
                Button {
                    text = Mentions.complete(text, with: option.name)
                } label: {
                    VStack(alignment: .leading, spacing: 0) {
                        Text("@\(option.name)")
                            .jmText(.label)
                            .foregroundStyle(index == 0 ? Color.jmAccentSoftText : Color.jmFg)
                        if let hint = option.hint {
                            Text(hint).jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                        }
                    }
                    .padding(.horizontal, JM.Space.s3)
                    .padding(.vertical, JM.Space.s2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        index == 0 ? Color.jmAccentSoft : Color.clear,
                        in: RoundedRectangle(cornerRadius: JM.Radius.sm, style: .continuous))
                }
                .buttonStyle(JMPlainStyle())
            }
        }
        .padding(JM.Space.s1)
        .frame(maxWidth: 320)
        .background(Color.jmPanelRaised, in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                .strokeBorder(Color.jmBorder, lineWidth: 1))
        .jmFloatShadow()
        .accessibilityLabel("People to mention")
    }

    private func submit() {
        // Return picks the first name while the list is open, as on the web.
        if let first = options.first {
            text = Mentions.complete(text, with: first.name)
            return
        }
        if canSend { onSubmit(text.trimmingCharacters(in: .whitespacesAndNewlines)) }
    }
}

// MARK: - MembersSheet (jm/MembersSheet)

struct MembersSheet: View {
    var channelName: String
    /// Person keys in the channel.
    var members: [String]
    var people: [PersonChoice]
    var personKey: String?
    /// Owner or admin.
    var canManage: Bool
    var busy: Bool
    var error: String?
    var onAdd: (String) -> Void
    var onRemove: (String) -> Void
    var onClose: () -> Void

    @State private var adding = false

    private func name(of key: String) -> String {
        people.first { $0.personKey == key }?.name ?? key
    }

    private var candidates: [PersonChoice] {
        people.filter { !members.contains($0.personKey) }
    }

    var body: some View {
        JMSheet(title: "Members", subtitle: channelName, onClose: onClose) {
            VStack(alignment: .leading, spacing: JM.Space.s1) {
                if members.isEmpty {
                    Text("Nobody yet.").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                }
                ForEach(members, id: \.self) { key in
                    HStack(spacing: JM.Space.s3) {
                        Avatar(name: name(of: key))
                        HStack(spacing: JM.Space.s2) {
                            Text(name(of: key)).jmText(.label).foregroundStyle(Color.jmFg).lineLimit(1)
                            if key == personKey {
                                Text("you").jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        // Nobody removes themselves: an admin who did would lose the channel.
                        if canManage && key != personKey {
                            JMButton("Remove", kind: .ghost, size: .sm) { onRemove(key) }
                                .disabled(busy)
                        }
                    }
                    .frame(minHeight: JM.Size.touch)
                }
            }

            if canManage && !candidates.isEmpty {
                if !adding {
                    JMButton("Add someone", kind: .primary, wide: true, tall: true) { adding = true }
                } else {
                    VStack(alignment: .leading, spacing: JM.Space.s3) {
                        Text("They will see this channel’s full history, including everything said before they joined.")
                            .jmText(.bodySm)
                            .foregroundStyle(Color.jmFgSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        VStack(spacing: JM.Space.s1) {
                            ForEach(candidates) { person in
                                Button {
                                    onAdd(person.personKey)
                                } label: {
                                    HStack(spacing: JM.Space.s3) {
                                        Avatar(name: person.name, outline: true)
                                        VStack(alignment: .leading, spacing: 0) {
                                            Text(person.name).jmText(.label).foregroundStyle(Color.jmFg).lineLimit(1)
                                            if let title = person.title, !title.isEmpty {
                                                Text(title).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                                            }
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        Icon(.plus, size: 18).foregroundStyle(Color.jmFgSecondary)
                                    }
                                    .padding(.horizontal, JM.Space.s3)
                                    .padding(.vertical, JM.Space.s2)
                                    .frame(minHeight: JM.Size.touch)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                                            .strokeBorder(Color.jmBorder, lineWidth: 1))
                                    .contentShape(RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                                }
                                .buttonStyle(JMPlainStyle())
                                .disabled(busy)
                                .opacity(busy ? JM.disabledOpacity : 1)
                                .accessibilityLabel("Add \(person.name) to the channel")
                            }
                        }
                    }
                }
            }

            if !canManage {
                Text("Only an owner or admin can change who is in this channel.")
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let error, !error.isEmpty {
                ErrorLine(error)
            }
        }
    }
}

// MARK: - Consent (phone only)

/// The App Store asks for a clear yes before a message goes to an AI
/// service. The web has no such step; this sheet is the phone's.
struct ConsentSheet: View {
    var onAllow: () -> Void
    var onClose: () -> Void

    var body: some View {
        JMSheet(title: "Before you send", onClose: onClose) {
            VStack(alignment: .leading, spacing: JM.Space.s3) {
                Text(AIDisclosure.text)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                JMButton("Allow and send", kind: .primary, wide: true, tall: true, action: onAllow)
                JMButton("Not now", kind: .ghost, wide: true, action: onClose)
            }
        }
    }
}
