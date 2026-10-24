import { useId, useState, type ReactNode } from 'react'
import { Icon } from '../shared/Icon'
import { Tag, Tick, cx, focus, press, rowBase, truncate } from '../shared/primitives'

export interface ActivityRowProps {
  /** The kind's word: "Email filed", "Task created", "Done". */
  kind: string
  /** needs-you: blue pill · done: blue check · error: red alert icon (real failures only). */
  tone?: 'neutral' | 'needs-you' | 'done' | 'error'
  skill?: string
  time?: string
  timeTitle?: string
  dateTime?: string
  /** headline, or message for old events. */
  headline: ReactNode
  detail?: ReactNode
  /** Short labels from the event's attachments. */
  attachments?: string[]
  defaultOpen?: boolean
  className?: string
}

/** The "Details" / "Body" toggle shared by ActivityRow and OutboxRow. */
export function Disclose({ open, controls, onToggle, children }: { open: boolean; controls: string; onToggle: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className={cx(
        'inline-flex cursor-pointer appearance-none items-center gap-jm-1 justify-self-start rounded-jm-sm border-0 bg-transparent py-0.5 pl-0 pr-jm-1 font-jm text-jm-caption text-jm-fg-secondary hover:text-jm-fg',
        press,
        focus,
      )}
    >
      <span className={cx('transition-transform duration-jm-press motion-reduce:transition-none', open && 'rotate-90')}>
        <Icon name="chevron" size={14} />
      </span>
      {children}
    </button>
  )
}

export function ActivityRow(p: ActivityRowProps) {
  const [open, setOpen] = useState(!!p.defaultOpen)
  const detailId = useId()
  const tone = p.tone ?? 'neutral'
  const chips = p.attachments ?? []
  return (
    <li className={cx(rowBase, p.className)}>
      <div className="flex min-w-0 items-center gap-jm-2 text-jm-fg-secondary">
        {tone === 'needs-you' ? (
          <Tag tone="action">{p.kind}</Tag>
        ) : (
          <span className={cx('inline-flex shrink-0 items-center gap-jm-1 text-jm-caption', tone === 'error' ? 'text-jm-fg' : 'text-jm-fg-secondary')}>
            {tone === 'done' && <Tick size={14} />}
            {tone === 'error' && (
              <span className="text-jm-error">
                <Icon name="alert" size={14} />
              </span>
            )}
            {p.kind}
          </span>
        )}
        {p.skill && <span className={cx('min-w-0 text-jm-caption', truncate)}>{p.skill}</span>}
        {p.time && (
          <time dateTime={p.dateTime} title={p.timeTitle} className="ml-auto shrink-0 text-jm-caption">
            {p.time}
          </time>
        )}
      </div>
      <p className="m-0 mt-0.5 text-jm-body-sm text-jm-fg">{p.headline}</p>
      {p.detail && (
        <Disclose open={open} controls={detailId} onToggle={() => setOpen(!open)}>
          {open ? 'Less' : 'Details'}
        </Disclose>
      )}
      {p.detail && open && (
        <p id={detailId} className="m-0 mt-jm-1 break-words rounded-jm-md bg-jm-panel px-jm-3 py-jm-2 text-jm-caption text-jm-fg-secondary">
          {p.detail}
        </p>
      )}
      {chips.length > 0 && (
        <ul className="m-0 mt-jm-1 flex list-none flex-wrap gap-jm-2 p-0">
          {chips.map((c, i) => (
            <li key={i} className="rounded-jm-pill border border-jm-border px-jm-2 py-0.5 text-jm-caption text-jm-fg-secondary">
              {c}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}
