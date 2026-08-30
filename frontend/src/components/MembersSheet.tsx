import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ThreadMember } from '../lib/types'
import type { Person } from '../lib/api'

/**
 * Who is in this channel — and, for an admin or owner, who else could be.
 *
 * ROSTER, NOT A TEXT FIELD. Membership is keyed on person_key, and a typed key
 * is a typo waiting to grant nothing (or, worse, to look like it granted
 * something). The list of people is the input.
 *
 * The copy says the join is retroactive because it is: a new member reads the
 * whole history, and someone adding a colleague to a deal channel deserves to
 * know that before they tap, not after.
 *
 * READ-ONLY IS A FIRST-CLASS MODE. A member opening this sees the roster with
 * no controls; so does a break-glass operator, who is allowed to know WHO is in
 * a channel but not what was said in it.
 */
interface Props {
  channelName: string
  members: ThreadMember[]
  people: Person[]
  /** Admin or owner. False renders the same list with nothing to press. */
  canManage: boolean
  /** Marks the viewer's own row. Null on a break-glass operator session. */
  personKey: string | null
  busy?: boolean
  error?: string | null
  onAdd: (personKey: string) => void
  onRemove: (personKey: string) => void
  onClose: () => void
}

export default function MembersSheet({
  channelName,
  members,
  people,
  canManage,
  personKey,
  busy,
  error,
  onAdd,
  onRemove,
  onClose,
}: Props) {
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const inChannel = new Set(members.map((m) => m.person_key))
  const nameOf = (key: string) =>
    people.find((p) => p.person_key === key)?.name ?? key
  const candidates = people.filter((p) => !inChannel.has(p.person_key))

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end"
      role="dialog"
      aria-modal="true"
      aria-label={`Members of ${channelName}`}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-ink-900/40 backdrop-blur-[2px]"
      />

      <div className="glass-moment relative max-h-[80dvh] w-full overflow-y-auto rounded-t-screen
                      px-screen-x pb-[max(20px,env(safe-area-inset-bottom))] pt-5">
        <div
          aria-hidden="true"
          className="mx-auto mb-4 h-1 w-9 rounded-full bg-light-border-strong dark:bg-dark-border-strong"
        />
        <h2 className="text-screen-title">Members</h2>
        <p className="mt-1 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          {channelName}
        </p>

        <ul className="mt-4 grid gap-2">
          {members.length === 0 && (
            <li className="text-body-s text-light-text-muted dark:text-dark-text-muted">
              Nobody yet.
            </li>
          )}
          {members.map((m) => (
            <li
              key={m.person_key}
              className="flex min-w-0 items-center gap-3 rounded-list border border-light-border
                         bg-light-surface px-4 py-3
                         dark:border-dark-border dark:bg-dark-surface"
            >
              <span
                aria-hidden="true"
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full
                           bg-light-accent text-[12px] font-semibold text-white
                           dark:bg-dark-accent-fill"
              >
                {nameOf(m.person_key).slice(0, 1).toUpperCase()}
              </span>
              <span className="min-w-0 flex-1 truncate text-row-title">
                {nameOf(m.person_key)}
                {m.person_key === personKey && (
                  <span className="ml-1.5 text-caption text-light-text-muted dark:text-dark-text-muted">
                    you
                  </span>
                )}
              </span>
              {/* You cannot remove yourself: an admin who does it loses the
                  channel from their own list and needs another admin to undo
                  it. Nothing is protecting them from that but this. */}
              {canManage && m.person_key !== personKey && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onRemove(m.person_key)}
                  className="shrink-0 text-caption text-light-text-muted underline-offset-2
                             hover:underline disabled:opacity-50 dark:text-dark-text-muted"
                >
                  Remove
                </button>
              )}
            </li>
          ))}
        </ul>

        {canManage && candidates.length > 0 && (
          <div className="mt-4">
            {!adding ? (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="w-full rounded-pill bg-light-accent px-5 py-3 text-body font-semibold
                           text-white shadow-accent-btn dark:bg-dark-accent-fill"
              >
                Add someone
              </button>
            ) : (
              <>
                <p className="text-caption text-light-text-secondary dark:text-dark-text-secondary">
                  They will see this channel’s full history, including everything
                  said before they joined.
                </p>
                <ul className="mt-2 grid gap-2">
                  {candidates.map((p) => (
                    <li key={p.person_key}>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => onAdd(p.person_key)}
                        className="flex w-full min-w-0 items-center gap-3 rounded-list border
                                   border-light-border bg-light-surface px-4 py-3 text-left
                                   disabled:opacity-50
                                   dark:border-dark-border dark:bg-dark-surface"
                      >
                        <span
                          aria-hidden="true"
                          className="grid h-8 w-8 shrink-0 place-items-center rounded-full
                                     border border-light-border-strong text-[12px] font-semibold
                                     text-light-text-secondary
                                     dark:border-dark-border-strong dark:text-dark-text-secondary"
                        >
                          {p.name.slice(0, 1).toUpperCase()}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate text-row-title">{p.name}</span>
                          {p.title && (
                            <span className="block truncate text-caption text-light-text-muted dark:text-dark-text-muted">
                              {p.title}
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}

        {error && (
          <p
            role="alert"
            className="mt-3 flex items-center gap-2 text-caption text-light-alert dark:text-dark-alert"
          >
            <span
              aria-hidden="true"
              className="block h-[7px] w-[7px] shrink-0 rounded-full bg-light-alert dark:bg-dark-alert"
            />
            {error}
          </p>
        )}
      </div>
    </div>,
    document.body,
  )
}
