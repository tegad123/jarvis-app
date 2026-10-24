import { useId, type ReactNode } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { Dots, ErrorLine, Tag, Tick, cx, truncate } from '../shared/primitives'

export interface SettingsSectionProps {
  title: string
  description?: ReactNode
  status?: { tone?: 'done' | 'action' | 'error' | 'busy' | 'neutral'; label: string }
  rows?: {
    id?: string
    title: ReactNode
    detail?: ReactNode
    action?: { label: string; onClick?: () => void; disabled?: boolean }
  }[]
  children?: ReactNode
  error?: string
  actions?: ReactNode
  className?: string
}

export function SettingsSection(p: SettingsSectionProps) {
  const id = useId()
  const st = p.status
  const tone = st?.tone ?? 'neutral'
  const rows = p.rows ?? []

  return (
    <section aria-labelledby={id} className={cx('box-border grid gap-jm-4 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-5', p.className)}>
      <header className="flex items-start gap-jm-3">
        <div className="min-w-0 flex-1">
          <h3 id={id} className="m-0 text-jm-heading text-jm-fg">
            {p.title}
          </h3>
          {p.description && <p className="m-0 mt-jm-1 text-jm-body-sm text-jm-fg-secondary">{p.description}</p>}
        </div>
        {st &&
          (tone === 'action' ? (
            <span className="mt-0.5 shrink-0">
              <Tag tone="action">{st.label}</Tag>
            </span>
          ) : (
            <span
              role="status"
              className={cx('mt-0.5 inline-flex shrink-0 items-center gap-jm-2 text-jm-body-sm', tone === 'neutral' ? 'text-jm-fg-secondary' : 'text-jm-fg')}
            >
              {tone === 'done' && <Tick />}
              {tone === 'error' && (
                <span className="text-jm-error">
                  <Icon name="alert" size={16} />
                </span>
              )}
              {tone === 'busy' && <Dots />}
              {st.label}
            </span>
          ))}
      </header>

      {rows.length > 0 && (
        <ul className="m-0 list-none border-t border-jm-border p-0">
          {rows.map((r, i) => (
            <li key={r.id ?? i} className="flex min-h-jm-touch items-center gap-jm-3 border-b border-jm-border py-jm-2">
              <div className="min-w-0 flex-1">
                <p className={cx('m-0 text-jm-body-sm text-jm-fg', truncate)}>{r.title}</p>
                {r.detail && <p className={cx('m-0 mt-0.5 text-jm-caption text-jm-fg-secondary', truncate)}>{r.detail}</p>}
              </div>
              {r.action && (
                <Button variant="ghost" size="sm" disabled={r.action.disabled} onClick={r.action.onClick}>
                  {r.action.label}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {p.children && <div className="grid gap-jm-3">{p.children}</div>}
      {p.error && <ErrorLine>{p.error}</ErrorLine>}
      {p.actions && <div className="flex flex-wrap items-center gap-jm-2">{p.actions}</div>}
    </section>
  )
}
