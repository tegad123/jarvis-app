import { useEffect, useRef, useState } from 'react'
import AsteriskMark from './AsteriskMark'
import SparkMark from './SparkMark'
import { relativeTime } from '../lib/time'
import { CHIP, type KindSemantic } from '../lib/kinds'
import type { Thread } from '../lib/types'

/**
 * The conversations list, built to the supplied design spec.
 *
 * Pinned direct line (glass tier 2) + CHANNELS section. The ghosted
 * "128 Alder Ave" teaser is the designed EMPTY state — it renders only while
 * there are no channels, and comes back if every channel is archived.
 *
 * SPARK MARK COLOUR IS PER-MODE. ink-300 is the DARK-mode accent tint; on
 * white paper it is a pale wash that reads as a smudge rather than a mark, so
 * light mode takes the full accent (ink-500). It was hardcoded to ink-300 for
 * both, which is why the light theme had almost no accent on this screen.
 * The hero keeps ink-300 in both modes — it is an ink ground either way.
 *
 * SPARK MARK MEANS "JARVIS OPENED THIS". It is shown only when
 * created_by === 'jarvis'; channels the user made get none. That is the whole
 * semantic of the mark, so do not decorate user channels with it.
 */

interface Props {
  direct: Thread | null
  channels: Thread[]
  onOpen: (thread: Thread) => void
  onCompose: () => void
  onRename: (thread: Thread) => void
  onArchive: (thread: Thread) => void
}

function PencilIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M4 20h4L19.5 8.5a2.5 2.5 0 0 0-3.5-3.5L4.5 16.5 4 20Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M14.5 6.5 18 10" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function MoreIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="5" cy="12" r="1.6" fill="currentColor" />
      <circle cx="12" cy="12" r="1.6" fill="currentColor" />
      <circle cx="19" cy="12" r="1.6" fill="currentColor" />
    </svg>
  )
}

/**
 * The badge row under a channel name — "2 UPDATES · Going quiet" in the design.
 *
 * Two kinds of signal, and they come from different places on purpose:
 *   COUNT chips (updates/alerts) need a producer that counts unread activity
 *     per channel. None exists yet, so `thread.badges` is optional and simply
 *     absent in production today — the row collapses to nothing rather than
 *     rendering a zero.
 *   The QUIET signal is DERIVED here from last_message_at, because that is
 *     real data the client already has, and "this deal has gone quiet" is
 *     exactly what the timestamp means once it is old enough.
 *
 * Sizes are the design's: 6px dot, 11px mono chip, 12px kind word. Small
 * enough to read as metadata, saturated enough to scan.
 */
const QUIET_AFTER_HOURS = 48

function isQuiet(t: Thread): boolean {
  if (!t.last_message_at) return false
  const age = Date.now() - new Date(t.last_message_at).getTime()
  return age > QUIET_AFTER_HOURS * 3600_000
}

/**
 * A count badge — "2 UPDATES", "1 ALERT".
 *
 * TONE IS READ FROM THE LABEL, not passed in, because the producer that will
 * eventually set `thread.badges` writes strings. The mockup tints "2 UPDATES"
 * amber and "1 ALERT" amber-red; an unrecognised label falls back to the
 * neutral chip rather than guessing a colour, so a future badge nobody
 * anticipated is quiet instead of wrongly alarming.
 */
function CountChip({ label }: { label: string }) {
  const l = label.toLowerCase()
  const tone = /alert|overdue|late/.test(l)
    ? 'alert'
    : /update|new|unread/.test(l)
      ? 'warning'
      : 'neutral'
  return <span className={CHIP[tone as KindSemantic]}>{label}</span>
}

/**
 * A state signal — "Going quiet", "Hot".
 *
 * Now a TINTED CHIP WITH ITS DOT rather than dot-plus-coloured-text. Same
 * reasoning as the feed's kind chip: six coloured glyphs on a card is not
 * enough coloured area to register, and the badge row was the one place the
 * design's warmth was supposed to show through.
 */
function KindSignal({ tone, word }: { tone: 'alert' | 'success' | 'warning'; word: string }) {
  const dot = {
    alert: 'bg-light-alert dark:bg-dark-alert',
    success: 'bg-light-success dark:bg-dark-success',
    warning: 'bg-light-warning dark:bg-dark-warning',
  }[tone]
  return (
    <span className={`${CHIP[tone]} chip-state`}>
      <span aria-hidden="true" className={`h-[6px] w-[6px] shrink-0 rounded-full ${dot}`} />
      {word}
    </span>
  )
}

function BadgeRow({ thread }: { thread: Thread }) {
  const chips = thread.badges ?? []
  const quiet = isQuiet(thread)
  if (!chips.length && !quiet) return null
  return (
    <span className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
      {chips.map((b, i) => (
        <CountChip key={i} label={b} />
      ))}
      {quiet && <KindSignal tone="alert" word="Going quiet" />}
    </span>
  )
}

function previewLine(t: Thread): string {
  if (!t.preview) return 'No messages yet'
  // Both sides are labelled in the design ("Jarvis: …" / "You: …"). Labelling
  // only one side makes the other look like the channel is talking to itself.
  return t.preview_role === 'assistant' ? `Jarvis: ${t.preview}` : `You: ${t.preview}`
}

function ChannelRow({
  thread,
  onOpen,
  onRename,
  onArchive,
}: {
  thread: Thread
  onOpen: () => void
  onRename: () => void
  onArchive: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const wrapRef = useRef<HTMLLIElement | null>(null)
  const pressTimer = useRef<number | null>(null)

  // Close on any outside interaction.
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [menuOpen])

  // Long-press opens the same menu as the overflow button, so the gesture and
  // the visible affordance stay in agreement.
  const startPress = () => {
    pressTimer.current = window.setTimeout(() => setMenuOpen(true), 500)
  }
  const cancelPress = () => {
    if (pressTimer.current) {
      clearTimeout(pressTimer.current)
      pressTimer.current = null
    }
  }

  return (
    // A row, NOT a card. The design groups channels into one container with
    // hairline dividers; separate floating cards read as unrelated items and
    // cost ~10px of vertical rhythm each, which is most of the density gap.
    <li ref={wrapRef} className="relative">
      <div className="relative flex items-stretch">
        <button
          type="button"
          onClick={onOpen}
          onPointerDown={startPress}
          onPointerUp={cancelPress}
          onPointerLeave={cancelPress}
          onContextMenu={(e) => {
            e.preventDefault()
            setMenuOpen(true)
          }}
          className="min-w-0 flex-1 px-4 py-3 text-left"
        >
          <span className="flex items-center gap-2">
            {/* Only Jarvis-opened channels carry the spark. */}
            {thread.created_by === 'jarvis' && (
              <SparkMark size={13} className="shrink-0 text-light-accent dark:text-ink-300" />
            )}
            <span className="min-w-0 flex-1 truncate text-row-title">{thread.name}</span>
            {thread.last_message_at && (
              <span className="shrink-0 text-[11.5px] font-medium leading-none text-light-text-muted dark:text-dark-text-muted">
                {relativeTime(thread.last_message_at)}
              </span>
            )}
            {thread.unread && (
              <span
                aria-label="Unread"
                className="h-[7px] w-[7px] shrink-0 rounded-full bg-light-info dark:bg-dark-accent"
              />
            )}
          </span>
          <span className="mt-1 block truncate text-body-s leading-snug text-light-text-secondary dark:text-dark-text-secondary">
            {previewLine(thread)}
          </span>
          <BadgeRow thread={thread} />
        </button>

        <button
          type="button"
          aria-label={`Options for ${thread.name}`}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
          className={`absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center
                      rounded-btn text-light-text-muted transition-opacity
                      focus-visible:opacity-100 dark:text-dark-text-muted
                      ${menuOpen ? 'opacity-100' : 'opacity-0'}`}
        >
          <MoreIcon />
        </button>
      </div>

      {menuOpen && (
        <div
          role="menu"
          className="glass-featured absolute right-2 top-[calc(100%-8px)] z-20 w-40
                     overflow-hidden rounded-card"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false)
              onRename()
            }}
            className="block w-full px-4 py-3 text-left text-body-s
                       text-light-text-primary dark:text-dark-text-primary"
          >
            Rename
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false)
              onArchive()
            }}
            className="block w-full px-4 py-3 text-left text-body-s
                       text-light-text-secondary dark:text-dark-text-secondary"
          >
            Archive
          </button>
        </div>
      )}
    </li>
  )
}

function GhostedTeaser() {
  return (
    // Non-tappable by construction: a <li> with no handler and no button.
    <li
      className="rounded-list border border-dashed border-light-border-strong
                 p-card-x opacity-60 dark:border-dark-border-strong"
    >
      <div className="flex items-start gap-2">
        <SparkMark size={16} className="mt-0.5 shrink-0 text-light-accent dark:text-ink-300" />
        <span className="min-w-0 flex-1 text-row-title">128 Alder Ave</span>
        <span
          className="shrink-0 rounded-chip border border-light-border px-2 py-0.5
                     font-mono text-label-mono uppercase text-light-text-muted
                     dark:border-dark-border dark:text-dark-text-muted"
        >
          Example
        </span>
      </div>
      <p className="mt-2 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
        When a new deal starts, Jarvis opens a channel for it. The emails, tasks,
        and deadlines for that property all live in one thread.
      </p>
    </li>
  )
}

export default function ConversationsList({
  direct,
  channels,
  onOpen,
  onCompose,
  onRename,
  onArchive,
}: Props) {
  return (
    <div className="grid min-w-0 gap-4">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-screen-title">Conversations</h2>
          <p className="mt-0.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            Jarvis keeps one channel per deal
          </p>
        </div>
        <button
          type="button"
          aria-label="New conversation"
          onClick={onCompose}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-btn
                     text-light-text-secondary dark:text-dark-text-secondary"
        >
          <PencilIcon />
        </button>
      </header>

      {/* Pinned direct line — glass tier 2, the tokens' "pinned direct-line card". */}
      {direct && (
        <button
          type="button"
          onClick={() => onOpen(direct)}
          // INK WASH ON THE BRAND MOMENT. Glass tier 2 alone is a neutral
          // frost, so the one card the design wants you to notice was the same
          // temperature as the four channel rows below it. A faint accent wash
          // and an accent-tinted hairline keep it glass while making it read as
          // ink — this is the pinned direct line, the product's front door.
          className="glass-featured w-full overflow-hidden rounded-card px-4 py-3.5 text-left
                     !border-light-accent/25 bg-light-accent/[0.045]
                     dark:!border-dark-accent/25 dark:bg-dark-accent/[0.06]"
        >
          <div className="flex items-center gap-3">
            {/* BRAND MOMENT, tier 3: the disc sits on a radial ink-300 halo with
                an inset top highlight, so it catches light instead of reading
                as a flat tinted circle. This is the one mark on the screen
                allowed to glow. */}
            <span
              aria-hidden="true"
              className="relative grid h-10 w-10 shrink-0 place-items-center rounded-full
                         bg-ink-500/[0.16] text-light-accent
                         shadow-[inset_0_1px_0_rgba(255,255,255,0.8)]
                         dark:bg-ink-300/[0.20] dark:text-dark-accent
                         dark:shadow-[inset_0_1px_0_rgba(255,255,255,0.15)]"
            >
              <span
                aria-hidden="true"
                className="pointer-events-none absolute inset-[-6px] rounded-full
                           bg-[radial-gradient(circle,rgba(143,180,232,0.35)_0%,rgba(143,180,232,0)_70%)]
                           dark:bg-[radial-gradient(circle,rgba(143,180,232,0.22)_0%,rgba(143,180,232,0)_70%)]"
              />
              <AsteriskMark size={19} strokeWidth={2.2} />
            </span>

            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-1.5">
                <span className="min-w-0 truncate text-row-title">{direct.name}</span>
                <SparkMark size={11} className="shrink-0 text-light-accent dark:text-ink-300" />
                <span className="flex-1" />
                {direct.last_message_at && (
                  <span className="shrink-0 text-[11.5px] font-medium leading-none text-light-text-muted dark:text-dark-text-muted">
                    {relativeTime(direct.last_message_at)}
                  </span>
                )}
                {direct.unread && (
                  <span
                    aria-label="Unread"
                    className="h-[7px] w-[7px] shrink-0 rounded-full bg-light-info dark:bg-dark-accent"
                  />
                )}
              </span>
              <span className="mt-1 block truncate text-body-s leading-snug text-light-text-secondary dark:text-dark-text-secondary">
                {direct.preview
                  ? previewLine(direct)
                  : 'Your direct line. Always here.'}
              </span>
            </span>
          </div>
        </button>
      )}

      <section className="grid min-w-0 gap-2.5">
        <h3 className="flex flex-wrap items-baseline gap-x-2">
          {/* The section label is the screen's only structural heading below
              the title, so it carries the accent rather than being a third
              shade of grey. */}
          <span className="font-mono text-label-mono uppercase text-light-accent/80 dark:text-dark-accent/85">
            Channels
          </span>
          <span className="text-caption text-light-text-muted dark:text-dark-text-muted">
            · opened by Jarvis as deals start
          </span>
        </h3>

        {/* ONE container, hairline-divided. See the ChannelRow comment: this is
            the design's grouping, and it is also where most of the density
            comes from. `overflow-hidden` keeps the first/last rows inside the
            16px radius; `divide-y` draws the dividers the design shows. */}
        <ul
          className={
            channels.length === 0
              ? 'grid min-w-0 gap-3'
              : 'min-w-0 divide-y divide-light-border overflow-hidden rounded-list ' +
                'border border-light-border bg-light-surface ' +
                // Light mode gets the same TWO-LAYER language as dark: a tight
                // contact shadow to seat the card, plus a wider soft one for
                // depth. shadow-card alone (a single 1px/4% drop) is invisible
                // on warm paper and leaves the container reading as a flat
                // rectangle on a flat ground.
                'shadow-lift ' +
                'dark:divide-dark-border dark:border-dark-border dark:bg-dark-surface ' +
                'dark:shadow-lift-dark'
          }
        >
          {channels.length === 0 ? (
            <GhostedTeaser />
          ) : (
            channels.map((t) => (
              <ChannelRow
                key={t.thread_id}
                thread={t}
                onOpen={() => onOpen(t)}
                onRename={() => onRename(t)}
                onArchive={() => onArchive(t)}
              />
            ))
          )}
        </ul>
      </section>
    </div>
  )
}
