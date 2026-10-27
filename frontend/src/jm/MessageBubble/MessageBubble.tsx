import { useRef, type ReactNode } from 'react'
import { Button } from '../Button/Button'
import { AttachmentChip, Dots, cx, srOnly, type ChatAttachment } from '../shared/primitives'

export type { ChatAttachment }

export interface MessageBubbleProps {
  role: 'user' | 'assistant'
  /** Plain text. Never markdown or links from the model. */
  text?: string
  children?: ReactNode
  attachments?: ChatAttachment[]
  /** Words fade in as text grows. */
  streaming?: boolean
  /** Typing dots, before the first word. */
  typing?: boolean
  typingLabel?: string
  /** The turn ended before the model finished. */
  partial?: boolean
  partialLabel?: string
  /** The app's own controls (action events / saved actions). First is primary. */
  actions?: { id?: string; label: string; onClick?: () => void }[]
  /** Read-aloud control state. Omit to hide it. */
  speech?: 'off' | 'on' | 'speaking' | 'unavailable'
  onSpeak?: () => void
  time?: string
  /** Channels: who wrote it, shown above the bubble. A 'user' message with
   *  `from` is someone else's, so it sits on the left like a reply does. */
  from?: string
  /** Channels: names an @ can reach (without the @); matches are highlighted,
   *  and `self` (the viewer's own names) more strongly. */
  mentions?: { names: string[]; self?: string[] }
  className?: string
}

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** Highlight @name the way the server reads one (app/channels.py): not glued
 *  to a word before it, not running on into more letters after it. */
function Mentioned({ text, names, self = [], onBlue }: { text: string; names: string[]; self?: string[]; onBlue?: boolean }) {
  const all = [...new Set([...names, ...self])].filter(Boolean).sort((a, b) => b.length - a.length)
  if (!all.length || !text.includes('@')) return <>{text}</>
  const re = new RegExp(`(?<![\\w@.])@(${all.map(escapeRe).join('|')})(?![\\w-])`, 'gi')
  const mine = new Set(self.map((n) => n.toLowerCase()))
  const out: ReactNode[] = []
  let at = 0
  for (const m of text.matchAll(re)) {
    const i = m.index ?? 0
    if (i > at) out.push(text.slice(at, i))
    out.push(
      <span
        key={i}
        data-mention={mine.has(m[1].toLowerCase()) ? 'self' : 'other'}
        className={
          // Inside your own blue bubble a tinted chip would vanish: weight and an underline instead.
          onBlue
            ? 'font-semibold underline underline-offset-2'
            : cx('rounded-jm-sm px-0.5', mine.has(m[1].toLowerCase()) ? 'bg-jm-accent text-jm-on-accent' : 'bg-jm-accent-soft text-jm-accent-soft-text')
        }
      >
        {m[0]}
      </span>,
    )
    at = i + m[0].length
  }
  if (at < text.length) out.push(text.slice(at))
  return <>{out}</>
}

/**
 * Word by word: each word fades in once; words from one chunk are staggered by
 * --jm-dur-word-stagger, capped at 12. A word's delay is fixed the first time
 * it renders, so later chunks never restart earlier words.
 */
function StreamText({ text }: { text: string }) {
  const delays = useRef<number[]>([])
  const parts = text.split(/(\s+)/)
  const known = delays.current.length
  const nodes: ReactNode[] = []
  let w = 0
  parts.forEach((part, i) => {
    if (!part) return
    if (/^\s+$/.test(part)) {
      nodes.push(part)
      return
    }
    if (delays.current[w] === undefined) delays.current[w] = Math.min(Math.max(w - known, 0), 12)
    nodes.push(
      <span
        key={i}
        className="animate-jm-word motion-reduce:animate-none"
        style={{ animationDelay: `calc(var(--jm-dur-word-stagger) * ${delays.current[w]})` }}
      >
        {part}
      </span>,
    )
    w++
  })
  return <>{nodes}</>
}

export function MessageBubble(p: MessageBubbleProps) {
  // "user" here means "on the right, in the blue bubble": the viewer's own.
  const user = p.role === 'user' && !p.from
  const text = p.text !== undefined ? p.text : p.children
  const hasText = text !== undefined && text !== null && text !== ''
  const bubbleBase = 'm-0 box-border max-w-jm-bubble whitespace-pre-wrap break-words rounded-jm-xl px-jm-4 py-jm-3'
  const bubbleTone = user
    ? 'rounded-br-jm-tail bg-jm-bubble-user text-jm-on-bubble-user'
    : 'rounded-bl-jm-tail bg-jm-bubble-assistant text-jm-fg'

  let bubble: ReactNode = null
  if (!user && p.typing) {
    bubble = (
      <div role="status" className={cx(bubbleBase, bubbleTone, 'flex min-h-[46px] items-center')}>
        <Dots />
        <span className={srOnly}>{p.typingLabel ?? 'Jarvis is thinking'}</span>
      </div>
    )
  } else if (hasText) {
    bubble = (
      <p aria-busy={p.streaming || undefined} className={cx(bubbleBase, bubbleTone, 'text-jm-body')}>
        {p.streaming && typeof text === 'string' ? (
          <StreamText text={text} />
        ) : p.mentions && typeof text === 'string' ? (
          <Mentioned text={text} names={p.mentions.names} self={p.mentions.self} onBlue={user} />
        ) : (
          text
        )}
      </p>
    )
  }

  const s = p.speech
  const speak =
    !user && s && !p.streaming && !p.typing ? (
      <Button
        variant={s === 'speaking' ? 'secondary' : 'ghost'}
        size="sm"
        icon={s === 'off' || s === 'unavailable' ? 'speaker-off' : 'speaker'}
        aria-label={
          s === 'speaking'
            ? 'Stop reading aloud'
            : s === 'on'
              ? 'Read this reply aloud'
              : s === 'unavailable'
                ? 'Read aloud unavailable'
                : 'Turn on reading replies aloud'
        }
        aria-pressed={s === 'on' || s === 'speaking'}
        title={s === 'unavailable' ? 'No speech voices in this browser' : undefined}
        disabled={s === 'unavailable'}
        onClick={p.onSpeak}
      />
    ) : null

  const atts = p.attachments ?? []
  const actions = p.actions ?? []
  return (
    <div className={cx('flex flex-col gap-jm-2', user ? 'items-end' : 'items-start', p.className)}>
      {p.from && <span className="-mb-jm-1 px-jm-1 text-jm-caption font-semibold text-jm-fg-secondary">{p.from}</span>}
      {atts.length > 0 && (
        <div className={cx('flex max-w-jm-bubble flex-wrap gap-jm-2', user && 'justify-end')}>
          {atts.map((a, i) => (
            <AttachmentChip key={a.id ?? i} attachment={a} />
          ))}
        </div>
      )}
      {bubble}
      {p.partial && <p className="m-0 px-jm-1 text-jm-caption text-jm-fg-secondary">{p.partialLabel ?? 'This answer was cut short.'}</p>}
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-jm-2">
          {actions.map((a, i) => (
            <Button key={a.id ?? i} variant={i === 0 ? 'primary' : 'secondary'} onClick={a.onClick}>
              {a.label}
            </Button>
          ))}
        </div>
      )}
      {(speak || p.time) && (
        <div className="flex items-center gap-jm-1 text-jm-fg-secondary">
          {speak}
          {p.time && <span className="text-jm-caption">{p.time}</span>}
        </div>
      )}
    </div>
  )
}
