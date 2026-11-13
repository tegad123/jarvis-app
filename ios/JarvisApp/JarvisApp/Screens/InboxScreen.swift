import SwiftUI
import JarvisKit

/// The log of Jarvis's own inbox. Follows frontend/src/screens/Inbox.tsx.
/// The server answers owners and admins only; this draws what it returns.
struct InboxScreen: View {
    @EnvironmentObject private var model: AppModel

    @State private var data: InboxResponse?
    @State private var problem: LoadProblem?

    private var messages: [InboxMessage] { data?.messages ?? [] }

    private var subtitle: String {
        guard let mailbox = data?.mailbox, !mailbox.isEmpty else { return "Mail sent to Jarvis" }
        let waiting = data?.waiting ?? 0
        return waiting > 0 ? "\(mailbox) · \(waiting) unread" : mailbox
    }

    var body: some View {
        Page(title: "Inbox", subtitle: subtitle) {
            if let problem {
                ErrorState(
                    label: "Inbox unavailable", message: problem.message, detail: problem.detail,
                    stale: !messages.isEmpty, onRetry: { Task { await load() } })
            }

            if let data, data.needsConnect {
                EmptyState(
                    icon: .inbox,
                    text: data.notice ?? "Jarvis's mailbox is not connected, so this list is empty.",
                    actionLabel: "Connect Jarvis's mailbox", actionPrimary: true,
                    action: {
                        if let url = model.client.webURL("/api/google/assistant/connect/start") {
                            Platform.open(url)
                        }
                    })
            }

            if let data, data.status == "unavailable", problem == nil {
                EmptyState(icon: .inbox, text: data.notice ?? "Jarvis could not read his inbox just now.")
            }

            if let data, data.status == "connected", messages.isEmpty, problem == nil {
                EmptyState(icon: .inbox, text: "Nothing is in Jarvis's inbox.")
            }

            if !messages.isEmpty {
                JMList(items: messages) { message in
                    InboxRow(message: message)
                }
            }
        }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(nanoseconds: 30_000_000_000)
            }
        }
    }

    private func load() async {
        do {
            data = try await model.client.inbox()
            problem = nil
        } catch {
            problem = model.problem(for: error, message: "Jarvis could not read his inbox.")
        }
    }
}

private struct InboxRow: View {
    var message: InboxMessage

    private var unread: Bool { message.unread == true }

    var body: some View {
        let when = JarvisTime.relative(message.date)
        VStack(alignment: .leading, spacing: JM.Space.s1) {
            HStack(spacing: JM.Space.s2) {
                if unread {
                    Text("Unread")
                        .jmText(.caption)
                        .foregroundStyle(Color.jmAccentSoftText)
                        .padding(.horizontal, JM.Space.s2)
                        .padding(.vertical, 2)
                        .background(Color.jmAccentSoft, in: Capsule())
                        .fixedSize()
                } else {
                    Text("Read").jmText(.caption).foregroundStyle(Color.jmFgSecondary).fixedSize()
                }
                Text((message.from ?? "").isEmpty ? "Unknown sender" : (message.from ?? ""))
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if !when.isEmpty {
                    Text(when)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize()
                        .accessibilityLabel(JarvisTime.absolute(message.date))
                }
            }
            Text(message.subject ?? "")
                .jmText(.bodySm, weight: unread ? .medium : nil)
                .foregroundStyle(Color.jmFg)
                .fixedSize(horizontal: false, vertical: true)
            if let snippet = message.snippet, !snippet.isEmpty {
                Text(snippet)
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let handling = message.handling, !handling.isEmpty {
                Text(handling)
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFg)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
