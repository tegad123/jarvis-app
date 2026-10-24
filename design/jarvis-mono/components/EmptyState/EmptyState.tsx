import type { ReactNode } from 'react'
import { Button } from '../Button/Button'
import { Icon, type IconName } from '../shared/Icon'
import { cx } from '../shared/primitives'

export interface EmptyStateProps {
  /** Default "Jarvis is watching. Nothing needs you right now." */
  children?: ReactNode
  /** null hides the icon. Default 'inbox'. */
  icon?: IconName | null
  title?: string
  action?: { label: string; onClick?: () => void; primary?: boolean }
  className?: string
}

export function EmptyState({ children, icon = 'inbox', title, action, className }: EmptyStateProps) {
  return (
    <div role="status" className={cx('grid justify-items-center gap-jm-2 px-jm-4 py-jm-8 text-center', className)}>
      {icon !== null && (
        <span aria-hidden="true" className="mb-jm-2 grid h-10 w-10 place-items-center rounded-full border border-jm-border text-jm-fg-secondary">
          <Icon name={icon} size={20} />
        </span>
      )}
      {title && <p className="m-0 text-jm-heading text-jm-fg">{title}</p>}
      <p className="m-0 max-w-[320px] text-jm-body-sm text-jm-fg-secondary">{children ?? 'Jarvis is watching. Nothing needs you right now.'}</p>
      {action && (
        <Button className="mt-jm-2" variant={action.primary ? 'primary' : 'secondary'} onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  )
}
