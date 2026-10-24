import { useState } from 'react'
import { Button } from '../Button/Button'
import type { Person } from '../SignIn/SignIn'
import { Icon } from '../shared/Icon'
import { Avatar, ErrorLine, Sheet, cx, focus, press, srOnly, truncate } from '../shared/primitives'

export interface MembersSheetProps {
  channelName: string
  /** Person keys in the channel. */
  members: string[]
  people: Person[]
  /** The viewer; null on operator access. */
  personKey?: string | null
  /** Owner or admin. */
  canManage?: boolean
  onAdd?: (id: string) => void
  onRemove?: (id: string) => void
  onClose?: () => void
  busy?: boolean
  error?: string | null
  defaultAdding?: boolean
  readOnlyNote?: string
  inline?: boolean
}

export function MembersSheet(p: MembersSheetProps) {
  const [adding, setAdding] = useState(!!p.defaultAdding)
  const nameOf = (key: string) => p.people.find((x) => x.id === key)?.name ?? key
  const inChannel = new Set(p.members)
  const candidates = p.people.filter((x) => !inChannel.has(x.id))

  return (
    <Sheet title="Members" subtitle={p.channelName} onClose={p.onClose} inline={p.inline}>
      <ul className="m-0 grid list-none gap-jm-1 p-0">
        {p.members.length === 0 && <li className="text-jm-body-sm text-jm-fg-secondary">Nobody yet.</li>}
        {p.members.map((key) => (
          <li key={key} className="flex min-h-jm-touch items-center gap-jm-3">
            <Avatar name={nameOf(key)} />
            <span className={cx('min-w-0 flex-1 text-jm-label text-jm-fg', truncate)}>
              {nameOf(key)}
              {key === p.personKey && <span className="ml-jm-2 text-jm-caption text-jm-fg-secondary">you</span>}
            </span>
            {/* Nobody removes themselves: an admin who did would lose the channel. */}
            {p.canManage && key !== p.personKey && (
              <Button variant="ghost" size="sm" disabled={p.busy} onClick={() => p.onRemove?.(key)}>
                Remove
              </Button>
            )}
          </li>
        ))}
      </ul>

      {p.canManage &&
        candidates.length > 0 &&
        (!adding ? (
          <Button variant="primary" className="min-h-jm-touch w-full" onClick={() => setAdding(true)}>
            Add someone
          </Button>
        ) : (
          <div className="grid gap-jm-3">
            <p className="m-0 text-jm-body-sm text-jm-fg-secondary">
              They will see this channel’s full history, including everything said before they joined.
            </p>
            <ul className="m-0 grid list-none gap-jm-1 p-0">
              {candidates.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    disabled={p.busy}
                    onClick={() => p.onAdd?.(c.id)}
                    className={cx(
                      'box-border flex min-h-jm-touch w-full cursor-pointer appearance-none items-center gap-jm-3 rounded-jm-md border border-jm-border bg-transparent px-jm-3 py-jm-2 text-left font-jm text-jm-fg',
                      'enabled:hover:bg-jm-bg disabled:cursor-default disabled:opacity-jm-disabled',
                      press,
                      focus,
                    )}
                  >
                    <Avatar name={c.name} outline />
                    <span className="grid min-w-0 flex-1">
                      <span className={cx('text-jm-label text-jm-fg', truncate)}>{c.name}</span>
                      {c.title && <span className={cx('text-jm-caption text-jm-fg-secondary', truncate)}>{c.title}</span>}
                    </span>
                    <span className="text-jm-fg-secondary">
                      <Icon name="plus" size={18} />
                    </span>
                    <span className={srOnly}>Add to the channel</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}

      {!p.canManage && (
        <p className="m-0 text-jm-caption text-jm-fg-secondary">{p.readOnlyNote ?? 'Only an owner or admin can change who is in this channel.'}</p>
      )}
      {p.error && <ErrorLine>{p.error}</ErrorLine>}
    </Sheet>
  )
}
