import { cx, focus, press } from '../shared/primitives'

export interface FilterChipsProps {
  label?: string
  options: (string | { value: string; label: string })[]
  /** null = All. */
  active?: string | null
  onChange?: (value: string | null) => void
  format?: (value: string) => string
  allLabel?: string
  /** A pick-one control rather than a filter: no All chip. */
  hideAll?: boolean
  className?: string
}

export function FilterChips({ label, options, active = null, onChange, format, allLabel = 'All', hideAll, className }: FilterChipsProps) {
  const opts = options.map((o) => (typeof o === 'string' ? { value: o, label: format ? format(o) : o } : o))
  if (!opts.length) return null

  const chip = (value: string | null, text: string) => {
    const on = active === value
    return (
      <button
        key={value ?? '__all'}
        type="button"
        aria-pressed={on}
        onClick={() => onChange?.(value === null || on ? null : value)}
        className={cx(
          'h-jm-control-sm shrink-0 cursor-pointer appearance-none whitespace-nowrap rounded-jm-pill border px-jm-3 font-jm text-jm-label',
          on
            ? 'border-transparent bg-jm-accent-soft text-jm-accent-soft-text'
            : 'border-jm-border bg-transparent text-jm-fg-secondary hover:border-jm-border-strong hover:text-jm-fg',
          press,
          focus,
        )}
      >
        {text}
      </button>
    )
  }

  return (
    <div role="group" aria-label={label ?? 'Filter'} className={cx('flex min-w-0 items-center gap-jm-3', className)}>
      {label && (
        <span aria-hidden="true" className="min-w-[40px] shrink-0 text-jm-caption text-jm-fg-secondary">
          {label}
        </span>
      )}
      {/* Padding keeps the focus ring clear of the scroller's clip. */}
      <div className="-m-1 flex min-w-0 flex-1 gap-jm-2 overflow-x-auto p-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {!hideAll && chip(null, allLabel)}
        {opts.map((o) => chip(o.value, o.label))}
      </div>
    </div>
  )
}
