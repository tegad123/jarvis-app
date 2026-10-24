import { useEffect, useRef, useState } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { cx, focus, press, srOnly, truncate } from '../shared/primitives'

export interface ConversationRowProps {
  name: string
  /** Last message snippet. */
  preview?: string | null
  /** 'assistant' prefixes "Jarvis: ", 'user' prefixes "You: ". */
  previewRole?: 'assistant' | 'user' | null
  /** Already relative: "12m", "2h", "3d". */
  time?: string
  unread?: boolean
  /** last_message_at older than 48 hours. Shows "Quiet · {time}". */
  quiet?: boolean
  /** created_by === 'jarvis': the sparkle before the name. */
  byJarvis?: boolean
  /** The person's own line (is_direct). No menu, chat glyph. */
  direct?: boolean
  active?: boolean
  /** Operator session: the preview is withheld by the server. */
  bodiesHidden?: boolean
  onClick?: () => void
  /** Rename / Archive. Omit for the direct line. */
  menu?: { label: string; onSelect?: () => void }[]
  menuOpen?: boolean
  onMenuOpenChange?: (open: boolean) => void
  className?: string
}

export function ConversationRow(p: ConversationRowProps) {
  const [internal, setInternal] = useState(false)
  const controlled = p.menuOpen !== undefined
  const open = controlled ? !!p.menuOpen : internal
  const wrap = useRef<HTMLDivElement>(null)
  const hasMenu = !!p.menu?.length

  const setOpen = (v: boolean) => {
    p.onMenuOpenChange?.(v)
    if (!controlled) setInternal(v)
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  let preview: string
  if (p.bodiesHidden) preview = 'Messages hidden for operator access'
  else if (p.preview) preview = (p.previewRole === 'assistant' ? 'Jarvis: ' : p.previewRole === 'user' ? 'You: ' : '') + p.preview
  else preview = p.direct ? 'Your direct line. Always here.' : 'No messages yet'

  const time = p.time ? (p.quiet ? `Quiet · ${p.time}` : p.time) : p.quiet ? 'Quiet' : null

  return (
    <div ref={wrap} className={cx('group relative min-w-0', p.className)}>
      {/* The selected conversation: the blue tint and a 3px blue marker. */}
      {p.active && <span className="absolute bottom-2.5 left-0 top-2.5 z-10 w-[3px] rounded-r-sm bg-jm-accent" aria-hidden="true" />}
      <button
        type="button"
        onClick={p.onClick}
        aria-current={p.active ? 'page' : undefined}
        onContextMenu={
          hasMenu
            ? (e) => {
                e.preventDefault()
                setOpen(true)
              }
            : undefined
        }
        className={cx(
          'block w-full cursor-pointer appearance-none rounded-jm-md border-0 px-jm-3 py-jm-2 text-left font-jm text-jm-fg',
          p.active ? 'bg-jm-accent-soft' : 'bg-transparent group-hover:bg-jm-panel',
          press,
          focus,
        )}
      >
        <span className="flex min-w-0 items-center gap-jm-2">
          {p.direct && (
            <span className="text-jm-fg-secondary">
              <Icon name="chat" size={16} />
            </span>
          )}
          {!p.direct && p.byJarvis && (
            <span className="text-jm-fg-secondary" title="Opened by Jarvis">
              <Icon name="spark" size={14} />
              <span className={srOnly}>Opened by Jarvis: </span>
            </span>
          )}
          <span className={cx('min-w-0 flex-1 text-jm-label', truncate)}>{p.name}</span>
          {time && (
            <span className={cx('shrink-0 text-jm-caption text-jm-fg-secondary', hasMenu && 'group-hover:invisible group-focus-within:invisible')}>
              {time}
            </span>
          )}
          {p.unread && <span role="img" aria-label="Unread" className="block h-1.5 w-1.5 shrink-0 rounded-full bg-jm-fg" />}
        </span>
        <span className={cx('mt-0.5 block text-jm-body-sm text-jm-fg-secondary', truncate)}>{preview}</span>
      </button>
      {hasMenu && (
        <Button
          variant="ghost"
          size="sm"
          icon="more"
          aria-label={`Options for ${p.name}`}
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className={cx('absolute right-jm-1 top-jm-1 opacity-0 focus-visible:opacity-100 group-hover:opacity-100', open && 'opacity-100')}
        />
      )}
      {hasMenu && open && (
        <div
          role="menu"
          className="absolute right-jm-2 top-[calc(100%-var(--jm-space-1))] z-20 min-w-[160px] rounded-jm-md border border-jm-border bg-jm-panel-raised p-jm-1 shadow-jm-float animate-jm-sheet motion-reduce:animate-jm-fade"
        >
          {p.menu!.map((m, i) => (
            <button
              key={i}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                m.onSelect?.()
              }}
              className={cx(
                'block w-full cursor-pointer appearance-none rounded-jm-sm border-0 bg-transparent px-jm-3 py-jm-2 text-left font-jm text-jm-body-sm text-jm-fg hover:bg-jm-panel',
                focus,
              )}
            >
              {m.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
