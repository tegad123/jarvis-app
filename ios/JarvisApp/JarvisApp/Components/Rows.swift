import SwiftUI
import JarvisKit

// The small parts the task and lead rows share (jm/TaskRow, jm/LeadRow and
// the row helpers in jm/shared/primitives.tsx).

enum MetaItem {
    case plain(String)
    /// Drawn as the blue pill: an overdue date.
    case action(String)
}

/// A row's quiet last line: items with a middle dot between them, wrapping.
struct MetaLine: View {
    var items: [MetaItem]

    var body: some View {
        FlowLayout(spacing: 0, lineSpacing: JM.Space.s1) {
            ForEach(items.numbered) { item in
                HStack(spacing: 0) {
                    if item.id > 0 {
                        Text("·")
                            .jmText(.caption)
                            .foregroundStyle(Color.jmFgSecondary)
                            .padding(.horizontal, 6)
                            .accessibilityHidden(true)
                    }
                    switch item.value {
                    case .plain(let text):
                        Text(text).jmText(.caption).foregroundStyle(Color.jmFgSecondary).lineLimit(1)
                    case .action(let text):
                        Tag(text, tone: .action)
                    }
                }
            }
        }
    }
}

/// The small spark: Jarvis filed or added this.
struct SparkGlyph: View {
    var label: String

    var body: some View {
        Icon(.spark, size: 14)
            .foregroundStyle(Color.jmFgSecondary)
            .accessibilityHidden(false)
            .accessibilityLabel(label)
    }
}

/// The outlined pill a status sits in: a mark and a word.
struct StatusPill<Mark: View>: View {
    var word: String
    @ViewBuilder var mark: () -> Mark

    var body: some View {
        HStack(spacing: 6) {
            mark()
            Text(word).jmText(.caption).foregroundStyle(Color.jmFg).lineLimit(1)
        }
        .padding(.horizontal, JM.Space.s2)
        .padding(.vertical, 2)
        .overlay(Capsule().strokeBorder(Color.jmBorder, lineWidth: 1))
        .contentShape(Capsule())
        .fixedSize()
    }
}

/// A task's status mark: a check when done, a filled ring when in progress,
/// an empty ring when open.
struct TaskStatusMark: View {
    var status: String

    var body: some View {
        switch status {
        case "done":
            Tick(size: 14)
        case "in_progress":
            ZStack {
                Circle().strokeBorder(Color.jmFg, lineWidth: 1.5)
                Circle().fill(Color.jmFg).frame(width: 3, height: 3)
            }
            .frame(width: 10, height: 10)
        case "archived":
            EmptyView()
        default:
            Circle().strokeBorder(Color.jmBorderStrong, lineWidth: 1.5).frame(width: 10, height: 10)
        }
    }
}

/// Three rising bars: lit in the text colour, unlit in the strong border.
/// Never coloured.
struct TempBars: View {
    var rank: Int

    var body: some View {
        HStack(alignment: .bottom, spacing: 3) {
            ForEach(0..<3, id: \.self) { index in
                RoundedRectangle(cornerRadius: 2)
                    .fill(index < rank ? Color.jmFg : Color.jmBorderStrong)
                    .frame(width: 3, height: CGFloat(6 + index * 3))
            }
        }
        .frame(width: 24, height: 24)
        .contentShape(Rectangle())
    }
}
