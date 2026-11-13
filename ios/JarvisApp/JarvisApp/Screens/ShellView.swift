import SwiftUI
import JarvisKit

/// The signed-in app, as `Shell` in frontend/src/App.tsx runs it on a phone:
/// the home list fills the screen, and picking a row replaces it with that
/// screen, which has a way back.
enum NavID: String, Equatable {
    case feed, approvals, inbox, tasks, leads, settings
}

enum Route: Equatable {
    case nav(NavID)
    case thread(String)
}

enum ChannelSheetMode: Equatable {
    case create
    case rename(ThreadItem)
}

enum CategorySheetMode: Equatable {
    case new
    case edit(id: String, name: String)
    case move(threadID: String, threadName: String, current: String)

    var key: String {
        switch self {
        case .new: return "new"
        case .edit(let id, _): return "edit-\(id)"
        case .move(let threadID, _, _): return "move-\(threadID)"
        }
    }
}

struct ShellView: View {
    @EnvironmentObject private var model: AppModel

    @State private var route: Route?
    @State private var threads: [ThreadItem]?
    @State private var categories: [CategoryItem] = []
    @State private var jarvisReplies = "mention"
    @State private var threadsError: String?
    @State private var people: [PersonChoice] = []
    @State private var pending = 0
    @State private var inboxUnread = 0
    @State private var channelSheet: ChannelSheetMode?
    @State private var categorySheet: CategorySheetMode?
    @State private var sheetBusy = false
    @State private var sheetError: String?

    /// Previews open straight onto a screen.
    init(route: Route? = nil) {
        _route = State(initialValue: route)
    }

    private var role: String { model.role }
    private var manager: Bool { model.isManager }
    private var seesInbox: Bool { role == "owner" || role == "admin" }

    private var direct: ThreadItem? { threads?.first { $0.direct } }
    private var channels: [ThreadItem] { (threads ?? []).filter { !$0.direct } }
    private var categoryOptions: [SelectOption] {
        categories.map { SelectOption(value: $0.categoryId, label: $0.name) }
    }

    private var openThread: ThreadItem? {
        guard case .thread(let id) = route else { return nil }
        return threads?.first { $0.threadId == id }
    }

    var body: some View {
        ZStack {
            HomeView(
                nav: nav,
                direct: direct,
                sections: sections,
                ungrouped: ungrouped,
                loaded: threads != nil,
                error: threadsError,
                manager: manager,
                hasCategories: !categories.isEmpty,
                personName: personName,
                roleLabel: Roles.label(role),
                onSelect: select,
                onOpen: { route = .thread($0.threadId) },
                onNewChannel: {
                    sheetError = nil
                    channelSheet = .create
                },
                onNewCategory: {
                    sheetError = nil
                    categorySheet = .new
                },
                onEditCategory: { category in
                    sheetError = nil
                    categorySheet = .edit(id: category.categoryId, name: category.name)
                },
                onRename: { thread in
                    sheetError = nil
                    channelSheet = .rename(thread)
                },
                onMove: { thread in
                    sheetError = nil
                    categorySheet = .move(threadID: thread.threadId, threadName: thread.name, current: thread.categoryId ?? "")
                },
                onArchive: { thread in Task { await archive(thread) } }
            )
            .accessibilityHidden(route != nil)

            if let route {
                screen(route)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.jmBg.ignoresSafeArea())
                    .environment(\.jmBack, { self.route = nil })
                    .overlay(alignment: .leading) { backSwipe }
                    .transition(.move(edge: .trailing))
                    .zIndex(1)
            }
        }
        .animation(JM.Motion.easeOut(JM.Motion.sheet), value: route)
        .jmSheet(isPresented: channelSheet != nil, onClose: closeSheets) {
            if let mode = channelSheet {
                NewChannelSheet(
                    mode: mode, categories: categoryOptions, busy: sheetBusy, error: sheetError,
                    onSubmit: { name, category in Task { await submitChannel(mode, name: name, category: category) } },
                    onClose: closeSheets)
            }
        }
        .jmSheet(isPresented: categorySheet != nil, onClose: closeSheets) {
            if let mode = categorySheet {
                CategorySheet(
                    mode: mode, categories: categoryOptions, busy: sheetBusy, error: sheetError,
                    onSave: { value in Task { await submitCategory(mode, value: value, remove: false) } },
                    onDelete: { Task { await submitCategory(mode, value: "", remove: true) } },
                    onClose: closeSheets)
                .id(mode.key)
            }
        }
        .task {
            people = (try? await model.client.directory()) ?? []
        }
        .task {
            await every(10) { await loadThreads() }
        }
        .task(id: route) {
            await every(20) { await loadPending() }
        }
        .task {
            await every(60) { await loadInboxCount() }
        }
    }

    /// A thin strip on the leading edge: drag it right to go back, as the
    /// system's own back swipe does.
    private var backSwipe: some View {
        Color.clear
            .frame(width: 16)
            .frame(maxHeight: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 24).onEnded { value in
                    if value.translation.width > 60, abs(value.translation.height) < 80 { route = nil }
                }
            )
            .accessibilityHidden(true)
    }

    // MARK: What the home list shows

    private var personName: String {
        let name = model.session?.displayName ?? ""
        return name.isEmpty ? "Signed in" : name
    }

    private var nav: [NavItem] {
        var items = [
            NavItem(id: .feed, label: "Activity", icon: .activity),
            NavItem(id: .approvals, label: "Approvals", icon: .approvals, count: pending, attention: pending > 0),
        ]
        if seesInbox {
            items.append(NavItem(id: .inbox, label: "Inbox", icon: .inbox, count: inboxUnread, attention: inboxUnread > 0))
        }
        if model.session?.allows("tasks") != false {
            items.append(NavItem(id: .tasks, label: "Tasks", icon: .tasks))
        }
        if model.session?.allows("leads") != false {
            items.append(NavItem(id: .leads, label: "Leads", icon: .leads))
        }
        if Roles.canSeeSettings(role) {
            items.append(NavItem(id: .settings, label: "Settings", icon: .settings))
        }
        return items
    }

    /// One section per category. A member only sees the ones with channels.
    private var sections: [HomeSection] {
        categories
            .map { category in
                HomeSection(category: category, channels: channels.filter { $0.categoryId == category.categoryId })
            }
            .filter { manager || !$0.channels.isEmpty }
    }

    private var ungrouped: [ThreadItem] {
        let known = Set(categories.map(\.categoryId))
        return channels.filter { thread in
            guard let id = thread.categoryId, !id.isEmpty else { return true }
            return !known.contains(id)
        }
    }

    private func select(_ id: NavID) {
        if id == .settings && !Roles.canSeeSettings(role) { return }
        if id == .inbox && !seesInbox { return }
        route = .nav(id)
    }

    // MARK: Screens

    @ViewBuilder
    private func screen(_ route: Route) -> some View {
        switch route {
        case .thread(let id):
            if let thread = openThread {
                ChatScreen(
                    thread: thread, people: people, jarvisReplies: jarvisReplies,
                    onRead: { markRead(id) },
                    onThreadsChanged: { Task { await loadThreads() } })
                .id(thread.threadId)
            } else if threads == nil && threadsError == nil {
                Color.jmBg
            } else {
                Page(title: "Jarvis") {
                    EmptyState(icon: .chat, text: threadsError ?? "Pick a conversation.")
                }
            }
        case .nav(let id):
            switch id {
            case .feed:
                ActivityScreen()
            case .approvals:
                if role == "operator" {
                    Page(title: "Approvals") {
                        EmptyState(icon: .approvals, text: "Approvals are the account’s people to make.")
                    }
                } else {
                    ApprovalsScreen(onDecided: { Task { await loadPending() } })
                }
            case .inbox:
                InboxScreen()
            case .tasks:
                TasksScreen(people: people)
            case .leads:
                LeadsScreen(people: people)
            case .settings:
                SettingsScreen()
            }
        }
    }

    // MARK: Loading

    private func every(_ seconds: Double, _ work: () async -> Void) async {
        while !Task.isCancelled {
            await work()
            try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
        }
    }

    @discardableResult
    private func loadThreads() async -> Bool {
        do {
            let response = try await model.client.threads()
            threads = response.threads
            categories = response.categories ?? []
            if let replies = response.jarvisReplies { jarvisReplies = replies }
            threadsError = nil
            return true
        } catch {
            if model.words(for: error, fallback: "") != nil {
                threadsError = "Could not load your conversations."
            }
            return false
        }
    }

    private func loadPending() async {
        if role == "operator" { return }
        do {
            pending = try await model.client.approvals().filter(\.isPending).count
        } catch {
            _ = model.words(for: error, fallback: "")
        }
    }

    private func loadInboxCount() async {
        guard seesInbox else { return }
        do {
            inboxUnread = try await model.client.inbox().waiting
        } catch {
            _ = model.words(for: error, fallback: "")
        }
    }

    private func markRead(_ id: String) {
        guard let index = threads?.firstIndex(where: { $0.threadId == id }) else { return }
        threads?[index].unread = false
        threads?[index].unreadCount = 0
        threads?[index].mentions = 0
    }

    // MARK: Channel and category changes

    private func closeSheets() {
        channelSheet = nil
        categorySheet = nil
        sheetError = nil
    }

    private func archive(_ thread: ThreadItem) async {
        do {
            try await model.client.archiveThread(id: thread.threadId)
            if route == .thread(thread.threadId) { route = nil }
            await loadThreads()
        } catch {
            if let words = model.words(for: error, fallback: "Could not archive that channel.") {
                threadsError = words
            }
        }
    }

    private func submitChannel(_ mode: ChannelSheetMode, name: String, category: String) async {
        sheetBusy = true
        sheetError = nil
        defer { sheetBusy = false }
        do {
            switch mode {
            case .create:
                let created = try await model.client.createThread(name: name, categoryID: category.isEmpty ? nil : category)
                channelSheet = nil
                await loadThreads()
                route = .thread(created.threadId)
            case .rename(let thread):
                try await model.client.renameThread(id: thread.threadId, name: name)
                channelSheet = nil
                await loadThreads()
            }
        } catch {
            sheetError = model.words(for: error, fallback: "That did not work.")
        }
    }

    private func submitCategory(_ mode: CategorySheetMode, value: String, remove: Bool) async {
        sheetBusy = true
        sheetError = nil
        defer { sheetBusy = false }
        do {
            switch mode {
            case .new:
                try await model.client.createCategory(name: value)
            case .edit(let id, _):
                if remove {
                    try await model.client.deleteCategory(id: id)
                } else {
                    try await model.client.renameCategory(id: id, name: value)
                }
            case .move(let threadID, _, _):
                try await model.client.setThreadCategory(id: threadID, categoryID: value.isEmpty ? nil : value)
            }
            categorySheet = nil
            await loadThreads()
        } catch {
            sheetError = model.words(for: error, fallback: "That did not work.")
        }
    }
}

// MARK: - Home (jm/Sidebar/Sidebar.tsx, full width on a phone)

struct NavItem: Identifiable {
    var id: NavID
    var label: String
    var icon: JarvisIcon
    var count = 0
    var attention = false
}

struct HomeSection: Identifiable {
    var category: CategoryItem
    var channels: [ThreadItem]
    var id: String { category.categoryId }
}

struct HomeView: View {
    @EnvironmentObject private var model: AppModel

    var nav: [NavItem]
    var direct: ThreadItem?
    var sections: [HomeSection]
    var ungrouped: [ThreadItem]
    var loaded: Bool
    var error: String?
    var manager: Bool
    var hasCategories: Bool
    var personName: String
    var roleLabel: String
    var onSelect: (NavID) -> Void
    var onOpen: (ThreadItem) -> Void
    var onNewChannel: () -> Void
    var onNewCategory: () -> Void
    var onEditCategory: (CategoryItem) -> Void
    var onRename: (ThreadItem) -> Void
    var onMove: (ThreadItem) -> Void
    var onArchive: (ThreadItem) -> Void

    private var anyChannel: Bool {
        !ungrouped.isEmpty || sections.contains { !$0.channels.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s4) {
            HStack {
                Text("Jarvis")
                    .jmText(.heading)
                    .foregroundStyle(Color.jmFg)
                    .accessibilityAddTraits(.isHeader)
                Spacer(minLength: 0)
                if manager {
                    JMButton(icon: .compose, kind: .ghost, label: "New channel", action: onNewChannel)
                }
            }
            .frame(minHeight: JM.Size.control)
            .padding(.leading, JM.Space.s3)

            VStack(spacing: 2) {
                ForEach(nav) { item in
                    navRow(item)
                }
            }

            ScrollView {
                VStack(alignment: .leading, spacing: JM.Space.s4) {
                    if let direct {
                        ConversationRow(thread: direct, onTap: { onOpen(direct) })
                    }
                    ForEach(sections) { section in
                        VStack(alignment: .leading, spacing: 2) {
                            sectionHeading(section.category.name) {
                                if manager {
                                    JMButton(icon: .more, kind: .ghost, label: "Edit \(section.category.name)") {
                                        onEditCategory(section.category)
                                    }
                                }
                            }
                            if section.channels.isEmpty {
                                note("No channels yet.")
                            } else {
                                ForEach(section.channels) { thread in
                                    channelRow(thread)
                                }
                            }
                        }
                    }
                    if !ungrouped.isEmpty || !anyChannel || manager {
                        VStack(alignment: .leading, spacing: 2) {
                            sectionHeading("Channels") {
                                if manager {
                                    JMButton(icon: .plus, kind: .ghost, label: "New category", action: onNewCategory)
                                }
                            }
                            if !ungrouped.isEmpty {
                                ForEach(ungrouped) { thread in
                                    channelRow(thread)
                                }
                            } else if !anyChannel {
                                note(loaded ? (error ?? "When a new deal starts, Jarvis opens a channel for it.") : "Loading…")
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollBounceBehavior(.basedOnSize)

            footer
        }
        .padding(JM.Space.s3)
        .background(Color.jmBg.ignoresSafeArea())
    }

    private func navRow(_ item: NavItem) -> some View {
        Button {
            onSelect(item.id)
        } label: {
            HStack(spacing: JM.Space.s3) {
                Icon(item.icon, size: 18)
                Text(item.label)
                    .jmText(.label)
                    .frame(maxWidth: .infinity, alignment: .leading)
                if item.count > 0 {
                    if item.attention {
                        CountBadge(count: item.count)
                            .accessibilityLabel("\(item.count) waiting on you")
                    } else {
                        Text("\(item.count)").jmText(.caption).monospacedDigit()
                    }
                }
            }
            .foregroundStyle(Color.jmFgSecondary)
            .padding(.horizontal, JM.Space.s3)
            .frame(minHeight: JM.Size.control)
        }
        .buttonStyle(JMRowStyle())
    }

    private func sectionHeading<Trailing: View>(_ label: String, @ViewBuilder trailing: () -> Trailing) -> some View {
        HStack(spacing: JM.Space.s1) {
            Text(label)
                .jmText(.caption)
                .foregroundStyle(Color.jmFgSecondary)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            trailing()
        }
        .frame(minHeight: JM.Size.controlSm)
        .padding(.leading, JM.Space.s3)
        .padding(.bottom, JM.Space.s1)
    }

    private func note(_ text: String) -> some View {
        Text(text)
            .jmText(.bodySm)
            .foregroundStyle(Color.jmFgSecondary)
            .padding(.horizontal, JM.Space.s3)
            .fixedSize(horizontal: false, vertical: true)
    }

    /// A channel row. The web's row menu (right-click, or the hover button)
    /// is the long-press menu here.
    @ViewBuilder
    private func channelRow(_ thread: ThreadItem) -> some View {
        if manager {
            ConversationRow(thread: thread, onTap: { onOpen(thread) })
                .contextMenu {
                    Button("Rename") { onRename(thread) }
                    if hasCategories {
                        Button("Move to category") { onMove(thread) }
                    }
                    Button("Archive") { onArchive(thread) }
                }
        } else {
            ConversationRow(thread: thread, onTap: { onOpen(thread) })
        }
    }

    private var footer: some View {
        HStack(spacing: JM.Space.s2) {
            Avatar(name: personName)
            VStack(alignment: .leading, spacing: 0) {
                Text(personName).jmText(.label).foregroundStyle(Color.jmFg).lineLimit(1)
                if !roleLabel.isEmpty {
                    Text(roleLabel).jmText(.caption).foregroundStyle(Color.jmFgSecondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            JMButton(
                icon: model.light ? .moon : .sun, kind: .ghost,
                label: model.light ? "Switch to dark" : "Switch to light"
            ) {
                model.light.toggle()
            }
            JMButton(icon: .signout, kind: .ghost, label: "Sign out") {
                Task { await model.signOut() }
            }
        }
        .padding(.leading, JM.Space.s1)
        .padding(.top, JM.Space.s3)
        .overlay(alignment: .top) {
            Rectangle().fill(Color.jmBorder).frame(height: 1)
        }
    }
}

/// A full-width row that tints while pressed, as the web's rows do on hover.
struct JMRowStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(
                configuration.isPressed ? Color.jmPanel : Color.clear,
                in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
            .contentShape(RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
    }
}

/// The accent pill with a number: approvals waiting, mentions of you.
struct CountBadge: View {
    var count: Int

    var body: some View {
        Text("\(count)")
            .jmText(.caption, weight: .semibold)
            .monospacedDigit()
            .foregroundStyle(Color.jmOnAccent)
            .padding(.horizontal, 6)
            .frame(minWidth: 20, minHeight: 20)
            .background(Color.jmAccent, in: Capsule())
            .fixedSize()
    }
}

// MARK: - ConversationRow (jm/ConversationRow)

struct ConversationRow: View {
    var thread: ThreadItem
    var onTap: () -> Void

    private var preview: String {
        guard let text = thread.preview, !text.isEmpty else {
            return thread.direct ? "Your direct line. Always here." : "No messages yet"
        }
        let who: String
        if thread.previewRole == "assistant" {
            who = "Jarvis: "
        } else if thread.previewRole == "user", thread.previewMine == true || thread.direct {
            who = "You: "
        } else if thread.previewRole == "user", let author = thread.previewAuthorName, !author.isEmpty {
            who = "\(author): "
        } else {
            who = ""
        }
        return who + text
    }

    private var time: String? {
        let quiet = !thread.direct && JarvisTime.isQuiet(thread.lastMessageAt)
        let stamp = JarvisTime.relative(thread.lastMessageAt)
        if stamp.isEmpty { return quiet ? "Quiet" : nil }
        return quiet ? "Quiet · \(stamp)" : stamp
    }

    private var unread: Bool { thread.unread == true }
    private var mentions: Int { thread.mentions ?? 0 }

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: JM.Space.s2) {
                    if thread.direct {
                        Icon(.chat, size: 16).foregroundStyle(Color.jmFgSecondary)
                    } else if thread.createdBy == "jarvis" {
                        Icon(.spark, size: 14)
                            .foregroundStyle(Color.jmFgSecondary)
                            .accessibilityHidden(false)
                            .accessibilityLabel("Opened by Jarvis")
                    }
                    Text(thread.name)
                        .jmText(.label, weight: unread ? .semibold : nil)
                        .foregroundStyle(Color.jmFg)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if let time {
                        Text(time)
                            .jmText(.caption)
                            .foregroundStyle(Color.jmFgSecondary)
                            .fixedSize()
                    }
                    if mentions > 0 {
                        CountBadge(count: mentions)
                            .accessibilityLabel("\(mentions) \(mentions == 1 ? "mention" : "mentions") of you")
                    } else if unread {
                        Circle()
                            .fill(Color.jmFg)
                            .frame(width: 6, height: 6)
                            .accessibilityLabel("Unread")
                    }
                }
                Text(preview)
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, JM.Space.s3)
            .padding(.vertical, JM.Space.s2)
        }
        .buttonStyle(JMRowStyle())
    }
}

// MARK: - NewChannelSheet (jm/NewChannelSheet)

struct NewChannelSheet: View {
    var mode: ChannelSheetMode
    var categories: [SelectOption]
    var busy: Bool
    var error: String?
    var onSubmit: (String, String) -> Void
    var onClose: () -> Void

    @State private var name: String
    @State private var category = ""

    init(
        mode: ChannelSheetMode, categories: [SelectOption], busy: Bool, error: String?,
        onSubmit: @escaping (String, String) -> Void, onClose: @escaping () -> Void
    ) {
        self.mode = mode
        self.categories = categories
        self.busy = busy
        self.error = error
        self.onSubmit = onSubmit
        self.onClose = onClose
        if case .rename(let thread) = mode {
            _name = State(initialValue: thread.name)
        } else {
            _name = State(initialValue: "")
        }
    }

    private var renaming: Bool { mode != .create }
    private var trimmed: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        JMSheet(title: renaming ? "Rename channel" : "New channel", onClose: onClose) {
            VStack(alignment: .leading, spacing: JM.Space.s3) {
                JMField("e.g. 128 Alder Ave", text: $name, large: true, onSubmit: submit)
                    .disabled(busy)
                    .accessibilityLabel("Channel name")
                    .onChange(of: name) { _, typed in
                        if typed.count > 120 { name = String(typed.prefix(120)) }
                    }
                if !renaming && !categories.isEmpty {
                    HStack(spacing: JM.Space.s3) {
                        Text("Category").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                        Spacer(minLength: 0)
                        JMSelect(
                            label: "Category", value: category,
                            options: [SelectOption(value: "", label: "No category")] + categories,
                            onChange: { category = $0 })
                        .disabled(busy)
                    }
                }
                JMButton(busy ? "Working…" : (renaming ? "Save" : "Create"), kind: .primary, wide: true, tall: true, action: submit)
                    .disabled(busy || trimmed.isEmpty)
                if let error, !error.isEmpty {
                    ErrorLine(error)
                }
            }
        }
    }

    private func submit() {
        if !trimmed.isEmpty && !busy { onSubmit(trimmed, category) }
    }
}

// MARK: - CategorySheet (components/CategorySheet.tsx)

struct CategorySheet: View {
    var mode: CategorySheetMode
    var categories: [SelectOption]
    var busy: Bool
    var error: String?
    var onSave: (String) -> Void
    var onDelete: () -> Void
    var onClose: () -> Void

    @State private var value: String
    @State private var confirm = false

    init(
        mode: CategorySheetMode, categories: [SelectOption], busy: Bool, error: String?,
        onSave: @escaping (String) -> Void, onDelete: @escaping () -> Void, onClose: @escaping () -> Void
    ) {
        self.mode = mode
        self.categories = categories
        self.busy = busy
        self.error = error
        self.onSave = onSave
        self.onDelete = onDelete
        self.onClose = onClose
        switch mode {
        case .new: _value = State(initialValue: "")
        case .edit(_, let name): _value = State(initialValue: name)
        case .move(_, _, let current): _value = State(initialValue: current)
        }
    }

    private var moving: Bool {
        if case .move = mode { return true }
        return false
    }

    private var editing: Bool {
        if case .edit = mode { return true }
        return false
    }

    private var title: String {
        switch mode {
        case .new: return "New category"
        case .edit: return "Edit category"
        case .move(_, let threadName, _): return "Move \(threadName)"
        }
    }

    private var submitLabel: String {
        if busy { return "Working…" }
        switch mode {
        case .new: return "Add"
        case .edit: return "Save"
        case .move: return "Move"
        }
    }

    private var trimmed: String { value.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        JMSheet(
            title: title,
            subtitle: moving ? "Who can see the channel doesn’t change." : nil,
            onClose: onClose
        ) {
            VStack(alignment: .leading, spacing: JM.Space.s3) {
                if moving {
                    HStack(spacing: JM.Space.s3) {
                        Text("Category").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                        Spacer(minLength: 0)
                        JMSelect(
                            label: "Category", value: value,
                            options: [SelectOption(value: "", label: "No category")] + categories,
                            onChange: { value = $0 })
                        .disabled(busy)
                    }
                } else {
                    JMField("e.g. Listings", text: $value, large: true, onSubmit: submit)
                        .disabled(busy)
                        .accessibilityLabel("Category name")
                        .onChange(of: value) { _, typed in
                            if typed.count > 60 { value = String(typed.prefix(60)) }
                        }
                }
                JMButton(submitLabel, kind: .primary, wide: true, tall: true, action: submit)
                    .disabled(busy || (!moving && trimmed.isEmpty))
                if editing {
                    JMButton(confirm ? "Remove it — channels stay, ungrouped" : "Remove category", kind: .ghost, wide: true) {
                        if confirm { onDelete() } else { confirm = true }
                    }
                    .disabled(busy)
                }
                if let error, !error.isEmpty {
                    ErrorLine(error)
                }
            }
        }
    }

    private func submit() {
        if busy { return }
        if !moving && trimmed.isEmpty { return }
        onSave(moving ? value : trimmed)
    }
}
