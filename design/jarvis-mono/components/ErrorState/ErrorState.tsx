import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { cx } from '../shared/primitives'

export interface ErrorStateProps {
  /** The screen's own name: "Leads unavailable". */
  label?: string
  message: string
  detail?: string
  onRetry?: () => void
  busy?: boolean
  /** The last good data is still on screen. */
  stale?: boolean
  staleLabel?: string
  staleMessage?: string
  className?: string
}

export function ErrorState(p: ErrorStateProps) {
  return (
    <div role="alert" className={cx('box-border grid gap-jm-2 rounded-jm-lg border border-jm-error bg-jm-panel p-jm-4', p.className)}>
      <div className="flex items-center gap-jm-2 text-jm-fg">
        <span className="text-jm-error">
          <Icon name="alert" size={18} />
        </span>
        <span className="text-jm-label">{p.stale ? (p.staleLabel ?? 'Not updating') : (p.label ?? 'Unavailable')}</span>
      </div>
      <p className="m-0 text-jm-body-sm text-jm-fg">{p.message}</p>
      {p.detail && <p className="m-0 break-words text-jm-caption text-jm-fg-secondary">{p.detail}</p>}
      {p.stale && (
        <p className="m-0 text-jm-body-sm text-jm-fg-secondary">{p.staleMessage ?? 'What you see is the last thing Jarvis loaded. It may be out of date.'}</p>
      )}
      {p.onRetry && (
        <div className="mt-jm-1">
          <Button size="sm" icon="refresh" disabled={p.busy} onClick={p.onRetry}>
            {p.busy ? 'Trying…' : 'Try again'}
          </Button>
        </div>
      )}
    </div>
  )
}
