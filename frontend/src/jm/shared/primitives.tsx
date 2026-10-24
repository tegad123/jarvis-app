/**
 * Small pieces the components share: the done check, typing dots, tags, the
 * error line, list containers, a text field, a select, the sheet frame and
 * attachment chips. List, Rows and Field are exported for screens to use.
 */
import {
  useEffect,
  type InputHTMLAttributes,
  type ReactNode,
  type HTMLAttributes,
  forwardRef,
} from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

/** Keyboard focus: a 2px bg gap, then 2px of accent blue. */
export const focus = 'focus-visible:outline-none focus-visible:shadow-jm-focus'

/** Colour changes on hover and press. */
export const press = 'transition-colors duration-jm-press ease-jm-out motion-reduce:transition-none'

export const truncate = 'overflow-hidden text-ellipsis whitespace-nowrap'

export const srOnly = 'sr-only'

export function initials(name?: string | null): string {
  const parts = String(name ?? '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return 'J'
  return (parts[0][0] + (parts[1]?.[0] ?? '')).toUpperCase()
}

/** The done check: accent-text blue, draws itself over --jm-dur-tick when it mounts. */
export function Tick({ size = 16 }: { size?: number }) {
  return (
    <svg className="block shrink-0 text-jm-accent-text" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M5 12.5l4.5 4.5L19 7.5"
        stroke="currentColor"
        strokeWidth={2.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="[stroke-dasharray:20] animate-jm-tick motion-reduce:animate-none"
      />
    </svg>
  )
}

/** Three typing dots. Under reduced motion they hold still at three opacities. */
export function Dots({ className }: { className?: string }) {
  const still = ['motion-reduce:opacity-35', 'motion-reduce:opacity-60', 'motion-reduce:opacity-90']
  return (
    <span className={cx('inline-flex h-4 items-center gap-jm-1 text-jm-fg-secondary', className)} aria-hidden="true">
      {still.map((s, i) => (
        <i
          key={i}
          className={cx('block h-1.5 w-1.5 rounded-full bg-current opacity-30 animate-jm-dot motion-reduce:animate-none', s)}
          style={{ animationDelay: `calc(var(--jm-dur-dots-offset) * ${i})` }}
        />
      ))}
    </span>
  )
}

/** The live dot: breathes while the browser is in use or the mic records. */
export function LiveDot() {
  return <span className="block h-1.5 w-1.5 shrink-0 rounded-full bg-jm-fg animate-jm-live motion-reduce:animate-none" aria-hidden="true" />
}

export const tagBase =
  'inline-flex shrink-0 items-center gap-jm-1 whitespace-nowrap rounded-jm-pill border px-jm-2 py-0.5 text-jm-caption'
export const tagNeutral = 'border-jm-border bg-transparent text-jm-fg-secondary'
export const tagAction = 'border-transparent bg-jm-accent-soft text-jm-accent-soft-text'

/** A tag. tone="action" is the blue Action needed pill: tint, dot and words. */
export function Tag({ tone, children, className }: { tone?: 'action'; children: ReactNode; className?: string }) {
  return (
    <span className={cx(tagBase, tone === 'action' ? tagAction : tagNeutral, className)}>
      {tone === 'action' && <span className="block h-1.5 w-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />}
      {children}
    </span>
  )
}

/** An error sentence: the red is the alert icon only; the words are fg. */
export function ErrorLine({ id, small, children }: { id?: string; small?: boolean; children: ReactNode }) {
  return (
    <p id={id} role="alert" className={cx('m-0 flex items-start gap-jm-2 text-jm-fg', small ? 'text-jm-caption' : 'text-jm-body-sm')}>
      <span className="mt-0.5 text-jm-error">
        <Icon name="alert" size={16} />
      </span>
      <span>{children}</span>
    </p>
  )
}

/** "Made by Jarvis": the outline sparkle, grey, with its words for screen readers. */
export function SparkGlyph({ label }: { label: string }) {
  return (
    <span className="inline-flex shrink-0 text-jm-fg-secondary" title={label}>
      <Icon name="spark" size={14} />
      <span className={srOnly}>{label}</span>
    </span>
  )
}

/** Interleaves a quiet middle dot between metadata items. */
export function joinDots(nodes: ReactNode[]): ReactNode[] {
  const out: ReactNode[] = []
  nodes.forEach((n, i) => {
    if (i) out.push(<span key={`sep${i}`} className="mx-1.5 text-jm-fg-secondary" aria-hidden="true">·</span>)
    out.push(n)
  })
  return out
}

export function Avatar({ name, outline }: { name?: string | null; outline?: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={cx(
        'grid h-8 w-8 shrink-0 place-items-center rounded-full border text-jm-caption',
        outline ? 'border-jm-border-strong bg-transparent text-jm-fg-secondary' : 'border-jm-border bg-jm-panel-raised text-jm-fg',
      )}
    >
      {initials(name)}
    </span>
  )
}

/* ------------------------------------------------------------- containers */

/** The card list for TaskRow and LeadRow: panel, hairline dividers between rows. */
export function List({ className, ...rest }: HTMLAttributes<HTMLUListElement>) {
  return <ul {...rest} className={cx('m-0 list-none rounded-jm-lg border border-jm-border bg-jm-panel px-jm-4', className)} />
}

/** The plain list for ActivityRow and OutboxRow: rows straight on bg. */
export function Rows({ className, ...rest }: HTMLAttributes<HTMLOListElement>) {
  return <ol {...rest} className={cx('m-0 list-none p-0', className)} />
}

/** A divided row inside List or Rows. */
export const rowBase = 'grid min-w-0 gap-jm-1 border-b border-jm-border py-jm-3 last:border-b-0'

/** A text field: pill, border-strong edge, bg fill. `large` for sign-in and sheets. */
export const Field = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & { large?: boolean }>(
  function Field({ className, large, ...rest }, ref) {
    return (
      <input
        ref={ref}
        {...rest}
        className={cx(
          'min-w-0 appearance-none rounded-jm-pill border border-jm-border-strong bg-jm-bg font-jm text-jm-fg outline-none placeholder:text-jm-fg-secondary',
          focus,
          'disabled:opacity-jm-disabled',
          large ? 'h-jm-touch w-full px-jm-5 text-jm-body' : 'h-jm-control px-jm-4 text-jm-body-sm',
          className,
        )}
      />
    )
  },
)

export interface SelectOption {
  value: string
  label: string
}

export function Select({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string
  value: string
  options: SelectOption[]
  disabled?: boolean
  onChange?: (value: string) => void
}) {
  return (
    <span className="relative inline-flex items-center">
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.value)}
        className={cx(
          'h-jm-control-sm max-w-[180px] cursor-pointer appearance-none rounded-jm-pill border border-jm-border-strong bg-jm-bg pl-jm-3 pr-7 font-jm text-jm-caption text-jm-fg',
          focus,
          'disabled:cursor-default disabled:opacity-jm-disabled',
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <span className="pointer-events-none absolute right-2.5 rotate-90 text-jm-fg-secondary">
        <Icon name="chevron" size={14} />
      </span>
    </span>
  )
}

/* ------------------------------------------------------------------ sheet */

/**
 * The sheet frame: bottom sheet on a phone, centred dialog from 640px.
 * `inline` renders it in place (previews, docs) instead of over the page.
 */
export function Sheet({
  title,
  subtitle,
  onClose,
  inline,
  children,
}: {
  title: string
  subtitle?: ReactNode
  onClose?: () => void
  inline?: boolean
  children: ReactNode
}) {
  useEffect(() => {
    if (!onClose || inline) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, inline])

  const node = (
    <div
      role="dialog"
      aria-modal={inline ? undefined : true}
      aria-label={title}
      className={cx(
        'flex justify-center sm:items-center sm:p-jm-6',
        inline ? 'relative min-h-[420px] items-end overflow-hidden rounded-jm-lg' : 'fixed inset-0 z-50 items-end',
      )}
    >
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 cursor-default border-0 bg-jm-scrim p-0 animate-jm-fade"
      />
      <div
        className={cx(
          'relative box-border grid max-h-[85vh] w-full max-w-jm-sheet gap-jm-4 overflow-y-auto',
          'rounded-t-jm-xl border border-b-0 border-jm-border bg-jm-panel px-jm-5 pb-jm-5 pt-jm-3 shadow-jm-float',
          'sm:rounded-jm-xl sm:border-b sm:pt-jm-5',
          'animate-jm-sheet motion-reduce:animate-jm-fade',
        )}
      >
        <span className="block h-1 w-9 justify-self-center rounded-sm bg-jm-border-strong sm:hidden" aria-hidden="true" />
        <div className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-jm-2">
          <div>
            <h2 className="m-0 text-jm-title text-jm-fg">{title}</h2>
            {subtitle && <p className="m-0 mt-0.5 text-jm-body-sm text-jm-fg-secondary">{subtitle}</p>}
          </div>
          {onClose && <CloseButton onClick={onClose} />}
        </div>
        {children}
      </div>
    </div>
  )
  if (inline || typeof document === 'undefined') return node
  return createPortal(node, document.body)
}

function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label="Close"
      onClick={onClick}
      className={cx(
        'grid h-jm-control-sm w-jm-control-sm place-items-center rounded-jm-pill border-0 bg-transparent text-jm-fg-secondary hover:bg-jm-panel-raised hover:text-jm-fg',
        press,
        focus,
      )}
    >
      <Icon name="close" size={16} />
    </button>
  )
}

/* ------------------------------------------------------------ attachments */

export interface ChatAttachment {
  id?: string
  name: string
  /** Human size, e.g. "1.2 MB". */
  size?: string
  isImage?: boolean
  /** Image source for thumbnails. */
  src?: string
  /** Opens or downloads the stored file. */
  href?: string
  /** false: stored but never shown to the model ("saved, not readable yet"). */
  readable?: boolean
}

export function AttachmentChip({ attachment: a, onRemove }: { attachment: ChatAttachment; onRemove?: () => void }) {
  const link = a.href ? { href: a.href, target: '_blank', rel: 'noreferrer' } : {}
  const Tag = a.href ? 'a' : 'span'
  const meta = [a.size, a.readable === false ? 'saved, not readable yet' : null].filter(Boolean).join(' · ')
  return (
    <span className="relative inline-flex max-w-full">
      {a.isImage ? (
        <Tag
          {...link}
          title={a.name + (a.size ? ` · ${a.size}` : '')}
          className="block h-jm-thumb w-jm-thumb overflow-hidden rounded-jm-md border border-jm-border bg-jm-panel"
        >
          <img src={a.src} alt={a.name} loading="lazy" className="block h-full w-full object-cover" />
        </Tag>
      ) : (
        <Tag
          {...link}
          download={a.href ? a.name : undefined}
          className="inline-flex min-w-0 max-w-[280px] items-center gap-jm-2 rounded-jm-md border border-jm-border bg-jm-panel px-jm-3 py-jm-2 text-jm-fg no-underline"
        >
          <span className="text-jm-fg-secondary">
            <Icon name="file" size={18} />
          </span>
          <span className="grid min-w-0">
            <span className={cx('text-jm-body-sm', truncate)}>{a.name}</span>
            {meta && <span className="text-jm-caption text-jm-fg-secondary">{meta}</span>}
          </span>
        </Tag>
      )}
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${a.name}`}
          onClick={onRemove}
          className={cx('absolute -right-1.5 -top-1.5 grid h-5 w-5 cursor-pointer place-items-center rounded-full border-0 bg-jm-fg p-0 text-jm-on-fg', focus)}
        >
          <Icon name="close" size={12} />
        </button>
      )}
    </span>
  )
}
