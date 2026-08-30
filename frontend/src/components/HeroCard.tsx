import type React from 'react'
import { kindStyle, needsYou } from '../lib/kinds'
import type { FeedSummary } from '../lib/types'

/**
 * The smoked hero — the one status surface on the feed screen.
 *
 * Per the tokens the smoked hero is its own thing, NOT a glass tier: an opaque
 * ink gradient that looks identical in light and dark. So everything inside it
 * is styled for a dark ground in both modes, with no dark: variants.
 *
 * Two variants, both real paths:
 *   - "N things need you"  when today has warning/alert events
 *   - "All clear."         when it does not
 *
 * Counts come from the server (/api/feed/summary), NOT from the rendered page.
 * They used to be derived from the same 50 events the list showed, so once the
 * feed passed 50 events "today" silently became "the last 50" — adding events
 * could make a count go down. The server counts the whole day; this component
 * only turns kinds into words.
 */
interface Props {
  /** null while loading or when the summary call failed. */
  summary: FeedSummary | null
}

export interface HeroSummary {
  todayCount: number
  needsYouCount: number
  /** e.g. "1 task created · 1 done · 1 email filed" */
  aggregate: string
}

/**
 * "Email filed" x5 -> "5 emails filed", not "5 email filed".
 *
 * The kind map's words label ONE event ("Email filed", "Task created"), so
 * using them verbatim in a count produces the telemetry grammar this pass
 * exists to remove. The noun is the first word and the only part that
 * inflects; the verb stays put. Words that are already a complete state
 * ("Done") take no plural at all — "3 dones" is not English.
 */
const NO_PLURAL = new Set(['done'])

function pluralise(word: string, n: number): string {
  const lower = word.toLowerCase()
  if (n === 1 || NO_PLURAL.has(lower)) return lower
  const [noun, ...rest] = lower.split(' ')
  const plural = /(s|x|z|ch|sh)$/.test(noun) ? `${noun}es` : `${noun}s`
  return [plural, ...rest].join(' ')
}

/** Exported for testing and so the copy logic lives in one place. */
export function summarise(byKind: Record<string, number>): HeroSummary {
  // Collapse raw kinds into display WORDS, so 'task.created' and any future
  // synonym mapping to the same word share one tally.
  const counts = new Map<string, number>()
  let needsYouCount = 0
  let todayCount = 0

  for (const [kind, n] of Object.entries(byKind)) {
    const { word, semantic } = kindStyle(kind)
    counts.set(word, (counts.get(word) ?? 0) + n)
    if (needsYou(semantic)) needsYouCount += n
    todayCount += n
  }

  const aggregate = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([word, n]) => `${n} ${pluralise(word, n)}`)
    .join(' · ')

  return { todayCount, needsYouCount, aggregate }
}

/**
 * DESKTOP STRIP (lg+). On a 1000px-wide column the mobile hero is a billboard
 * — three lines of type on 150px of navy with nothing to say. The desktop
 * form is one 88px line: kicker · count · status. Same words, same data,
 * same smoked surface; only the shape changes. Rendered alongside the mobile
 * block and switched by the breakpoint, so neither layout is ever computed
 * from a media query in JS.
 */
function Strip({ children }: { children: React.ReactNode }) {
  return (
    <section
      className="smoked-hero hidden h-[88px] items-center gap-4 overflow-hidden
                 rounded-card px-6 lg:flex"
    >
      {children}
    </section>
  )
}

function Sep() {
  return (
    <span aria-hidden="true" className="shrink-0 text-hero text-ink-300/60">
      ·
    </span>
  )
}

export default function HeroCard({ summary }: Props) {
  // No summary means we genuinely do not know today's numbers. Show that,
  // rather than counting the rendered page — numbers that look authoritative
  // and are quietly wrong are worse than an honest gap.
  if (!summary) {
    return (
      <>
        <section className="smoked-hero overflow-hidden rounded-hero p-5 lg:hidden">
          <p className="font-mono text-label-mono uppercase text-ink-300">Today</p>
          <p className="mt-2 text-hero text-ink-50">Summary unavailable.</p>
          <p className="mt-3 text-screen-title text-ink-300">
            The feed below is still live.
          </p>
        </section>
        <Strip>
          <p className="shrink-0 font-mono text-label-mono uppercase text-ink-300">Today</p>
          <Sep />
          <p className="min-w-0 truncate text-hero text-ink-50">Summary unavailable.</p>
          <Sep />
          <p className="shrink-0 text-hero text-ink-300">The feed below is still live.</p>
        </Strip>
      </>
    )
  }

  const { todayCount, needsYouCount, aggregate } = summarise(summary.counts)
  const countLine = todayCount === 0 ? 'Nothing yet today.' : aggregate
  const status =
    needsYouCount > 0 ? (
      <>
        <span
          aria-hidden="true"
          className="block h-[7px] w-[7px] rounded-full bg-light-warning"
        />
        {needsYouCount} {needsYouCount === 1 ? 'thing needs' : 'things need'} you
      </>
    ) : (
      'All clear.'
    )

  return (
    <>
      <section className="smoked-hero overflow-hidden rounded-hero p-5 lg:hidden">
        <p className="font-mono text-label-mono uppercase text-ink-300">Today</p>

        <p className="mt-2 text-hero text-ink-50">{countLine}</p>

        {needsYouCount > 0 ? (
          <p className="mt-3 flex items-center gap-2 text-screen-title text-white">{status}</p>
        ) : (
          <p className="mt-3 text-screen-title text-white">{status}</p>
        )}
      </section>

      <Strip>
        <p className="shrink-0 font-mono text-label-mono uppercase text-ink-300">Today</p>
        <Sep />
        <p className="min-w-0 truncate text-hero text-ink-50">{countLine}</p>
        <Sep />
        <p className="flex shrink-0 items-center gap-2 text-screen-title text-white">{status}</p>
      </Strip>
    </>
  )
}
