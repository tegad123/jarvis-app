import SwiftUI
import JarvisKit

/// Activity: today's summary and what Jarvis has done, newest first.
/// Follows frontend/src/screens/Feed.tsx.
struct ActivityScreen: View {
    @EnvironmentObject private var model: AppModel

    @State private var events: [FeedEvent]?
    @State private var summary: FeedSummary?
    @State private var summaryLoaded = false
    @State private var problem: LoadProblem?
    @State private var kindFilter: String?
    @State private var skillFilter: String?

    private var all: [FeedEvent] { events ?? [] }

    private var kindOptions: [SelectOption] {
        Set(all.map(\.kind)).sorted().map { SelectOption(value: $0, label: KindStyle.of($0).word) }
    }

    private var skillOptions: [SelectOption] {
        Set(all.map(\.skill)).sorted().map { SelectOption(value: $0, label: $0) }
    }

    private var visible: [FeedEvent] {
        all.filter { event in
            (kindFilter == nil || event.kind == kindFilter) && (skillFilter == nil || event.skill == skillFilter)
        }
    }

    var body: some View {
        Page(title: "Activity") {
            if summaryLoaded {
                let day = summary.map { DaySummary(counts: $0.counts) }
                TodaySummary(items: day?.items ?? [], needsYou: day?.needsYouCount ?? 0, unavailable: day == nil)
            }

            if let problem {
                ErrorState(
                    label: "Feed unavailable", message: problem.message, detail: problem.detail,
                    stale: !all.isEmpty, onRetry: { Task { await load() } })
            }

            if !kindOptions.isEmpty || !skillOptions.isEmpty {
                VStack(alignment: .leading, spacing: JM.Space.s2) {
                    FilterChips(label: "Kind", options: kindOptions, active: kindFilter) { kindFilter = $0 }
                    FilterChips(label: "Skill", options: skillOptions, active: skillFilter) { skillFilter = $0 }
                }
            }

            if events == nil && problem == nil {
                LoadingLine()
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, JM.Space.s8)
            } else if visible.isEmpty && problem == nil {
                EmptyState(icon: .activity, text: "Jarvis is watching. Nothing needs you right now.")
            } else {
                JMList(items: visible.numbered, plain: true) { row in
                    ActivityRow(event: row.value)
                }
            }
        }
        .task {
            while !Task.isCancelled {
                await load()
                try? await Task.sleep(nanoseconds: 15_000_000_000)
            }
        }
    }

    /// A whole page each time, not a merge: a page cannot drift out of order.
    /// A summary that fails never blanks the list.
    private func load() async {
        async let feed = model.client.feed(limit: 50)
        async let today = model.client.feedSummary(timeZone: TimeZone.current.identifier)
        do {
            events = try await feed
            problem = nil
        } catch {
            _ = try? await today
            // What is already on screen stays there.
            problem = model.problem(
                for: error, message: "Jarvis could not read the activity feed.",
                offline: "Can't reach Jarvis right now.")
            return
        }
        do {
            summary = try await today
        } catch {
            // A 401 here means the session is gone for the feed too.
            _ = model.problem(for: error, message: "")
            summary = nil
        }
        summaryLoaded = true
    }
}

// MARK: - ActivityRow (jm/ActivityRow)

struct ActivityRow: View {
    var event: FeedEvent
    @State private var open = false

    private var style: KindStyle { KindStyle.of(event.kind) }

    var body: some View {
        let detail = event.detail ?? ""
        let chips = FeedWording.chips(event)
        VStack(alignment: .leading, spacing: JM.Space.s1) {
            HStack(spacing: JM.Space.s2) {
                kind
                Text(event.skill)
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Text(JarvisTime.relative(event.ts))
                    .jmText(.caption)
                    .foregroundStyle(Color.jmFgSecondary)
                    .fixedSize()
                    .accessibilityLabel(JarvisTime.absolute(event.ts))
            }
            Text(FeedWording.headline(event))
                .jmText(.bodySm)
                .foregroundStyle(Color.jmFg)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 2)
            if !detail.isEmpty {
                Disclose(open: open, label: open ? "Less" : "Details") { open.toggle() }
                if open {
                    Text(detail)
                        .jmText(.caption)
                        .foregroundStyle(Color.jmFgSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                        .padding(.horizontal, JM.Space.s3)
                        .padding(.vertical, JM.Space.s2)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.jmPanel, in: RoundedRectangle(cornerRadius: JM.Radius.md, style: .continuous))
                        .padding(.top, JM.Space.s1)
                }
            }
            if !chips.isEmpty {
                FlowLayout(spacing: JM.Space.s2, lineSpacing: JM.Space.s2) {
                    ForEach(chips.numbered) { chip in
                        Text(chip.value)
                            .jmText(.caption)
                            .foregroundStyle(Color.jmFgSecondary)
                            .lineLimit(1)
                            .padding(.horizontal, JM.Space.s2)
                            .padding(.vertical, 2)
                            .overlay(Capsule().strokeBorder(Color.jmBorder, lineWidth: 1))
                    }
                }
                .padding(.top, JM.Space.s1)
            }
        }
    }

    /// Needs-you is the blue pill; done has the check; a real failure has
    /// the red alert glyph; the rest is a quiet word.
    @ViewBuilder
    private var kind: some View {
        switch style.semantic {
        case .warning:
            Tag(style.word, tone: .action)
        case .success:
            HStack(spacing: JM.Space.s1) {
                Tick(size: 14)
                Text(style.word).jmText(.caption).foregroundStyle(Color.jmFgSecondary)
            }
            .fixedSize()
        case .alert:
            HStack(spacing: JM.Space.s1) {
                Icon(.alert, size: 14).foregroundStyle(Color.jmError)
                Text(style.word).jmText(.caption).foregroundStyle(Color.jmFg)
            }
            .fixedSize()
        case .info, .neutral:
            Text(style.word).jmText(.caption).foregroundStyle(Color.jmFgSecondary).fixedSize()
        }
    }
}

/// The "Details" toggle under a row.
struct Disclose: View {
    var open: Bool
    var label: String
    var action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: JM.Space.s1) {
                Icon(.chevron, size: 14)
                    .rotationEffect(.degrees(open ? 90 : 0))
                    .animation(JM.Motion.easeOut(JM.Motion.press), value: open)
                Text(label).jmText(.caption)
            }
            .foregroundStyle(Color.jmFgSecondary)
            .padding(.vertical, 2)
            .padding(.trailing, JM.Space.s1)
        }
        .buttonStyle(JMPlainStyle())
        .accessibilityValue(open ? "Expanded" : "Collapsed")
    }
}
