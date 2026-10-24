/**
 * The Jarvis Mono icon set: 24px grid, 1.75 stroke, round caps and joins,
 * currentColor. No fills except the stop square and the dots of "more".
 */

const PATHS = {
  plus: 'M12 5v14M5 12h14',
  mic: 'M12 3a3 3 0 0 0-3 3v5a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3ZM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21',
  send: 'M12 19V5M6 11l6-6 6 6',
  check: 'M5 12.5l4.5 4.5L19 7.5',
  close: 'M6 6l12 12M18 6L6 18',
  chevron: 'M9 6l6 6-6 6',
  more: 'M5.5 12h.01M12 12h.01M18.5 12h.01',
  speaker: 'M11 5 6.5 9H3v6h3.5L11 19V5ZM15.5 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11',
  'speaker-off': 'M11 5 6.5 9H3v6h3.5L11 19V5ZM16 9.5l5 5M21 9.5l-5 5',
  file: 'M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5L14 3ZM13.5 3.2V8h4.8',
  compose: 'M12 5H6a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-6M17.5 3.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4 8.5-8.5Z',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18',
  expand: 'M14 4h6v6M20 4l-7 7M10 20H4v-6M4 20l7-7',
  shrink: 'M20 10h-6V4M14 10l7-7M4 14h6v6M10 14l-7 7',
  copy: 'M9 9h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V10a1 1 0 0 1 1-1ZM5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1',
  alert: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7.5v5.5M12 16.5h.01',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2.5V4M12 20v1.5M2.5 12H4M20 12h1.5M5.3 5.3l1 1M17.7 17.7l1 1M5.3 18.7l1-1M17.7 6.3l1-1',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z',
  signout: 'M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4 4 4M6 12h10',
  spark: 'M12 3.5c.6 4.6 2.3 6.4 6.5 7.5-4.2 1.1-5.9 2.9-6.5 7.5-.6-4.6-2.3-6.4-6.5-7.5 4.2-1.1 5.9-2.9 6.5-7.5Z',
  chat: 'M20 12a8 8 0 0 1-8 8H4l1.6-3.2A8 8 0 1 1 20 12Z',
  activity: 'M4 6h16M4 12h16M4 18h10',
  approvals: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM8 12.5l2.7 2.7L16 9.8',
  tasks: 'M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1ZM8.5 12.2l2.3 2.3 4.7-4.9',
  leads: 'M12 4a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM5 20a7 7 0 0 1 14 0',
  settings: 'M4 6h16M4 12h16M4 18h16M15 4v4M8 10v4M17 16v4',
  back: 'M15 5l-7 7 7 7',
  members: 'M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1M9.5 3.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7ZM17 11a3 3 0 1 0 0-6M21 19v-1a3.6 3.6 0 0 0-2.5-3.4',
  eye: 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z',
  'eye-off': 'M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM4 20 20 4',
  inbox: 'M4 13h4l1.5 3h5l1.5-3h4M5.5 5h13L21 13v5a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-5l2.5-8Z',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
} as const

export type IconName = keyof typeof PATHS | 'stop'

export interface IconProps {
  name: IconName
  size?: number
  className?: string
}

export function Icon({ name, size = 20, className }: IconProps) {
  return (
    <svg
      className={['block shrink-0', className].filter(Boolean).join(' ')}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      {name === 'stop' ? (
        <rect x={7} y={7} width={10} height={10} rx={2} fill="currentColor" />
      ) : (
        <path
          d={PATHS[name]}
          stroke="currentColor"
          strokeWidth={name === 'more' ? 3 : 1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  )
}
