/**
 * The spark mark: a filled 4-point star in ink-300.
 *
 * One of the three fills the tokens permit (kind dots, spark mark, asterisk
 * mark) — everything else in the icon system is stroke-only. Used to head a
 * channel row.
 */
interface Props {
  size?: number
  className?: string
}

export default function SparkMark({ size = 16, className = '' }: Props) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      className={className}
      aria-hidden="true"
    >
      {/* Concave-sided 4-point star: points at N/E/S/W pinched toward centre. */}
      <path
        d="M12 1c.6 5.4 2.3 8.4 7.6 9.9l1.4.4c-5.9 1.2-8.6 3.2-9 12.7-.4-9.5-3.1-11.5-9-12.7l1.4-.4C9.7 9.4 11.4 6.4 12 1Z"
        fill="currentColor"
      />
    </svg>
  )
}
