import { useCallback, useEffect, useState } from 'react'
import { ErrorState } from '../components/States'
import { ApiError, Unauthorized, decideApproval, getApprovals } from '../lib/api'
import { CHIP, DOT_BG, WORD_TEXT } from '../lib/kinds'
import { relativeTime } from '../lib/time'
import type { Approval, ConfigChange } from '../lib/types'

/**
 * The approvals queue.
 *
 * Pending requests are the "waiting on the user" case, which the tokens assign
 * to the warning semantic — dot plus word, never a dot alone. Resolved cards
 * stay visible with their outcome rather than disappearing: the point of an
 * approvals log is that you can see what was decided.
 *
 * Edit is deliberately inert in v1 (no edit surface exists behind it) but is
 * rendered at equal weight to Approve, because the two are meant to read as a
 * genuine pair of choices, not a primary action with a decoration.
 */

interface Props {
  onUnauthorized: () => void
}

/** A config-change renders as a DIFF, not a key/value dump.
 *
 * The flat list below is right for "send this email" — every field is an
 * independent fact. A config change is a before/after on specific keys, and
 * that is exactly the case where precision matters most: the owner is agreeing
 * to one transition, and the server will refuse to apply it if the live value
 * has moved since. Showing that as `changes: [object Object]` would ask them to
 * approve something they cannot read.
 */
function ConfigDiff({ changes }: { changes: ConfigChange[] }) {
  const show = (v: unknown) =>
    v === undefined ? '—' : Array.isArray(v) ? v.join(', ') : String(v)
  return (
    <ul className="mt-3 grid gap-2">
      {changes.map((c, i) => (
        <li
          key={i}
          className="rounded-list border border-light-border p-3
                     dark:border-dark-border"
        >
          <p className="text-body-s text-light-text-primary dark:text-dark-text-primary">
            {c.label || c.key}
          </p>
          <p className="mt-1 font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted">
            {c.key}
          </p>
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2 text-body-s">
            <span className="min-w-0 break-words text-light-text-secondary line-through dark:text-dark-text-secondary">
              {show(c.current)}
            </span>
            <span aria-hidden="true" className="text-light-text-muted dark:text-dark-text-muted">
              →
            </span>
            <span className="min-w-0 break-words font-medium text-light-text-primary dark:text-dark-text-primary">
              {show(c.proposed)}
            </span>
          </div>
        </li>
      ))}
    </ul>
  )
}

function PayloadDetails({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(
    ([, v]) => v !== null && v !== undefined && v !== '',
  )
  if (!entries.length) return null
  return (
    <dl className="mt-3 grid gap-1.5">
      {entries.map(([k, v]) => (
        <div key={k} className="flex min-w-0 gap-2">
          <dt className="w-28 shrink-0 font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted">
            {k.replace(/_/g, ' ')}
          </dt>
          <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            {Array.isArray(v) ? v.join(', ') : String(v)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function ApprovalCard({
  approval,
  busy,
  onDecide,
}: {
  approval: Approval
  busy: boolean
  onDecide: (id: string, d: 'approve' | 'reject') => void
}) {
  const pending = approval.status === 'pending'
  const semantic = pending
    ? 'warning'
    : approval.status === 'approved'
      ? 'success'
      : 'neutral'
  const word = pending
    ? 'Needs approval'
    : approval.status === 'approved'
      ? 'Approved'
      : 'Rejected'

  return (
    <li className="glass-featured overflow-hidden rounded-card p-card-x">
      {/* Same chip language as the feed: state as a tinted container, skill as
          quiet provenance after it. An approval card is the one place in the
          product where a person is being ASKED something, so "Needs approval"
          reading as a grey label was the worst instance of the flat metadata
          row — it looked like a filing status rather than a question. */}
      <div className="flex items-center gap-2">
        <span className={`${CHIP[semantic]} shrink-0`}>
          <span
            aria-hidden="true"
            className={`block h-[6px] w-[6px] shrink-0 rounded-full ${DOT_BG[semantic]}`}
          />
          {word}
        </span>
        <span className="min-w-0 truncate font-mono text-[10px] font-medium uppercase
                         tracking-[0.06em] text-light-text-muted dark:text-dark-text-muted">
          {approval.skill}
        </span>
        <time
          dateTime={approval.ts}
          className="ml-auto shrink-0 text-caption text-light-text-muted dark:text-dark-text-muted"
        >
          {relativeTime(approval.ts)}
        </time>
      </div>

      <p className="mt-2 text-hero font-semibold tracking-[-0.006em]
                    text-light-text-primary dark:text-dark-text-primary">
        {approval.summary}
      </p>

      {/* Unknown kinds fall through to the flat list rather than erroring: a
          request from a newer producer must degrade, not break the queue. */}
      {approval.display ? (
        <PayloadDetails payload={approval.display} />
      ) : approval.kind === 'config-change' &&
        Array.isArray(approval.payload?.changes) ? (
        <ConfigDiff changes={approval.payload.changes as ConfigChange[]} />
      ) : (
        <PayloadDetails payload={approval.payload} />
      )}

      {pending ? (
        <div className="mt-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled
            title="Editing a request lands with the full approvals contract"
            className="rounded-btn border border-light-border-strong px-4 py-3 text-body
                       font-semibold text-light-text-secondary opacity-50
                       dark:border-dark-border-strong dark:text-dark-text-secondary"
          >
            Edit
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide(approval.id, 'approve')}
            className="rounded-btn bg-light-accent px-4 py-3 text-body font-semibold
                       text-white shadow-accent-btn transition-opacity
                       disabled:opacity-50 dark:bg-dark-accent-fill"
          >
            {busy ? 'Saving…' : 'Approve'}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecide(approval.id, 'reject')}
            className="col-span-2 rounded-btn px-4 py-2 text-body-s
                       text-light-text-secondary transition-opacity disabled:opacity-50
                       dark:text-dark-text-secondary"
          >
            Reject
          </button>
        </div>
      ) : (
        // DECIDED APPROVALS STAY QUIET — deliberately NOT a chip. This is the
        // footer of a settled card, and chipping it would give history the same
        // presence as the pending question above it, which is backwards for a
        // queue: the loud thing should be the one still waiting on you.
        <p className="mt-3 flex flex-wrap items-baseline gap-x-2 text-caption text-light-text-muted dark:text-dark-text-muted">
          <span className={`font-mono text-label-mono uppercase ${WORD_TEXT[semantic]}`}>
            {word}
          </span>
          {approval.decided_by && <span>by {approval.decided_by}</span>}
          {approval.decided_at && <span>· {relativeTime(approval.decided_at)}</span>}
        </p>
      )}
    </li>
  )
}

export default function Approvals({ onUnauthorized }: Props) {
  const [items, setItems] = useState<Approval[] | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await getApprovals()
      setItems(r.approvals)
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError({
        message: 'Jarvis could not read the approvals queue.',
        detail: err instanceof ApiError ? (err.detail ?? `HTTP ${err.status}`) : undefined,
      })
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
  }, [load])

  const decide = useCallback(
    async (id: string, decision: 'approve' | 'reject') => {
      setBusyId(id)
      setError(null)
      try {
        const updated = await decideApproval(id, decision)
        setItems((cur) => (cur ?? []).map((a) => (a.id === id ? updated : a)))
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorized()
        setError({
          message: err instanceof ApiError ? err.message : 'Could not record that decision.',
        })
        // Someone else may have decided it; re-read rather than guess.
        void load()
      } finally {
        setBusyId(null)
      }
    },
    [load, onUnauthorized],
  )

  const pending = (items ?? []).filter((a) => a.status === 'pending')
  const resolved = (items ?? []).filter((a) => a.status !== 'pending')

  return (
    <div className="grid min-w-0 gap-4">
      <header>
        <h2 className="text-screen-title">Approvals</h2>
        <p className="mt-0.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          Nothing goes out without your say-so
        </p>
      </header>

      {error && (
        <ErrorState
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={(items?.length ?? 0) > 0}
          label="Approvals unavailable"
        />
      )}

      {items === null && !error ? (
        <p className="py-8 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
          Loading…
        </p>
      ) : (
        <>
          {pending.length === 0 && !error && (
            <p className="rounded-list border border-light-border bg-light-surface p-card-x
                          text-body text-light-text-secondary shadow-card
                          dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary">
              Nothing waiting on you.
            </p>
          )}

          {pending.length > 0 && (
            <ul className="grid min-w-0 gap-3">
              {pending.map((a) => (
                <ApprovalCard
                  key={a.id}
                  approval={a}
                  busy={busyId === a.id}
                  onDecide={decide}
                />
              ))}
            </ul>
          )}

          {resolved.length > 0 && (
            <section className="grid min-w-0 gap-3">
              <h3 className="font-mono text-label-mono uppercase text-light-text-secondary dark:text-dark-text-secondary">
                Decided
              </h3>
              <ul className="grid min-w-0 gap-3">
                {resolved.map((a) => (
                  <ApprovalCard key={a.id} approval={a} busy={false} onDecide={decide} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  )
}
