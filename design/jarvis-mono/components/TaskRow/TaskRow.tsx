import type { ReactNode } from 'react'
import { Button } from '../Button/Button'
import { Select, SparkGlyph, Tag, Tick, cx, focus, joinDots, rowBase, truncate } from '../shared/primitives'

export type TaskStatus = 'open' | 'in_progress' | 'done' | 'archived'

export interface TaskRowProps {
  title: string
  /** Null on an operator session (withheld by the server). */
  detail?: string | null
  status?: TaskStatus
  priority?: 'high' | 'medium' | 'low'
  /** Server-computed. Shows dueLabel as the blue Action needed pill. */
  overdue?: boolean
  stale?: boolean
  dueLabel?: string
  /** e.g. "4 days open". */
  openFor?: string
  assigneeName?: string
  byJarvis?: boolean
  canStatus?: boolean
  onStatus?: (next: 'open' | 'in_progress' | 'done') => void
  canManage?: boolean
  people?: { id: string; name?: string }[]
  assignee?: string | null
  onAssign?: (id: string | null) => void
  onArchive?: () => void
  busy?: boolean
  className?: string
}

const LABEL: Record<TaskStatus, string> = { open: 'Open', in_progress: 'In progress', done: 'Done', archived: 'Archived' }
// Archive is a decision, not a step: it is never in the cycle.
const CYCLE = ['open', 'in_progress', 'done'] as const

export const statusPill =
  'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-jm-pill border border-jm-border bg-transparent px-jm-2 py-0.5 font-jm text-jm-caption text-jm-fg'

function StatusMark({ status }: { status: TaskStatus }) {
  if (status === 'done') return <Tick size={14} />
  if (status === 'archived') return null
  return (
    <span
      aria-hidden="true"
      className={cx(
        'relative box-border block h-2.5 w-2.5 rounded-full border-[1.5px]',
        status === 'in_progress' ? "border-jm-fg after:absolute after:inset-0.5 after:rounded-full after:bg-jm-fg after:content-['']" : 'border-jm-border-strong',
      )}
    />
  )
}

export function TaskRow(p: TaskRowProps) {
  const status = p.status ?? 'open'
  const word = LABEL[status]
  const next = CYCLE[(CYCLE.indexOf(status as (typeof CYCLE)[number]) + 1) % CYCLE.length]
  const inner = (
    <>
      <StatusMark status={status} />
      <span>{word}</span>
    </>
  )
  const pill =
    p.canStatus && status !== 'archived' ? (
      <button
        type="button"
        disabled={p.busy}
        aria-label={`Status: ${word}. Change to ${LABEL[next]}`}
        onClick={() => p.onStatus?.(next)}
        className={cx(statusPill, 'cursor-pointer appearance-none enabled:hover:border-jm-border-strong disabled:opacity-jm-disabled', focus)}
      >
        {inner}
      </button>
    ) : (
      <span className={statusPill}>{inner}</span>
    )

  const meta: ReactNode[] = [<span key="a">{p.assigneeName || 'Unassigned'}</span>]
  if (p.dueLabel)
    meta.push(
      p.overdue ? (
        <Tag key="d" tone="action">
          {p.dueLabel}
        </Tag>
      ) : (
        <span key="d">{p.dueLabel}</span>
      ),
    )
  if (p.openFor) meta.push(<span key="o">{p.openFor}</span>)
  if (p.stale) meta.push(<span key="s">Gone quiet</span>)

  const options = [{ value: '', label: 'Unassigned' }, ...(p.people ?? []).map((x) => ({ value: x.id, label: x.name || x.id }))]
  const settled = status === 'done' || status === 'archived'

  return (
    <li className={cx(rowBase, p.className)} data-status={status}>
      <div className="flex min-w-0 items-center gap-jm-2">
        {pill}
        <span className={cx('min-w-0 flex-1 text-jm-body-sm', truncate, settled ? 'text-jm-fg-secondary line-through' : 'text-jm-fg')}>{p.title}</span>
        {p.priority === 'high' && <Tag>High</Tag>}
        {p.byJarvis && <SparkGlyph label="Filed by Jarvis" />}
      </div>
      {p.detail && <p className={cx('m-0 text-jm-body-sm text-jm-fg-secondary', truncate)}>{p.detail}</p>}
      <div className="flex min-h-jm-control-sm min-w-0 items-center gap-jm-2 text-jm-caption text-jm-fg-secondary">
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-y-jm-1">{joinDots(meta)}</span>
        {p.canManage && (
          <span className="ml-auto flex shrink-0 items-center gap-jm-1">
            <Select label="Assignee" value={p.assignee ?? ''} disabled={p.busy} options={options} onChange={(v) => p.onAssign?.(v || null)} />
            <Button variant="ghost" size="sm" disabled={p.busy} onClick={p.onArchive}>
              Archive
            </Button>
          </span>
        )}
      </div>
    </li>
  )
}
