import { todayLabel } from '../lib/time'
import type { Theme } from '../lib/theme'

/**
 * Frosted chrome header — glass tier 1. Content scrolls beneath it; it is
 * never opaque.
 */
interface Props {
  displayName?: string
  theme: Theme
  onToggleTheme: () => void
  onSignOut: () => void
}

function initials(name?: string): string {
  if (!name) return 'J'
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return 'J'
  return (parts[0][0] + (parts[1]?.[0] ?? '')).toUpperCase()
}

function SunIcon() {
  // 24px grid, stroke-only, stroke-width 2 per the tokens' ICON STYLE.
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="4.5" stroke="currentColor" strokeWidth="2" />
      {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => {
        const r = (deg * Math.PI) / 180
        return (
          <line
            key={deg}
            x1={12 + Math.cos(r) * 8}
            y1={12 + Math.sin(r) * 8}
            x2={12 + Math.cos(r) * 10.5}
            y2={12 + Math.sin(r) * 10.5}
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        )
      })}
    </svg>
  )
}

function MoonIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M20 14.2A8.2 8.2 0 0 1 9.8 4a8.5 8.5 0 1 0 10.2 10.2Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export default function Chrome({
  displayName,
  theme,
  onToggleTheme,
  onSignOut,
}: Props) {
  return (
    // lg: the header starts where the rail ends and its inner row is the same
    // 1080px/48px container the content uses, so "Hi, Dana" sits on the
    // content's left edge rather than floating in the void.
    <header className="glass-chrome glass-chrome-header fixed inset-x-0 top-0 z-30 lg:left-[220px]">
      <div
        className="mx-auto flex w-full max-w-[700px] items-center gap-3 px-screen-x pb-3
                   pt-[max(12px,env(safe-area-inset-top))]
                   md:max-w-[788px] md:pl-[88px]
                   lg:max-w-[1080px] lg:px-12 lg:pb-4 lg:pt-4"
      >
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-screen-title">
            {displayName ? `Hi, ${displayName}` : 'Hi there'}
          </h1>
          <p className="mt-0.5 text-caption text-light-text-secondary dark:text-dark-text-secondary">
            {todayLabel()}
          </p>
        </div>

        <button
          type="button"
          onClick={onToggleTheme}
          aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-btn
                     text-light-text-secondary dark:text-dark-text-secondary
                     transition-colors duration-[120ms]
                     lg:hover:bg-light-accent/[0.06] lg:hover:text-light-text-primary
                     dark:lg:hover:bg-white/[0.06] dark:lg:hover:text-dark-text-primary"
        >
          {theme === 'dark' ? <SunIcon /> : <MoonIcon />}
        </button>

        <button
          type="button"
          onClick={onSignOut}
          aria-label="Sign out"
          title="Sign out"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full
                     bg-light-accent font-mono text-label-mono text-white
                     dark:bg-dark-accent-fill
                     transition-shadow duration-[120ms] lg:hover:shadow-accent-btn"
        >
          {initials(displayName)}
        </button>
      </div>
    </header>
  )
}
