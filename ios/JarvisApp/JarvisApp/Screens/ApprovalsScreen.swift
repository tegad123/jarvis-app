import SwiftUI
import JarvisKit

/// The approvals queue: pending first, decided ones quiet below.
/// Follows frontend/src/screens/Approvals.tsx. Approve and Reject show only
/// as far as the server's can_approve / can_reject allow.
struct ApprovalsScreen: View {
    @EnvironmentObject private var model: AppModel

    /// Lets the home list's count follow a decision without waiting for its poll.
    var onDecided: () -> Void = {}

    @State private var items: [ApprovalItem]?
    @State private var problem: LoadProblem?
    @State private var busyID: String?

    private var pending: [ApprovalItem] { (items ?? []).filter(\.isPending) }
    private var resolved: [ApprovalItem] { (items ?? []).filter { !$0.isPending } }

    var body: some View {
        Page(title: "Approvals", subtitle: "Nothing goes out without your say-so") {
            if let problem {
                ErrorState(
                    label: "Approvals unavailable", message: problem.message, detail: problem.detail,
                    stale: !(items ?? []).isEmpty, onRetry: { Task { await load() } })
            }

            if items == nil && problem == nil {
                LoadingLine()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, JM.Space.s8)
            } else {
                if pending.isEmpty && problem == nil {
                    EmptyState(icon: .approvals, text: "Nothing waiting on you.")
                }
                if !pending.isEmpty {
                    VStack(spacing: JM.Space.s3) {
                        ForEach(pending) { approval in
                            ApprovalCard(
                                approval: approval, busy: busyID == approval.id,
                                onApprove: { Task { await decide(approval.id, approve: true) } },
                                onReject: { Task { await decide(approval.id, approve: false) } })
                        }
                    }
                }
                if !resolved.isEmpty {
                    VStack(alignment: .leading, spacing: JM.Space.s3) {
                        Text("Decided")
                            .jmText(.caption)
                            .foregroundStyle(Color.jmFgSecondary)
                            .accessibilityAddTraits(.isHeader)
                        ForEach(resolved) { approval in
                            ApprovalCard(approval: approval, busy: false, onApprove: {}, onReject: {})
                        }
                    }
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        do {
            items = try await model.client.approvals()
            problem = nil
        } catch {
            problem = model.problem(for: error, message: "Jarvis could not read the approvals queue.")
        }
    }

    private func decide(_ id: String, approve: Bool) async {
        busyID = id
        problem = nil
        defer { busyID = nil }
        do {
            if let updated = try await model.client.decide(approvalID: id, approve: approve) {
                items = (items ?? []).map { $0.id == id ? updated : $0 }
            } else {
                await load()
            }
            onDecided()
        } catch {
            if let words = model.words(for: error, fallback: "Could not record that decision.") {
                problem = LoadProblem(message: words, detail: nil)
                // Someone else may have decided it; read again rather than guess.
                await reload(keeping: problem)
            }
        }
    }

    /// Reads the queue again without dropping the sentence that says why.
    private func reload(keeping said: LoadProblem?) async {
        guard let fresh = try? await model.client.approvals() else { return }
        items = fresh
        problem = said
    }
}

// MARK: - ApprovalCard (jm/ApprovalCard)

struct ApprovalCard: View {
    var approval: ApprovalItem
    var busy: Bool
    var onApprove: () -> Void
    var onReject: () -> Void

    private var pending: Bool { approval.isPending }
    private var canApprove: Bool { approval.canApprove != false }
    private var canReject: Bool { approval.canReject != false }

    var body: some View {
        let fields = approval.fields
        let changes = approval.changes ?? []
        JMCard(filled: pending) {
            HStack(spacing: JM.Space.s2) {
                if pending {
                    Tag("Action needed", tone: .action)
                }
                if let skill = approval.skill, !skill.isEmpty {
                    Text(skill).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                }
                Spacer(minLength: 0)
                Text(JarvisTime.relative(approval.ts))
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize()
            }

            Text(approval.summary)
                .jmText(.heading, weight: pending ? nil : .medium)
                .foregroundStyle(Color.jmFg)
                .fixedSize(horizontal: false, vertical: true)

            if !fields.isEmpty {
                VStack(alignment: .leading, spacing: JM.Space.s2) {
                    ForEach(fields.map { FieldRow(key: $0.key, value: $0.value.display) }) { field in
                        HStack(alignment: .top, spacing: JM.Space.s3) {
                            Text(ApprovalWording.fieldLabel(field.key))
                                .jmText(.caption)
                                .foregroundStyle(Color.jmFgSecondary)
                                .padding(.top, 2)
                                .frame(width: 88, alignment: .leading)
                            Text(field.value)
                                .jmText(.bodySm)
                                .foregroundStyle(Color.jmFg)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }

            if !changes.isEmpty {
                VStack(spacing: JM.Space.s2) {
                    ForEach(changes) { change in
                        VStack(alignment: .leading, spacing: 0) {
                            Text(change.label ?? change.key).jmText(.bodySm).foregroundStyle(Color.jmFg)
                            if change.label != nil {
                                Text(change.key)
                                    .jmText(.caption)
                                    .foregroundStyle(Color.jmFgSecondary)
                                    .padding(.top, 2)
                            }
                            FlowLayout(spacing: JM.Space.s2, lineSpacing: 0) {
                                Text(change.current.display)
                                    .jmText(.bodySm)
                                    .strikethrough()
                                    .foregroundStyle(Color.jmFgSecondary)
                                    .accessibilityLabel("From \(change.current.display)")
                                Text("→")
                                    .jmText(.bodySm)
                                    .foregroundStyle(Color.jmFgSecondary)
                                    .accessibilityHidden(true)
                                Text(change.proposed.display)
                                    .jmText(.bodySm, weight: .medium)
                                    .foregroundStyle(Color.jmFg)
                                    .accessibilityLabel("to \(change.proposed.display)")
                            }
                            .padding(.top, JM.Space.s2)
                        }
                        .padding(JM.Space.s3)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .overlay(
                            RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous)
                                .strokeBorder(Color.jmBorder, lineWidth: 1))
                    }
                }
            }

            if pending {
                if let note = ApprovalWording.note(approval) {
                    Text(note)
                        .jmText(.bodySm)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if canApprove || canReject {
                    HStack(spacing: JM.Space.s2) {
                        if canApprove {
                            JMButton(
                                busy ? "Saving…" : ApprovalWording.approveLabel(approval),
                                kind: .primary, wide: true, action: onApprove)
                        }
                        if canReject {
                            JMButton("Reject", wide: true, action: onReject)
                        }
                    }
                    .disabled(busy)
                }
            } else {
                HStack(spacing: JM.Space.s2) {
                    if approval.status == "approved" {
                        Tick(size: 14)
                    } else {
                        Icon(.close, size: 14)
                    }
                    Text(ApprovalWording.decided(approval)).jmText(.caption)
                }
                .foregroundStyle(Color.jmFgSecondary)
            }
        }
    }
}

private struct FieldRow: Identifiable {
    var key: String
    var value: String
    var id: String { key }
}
