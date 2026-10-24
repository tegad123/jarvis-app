import { Button } from '../Button/Button'
import { statusPill } from '../TaskRow/TaskRow'
import { SparkGlyph, Tick, cx, focus, joinDots, rowBase, tagAction, tagBase, tagNeutral, truncate } from '../shared/primitives'

export interface LeadRowProps {
  name: string
  property?: string
  price?: string
  temperature?: 'Hot' | 'Warm' | 'Cold' | ''
  temperatureRank?: number
  status?: 'New' | 'No Response' | 'Responded' | 'Not Interested' | ''
  /** follow_up, else "Nudged {last_nudge}". Default "No activity yet". */
  activity?: string
  agent?: string
  date?: string
  byJarvis?: boolean
  /** Only for the app's own store with a signed-in person. */
  canEdit?: boolean
  /** One field per tap: { temperature } or { status }. */
  onChange?: (fields: { temperature?: string; status?: string }) => void
  onArchive?: () => void
  busy?: boolean
  className?: string
}

const STATUS_CYCLE = ['New', 'No Response', 'Responded', 'Not Interested']
const TEMP_CYCLE = ['', 'Cold', 'Warm', 'Hot']

/** Three rising bars: filled in fg, empty in border-strong. Never coloured. */
function TempBars({ rank }: { rank: number }) {
  return (
    <span className="flex h-3 items-end gap-[3px]" aria-hidden="true">
      {[6, 9, 12].map((height, i) => (
        <i key={i} className={cx('block w-[3px] rounded-sm', i < rank ? 'bg-jm-fg' : 'bg-jm-border-strong')} style={{ height }} />
      ))}
    </span>
  )
}

export function LeadRow(p: LeadRowProps) {
  const temp = p.temperature ?? ''
  const rank = p.temperatureRank ?? Math.max(0, TEMP_CYCLE.indexOf(temp))
  const tempLabel = temp ? `Temperature: ${temp}` : 'No temperature set'
  const nextTemp = TEMP_CYCLE[(TEMP_CYCLE.indexOf(temp) + 1) % TEMP_CYCLE.length]
  const barsBox = 'inline-grid h-6 w-6 shrink-0 place-items-center rounded-jm-sm border-0 bg-transparent p-0'

  const bars = p.canEdit ? (
    <button
      type="button"
      disabled={p.busy}
      title={tempLabel}
      aria-label={`${tempLabel}. Change to ${nextTemp || 'none'}`}
      onClick={() => p.onChange?.({ temperature: nextTemp })}
      className={cx(barsBox, 'cursor-pointer appearance-none enabled:hover:bg-jm-panel-raised disabled:opacity-jm-disabled', focus)}
    >
      <TempBars rank={rank} />
    </button>
  ) : (
    <span role="img" title={tempLabel} aria-label={tempLabel} className={barsBox}>
      <TempBars rank={rank} />
    </span>
  )

  const status = p.status ?? ''
  const s = status.toLowerCase()
  // No Response needs a follow-up: the blue pill. Responded: the blue check.
  const statusClass = s === 'no response' ? cx(tagBase, tagAction) : s === 'responded' ? statusPill : cx(tagBase, tagNeutral)
  const statusInner =
    s === 'responded' ? (
      <>
        <Tick size={14} />
        <span>{status}</span>
      </>
    ) : (
      status
    )
  const nextStatus = STATUS_CYCLE[(STATUS_CYCLE.indexOf(status) + 1) % STATUS_CYCLE.length]
  const statusNode = !status ? null : p.canEdit ? (
    <button
      type="button"
      disabled={p.busy}
      aria-label={`Status: ${status}. Change to ${nextStatus}`}
      onClick={() => p.onChange?.({ status: nextStatus })}
      className={cx(statusClass, 'cursor-pointer appearance-none font-jm disabled:opacity-jm-disabled', s !== 'no response' && 'enabled:hover:border-jm-border-strong', focus)}
    >
      {statusInner}
    </button>
  ) : (
    <span className={statusClass}>{statusInner}</span>
  )

  const place = [p.property, p.price].filter(Boolean).join(' · ')
  const meta = [p.activity || 'No activity yet', p.agent, p.date].filter(Boolean).map((m, i) => <span key={i}>{m}</span>)

  return (
    <li className={cx(rowBase, p.className)}>
      <div className="flex min-w-0 items-center gap-jm-2">
        {bars}
        <span className={cx('min-w-0 flex-1 text-jm-body-sm text-jm-fg', truncate)}>{p.name}</span>
        {p.byJarvis && <SparkGlyph label="Added by Jarvis" />}
        {statusNode}
      </div>
      {place && <p className={cx('m-0 text-jm-body-sm text-jm-fg-secondary', truncate)}>{place}</p>}
      <div className="flex min-h-jm-control-sm min-w-0 items-center gap-jm-2 text-jm-caption text-jm-fg-secondary">
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-y-jm-1">{joinDots(meta)}</span>
        {p.canEdit && (
          <span className="ml-auto flex shrink-0 items-center">
            <Button variant="ghost" size="sm" disabled={p.busy} onClick={p.onArchive}>
              Archive
            </Button>
          </span>
        )}
      </div>
    </li>
  )
}
