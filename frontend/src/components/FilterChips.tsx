/**
 * Filter chips. Values are derived from the events actually loaded rather than
 * from a hardcoded list, because `kind` and `skill` are open-ended strings — a
 * new skill or a new kind should appear as a filter without a code change.
 */
interface Props {
  label: string
  options: string[]
  active: string | null
  onChange: (value: string | null) => void
  /** Optional display transform, e.g. kind string → kind word. */
  format?: (value: string) => string
}

export default function FilterChips({
  label,
  options,
  active,
  onChange,
  format,
}: Props) {
  if (options.length === 0) return null

  // ACTIVE IS SATURATED, INACTIVE IS A TINT — not a grey outline. A row of
  // hairline-grey pills either side of one ink pill made the strip read as
  // disabled-plus-one; giving the unselected state a faint ink wash keeps the
  // whole control in the accent family, so the selected one reads as MORE of
  // the same rather than as the only live thing on screen. The selected chip
  // also carries the accent drop shadow, which is what lifts it off the row.
  const chip = (selected: boolean) =>
    [
      'shrink-0 rounded-chip px-2.5 py-1 font-mono text-[10.5px] font-semibold uppercase tracking-[0.07em]',
      'transition-colors lg:duration-[120ms]',
      selected
        ? 'bg-light-accent text-white shadow-accent-btn dark:bg-dark-accent-fill dark:text-white'
        : 'bg-light-accent/[0.06] text-light-accent/85 ' +
          'dark:bg-dark-accent/[0.10] dark:text-dark-accent/90 ' +
          'lg:hover:bg-light-accent/[0.12] dark:lg:hover:bg-dark-accent/[0.18]',
    ].join(' ')

  return (
    // min-w-0 is load-bearing: this sits in a grid whose column is sized by its
    // widest item, and a horizontally scrolling strip is as wide as its content.
    // Without it the track grows past the viewport and drags the hero and every
    // feed row out with it.
    // lg: one toolbar line — the scroller's mobile pb-1 (thumb room for the
    // scrollbar-less strip) would sit the chips a hair above the label.
    <div className="flex w-full min-w-0 items-center gap-2.5 lg:gap-3">
      {/* Named, because two unlabelled rows each offering "All" reads as a
          duplicate control rather than two independent filters. */}
      <span
        className="w-9 shrink-0 font-mono text-[10.5px] font-medium uppercase tracking-[0.07em]
                   text-light-text-muted dark:text-dark-text-muted"
      >
        {label}
      </span>
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1 lg:pb-0
                      [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <button
          type="button"
          className={chip(active === null)}
          onClick={() => onChange(null)}
        >
          All
        </button>
        {options.map((o) => (
          <button
            key={o}
            type="button"
            className={chip(active === o)}
            onClick={() => onChange(active === o ? null : o)}
          >
            {format ? format(o) : o}
          </button>
        ))}
      </div>
    </div>
  )
}
