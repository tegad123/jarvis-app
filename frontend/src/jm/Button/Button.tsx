import type { ButtonHTMLAttributes } from 'react'
import { Icon, type IconName } from '../shared/Icon'
import { cx, focus, press } from '../shared/primitives'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Default 'secondary'. Primary is the blue fill; one primary per decision. */
  variant?: 'primary' | 'secondary' | 'ghost'
  size?: 'md' | 'sm'
  /** With no children the button becomes a round icon button: pass aria-label. */
  icon?: IconName
}

const variants = {
  primary: 'bg-jm-accent text-jm-on-accent enabled:hover:bg-jm-accent-hover enabled:active:bg-jm-accent-hover',
  secondary: 'border-jm-border bg-jm-panel-raised text-jm-fg enabled:hover:border-jm-border-strong',
  ghost: 'bg-transparent text-jm-fg-secondary enabled:hover:bg-jm-panel enabled:hover:text-jm-fg aria-pressed:text-jm-fg',
}

export function Button({ variant = 'secondary', size = 'md', icon, className, children, type = 'button', ...rest }: ButtonProps) {
  const iconOnly = !!icon && (children === undefined || children === null || children === false)
  return (
    <button
      type={type}
      {...rest}
      className={cx(
        'inline-flex cursor-pointer appearance-none items-center justify-center whitespace-nowrap rounded-jm-pill border border-transparent font-jm text-jm-label no-underline',
        'disabled:cursor-default disabled:opacity-jm-disabled',
        size === 'sm' ? 'min-h-jm-control-sm gap-jm-1 px-jm-3' : 'min-h-jm-control gap-jm-2 px-jm-4',
        iconOnly && (size === 'sm' ? 'w-jm-control-sm px-0' : 'w-jm-control px-0'),
        variants[variant],
        press,
        focus,
        className,
      )}
    >
      {icon && <Icon name={icon} size={size === 'sm' ? 16 : 18} />}
      {!iconOnly && children}
    </button>
  )
}
