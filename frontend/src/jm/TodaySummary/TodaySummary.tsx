import { Tag, Tick, cx, truncate } from '../shared/primitives'

export interface TodaySummaryProps {
  /** Counted and pluralised server-side: "5 emails filed". */
  items?: string[]
  needsYou?: number
  unavailable?: boolean
  label?: string
  className?: string
}

export function TodaySummary({ items = [], needsYou = 0, unavailable, label = 'Today', className }: TodaySummaryProps) {
  const line = unavailable ? 'Summary unavailable.' : items.length ? items.join(' · ') : 'Nothing yet today.'
  return (
    <section
      aria-label={label}
      className={cx('box-border flex min-h-[48px] min-w-0 items-center gap-jm-3 rounded-jm-lg border border-jm-border bg-jm-panel px-jm-4 py-jm-2', className)}
    >
      <span className="shrink-0 text-jm-caption text-jm-fg-secondary">{label}</span>
      <span title={line} className={cx('min-w-0 flex-1 text-jm-body-sm text-jm-fg', truncate)}>
        {line}
      </span>
      {unavailable ? (
        <span className="shrink-0 text-jm-caption text-jm-fg-secondary">The feed below is still live.</span>
      ) : needsYou > 0 ? (
        <Tag tone="action">{needsYou} {needsYou === 1 ? 'thing needs you' : 'things need you'}</Tag>
      ) : (
        <span className="inline-flex shrink-0 items-center gap-jm-2 text-jm-body-sm text-jm-fg-secondary">
          <Tick />
          All clear.
        </span>
      )}
    </section>
  )
}
