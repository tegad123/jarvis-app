import SwiftUI
import JarvisKit

// The larger Jarvis Mono parts: the page frame, the bottom sheet, and the
// blocks every screen shares. Each names the web file it follows.

// MARK: - Back (components/Page.tsx BackContext)

private struct JMBackKey: EnvironmentKey {
    static let defaultValue: (() -> Void)? = nil
}

extension EnvironmentValues {
    /// Set by the shell: on a phone a screen replaces the home list and
    /// needs a way back to it.
    var jmBack: (() -> Void)? {
        get { self[JMBackKey.self] }
        set { self[JMBackKey.self] = newValue }
    }
}

struct BackButton: View {
    var label = "Back"
    @Environment(\.jmBack) private var back

    var body: some View {
        if let back {
            JMButton(icon: .back, kind: .ghost, label: label, action: back)
        }
    }
}

// MARK: - Page (components/Page.tsx)

/// One screen: a header row (back, title, actions) and a subtitle above a
/// scrolling column.
struct Page<Actions: View, Content: View>: View {
    var title: String
    var subtitle: String?
    @ViewBuilder var actions: () -> Actions
    @ViewBuilder var content: () -> Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: JM.Space.s5) {
                VStack(alignment: .leading, spacing: JM.Space.s1) {
                    HStack(spacing: JM.Space.s2) {
                        BackButton()
                        Text(title)
                            .jmText(.title)
                            .foregroundStyle(Color.jmFg)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityAddTraits(.isHeader)
                        actions()
                    }
                    if let subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .jmText(.bodySm)
                            .foregroundStyle(Color.jmFgSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                content()
            }
            .padding(.horizontal, JM.Space.s4)
            .padding(.top, JM.Space.s4)
            .padding(.bottom, JM.Space.s12)
            .frame(maxWidth: JM.Size.page, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color.jmBg.ignoresSafeArea())
    }
}

extension Page where Actions == EmptyView {
    init(title: String, subtitle: String? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.init(title: title, subtitle: subtitle, actions: { EmptyView() }, content: content)
    }
}

/// "Loading…", as the web prints it while a list is on its way.
struct LoadingLine: View {
    var body: some View {
        Text("Loading…")
            .jmText(.bodySm)
            .foregroundStyle(Color.jmFgSecondary)
    }
}

// MARK: - EmptyState (jm/EmptyState)

struct EmptyState: View {
    var icon: JarvisIcon? = .inbox
    var title: String?
    var text = "Jarvis is watching. Nothing needs you right now."
    var actionLabel: String?
    var actionPrimary = false
    var action: () -> Void = {}

    var body: some View {
        VStack(spacing: JM.Space.s2) {
            if let icon {
                Icon(icon, size: 20)
                    .foregroundStyle(Color.jmFgSecondary)
                    .frame(width: 40, height: 40)
                    .overlay(Circle().strokeBorder(Color.jmBorder, lineWidth: 1))
                    .padding(.bottom, JM.Space.s2)
            }
            if let title {
                Text(title).jmText(.heading).foregroundStyle(Color.jmFg)
            }
            Text(text)
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFgSecondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
            if let actionLabel {
                JMButton(actionLabel, kind: actionPrimary ? .primary : .secondary, action: action)
                    .padding(.top, JM.Space.s2)
            }
        }
        .padding(.horizontal, JM.Space.s4)
        .padding(.vertical, JM.Space.s8)
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .contain)
    }
}

// MARK: - ErrorState (jm/ErrorState)

struct ErrorState: View {
    var label = "Unavailable"
    var message: String
    var detail: String?
    /// There is older data under this notice.
    var stale = false
    var busy = false
    var onRetry: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s2) {
            HStack(spacing: JM.Space.s2) {
                Icon(.alert, size: 18).foregroundStyle(Color.jmError)
                Text(stale ? "Not updating" : label).jmText(.label).foregroundStyle(Color.jmFg)
            }
            Text(message)
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFg)
                .fixedSize(horizontal: false, vertical: true)
            if let detail, !detail.isEmpty {
                Text(detail)
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if stale {
                Text("What you see is the last thing Jarvis loaded. It may be out of date.")
                    .jmText(.bodySm)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let onRetry {
                JMButton(busy ? "Trying…" : "Try again", icon: .refresh, size: .sm, action: onRetry)
                    .disabled(busy)
                    .padding(.top, JM.Space.s1)
            }
        }
        .padding(JM.Space.s4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous).strokeBorder(Color.jmError, lineWidth: 1))
        .accessibilityElement(children: .contain)
    }
}

// MARK: - FilterChips (jm/FilterChips)

/// One row of filter chips. Nil is "All"; tapping the lit chip clears it.
struct FilterChips: View {
    var label: String?
    var options: [SelectOption]
    var active: String?
    var allLabel = "All"
    var hideAll = false
    var onChange: (String?) -> Void

    var body: some View {
        if !options.isEmpty {
            HStack(spacing: JM.Space.s3) {
                if let label {
                    Text(label)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .frame(minWidth: 40, alignment: .leading)
                        .accessibilityHidden(true)
                }
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: JM.Space.s2) {
                        if !hideAll {
                            chip(nil, allLabel)
                        }
                        ForEach(options) { option in
                            chip(option.value, option.label)
                        }
                    }
                    .padding(.vertical, 1)
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityLabel(label ?? "Filter")
        }
    }

    private func chip(_ value: String?, _ text: String) -> some View {
        let on = active == value
        return Button {
            onChange(value == nil || on ? nil : value)
        } label: {
            Text(text)
                .jmText(.label)
                .lineLimit(1)
                .foregroundStyle(on ? Color.jmAccentSoftText : Color.jmFgSecondary)
                .padding(.horizontal, JM.Space.s3)
                .frame(height: JM.Size.controlSm)
                .background(on ? Color.jmAccentSoft : Color.clear, in: Capsule())
                .overlay(Capsule().strokeBorder(on ? Color.clear : Color.jmBorder, lineWidth: 1))
        }
        .buttonStyle(JMPlainStyle())
        .accessibilityAddTraits(on ? .isSelected : [])
    }
}

// MARK: - SystemLine (jm/SystemLine)

enum SystemTone {
    case neutral, done, error, action
}

/// A line that is not a message: a note from the app in the conversation.
struct SystemLine: View {
    var tone: SystemTone = .neutral
    var text: String
    var detail: String?
    var actionLabel: String?
    var action: () -> Void = {}

    var body: some View {
        HStack(alignment: .top, spacing: JM.Space.s2) {
            if tone != .action {
                Group {
                    switch tone {
                    case .done: Tick()
                    case .error: Icon(.alert, size: 16).foregroundStyle(Color.jmError)
                    default: Circle().fill(Color.jmFgSecondary).frame(width: 6, height: 6)
                    }
                }
                .frame(width: 16, height: 20)
            } else {
                Tag("Action needed", tone: .action)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(text)
                    .jmText(.bodySm)
                    .foregroundStyle(tone == .action || tone == .error ? Color.jmFg : Color.jmFgSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail, !detail.isEmpty {
                    Text(detail)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if let actionLabel {
                JMButton(actionLabel, size: .sm, action: action)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - TodaySummary (jm/TodaySummary)

struct TodaySummary: View {
    var items: [String] = []
    var needsYou = 0
    var unavailable = false

    private var line: String {
        if unavailable { return "Summary unavailable." }
        return items.isEmpty ? "Nothing yet today." : items.joined(separator: " · ")
    }

    var body: some View {
        HStack(spacing: JM.Space.s3) {
            Text("Today")
                .jmText(.caption)
                .foregroundStyle(Color.jmFgSecondary)
                .fixedSize()
            Text(line)
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFg)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            if unavailable {
                Text("The feed below is still live.")
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize()
            } else if needsYou > 0 {
                Tag("\(needsYou) \(needsYou == 1 ? "thing needs you" : "things need you")", tone: .action)
            } else {
                HStack(spacing: JM.Space.s2) {
                    Tick()
                    Text("All clear.").jmText(.bodySm).foregroundStyle(Color.jmFgSecondary)
                }
                .fixedSize()
            }
        }
        .padding(.horizontal, JM.Space.s4)
        .padding(.vertical, JM.Space.s2)
        .frame(minHeight: 48)
        .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous).strokeBorder(Color.jmBorder, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

// MARK: - SettingsSection (jm/SettingsSection)

struct SettingsRow: Identifiable {
    var id: String
    var title: String
    var detail: String?
    var actionLabel: String?
    var actionDisabled = false
    var action: () -> Void = {}
}

/// A status at the top right of a section: a tag when it asks for the
/// reader, otherwise a mark and a word.
struct StatusLabel: View {
    var status: StatusCopy
    var isError = false

    var body: some View {
        if status.tone == .action {
            Tag(status.label, tone: .action)
        } else {
            HStack(spacing: JM.Space.s2) {
                if isError {
                    Icon(.alert, size: 16).foregroundStyle(Color.jmError)
                } else if status.tone == .done {
                    Tick()
                } else if status.tone == .busy {
                    Dots()
                }
                Text(status.label)
                    .jmText(.bodySm)
                    .foregroundStyle(status.tone == .neutral ? Color.jmFgSecondary : Color.jmFg)
            }
            .fixedSize()
        }
    }
}

struct SettingsSection<Content: View, Actions: View>: View {
    var title: String
    var description: String?
    var status: StatusCopy?
    var rows: [SettingsRow] = []
    var error: String?
    /// False when there is nothing to draw in that slot right now, so the
    /// card leaves no gap for it.
    var showsContent = true
    var showsActions = true
    @ViewBuilder var content: () -> Content
    @ViewBuilder var actions: () -> Actions

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s4) {
            HStack(alignment: .top, spacing: JM.Space.s3) {
                VStack(alignment: .leading, spacing: JM.Space.s1) {
                    Text(title)
                        .jmText(.heading)
                        .foregroundStyle(Color.jmFg)
                        .accessibilityAddTraits(.isHeader)
                    if let description {
                        Text(description)
                            .jmText(.bodySm)
                            .foregroundStyle(Color.jmFgSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let status {
                    StatusLabel(status: status).padding(.top, 2)
                }
            }

            if !rows.isEmpty {
                VStack(spacing: 0) {
                    Rectangle().fill(Color.jmBorder).frame(height: 1)
                    ForEach(rows) { row in
                        HStack(spacing: JM.Space.s3) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(row.title).jmText(.bodySm).foregroundStyle(Color.jmFg).lineLimit(1)
                                if let detail = row.detail, !detail.isEmpty {
                                    Text(detail).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(2)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            if let label = row.actionLabel {
                                JMButton(label, kind: .ghost, size: .sm, action: row.action)
                                    .disabled(row.actionDisabled)
                            }
                        }
                        .padding(.vertical, JM.Space.s2)
                        .frame(minHeight: JM.Size.touch)
                        Rectangle().fill(Color.jmBorder).frame(height: 1)
                    }
                }
            }

            if showsContent && Content.self != EmptyView.self {
                VStack(alignment: .leading, spacing: JM.Space.s3) {
                    content()
                }
            }
            if let error, !error.isEmpty {
                ErrorLine(error)
            }
            if showsActions && Actions.self != EmptyView.self {
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    actions()
                }
            }
        }
        .padding(JM.Space.s5)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous).strokeBorder(Color.jmBorder, lineWidth: 1))
    }
}

extension SettingsSection where Content == EmptyView, Actions == EmptyView {
    init(title: String, description: String? = nil, status: StatusCopy? = nil, rows: [SettingsRow] = [], error: String? = nil) {
        self.init(
            title: title, description: description, status: status, rows: rows, error: error,
            content: { EmptyView() }, actions: { EmptyView() })
    }
}

extension SettingsSection where Actions == EmptyView {
    init(
        title: String, description: String? = nil, status: StatusCopy? = nil, rows: [SettingsRow] = [],
        error: String? = nil, showsContent: Bool = true, @ViewBuilder content: @escaping () -> Content
    ) {
        self.init(
            title: title, description: description, status: status, rows: rows, error: error,
            showsContent: showsContent, content: content, actions: { EmptyView() })
    }
}

extension SettingsSection where Content == EmptyView {
    init(
        title: String, description: String? = nil, status: StatusCopy? = nil, rows: [SettingsRow] = [],
        error: String? = nil, showsActions: Bool = true, @ViewBuilder actions: @escaping () -> Actions
    ) {
        self.init(
            title: title, description: description, status: status, rows: rows, error: error,
            showsActions: showsActions, content: { EmptyView() }, actions: actions)
    }
}

// MARK: - Sheet (Sheet in jm/shared/primitives.tsx)

private struct SheetHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = max(value, nextValue())
    }
}

/// The bottom sheet's panel: grab handle, title, close, then the content.
/// It is as tall as its content, up to 85% of the screen, then it scrolls.
struct JMSheet<Content: View>: View {
    var title: String
    var subtitle: String?
    var onClose: () -> Void
    @ViewBuilder var content: () -> Content

    @State private var contentHeight: CGFloat = 0
    @State private var drag: CGFloat = 0

    private var shape: UnevenRoundedRectangle {
        UnevenRoundedRectangle(
            topLeadingRadius: JM.Radius.xl, bottomLeadingRadius: 0, bottomTrailingRadius: 0,
            topTrailingRadius: JM.Radius.xl, style: .continuous)
    }

    var body: some View {
        GeometryReader { proxy in
            let safeBottom = proxy.safeAreaInsets.bottom
            let screen = proxy.size.height + proxy.safeAreaInsets.top + safeBottom
            let limit = max(120, min(screen * 0.85, proxy.size.height))
            VStack(spacing: 0) {
                Spacer(minLength: 0)
                ScrollView {
                    VStack(alignment: .leading, spacing: JM.Space.s4) {
                        header
                        content()
                    }
                    .padding(.horizontal, JM.Space.s5)
                    .padding(.top, JM.Space.s3)
                    .padding(.bottom, max(JM.Space.s5, safeBottom) - safeBottom)
                    .background(GeometryReader { inner in
                        Color.clear.preference(key: SheetHeightKey.self, value: inner.size.height)
                    })
                }
                .onPreferenceChange(SheetHeightKey.self) { contentHeight = $0 }
                .scrollBounceBehavior(.basedOnSize)
                .scrollDismissesKeyboard(.interactively)
                .frame(height: min(max(contentHeight, 1), limit))
                .frame(maxWidth: JM.Size.sheet)
                .background {
                    shape.fill(Color.jmPanel).ignoresSafeArea(edges: .bottom)
                }
                .overlay {
                    shape.strokeBorder(Color.jmBorder, lineWidth: 1).ignoresSafeArea(edges: .bottom)
                }
                .jmFloatShadow()
                .frame(maxWidth: .infinity)
                .offset(y: drag)
            }
        }
        .accessibilityAddTraits(.isModal)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: JM.Space.s4) {
            RoundedRectangle(cornerRadius: 2)
                .fill(Color.jmBorderStrong)
                .frame(width: 36, height: 4)
                .frame(maxWidth: .infinity)
                .accessibilityHidden(true)
            HStack(alignment: .top, spacing: JM.Space.s2) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .jmText(.title)
                        .foregroundStyle(Color.jmFg)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    if let subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .jmText(.bodySm)
                            .foregroundStyle(Color.jmFgSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button(action: onClose) {
                    Icon(.close, size: 16)
                        .foregroundStyle(Color.jmFgSecondary)
                        .frame(width: JM.Size.controlSm, height: JM.Size.controlSm)
                }
                .buttonStyle(JMPlainStyle())
                .accessibilityLabel("Close")
            }
        }
        .contentShape(Rectangle())
        .gesture(
            DragGesture()
                .onChanged { value in drag = max(0, value.translation.height) }
                .onEnded { value in
                    if value.translation.height > 80 { onClose() }
                    drag = 0
                }
        )
    }
}

private struct SheetHost<Panel: View>: ViewModifier {
    var isPresented: Bool
    var onClose: () -> Void
    @ViewBuilder var panel: () -> Panel

    func body(content: Content) -> some View {
        content.overlay {
            ZStack(alignment: .bottom) {
                if isPresented {
                    Color.jmScrim
                        .ignoresSafeArea()
                        .onTapGesture(perform: onClose)
                        .accessibilityLabel("Close")
                        .accessibilityAddTraits(.isButton)
                        .transition(.opacity)
                    panel()
                        .transition(.move(edge: .bottom))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottom)
            .animation(JM.Motion.easeOut(JM.Motion.sheet), value: isPresented)
        }
    }
}

extension View {
    /// Shows a bottom sheet over this screen while `isPresented` is true.
    /// The panel is a `JMSheet`.
    func jmSheet<Panel: View>(
        isPresented: Bool, onClose: @escaping () -> Void, @ViewBuilder panel: @escaping () -> Panel
    ) -> some View {
        modifier(SheetHost(isPresented: isPresented, onClose: onClose, panel: panel))
    }
}
