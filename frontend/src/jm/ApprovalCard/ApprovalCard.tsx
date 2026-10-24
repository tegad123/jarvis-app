import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { Tag, Tick, cx, srOnly, truncate } from '../shared/primitives'

export interface ApprovalChange {
  key: string
  label?: string
  current: unknown
  proposed: unknown
}

export interface ApprovalCardProps {
  summary: string
  skill?: string
  time?: string
  status?: 'pending' | 'approved' | 'rejected'
  /** The approval's display (or payload) fields. */
  fields?: Record<string, unknown> | [string, unknown][]
  /** Config change: rendered as a diff. */
  changes?: ApprovalChange[]
  /** From the server. Default true. */
  canApprove?: boolean
  canReject?: boolean
  /** Whose browser this approval belongs to. */
  forPerson?: string | null
  /** "Approve for 30 min" for a browser_task. */
  approveLabel?: string
  onApprove?: () => void
  onReject?: () => void
  busy?: boolean
  decidedBy?: string
  decidedAt?: string
  className?: string
}

const show = (v: unknown) => (v === undefined || v === null ? '—' : Array.isArray(v) ? v.join(', ') : String(v))

export function ApprovalCard(p: ApprovalCardProps) {
  const status = p.status ?? 'pending'
  const pending = status === 'pending'
  const canApprove = p.canApprove !== false
  const canReject = p.canReject !== false
  const raw = p.fields ?? []
  const fields = (Array.isArray(raw) ? raw : Object.entries(raw)).filter(([, v]) => v !== null && v !== undefined && v !== '')
  const changes = p.changes ?? []

  let note: string | null = null
  if (pending && !canApprove && canReject) {
    note = p.forPerson
      ? `For ${p.forPerson}’s browser. Only they can approve it; you can reject it.`
      : 'Someone else approves this one; you can reject it.'
  } else if (pending && !canApprove && !canReject) {
    note = 'You can’t decide this one.'
  }

  return (
    <article
      data-status={status}
      className={cx('box-border grid gap-jm-3 rounded-jm-lg border border-jm-border p-jm-4', pending ? 'bg-jm-panel' : 'bg-transparent', p.className)}
    >
      <div className="flex min-w-0 items-center gap-jm-2 text-jm-fg-secondary">
        {pending && <Tag tone="action">Action needed</Tag>}
        {p.skill && <span className={cx('min-w-0 text-jm-caption', truncate)}>{p.skill}</span>}
        {p.time && <span className="ml-auto shrink-0 text-jm-caption">{p.time}</span>}
      </div>
      <p className={cx('m-0 text-jm-heading text-jm-fg', !pending && 'font-medium')}>{p.summary}</p>

      {fields.length > 0 && (
        <dl className="m-0 grid gap-jm-2">
          {fields.map(([k, v]) => {
            const key = String(k).replace(/_/g, ' ')
            return (
              <div key={String(k)} className="grid grid-cols-[88px_minmax(0,1fr)] gap-jm-3">
                <dt className="pt-0.5 text-jm-caption text-jm-fg-secondary">{key.charAt(0).toUpperCase() + key.slice(1)}</dt>
                <dd className="m-0 whitespace-pre-wrap break-words text-jm-body-sm text-jm-fg">{show(v)}</dd>
              </div>
            )
          })}
        </dl>
      )}

      {changes.length > 0 && (
        <ul className="m-0 grid list-none gap-jm-2 p-0">
          {changes.map((c, i) => (
            <li key={c.key ?? i} className="rounded-jm-md border border-jm-border p-jm-3">
              <p className="m-0 text-jm-body-sm text-jm-fg">{c.label ?? c.key}</p>
              {c.label && <p className="m-0 mt-0.5 text-jm-caption text-jm-fg-secondary">{c.key}</p>}
              <p className="m-0 mt-jm-2 flex flex-wrap items-center gap-jm-2 text-jm-body-sm">
                <span className="break-words text-jm-fg-secondary line-through">
                  <span className={srOnly}>From </span>
                  {show(c.current)}
                </span>
                <span className="text-jm-fg-secondary" aria-hidden="true">
                  →
                </span>
                <span className="break-words font-medium text-jm-fg">
                  <span className={srOnly}> to </span>
                  {show(c.proposed)}
                </span>
              </p>
            </li>
          ))}
        </ul>
      )}

      {pending ? (
        <>
          {note && <p className="m-0 text-jm-body-sm text-jm-fg-secondary">{note}</p>}
          {(canApprove || canReject) && (
            <div className="flex gap-jm-2 [&>*]:flex-1">
              {canApprove && (
                <Button variant="primary" disabled={p.busy} onClick={p.onApprove}>
                  {p.busy ? 'Saving…' : (p.approveLabel ?? 'Approve')}
                </Button>
              )}
              {canReject && (
                <Button variant="secondary" disabled={p.busy} onClick={p.onReject}>
                  Reject
                </Button>
              )}
            </div>
          )}
        </>
      ) : (
        <p className="m-0 flex items-center gap-jm-2 text-jm-caption text-jm-fg-secondary">
          {status === 'approved' ? <Tick size={14} /> : <Icon name="close" size={14} />}
          <span>
            {status === 'approved' ? 'Approved' : 'Rejected'}
            {p.decidedBy ? ` by ${p.decidedBy}` : ''}
            {p.decidedAt ? ` · ${p.decidedAt}` : ''}
          </span>
        </p>
      )}
    </article>
  )
}
