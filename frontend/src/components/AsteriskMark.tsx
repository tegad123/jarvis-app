/**
 * The Jarvis mark: an 8-spoke asterisk, stroke-only, on the 24px icon grid.
 * Per the tokens' ICON STYLE block this is one of the few marks that is not
 * a plain 2px stroke icon — it is the brand moment, so it carries slightly
 * heavier spokes and no fill.
 */
interface Props {
  size?: number
  className?: string
  strokeWidth?: number
}

// Eight spokes at 45° intervals from the centre of a 24px box.
const SPOKES = Array.from({ length: 8 }, (_, i) => {
  const angle = (Math.PI / 4) * i
  const inner = 2.5
  const outer = 10
  return {
    x1: 12 + Math.cos(angle) * inner,
    y1: 12 + Math.sin(angle) * inner,
    x2: 12 + Math.cos(angle) * outer,
    y2: 12 + Math.sin(angle) * outer,
  }
})

export default function AsteriskMark({
  size = 24,
  className = '',
  strokeWidth = 2,
}: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      role="img"
      aria-label="Jarvis"
    >
      {SPOKES.map((s, i) => (
        <line
          key={i}
          x1={s.x1}
          y1={s.y1}
          x2={s.x2}
          y2={s.y2}
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
      ))}
    </svg>
  )
}
