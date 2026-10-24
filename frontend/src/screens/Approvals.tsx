import { useCallback, useEffect, useState } from 'react'
import Page from '../components/Page'
import { ApiError, Unauthorized, decideApproval, getApprovals } from '../lib/api'
import { ApprovalCard, EmptyState, ErrorState } from '../jm'
import { relativeTime } from '../lib/time'
import type { Approval, ConfigChange } from '../lib/types'

/**
 * The approvals queue. Pending requests first; decided ones stay visible and
 * quiet below, because the point of an approvals log is that you can see what
 * was decided. Approve and Reject show only as far as the server's
 * can_approve / can_reject allow, and the server refuses the rest.
 */

interface Props {
  onUnauthorized: () => void
  /** Lets the sidebar count follow a decision without waiting for its poll. */
  onDecided?: () => void
}

function Card({
  approval: a,
  busy,
  onDecide,
}: {
  approval: Approval
  busy: boolean
  onDecide: (id: string, d: 'approve' | 'reject') => void
}) {
  // Unknown kinds fall through to the flat list rather than erroring: a
  // request from a newer producer must degrade, not break the queue.
  const changes =
    !a.display && a.kind === 'config-change' && Array.isArray(a.payload?.changes)
      ? (a.payload.changes as ConfigChange[])
      : undefined
  return (
    <li>
      <ApprovalCard
        summary={a.summary}
        skill={a.skill}
        time={relativeTime(a.ts)}
        status={a.status}
        fields={changes ? undefined : (a.display ?? a.payload)}
        changes={changes}
        canApprove={a.can_approve !== false}
        canReject={a.can_reject !== false}
        forPerson={a.for_person}
        approveLabel={a.payload?.type === 'browser_task' ? 'Approve for 30 min' : 'Approve'}
        onApprove={() => onDecide(a.id, 'approve')}
        onReject={() => onDecide(a.id, 'reject')}
        busy={busy}
        decidedBy={a.decided_by}
        decidedAt={a.decided_at ? relativeTime(a.decided_at) : undefined}
      />
    </li>
  )
}

export default function Approvals({ onUnauthorized, onDecided }: Props) {
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
        onDecided?.()
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorized()
        setError({ message: err instanceof ApiError ? err.message : 'Could not record that decision.' })
        // Someone else may have decided it; re-read rather than guess.
        void load()
      } finally {
        setBusyId(null)
      }
    },
    [load, onUnauthorized, onDecided],
  )

  const pending = (items ?? []).filter((a) => a.status === 'pending')
  const resolved = (items ?? []).filter((a) => a.status !== 'pending')

  return (
    <Page title="Approvals" subtitle="Nothing goes out without your say-so">
      {error && (
        <ErrorState
          label="Approvals unavailable"
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={(items?.length ?? 0) > 0}
        />
      )}

      {items === null && !error ? (
        <p className="m-0 py-jm-8 text-center text-jm-body-sm text-jm-fg-secondary">Loading…</p>
      ) : (
        <>
          {pending.length === 0 && !error && <EmptyState icon="approvals">Nothing waiting on you.</EmptyState>}

          {pending.length > 0 && (
            <ul className="m-0 grid list-none gap-jm-3 p-0">
              {pending.map((a) => (
                <Card key={a.id} approval={a} busy={busyId === a.id} onDecide={decide} />
              ))}
            </ul>
          )}

          {resolved.length > 0 && (
            <section className="grid gap-jm-3">
              <h2 className="m-0 text-jm-caption text-jm-fg-secondary">Decided</h2>
              <ul className="m-0 grid list-none gap-jm-3 p-0">
                {resolved.map((a) => (
                  <Card key={a.id} approval={a} busy={false} onDecide={decide} />
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </Page>
  )
}
