import AsteriskMark from './AsteriskMark'

export type TabId = 'feed' | 'ask' | 'approvals' | 'leads' | 'tasks'

/**
 * Frosted tab bar — glass tier 1, same treatment as the header.
 * Icons are 24px-grid, stroke-only, stroke-width 2 per the tokens.
 */
interface Props {
  active: TabId
  onChange: (tab: TabId) => void
}

const ICON = { width: 22, height: 22, viewBox: '0 0 24 24', fill: 'none' } as const
const S = { stroke: 'currentColor', strokeWidth: 2, strokeLinecap: 'round' } as const

const TABS: { id: TabId; label: string; icon: JSX.Element }[] = [
  {
    id: 'feed',
    label: 'Feed',
    icon: (
      <svg {...ICON} aria-hidden="true">
        <line x1="4" y1="7" x2="20" y2="7" {...S} />
        <line x1="4" y1="12" x2="20" y2="12" {...S} />
        <line x1="4" y1="17" x2="14" y2="17" {...S} />
      </svg>
    ),
  },
  {
    id: 'ask',
    label: 'Ask',
    icon: (
      <svg {...ICON} aria-hidden="true">
        <path
          d="M20 12a8 8 0 0 1-8 8H5l1.6-3.2A8 8 0 1 1 20 12Z"
          {...S}
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
  {
    id: 'approvals',
    label: 'Approvals',
    icon: (
      <svg {...ICON} aria-hidden="true">
        <path d="M5 12.5 10 17.5 19 7" {...S} strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    id: 'tasks',
    label: 'Tasks',
    icon: (
      <svg {...ICON} aria-hidden="true">
        <rect x="4" y="4" width="16" height="16" rx="3" {...S} strokeLinejoin="round" />
        <path d="M8.5 12.2l2.3 2.3 4.7-4.9" {...S} strokeLinejoin="round" />
      </svg>
    ),
  },
  {
    id: 'leads',
    label: 'Leads',
    icon: (
      <svg {...ICON} aria-hidden="true">
        <circle cx="12" cy="8" r="3.5" {...S} />
        <path d="M5 19.5a7 7 0 0 1 14 0" {...S} strokeLinejoin="round" />
      </svg>
    ),
  },
]

export default function TabBar({ active, onChange }: Props) {
  return (
    // ONE nav, two shapes. Bottom bar on mobile (thumb reach); a narrow LEFT
    // RAIL from md up, because a full-width bar pinned to the bottom of a
    // desktop window reads as a phone app someone stretched. A rail is the
    // calmer register: it sits still, never competes with the content column,
    // and puts navigation where a desktop eye already looks.
    // lg (1024px+): the rail widens to 220px and each destination becomes a
    // 40px row — icon then label — with a filled pill for the active one. The
    // mark at the top is the rail's own header; the page header starts to its
    // right, so without it the rail's top 90px would be blank.
    <nav
      className="glass-chrome glass-chrome-tabbar fixed inset-x-0 bottom-0 z-30
                 md:inset-x-auto md:bottom-0 md:left-0 md:top-0 md:w-[72px] md:border-r
                 lg:w-[220px]"
    >
      <div
        className="hidden items-center gap-2.5 px-5 pb-6 pt-[max(24px,env(safe-area-inset-top))]
                   lg:flex"
      >
        <AsteriskMark size={22} strokeWidth={2.2} className="text-light-accent dark:text-dark-accent" />
        <span className="text-row-title">Jarvis</span>
      </div>
      <ul
        className="flex px-2 pb-[max(8px,env(safe-area-inset-bottom))] pt-2
                   md:h-full md:flex-col md:justify-start md:gap-1 md:px-2 md:pb-2 md:pt-[96px]
                   lg:h-auto lg:gap-0.5 lg:px-3 lg:pt-0"
      >
        {TABS.map((t) => {
          const on = t.id === active
          return (
            <li key={t.id} className="flex-1 md:flex-none">
              <button
                type="button"
                onClick={() => onChange(t.id)}
                aria-current={on ? 'page' : undefined}
                className={[
                  'flex w-full flex-col items-center gap-1 rounded-btn py-1.5 md:py-2.5',
                  'lg:h-10 lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:py-0',
                  'transition-colors duration-[120ms]',
                  on
                    ? 'text-light-accent dark:text-dark-accent ' +
                      'lg:bg-light-accent/[0.09] dark:lg:bg-dark-accent/[0.14]'
                    : 'text-light-text-muted dark:text-dark-text-muted ' +
                      'lg:hover:bg-light-accent/[0.05] lg:hover:text-light-text-primary ' +
                      'dark:lg:hover:bg-white/[0.05] dark:lg:hover:text-dark-text-primary',
                ].join(' ')}
              >
                {t.icon}
                {/* Sentence-case sans, per the mockup. The tokens reserve IBM
                    Plex Mono for labels/kinds INSIDE content, where uppercase
                    tracking reads as metadata; applied to navigation it shouted
                    and made the bar the loudest element on every screen. The
                    design shows destinations named the way a person says them. */}
                <span className="text-[11px] font-medium leading-none tracking-[0.01em] lg:text-[13.5px]">
                  {t.label}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
