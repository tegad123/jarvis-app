import { Button } from '../Button/Button'
import { ConversationRow, type ConversationRowProps } from '../ConversationRow/ConversationRow'
import { Icon, type IconName } from '../shared/Icon'
import { Avatar, cx, focus, press, srOnly, truncate } from '../shared/primitives'

export interface SidebarNavItem {
  id: string
  label: string
  icon?: IconName
  count?: number
  /** Shows the count as the solid blue badge (pending approvals). */
  attention?: boolean
}

export interface SidebarProps {
  brand?: string
  nav?: SidebarNavItem[]
  /** Id of the open destination or thread. */
  active?: string
  onSelect?: (id: string) => void
  onNewChat?: () => void
  newChatLabel?: string
  direct?: ConversationRowProps & { id: string }
  channels?: (ConversationRowProps & { id: string })[]
  channelsLabel?: string
  emptyLabel?: string
  person?: { name: string; role?: string }
  theme?: 'dark' | 'light'
  onToggleTheme?: () => void
  onSignOut?: () => void
  label?: string
  className?: string
}

export function Sidebar(p: SidebarProps) {
  const nav = p.nav ?? []
  const channels = p.channels ?? []
  const select = (id: string) => p.onSelect?.(id)

  return (
    <nav
      aria-label={p.label ?? 'Jarvis'}
      className={cx('box-border flex h-full min-h-0 w-jm-sidebar flex-col gap-jm-4 border-r border-jm-border bg-jm-bg p-jm-3', p.className)}
    >
      <div className="flex items-center justify-between pl-jm-3">
        <span className="text-jm-heading text-jm-fg">{p.brand ?? 'Jarvis'}</span>
        {p.onNewChat && <Button variant="ghost" icon="compose" aria-label={p.newChatLabel ?? 'New channel'} onClick={p.onNewChat} />}
      </div>

      {nav.length > 0 && (
        <ul className="m-0 grid list-none grid-cols-[minmax(0,1fr)] gap-0.5 p-0">
          {nav.map((item) => {
            const on = item.id === p.active
            return (
              <li key={item.id}>
                <button
                  type="button"
                  aria-current={on ? 'page' : undefined}
                  onClick={() => select(item.id)}
                  className={cx(
                    'flex min-h-jm-control w-full cursor-pointer appearance-none items-center gap-jm-3 rounded-jm-md border-0 px-jm-3 text-left font-jm text-jm-label',
                    on ? 'bg-jm-accent-soft text-jm-accent-soft-text' : 'bg-transparent text-jm-fg-secondary hover:bg-jm-panel hover:text-jm-fg',
                    press,
                    focus,
                  )}
                >
                  {item.icon && <Icon name={item.icon} size={18} />}
                  <span className="min-w-0 flex-1">{item.label}</span>
                  {item.count ? (
                    item.attention ? (
                      <span className="inline-grid h-5 min-w-5 shrink-0 place-items-center rounded-jm-pill bg-jm-accent px-1.5 text-jm-caption font-semibold tabular-nums text-jm-on-accent">
                        {item.count}
                        <span className={srOnly}> waiting on you</span>
                      </span>
                    ) : (
                      <span className="text-jm-caption tabular-nums text-jm-fg-secondary">{item.count}</span>
                    )
                  ) : null}
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)] content-start gap-jm-4 overflow-y-auto">
        {p.direct && (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-0.5">
            <ConversationRow {...p.direct} direct active={p.active === p.direct.id} onClick={() => select(p.direct!.id)} />
          </div>
        )}
        <div className="grid grid-cols-[minmax(0,1fr)] gap-0.5">
          <p className="m-0 mb-jm-1 px-jm-3 text-jm-caption text-jm-fg-secondary">{p.channelsLabel ?? 'Channels'}</p>
          {channels.length ? (
            channels.map((c) => <ConversationRow key={c.id} {...c} active={p.active === c.id} onClick={() => select(c.id)} />)
          ) : (
            <p className="m-0 px-jm-3 text-jm-body-sm text-jm-fg-secondary">{p.emptyLabel ?? 'When a new deal starts, Jarvis opens a channel for it.'}</p>
          )}
        </div>
      </div>

      {p.person && (
        <div className="flex items-center gap-jm-2 border-t border-jm-border pl-jm-1 pt-jm-3">
          <Avatar name={p.person.name} />
          <span className="grid min-w-0 flex-1">
            <span className={cx('text-jm-label text-jm-fg', truncate)}>{p.person.name}</span>
            {p.person.role && <span className="text-jm-caption text-jm-fg-secondary">{p.person.role}</span>}
          </span>
          {p.onToggleTheme && (
            <Button
              variant="ghost"
              icon={p.theme === 'light' ? 'moon' : 'sun'}
              aria-label={p.theme === 'light' ? 'Switch to dark' : 'Switch to light'}
              onClick={p.onToggleTheme}
            />
          )}
          {p.onSignOut && <Button variant="ghost" icon="signout" aria-label="Sign out" onClick={p.onSignOut} />}
        </div>
      )}
    </nav>
  )
}
