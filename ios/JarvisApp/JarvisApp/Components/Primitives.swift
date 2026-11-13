import SwiftUI
import JarvisKit

// The small Jarvis Mono parts, one for one with frontend/src/jm/shared/
// primitives.tsx and jm/Button: button, tag, tick, dots, avatar, field,
// select, list.

// MARK: - Button (jm/Button/Button.tsx)

enum JMButtonKind {
    case primary, secondary, ghost
}

enum JMButtonSize {
    case md, sm

    var height: CGFloat { self == .md ? JM.Size.control : JM.Size.controlSm }
    var padding: CGFloat { self == .md ? JM.Space.s4 : JM.Space.s3 }
    var gap: CGFloat { self == .md ? JM.Space.s2 : JM.Space.s1 }
    var icon: CGFloat { self == .md ? 18 : 16 }
}

/// The pill button. With an icon and no title it is a circle.
struct JMButton: View {
    var title: String?
    var icon: JarvisIcon?
    var kind: JMButtonKind = .secondary
    var size: JMButtonSize = .md
    /// Stretch to the width on offer.
    var wide = false
    /// A 44pt touch target, for a sheet's or the sign-in screen's main action.
    var tall = false
    /// Read aloud in place of the title: what an icon or a bare number means.
    var label: String?
    var action: () -> Void

    init(
        _ title: String? = nil, icon: JarvisIcon? = nil, kind: JMButtonKind = .secondary, size: JMButtonSize = .md,
        wide: Bool = false, tall: Bool = false, label: String? = nil, action: @escaping () -> Void
    ) {
        self.title = title
        self.icon = icon
        self.kind = kind
        self.size = size
        self.wide = wide
        self.tall = tall
        self.label = label
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            JMButtonLabel(title: title, icon: icon, size: size)
        }
        .buttonStyle(JMButtonStyle(kind: kind, size: size, iconOnly: title == nil, wide: wide, tall: tall))
        .accessibilityLabel(label ?? title ?? "")
    }
}

/// What sits inside a button: an icon, a title, or both.
struct JMButtonLabel: View {
    var title: String?
    var icon: JarvisIcon?
    var size: JMButtonSize = .md

    var body: some View {
        HStack(spacing: size.gap) {
            if let icon {
                Icon(icon, size: size.icon)
            }
            if let title {
                Text(title)
                    .jmText(.label)
                    .lineLimit(1)
            }
        }
    }
}

struct JMButtonStyle: ButtonStyle {
    var kind: JMButtonKind = .secondary
    var size: JMButtonSize = .md
    var iconOnly = false
    var wide = false
    var tall = false

    func makeBody(configuration: Configuration) -> some View {
        JMButtonChrome(
            label: configuration.label, pressed: configuration.isPressed,
            kind: kind, size: size, iconOnly: iconOnly, wide: wide, tall: tall)
    }
}

private struct JMButtonChrome<Label: View>: View {
    var label: Label
    var pressed: Bool
    var kind: JMButtonKind
    var size: JMButtonSize
    var iconOnly: Bool
    var wide: Bool
    var tall: Bool
    @Environment(\.isEnabled) private var isEnabled

    private var height: CGFloat { tall ? JM.Size.touch : size.height }

    private var foreground: Color {
        switch kind {
        case .primary: return .jmOnAccent
        case .secondary: return .jmFg
        case .ghost: return pressed ? .jmFg : .jmFgSecondary
        }
    }

    private var fill: Color {
        switch kind {
        case .primary: return pressed ? .jmAccentPressed : .jmAccent
        case .secondary: return .jmPanelRaised
        case .ghost: return pressed ? .jmPanel : .clear
        }
    }

    private var edge: Color {
        guard kind == .secondary else { return .clear }
        return pressed ? .jmBorderStrong : .jmBorder
    }

    var body: some View {
        label
            .foregroundStyle(foreground)
            .padding(.horizontal, iconOnly ? 0 : size.padding)
            .frame(minWidth: iconOnly ? height : nil, maxWidth: wide ? .infinity : nil, minHeight: height)
            .frame(width: iconOnly ? height : nil)
            .background(fill, in: Capsule())
            .overlay(Capsule().strokeBorder(edge, lineWidth: 1))
            .contentShape(Capsule())
            .opacity(isEnabled ? 1 : JM.disabledOpacity)
            .animation(JM.Motion.easeOut(JM.Motion.press), value: pressed)
    }
}

/// A plain tappable area with no chrome of its own: rows, chips, links.
struct JMPlainStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .opacity(configuration.isPressed ? 0.7 : 1)
            .contentShape(Rectangle())
    }
}

// MARK: - Marks

/// The one signal mark: a check, in the accent text colour.
struct Tick: View {
    var size: CGFloat = 16

    var body: some View {
        JarvisIconShape(icon: .check)
            .stroke(style: StrokeStyle(lineWidth: 2.25 * size / 24, lineCap: .round, lineJoin: .round))
            .foregroundStyle(Color.jmAccentText)
            .frame(width: size, height: size)
            .accessibilityHidden(true)
    }
}

/// Three dots that take turns: something is working.
struct Dots: View {
    @State private var on = false

    var body: some View {
        HStack(spacing: JM.Space.s1) {
            ForEach(0..<3, id: \.self) { index in
                Circle()
                    .fill(Color.jmFgSecondary)
                    .frame(width: 6, height: 6)
                    .opacity(on ? 1 : 0.3)
                    .animation(
                        .easeInOut(duration: JM.Motion.dots / 2)
                            .repeatForever(autoreverses: true)
                            .delay(Double(index) * JM.Motion.dotOffset),
                        value: on)
            }
        }
        .onAppear { on = true }
        .accessibilityLabel("Working")
    }
}

/// A small pulsing dot: this step is running now.
struct LiveDot: View {
    @State private var on = false

    var body: some View {
        Circle()
            .fill(Color.jmFg)
            .frame(width: 6, height: 6)
            .opacity(on ? 0.35 : 1)
            .animation(.easeInOut(duration: JM.Motion.live / 2).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
            .accessibilityHidden(true)
    }
}

enum TagTone {
    case neutral, action
}

/// A pill label. `action` is the one that asks for the reader.
struct Tag: View {
    var text: String
    var tone: TagTone = .neutral
    /// Past this width the label truncates; without it the tag never shrinks.
    var maxWidth: CGFloat?
    /// The action tag's dot. A lead's "No Response" has the tint without it.
    var dot = true

    init(_ text: String, tone: TagTone = .neutral, maxWidth: CGFloat? = nil, dot: Bool = true) {
        self.text = text
        self.tone = tone
        self.maxWidth = maxWidth
        self.dot = dot
    }

    private var pill: some View {
        HStack(spacing: JM.Space.s1) {
            if tone == .action && dot {
                Circle().frame(width: 6, height: 6)
            }
            Text(text).jmText(.caption).lineLimit(1)
        }
        .foregroundStyle(tone == .action ? Color.jmAccentSoftText : Color.jmFgSecondary)
        .padding(.horizontal, JM.Space.s2)
        .padding(.vertical, 2)
        .background(tone == .action ? Color.jmAccentSoft : Color.clear, in: Capsule())
        .overlay(Capsule().strokeBorder(tone == .action ? Color.clear : Color.jmBorder, lineWidth: 1))
    }

    var body: some View {
        if let maxWidth {
            HugLayout(maxWidth: maxWidth) { pill }
        } else {
            pill.fixedSize()
        }
    }
}

/// An alert glyph and a sentence: something did not work.
struct ErrorLine: View {
    var text: String
    var small = false

    init(_ text: String, small: Bool = false) {
        self.text = text
        self.small = small
    }

    var body: some View {
        HStack(alignment: .top, spacing: JM.Space.s2) {
            Icon(.alert, size: 16)
                .foregroundStyle(Color.jmError)
                .padding(.top, small ? 0 : 2)
            Text(text)
                .jmText(small ? .caption : .bodySm)
                .foregroundStyle(Color.jmFg)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// Initials in a circle.
struct Avatar: View {
    var name: String?
    var outline = false

    var body: some View {
        Text(Wording.initials(name))
            .jmText(.caption)
            .foregroundStyle(outline ? Color.jmFgSecondary : Color.jmFg)
            .frame(width: 32, height: 32)
            .background(outline ? Color.clear : Color.jmPanelRaised, in: Circle())
            .overlay(Circle().strokeBorder(outline ? Color.jmBorderStrong : Color.jmBorder, lineWidth: 1))
            .accessibilityHidden(true)
    }
}

// MARK: - Field and select

/// A text field: pill, strong edge, background fill. `large` for sign-in
/// and sheets.
struct JMField: View {
    var placeholder: String
    @Binding var text: String
    var large = false
    var secure = false
    var centered = false
    /// Extra tracking, in points, for the PIN.
    var spacing: CGFloat = 0
    /// Side padding, when something sits inside the field's edge.
    var inset: CGFloat?
    var onSubmit: () -> Void = {}

    @FocusState private var focused: Bool

    init(
        _ placeholder: String, text: Binding<String>, large: Bool = false, secure: Bool = false,
        centered: Bool = false, spacing: CGFloat = 0, inset: CGFloat? = nil, onSubmit: @escaping () -> Void = {}
    ) {
        self.placeholder = placeholder
        self._text = text
        self.large = large
        self.secure = secure
        self.centered = centered
        self.spacing = spacing
        self.inset = inset
        self.onSubmit = onSubmit
    }

    private var style: JMTextStyle { large ? .body : .bodySm }

    private var prompt: Text {
        Text(placeholder).foregroundColor(.jmFgSecondary)
    }

    var body: some View {
        Group {
            if secure {
                SecureField("", text: $text, prompt: prompt)
            } else {
                TextField("", text: $text, prompt: prompt)
            }
        }
        .font(style.font())
        .tracking(spacing)
        .foregroundStyle(Color.jmFg)
        .multilineTextAlignment(centered ? .center : .leading)
        .focused($focused)
        .onSubmit(onSubmit)
        .padding(.horizontal, inset ?? (large ? JM.Space.s5 : JM.Space.s4))
        .frame(height: large ? JM.Size.touch : JM.Size.control)
        .frame(maxWidth: .infinity)
        .background(Color.jmBg, in: Capsule())
        .overlay(Capsule().strokeBorder(Color.jmBorderStrong, lineWidth: 1))
        .jmFocusRing(focused, in: Capsule())
        .accessibilityLabel(placeholder)
    }
}

extension View {
    /// The web's focus ring: a 2pt gap, then 2pt of accent.
    func jmFocusRing<S: InsettableShape>(_ on: Bool, in shape: S) -> some View {
        overlay(
            shape
                .inset(by: -3)
                .stroke(Color.jmAccent, lineWidth: 2)
                .opacity(on ? 1 : 0)
        )
    }
}

struct SelectOption: Identifiable, Equatable {
    var value: String
    var label: String
    var id: String { value }
}

/// The web's small select: a pill that opens the system menu.
struct JMSelect: View {
    var label: String
    var value: String
    var options: [SelectOption]
    var onChange: (String) -> Void

    private var current: String {
        options.first { $0.value == value }?.label ?? options.first?.label ?? ""
    }

    var body: some View {
        Menu {
            ForEach(options) { option in
                Button {
                    onChange(option.value)
                } label: {
                    if option.value == value {
                        Label(option.label, systemImage: "checkmark")
                    } else {
                        Text(option.label)
                    }
                }
            }
        } label: {
            HugLayout(maxWidth: 180) {
                HStack(spacing: JM.Space.s1) {
                    Text(current)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFg)
                        .lineLimit(1)
                    Icon(.chevron, size: 14)
                        .rotationEffect(.degrees(90))
                        .foregroundStyle(Color.jmFgSecondary)
                }
                .padding(.leading, JM.Space.s3)
                .padding(.trailing, 10)
                .frame(height: JM.Size.controlSm)
                .background(Color.jmBg, in: Capsule())
                .overlay(Capsule().strokeBorder(Color.jmBorderStrong, lineWidth: 1))
                .contentShape(Capsule())
            }
        }
        .accessibilityLabel(label)
        .accessibilityValue(current)
    }
}

/// A date the web takes with <input type="date">: a pill that shows the day
/// and opens a calendar under it. Empty means no date.
struct JMDateField: View {
    var label: String
    /// YYYY-MM-DD, or empty.
    @Binding var day: String
    @State private var open = false

    private var picked: Binding<Date> {
        Binding(
            get: { JarvisFormat.localDate(day) ?? Date() },
            set: { day = JarvisFormat.dayString($0) })
    }

    var body: some View {
        VStack(alignment: .leading, spacing: JM.Space.s2) {
            HStack(spacing: JM.Space.s2) {
                Button {
                    if day.isEmpty { day = JarvisFormat.dayString(Date()) }
                    open.toggle()
                } label: {
                    Text(day.isEmpty ? label : JarvisFormat.shortDay(day))
                        .jmText(.bodySm)
                        .foregroundStyle(day.isEmpty ? Color.jmFgSecondary : Color.jmFg)
                        .padding(.horizontal, JM.Space.s4)
                        .frame(height: JM.Size.control)
                        .background(Color.jmBg, in: Capsule())
                        .overlay(Capsule().strokeBorder(Color.jmBorderStrong, lineWidth: 1))
                }
                .buttonStyle(JMPlainStyle())
                .accessibilityLabel(label)
                .accessibilityValue(day.isEmpty ? "No date" : JarvisFormat.shortDay(day))
                if !day.isEmpty {
                    JMButton("Clear", kind: .ghost, size: .sm) {
                        day = ""
                        open = false
                    }
                }
            }
            if open {
                DatePicker(label, selection: picked, displayedComponents: .date)
                    .datePickerStyle(.graphical)
                    .labelsHidden()
                    .tint(Color.jmAccent)
            }
        }
    }
}

// MARK: - List

/// A bordered list on a panel (List in primitives.tsx). Rows are divided by
/// a hairline; the last has none.
struct JMList<Data: RandomAccessCollection, Row: View>: View where Data.Element: Identifiable {
    var items: Data
    /// Rows straight on the background, with no card (Rows in primitives.tsx).
    var plain = false
    @ViewBuilder var row: (Data.Element) -> Row

    var body: some View {
        let last = items.last?.id
        let rows = VStack(spacing: 0) {
            ForEach(items) { item in
                row(item)
                    .padding(.vertical, JM.Space.s3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .overlay(alignment: .bottom) {
                        if item.id != last {
                            Rectangle().fill(Color.jmBorder).frame(height: 1)
                        }
                    }
            }
        }
        if plain {
            rows
        } else {
            rows
                .padding(.horizontal, JM.Space.s4)
                .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous)
                        .strokeBorder(Color.jmBorder, lineWidth: 1))
        }
    }
}

/// A row and its place in the list, for rows with no id of their own.
struct Numbered<Value>: Identifiable {
    let id: Int
    let value: Value
}

extension Array {
    var numbered: [Numbered<Element>] {
        enumerated().map { Numbered(id: $0.offset, value: $0.element) }
    }
}

/// A bordered card: the frame of ApprovalCard, SettingsSection and the
/// quick-add forms.
struct JMCard<Content: View>: View {
    var padding: CGFloat = JM.Space.s4
    var spacing: CGFloat = JM.Space.s3
    var filled = true
    var edge: Color = .jmBorder
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: spacing) {
            content()
        }
        .padding(padding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(filled ? Color.jmPanel : Color.clear, in: RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: JM.Radius.lg, style: .continuous).strokeBorder(edge, lineWidth: 1))
    }
}

// MARK: - Wrapping row

/// Lays children out left to right and wraps, like `flex-wrap`.
struct FlowLayout: Layout {
    var spacing: CGFloat = JM.Space.s2
    var lineSpacing: CGFloat = JM.Space.s2

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        return CGSize(width: rows.width, height: rows.height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let rows = arrange(width: bounds.width, subviews: subviews)
        for (index, origin) in rows.origins.enumerated() {
            subviews[index].place(
                at: CGPoint(x: bounds.minX + origin.x, y: bounds.minY + origin.y),
                anchor: .topLeading,
                proposal: ProposedViewSize(width: min(rows.sizes[index].width, bounds.width), height: nil))
        }
    }

    private func arrange(width: CGFloat, subviews: Subviews) -> (origins: [CGPoint], sizes: [CGSize], width: CGFloat, height: CGFloat) {
        var origins: [CGPoint] = []
        var sizes: [CGSize] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var widest: CGFloat = 0
        for subview in subviews {
            let size = subview.sizeThatFits(ProposedViewSize(width: width.isFinite ? width : nil, height: nil))
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + lineSpacing
                rowHeight = 0
            }
            origins.append(CGPoint(x: x, y: y))
            sizes.append(size)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            widest = max(widest, x - spacing)
        }
        return (origins, sizes, widest, y + rowHeight)
    }
}

/// Hugs its one child, up to a width: what `max-width` does to an inline
/// box on the web. Past the width the child is squeezed, so text truncates.
struct HugLayout: Layout {
    var maxWidth: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        guard let child = subviews.first else { return .zero }
        let ideal = child.sizeThatFits(.unspecified)
        let width = min(ideal.width, maxWidth, proposal.width ?? .infinity)
        return child.sizeThatFits(ProposedViewSize(width: width, height: proposal.height))
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(
            at: bounds.origin, anchor: .topLeading,
            proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}
