import { useState } from 'react'
import KindDot from './KindDot'
import { CHIP, kindStyle } from '../lib/kinds'
import { absoluteTime, relativeTime } from '../lib/time'
import type { FeedEvent } from '../lib/types'

/**
 * One wire-feed row: an opaque card sitting to the right of the dot spine.
 *
 * Body content is opaque per the tokens (glass is for chrome and featured
 * cards only), so this uses light.surface / dark.surface rather than any
 * translucent treatment.
 */
interface Props {
  event: FeedEvent
}

function attachmentLabel(a: Record<string, unknown>): string | null {
  // Attachments carry no schema, so pick the most human field available and
  // fall back to the type. Never render '[object Object]'.
  for (const key of ['title', 'subject', 'task_id', 'message_id', 'name']) {
    const v = a[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  const t = a.type
  return typeof t === 'string' ? t : null
}

export default function FeedRow({ event }: Props) {
  const { semantic, word } = kindStyle(event.kind)
  const [expanded, setExpanded] = useState(false)

  // Chip suppression. Two cases, and the second is the one that matters:
  //
  //  1. the chip text is literally repeated in the headline — plain noise;
  //  2. the chip IS the raw subject and the headline already covers it. The
  //     headline carries a *cleaned* topic (Re:/Fwd: and bracket-ids stripped),
  //     so a substring test would miss this and the raw subject — the exact
  //     telemetry the humanised copy exists to hide — would leak back in via
  //     the chip. Compare against event.subject, not against the headline.
  const body = (event.headline || event.message || '').toLowerCase()
  const rawSubject = event.subject?.trim().toLowerCase()
  const chips = event.attachments
    .map(attachmentLabel)
    .filter((l): l is string => !!l)
    .filter((l) => {
      const t = l.trim().toLowerCase()
      if (event.headline && rawSubject && t === rawSubject) return false
      return !body.includes(t.slice(0, 40))
    })

  return (
    // lg: the dot column is the fixed 32px gutter (no gap), so every card's
    // left edge lands on the same x. The dot is pushed down to the centre of
    // the headline line (--dot-top, set in index.css next to the density
    // rules it depends on) rather than the chip row.
    <li className="relative flex gap-3 lg:gap-0">
      {/* Dot column — sits on top of the spine drawn by the parent list. */}
      <div className="relative z-10 flex w-[15px] shrink-0 justify-center pt-[12px]
                      lg:w-[32px] lg:pt-[var(--dot-top)]">
        <KindDot semantic={semantic} variant="spine" />
      </div>

      <article
        // ELEVATION: a hairline border alone reads as a flat rectangle on a
        // flat ground. The design gives cards a soft drop plus, in dark mode, a
        // 1px inset top highlight so the top edge catches light the way the
        // hero and glass tiers do. Density: 14px inset and a 12px rhythm
        // instead of 16/18 — the design fits ~5 rows per viewport, and the
        // padding was most of what it cost.
        className="mb-3 min-w-0 flex-1 rounded-list border border-light-border
                   bg-light-surface px-4 py-3.5 shadow-lift
                   dark:border-dark-border dark:bg-dark-surface dark:shadow-lift-dark
                   hover-lift lg:mb-4 lg:rounded-card lg:px-6 lg:py-5
                   lg:hover:shadow-glass dark:lg:hover:shadow-glass-dark"
      >
        {/* THE KIND IS A CHIP; THE SKILL IS A FOOTNOTE.
            These were two mono labels of identical weight sitting side by side,
            so the row opened with a pair of grey words and the reader had to
            work out which one mattered. The kind is the answer to "what
            happened" and now carries a tinted container; the skill is
            provenance and sits after it, quieter and one step smaller. The
            hierarchy is the point — not the colour. */}
        <header className="flex items-center gap-2">
          <span className={CHIP[semantic]}>{word}</span>
          <span
            className="min-w-0 truncate font-mono text-[10px] font-medium uppercase
                       tracking-[0.06em] text-light-text-muted dark:text-dark-text-muted"
          >
            {event.skill}
          </span>
          <time
            dateTime={event.ts}
            title={absoluteTime(event.ts)}
            className="ml-auto shrink-0 text-[11.5px] font-medium
                       text-light-text-muted dark:text-dark-text-muted"
          >
            {relativeTime(event.ts)}
          </time>
        </header>

        {/* headline is the card body. `message` is the fallback for events
            written before the humanised schema existed — old rows still read. */}
        {/* Tight leading is what makes a two-line headline read as one title
            rather than two sentences. */}
        {/* TYPE CONFIDENCE: 600 not 500, and -0.006em. At 500 the headline had
            the same weight as the body copy under it and the card had no
            entry point; the tighter tracking is what keeps the extra weight
            from also reading as wider. */}
        <p className="mt-2 text-[13.9px] font-semibold leading-[1.3] tracking-[-0.006em]
                      text-light-text-primary dark:text-dark-text-primary">
          {event.headline || event.message}
        </p>

        {event.detail && (
          <>
            {/* A mono uppercase "DETAILS" read as another metadata label in a
                row that already has two — nothing about it said "tap me". A
                chevron that rotates is the affordance: it shows state as well
                as invitation, and it is the same 2px stroke-only icon language
                as the rest of the app. */}
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="mt-2 flex items-center gap-1 text-[12px] font-medium
                         text-light-text-muted dark:text-dark-text-muted
                         lg:transition-colors lg:duration-[120ms]
                         lg:hover:text-light-text-primary dark:lg:hover:text-dark-text-primary"
            >
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className={`h-3.5 w-3.5 transition-transform duration-150 ${expanded ? 'rotate-90' : ''}`}
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 6l6 6-6 6" />
              </svg>
              {expanded ? 'Less' : 'Details'}
            </button>
            {expanded && (
              <p className="mt-2 break-words border-l-2 border-light-border pl-3
                            font-mono text-caption text-light-text-secondary
                            dark:border-dark-border dark:text-dark-text-secondary">
                {event.detail}
              </p>
            )}
          </>
        )}

        {chips.length > 0 && (
          <ul className="mt-3 flex flex-wrap gap-2">
            {chips.map((label, i) => (
              <li
                key={i}
                className="rounded-chip border border-light-border px-2 py-1
                           font-mono text-label-mono uppercase
                           text-light-text-secondary
                           dark:border-dark-border dark:text-dark-text-secondary"
              >
                {label}
              </li>
            ))}
          </ul>
        )}
      </article>
    </li>
  )
}
