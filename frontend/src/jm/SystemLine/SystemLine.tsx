import type { ReactNode } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { Tag, Tick, cx, srOnly } from '../shared/primitives'

export interface SystemLineProps {
  /** neutral: notice · done: receipt with the blue check · action: the blue Action needed pill · error: the red alert icon, sentence in fg. */
  tone?: 'neutral' | 'done' | 'action' | 'error'
  children: ReactNode
  /** Smaller second line. */
  detail?: ReactNode
  /** Replaces "Action needed" in the pill on the action tone. */
  lead?: string
  action?: { label: string; onClick?: () => void }
  className?: string
}

export function SystemLine({ tone = 'neutral', children, detail, lead, action, className }: SystemLineProps) {
  const icon =
    tone === 'done' ? (
      <Tick />
    ) : tone === 'error' ? (
      <span className="text-jm-error">
        <Icon name="alert" size={16} />
      </span>
    ) : tone === 'neutral' ? (
      <span className="block h-1.5 w-1.5 rounded-full bg-current" />
    ) : null
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className={cx(
        'flex max-w-jm-bubble items-start gap-jm-2',
        tone === 'action' || tone === 'error' ? 'text-jm-fg' : 'text-jm-fg-secondary',
        className,
      )}
    >
      {icon && (
        <span className="grid h-5 w-4 shrink-0 place-items-center" aria-hidden="true">
          {icon}
        </span>
      )}
      {tone === 'action' && <Tag tone="action" className="-mt-px">{lead ?? 'Action needed'}</Tag>}
      <span className="min-w-0 flex-1 whitespace-pre-line text-jm-body-sm">
        {tone === 'done' && <span className={srOnly}>Done: </span>}
        {children}
        {detail && <span className="mt-0.5 block break-words text-jm-caption text-jm-fg-secondary">{detail}</span>}
      </span>
      {action && (
        <Button size="sm" variant="secondary" onClick={action.onClick} className="-mt-1">
          {action.label}
        </Button>
      )}
    </div>
  )
}
