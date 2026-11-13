import SwiftUI
import JarvisKit

/// Tasks: the shared list. The whole team sees the whole list. What each
/// role may do is enforced by the API; the flags here only decide whether a
/// control is drawn. Follows frontend/src/screens/Tasks.tsx.
struct TasksScreen: View {
    @EnvironmentObject private var model: AppModel

    var people: [PersonChoice]

    @State private var data: TasksResponse?
    @State private var problem: LoadProblem?
    @State private var busy = false
    @State private var composing: Bool
    @State private var statusFilter: String?
    @State private var priorityFilter: String?
    @State private var assigneeFilter: String?
    @State private var quietFilter: String?

    @State private var newTitle = ""
    @State private var newAssignee = ""
    @State private var newPriority = "medium"
    @State private var newDeadline = ""

    /// Previews open with the new-task form showing.
    init(people: [PersonChoice], composing: Bool = false) {
        self.people = people
        _composing = State(initialValue: composing)
    }

    private var store: Bool { data?.source == "store" }
    private var canWrite: Bool { store && model.personKey != nil }
    private var canManage: Bool { canWrite && model.isManager }

    private var roster: [SelectOption] {
        people.map { SelectOption(value: $0.personKey, label: $0.name.isEmpty ? $0.personKey : $0.name) }
    }

    /// Everyone who has a task, in the order they first appear.
    private var assignees: [SelectOption] {
        var seen: [SelectOption] = []
        for task in data?.tasks ?? [] {
            guard let key = task.assignee, !key.isEmpty, !seen.contains(where: { $0.value == key }) else { continue }
            let name = task.assigneeName ?? ""
            seen.append(SelectOption(value: key, label: name.isEmpty ? key : name))
        }
        return seen
    }

    private var visible: [TaskItem] {
        var all = data?.tasks ?? []
        if let statusFilter { all = all.filter { $0.status == statusFilter } }
        if let priorityFilter { all = all.filter { $0.priority == priorityFilter } }
        if let assigneeFilter { all = all.filter { $0.assignee == assigneeFilter } }
        if quietFilter != nil { all = all.filter { $0.stale == true } }
        return all
    }

    private var filtered: Bool {
        statusFilter != nil || priorityFilter != nil || assigneeFilter != nil || quietFilter != nil
    }

    private static let elsewhere = "Tasks for this tenant live in the assistant’s own list."

    private var subtitle: String {
        guard let data else { return "What the team is working on" }
        if data.source != "store" { return data.notice ?? Self.elsewhere }
        if data.bodiesVisible == false { return "Structure only — task details are hidden for operator access." }
        let open = (data.counts?.open ?? 0) + (data.counts?.inProgress ?? 0)
        return "\(open) open · \(data.counts?.stale ?? 0) gone quiet"
    }

    var body: some View {
        Page(title: "Tasks", subtitle: subtitle) {
            if canWrite {
                JMButton("New task", icon: .plus, kind: .primary, size: .sm) { composing = true }
                    .disabled(busy)
            }
        } content: {
            if composing {
                quickAdd
            }

            if let problem {
                ErrorState(
                    label: "Tasks unavailable", message: problem.message, detail: problem.detail,
                    stale: !(data?.tasks ?? []).isEmpty, onRetry: { Task { await load() } })
            }

            if store {
                VStack(alignment: .leading, spacing: JM.Space.s2) {
                    FilterChips(
                        label: "Show",
                        options: TaskWording.statusCycle.map { SelectOption(value: $0, label: TaskWording.statusLabel($0)) },
                        active: statusFilter
                    ) { statusFilter = $0 }
                    FilterChips(
                        label: "Level",
                        options: ["high", "medium", "low"].map { SelectOption(value: $0, label: TaskWording.priorityLabel($0)) },
                        active: priorityFilter
                    ) { priorityFilter = $0 }
                    if !assignees.isEmpty {
                        FilterChips(label: "Who", options: assignees, active: assigneeFilter) { assigneeFilter = $0 }
                    }
                    if (data?.counts?.stale ?? 0) > 0 {
                        FilterChips(
                            label: "Quiet", options: [SelectOption(value: "stale", label: "Gone quiet")],
                            active: quietFilter
                        ) { quietFilter = $0 }
                    }
                }
            }

            if data == nil && problem == nil {
                LoadingLine()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, JM.Space.s8)
            } else if let data, data.source != "store" {
                EmptyState(icon: .tasks, text: data.notice ?? Self.elsewhere)
            } else if visible.isEmpty && problem == nil {
                EmptyState(icon: .tasks, text: filtered ? "No tasks match those filters." : "Nothing on the list yet.")
            } else {
                JMList(items: visible) { task in
                    TaskRow(
                        task: task,
                        canStatus: canWrite && (canManage || (task.assignee != nil && task.assignee == model.personKey)),
                        canManage: canManage,
                        people: roster,
                        busy: busy,
                        onStatus: { next in
                            Task { await mutate { try await model.client.setTaskStatus(id: task.taskId, status: next) } }
                        },
                        onAssign: { person in
                            Task { await mutate { try await model.client.assignTask(id: task.taskId, assignee: person) } }
                        },
                        onArchive: {
                            Task { await mutate { try await model.client.archiveTask(id: task.taskId) } }
                        })
                }
            }
        }
        .task { await load() }
    }

    private var quickAdd: some View {
        JMCard {
            JMField("What needs doing", text: $newTitle, onSubmit: add)
                .accessibilityLabel("New task")
            FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                JMSelect(
                    label: "Assignee", value: newAssignee,
                    options: [SelectOption(value: "", label: "Unassigned")] + roster,
                    onChange: { newAssignee = $0 })
                JMSelect(
                    label: "Priority", value: newPriority,
                    options: ["high", "medium", "low"].map { SelectOption(value: $0, label: TaskWording.priorityLabel($0)) },
                    onChange: { newPriority = $0 })
                JMDateField(label: "Deadline", day: $newDeadline)
            }
            HStack(spacing: JM.Space.s2) {
                JMButton("Add", kind: .primary, action: add)
                    .disabled(busy || newTitle.trimmingCharacters(in: .whitespaces).isEmpty)
                JMButton("Cancel", kind: .ghost) {
                    composing = false
                    newTitle = ""
                }
            }
        }
    }

    private func add() {
        let title = newTitle.trimmingCharacters(in: .whitespaces)
        if title.isEmpty { return }
        let assignee = newAssignee.isEmpty ? nil : newAssignee
        let priority = newPriority
        let deadline = newDeadline.isEmpty ? nil : newDeadline
        composing = false
        newTitle = ""
        newAssignee = ""
        newPriority = "medium"
        newDeadline = ""
        Task {
            await mutate {
                try await model.client.createTask(title: title, assignee: assignee, priority: priority, deadline: deadline)
            }
        }
    }

    private func load() async {
        do {
            data = try await model.client.tasksResponse()
            problem = nil
        } catch {
            if let words = model.words(for: error, fallback: "Could not load tasks.") {
                problem = LoadProblem(message: words, detail: nil)
            }
        }
    }

    /// One change, then the list again. When the server refuses, its own
    /// sentence is shown: it knows which rule said no.
    private func mutate(_ change: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        do {
            try await change()
            problem = nil
            await load()
        } catch {
            if let words = model.words(for: error, fallback: "That change did not save.") {
                problem = LoadProblem(message: words, detail: nil)
            }
        }
    }
}

// MARK: - TaskRow (jm/TaskRow)

struct TaskRow: View {
    var task: TaskItem
    var canStatus: Bool
    var canManage: Bool
    var people: [SelectOption]
    var busy: Bool
    var onStatus: (String) -> Void
    var onAssign: (String?) -> Void
    var onArchive: () -> Void

    private var status: String { task.status ?? "open" }
    private var settled: Bool { status == "done" || status == "archived" }

    private var word: String {
        status == "archived" ? "Archived" : TaskWording.statusLabel(status)
    }

    private var meta: [MetaItem] {
        var items: [MetaItem] = [.plain((task.assigneeName ?? "").isEmpty ? "Unassigned" : (task.assigneeName ?? ""))]
        if let due = task.dueLabel, !due.isEmpty {
            items.append(task.isOverdue == true ? .action(due) : .plain(due))
        }
        items.append(.plain(JarvisTime.durationOpen(task.daysOpen)))
        if task.stale == true { items.append(.plain("Gone quiet")) }
        return items
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s1) {
            HStack(spacing: JM.Space.s2) {
                pill
                Text(task.title)
                    .jmText(.bodySm)
                    .strikethrough(settled)
                    .foregroundStyle(settled ? Color.jmFgSecondary : Color.jmFg)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if task.priority == "high" {
                    Tag("High")
                }
                if task.createdBy == "jarvis" {
                    SparkGlyph(label: "Filed by Jarvis")
                }
            }
            if let detail = task.detail, !detail.isEmpty {
                Text(detail).jmText(.bodySm).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
            }
            HStack(spacing: JM.Space.s2) {
                MetaLine(items: meta)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if canManage {
                    HStack(spacing: JM.Space.s1) {
                        JMSelect(
                            label: "Assignee", value: task.assignee ?? "",
                            options: [SelectOption(value: "", label: "Unassigned")] + people,
                            onChange: { onAssign($0.isEmpty ? nil : $0) })
                        JMButton("Archive", kind: .ghost, size: .sm, action: onArchive)
                    }
                    .disabled(busy)
                }
            }
            .frame(minHeight: JM.Size.controlSm)
        }
    }

    @ViewBuilder
    private var pill: some View {
        if canStatus && status != "archived" {
            let next = TaskWording.statusCycle[
                ((TaskWording.statusCycle.firstIndex(of: status) ?? -1) + 1) % TaskWording.statusCycle.count]
            Button {
                onStatus(next)
            } label: {
                StatusPill(word: word) { TaskStatusMark(status: status) }
            }
            .buttonStyle(JMPlainStyle())
            .disabled(busy)
            .opacity(busy ? JM.disabledOpacity : 1)
            .accessibilityLabel("Status: \(word). Change to \(TaskWording.statusLabel(next))")
        } else {
            StatusPill(word: word) { TaskStatusMark(status: status) }
        }
    }
}
