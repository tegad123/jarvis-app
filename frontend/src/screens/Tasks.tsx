import { useCallback, useEffect, useMemo, useState } from 'react'
import SparkMark from '../components/SparkMark'
import FilterChips from '../components/FilterChips'
import { ErrorState } from '../components/States'
import {
  ApiError,
  type Person,
  Unauthorized,
  archiveTask,
  assignTask,
  createTask,
  getPeople,
  getTasks,
  setTaskStatus,
} from '../lib/api'
import { CHIP, DOT_BG, type KindSemantic } from '../lib/kinds'
import { durationOpen } from '../lib/time'
import type { Role, Task, TasksResponse } from '../lib/types'

/**
 * Tasks — the shared list.
 *
 * THE WHOLE TEAM SEES THE WHOLE LIST. That is deliberately not the Leads rule,
 * where a member sees only their own: a shared task list you can only half see
 * is not shared, and the product's sentence is "one shared task list that you
 * and your team can see". What stays scoped is the FEED, where an event still
 * belongs to whoever the task is for.
 *
 * WHAT EACH ROLE MAY DO IS ENFORCED BY THE API, NOT HERE. The `can*` flags
 * below only decide whether to render a control; every one of them has a
 * server-side twin, and a crafted request without the button gets the same
 * 403. Hiding a control is a courtesy — it spares someone a refusal they
 * cannot act on — never the control itself.
 */

type StatusFilter = 'open' | 'in_progress' | 'done'

const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  in_progress: 'In progress',
  done: 'Done',
}
const PRIORITY_LABEL: Record<string, string> = {
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

// Short, closed vocabulary, so a cycle beats a picker for the common case
// (ticking your own work along). Archive is deliberately NOT in the cycle:
// it is a decision, not a step, and it lives behind its own control.
const STATUS_CYCLE = ['open', 'in_progress', 'done']

/**
 * Which of the five token semantics a row reads as, most-urgent first.
 *
 * There is no 'overdue' colour in the tokens — the semantics block names
 * `alert` as "deadlines, failures", so overdue is alert. Done is neutral (the
 * settled dot), and a quiet task borrows the tokens' own phrase, "Going
 * quiet", rather than inventing one.
 */
function taskTone(t: Task): { semantic: KindSemantic; word: string } {
  if (t.status === 'done') return { semantic: 'neutral', word: 'Done' }
  if (t.status === 'archived') return { semantic: 'neutral', word: 'Archived' }
  if (t.is_overdue) return { semantic: 'alert', word: 'Overdue' }
  if (t.stale) return { semantic: 'alert', word: 'Going quiet' }
  if (t.priority === 'high') return { semantic: 'warning', word: 'High' }
  if (t.status === 'in_progress') return { semantic: 'info', word: 'In progress' }
  return { semantic: 'info', word: 'Open' }
}

function TaskRow({
  task,
  people,
  canManage,
  canStatus,
  onStatus,
  onAssign,
  onArchive,
  busy,
}: {
  task: Task
  people: Person[]
  canManage: boolean
  canStatus: boolean
  onStatus: (id: string, status: string) => void
  onAssign: (id: string, assignee: string | null) => void
  onArchive: (id: string) => void
  busy: boolean
}) {
  const tone = taskTone(task)
  const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(task.status) + 1) % STATUS_CYCLE.length]

  const chip = (
    <span className={`${CHIP[tone.semantic]} chip-state shrink-0`}>
      <span
        aria-hidden="true"
        className={`block h-[6px] w-[6px] shrink-0 rounded-full ${DOT_BG[tone.semantic]}`}
      />
      {tone.word}
    </span>
  )

  return (
    <li
      // min-w-0 on the GRID ITEM itself. A grid item defaults to
      // min-width:auto, so a row whose content cannot shrink expands past its
      // own track and drags the whole column out with it. The parent's
      // min-w-0 is NOT sufficient — same lesson as the leads list.
      className="min-w-0 rounded-list border border-light-border bg-light-surface p-card-x shadow-lift
                 dark:border-dark-border dark:bg-dark-surface dark:shadow-lift-dark"
    >
      <div className="flex items-center gap-2">
        {canStatus ? (
          <button
            type="button"
            disabled={busy}
            aria-label={`Status: ${tone.word} — tap to change`}
            onClick={() => onStatus(task.task_id, next)}
            className="shrink-0 rounded-chip disabled:opacity-50"
          >
            {chip}
          </button>
        ) : (
          chip
        )}

        <span
          className={[
            'min-w-0 flex-1 truncate text-row-title',
            task.status === 'done'
              ? 'text-light-text-muted line-through dark:text-dark-text-muted'
              : '',
          ].join(' ')}
        >
          {task.title}
        </span>

        {/* The assistant filed this one. Same signal the leads list uses. */}
        {task.created_by === 'jarvis' && (
          <SparkMark size={11} className="shrink-0 text-light-accent dark:text-ink-300" />
        )}
      </div>

      {/* The body. Absent on an operator session because the SERVER withheld
          it, which is a different thing from nobody having written one — the
          screen says which, in the subtitle, rather than showing a blank. */}
      {task.detail && (
        <p className="mt-1 min-w-0 truncate text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          {task.detail}
        </p>
      )}

      <p className="mt-1.5 flex min-w-0 flex-wrap items-baseline gap-x-2 text-caption text-light-text-muted dark:text-dark-text-muted">
        <span className="min-w-0 truncate">{task.assignee_name || 'Unassigned'}</span>
        {task.due_label && (
          <span className={task.is_overdue ? 'text-light-alert dark:text-dark-alert' : ''}>
            · {task.due_label}
          </span>
        )}
        <span>· {durationOpen(task.days_open)}</span>

        {canManage && (
          <span className="ml-auto flex shrink-0 items-center gap-2">
            <select
              aria-label="Assignee"
              disabled={busy}
              value={task.assignee ?? ''}
              onChange={(e) => onAssign(task.task_id, e.target.value || null)}
              className="rounded-btn border border-light-border bg-light-surface px-1.5 py-0.5
                         text-caption text-light-text-secondary outline-none disabled:opacity-50
                         dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary"
            >
              <option value="">Unassigned</option>
              {people.map((p) => (
                <option key={p.person_key} value={p.person_key}>
                  {p.name || p.person_key}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={busy}
              onClick={() => onArchive(task.task_id)}
              className="text-caption text-light-text-muted underline-offset-2 hover:underline
                         disabled:opacity-50 dark:text-dark-text-muted"
            >
              Archive
            </button>
          </span>
        )}
      </p>
    </li>
  )
}

interface Props {
  role: Role
  /** Null on a break-glass operator session. Acting on the tenant's data needs
      a person — see auth.require_person; this only REFLECTS that. */
  personKey: string | null
  onUnauthorized: () => void
}

export default function Tasks({ role, personKey, onUnauthorized }: Props) {
  const [data, setData] = useState<TasksResponse | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [composing, setComposing] = useState(false)
  const [fStatus, setFStatus] = useState<StatusFilter | null>(null)
  const [fPriority, setFPriority] = useState<string | null>(null)
  const [fAssignee, setFAssignee] = useState<string | null>(null)
  const [fQuiet, setFQuiet] = useState<string | null>(null)

  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState('')
  const [newPriority, setNewPriority] = useState('medium')
  const [newDeadline, setNewDeadline] = useState('')

  const canWrite = data?.source === 'store' && personKey !== null
  const canManage = canWrite && (role === 'owner' || role === 'admin')

  const load = useCallback(async () => {
    try {
      const d = await getTasks()
      setData(d)
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError({ message: err instanceof ApiError ? err.message : 'Could not load tasks.' })
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
    void getPeople().then(setPeople)
  }, [load])

  const mutate = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await fn()
        setError(null)
        await load()
      } catch (err) {
        if (err instanceof Unauthorized) onUnauthorized()
        // THE SERVER'S SENTENCE, NOT OURS. It knows which rule refused this
        // and has already written a better line than a generic failure — the
        // leads screen throws that away and tells everyone "that change did
        // not save", which explains nothing to someone who simply lacks a
        // permission.
        else setError({ message: err instanceof ApiError ? err.message : 'That change did not save.' })
      } finally {
        setBusy(false)
      }
    },
    [load, onUnauthorized],
  )

  const onStatus = useCallback(
    (id: string, status: string) => void mutate(() => setTaskStatus(id, status)),
    [mutate],
  )
  const onAssign = useCallback(
    (id: string, assignee: string | null) => void mutate(() => assignTask(id, assignee)),
    [mutate],
  )
  const onArchive = useCallback(
    (id: string) => void mutate(() => archiveTask(id)),
    [mutate],
  )

  const assignees = useMemo(() => {
    const keys = new Map<string, string>()
    for (const t of data?.tasks ?? []) {
      if (t.assignee) keys.set(t.assignee, t.assignee_name || t.assignee)
    }
    return keys
  }, [data])

  const visible = useMemo(() => {
    let all = data?.tasks ?? []
    if (fStatus) all = all.filter((t) => t.status === fStatus)
    if (fPriority) all = all.filter((t) => t.priority === fPriority)
    if (fAssignee) all = all.filter((t) => t.assignee === fAssignee)
    if (fQuiet) all = all.filter((t) => t.stale)
    return all
  }, [data, fStatus, fPriority, fAssignee, fQuiet])

  const counts = data?.counts
  const subtitle = !data
    ? 'What the team is working on'
    : data.source !== 'store'
      ? data.notice ?? 'Tasks for this tenant live in the assistant’s own list.'
      : !data.bodies_visible
        ? 'Structure only — task details are hidden for operator access.'
        : `${(counts?.open ?? 0) + (counts?.in_progress ?? 0)} open · ${counts?.stale ?? 0} gone quiet`

  return (
    <div className="grid min-w-0 gap-4">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-screen-title">Tasks</h2>
          <p className="mt-0.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            {subtitle}
          </p>
        </div>
        {canWrite && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setComposing(true)}
            className="shrink-0 rounded-btn bg-light-accent px-3 py-1.5 text-[12px] font-semibold
                       text-white shadow-accent-btn disabled:opacity-50
                       dark:bg-dark-accent-fill"
          >
            New task
          </button>
        )}
      </header>

      {composing && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const title = newTitle.trim()
            if (!title) return
            setComposing(false)
            const input = {
              title,
              assignee: newAssignee || null,
              priority: newPriority,
              deadline: newDeadline || null,
            }
            setNewTitle(''); setNewAssignee(''); setNewPriority('medium'); setNewDeadline('')
            void mutate(() => createTask(input))
          }}
          className="glass-featured grid gap-2 rounded-card p-3.5"
        >
          <label className="font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] text-light-text-secondary dark:text-dark-text-secondary">
            New task
          </label>
          <input
            autoFocus
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="What needs doing"
            className="w-full rounded-btn border border-light-border bg-light-surface px-3 py-2
                       text-body-s text-light-text-primary outline-none
                       dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-primary"
          />
          <div className="flex min-w-0 flex-wrap gap-2">
            <select
              aria-label="Assignee"
              value={newAssignee}
              onChange={(e) => setNewAssignee(e.target.value)}
              className="min-w-0 flex-1 rounded-btn border border-light-border bg-light-surface px-2 py-1.5
                         text-body-s text-light-text-secondary outline-none
                         dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary"
            >
              <option value="">Unassigned</option>
              {people.map((p) => (
                <option key={p.person_key} value={p.person_key}>
                  {p.name || p.person_key}
                </option>
              ))}
            </select>
            <select
              aria-label="Priority"
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="min-w-0 rounded-btn border border-light-border bg-light-surface px-2 py-1.5
                         text-body-s text-light-text-secondary outline-none
                         dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary"
            >
              {['high', 'medium', 'low'].map((p) => (
                <option key={p} value={p}>{PRIORITY_LABEL[p]}</option>
              ))}
            </select>
            <input
              type="date"
              aria-label="Deadline"
              value={newDeadline}
              onChange={(e) => setNewDeadline(e.target.value)}
              className="min-w-0 rounded-btn border border-light-border bg-light-surface px-2 py-1.5
                         text-body-s text-light-text-secondary outline-none
                         dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary"
            />
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy || !newTitle.trim()}
              className="rounded-btn bg-light-accent px-3 py-1.5 text-[12px] font-semibold text-white
                         disabled:opacity-50 dark:bg-dark-accent-fill"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => { setComposing(false); setNewTitle('') }}
              className="rounded-btn px-3 py-1.5 text-[12px] font-medium text-light-text-secondary dark:text-dark-text-secondary"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <ErrorState
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={(data?.tasks.length ?? 0) > 0}
          label="Tasks unavailable"
        />
      )}

      {data?.source === 'store' && (
        <>
          <FilterChips
            label="Show"
            options={['open', 'in_progress', 'done']}
            active={fStatus}
            onChange={(v) => setFStatus(v as StatusFilter | null)}
            format={(id) => STATUS_LABEL[id] ?? id}
          />
          <FilterChips
            label="Level"
            options={['high', 'medium', 'low']}
            active={fPriority}
            onChange={setFPriority}
            format={(id) => PRIORITY_LABEL[id] ?? id}
          />
          {assignees.size > 0 && (
            <FilterChips
              label="Who"
              options={[...assignees.keys()]}
              active={fAssignee}
              onChange={setFAssignee}
              format={(k) => assignees.get(k) ?? k}
            />
          )}
          {(counts?.stale ?? 0) > 0 && (
            <FilterChips
              label="Quiet"
              options={['stale']}
              active={fQuiet}
              onChange={setFQuiet}
              format={() => 'Gone quiet'}
            />
          )}
        </>
      )}

      {data === null && !error ? (
        <p className="py-8 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
          Loading…
        </p>
      ) : data && data.source !== 'store' ? (
        <p className="rounded-list border border-light-border bg-light-surface p-card-x
                      text-body text-light-text-secondary shadow-card
                      dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary">
          {data.notice ?? 'Tasks for this tenant live in the assistant’s own list.'}
        </p>
      ) : visible.length === 0 && !error ? (
        <p className="rounded-list border border-light-border bg-light-surface p-card-x
                      text-body text-light-text-secondary shadow-card
                      dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary">
          {fStatus || fPriority || fAssignee || fQuiet
            ? 'No tasks match those filters.'
            : 'Nothing on the list yet.'}
        </p>
      ) : (
        <ul className="grid min-w-0 gap-3">
          {visible.map((t) => (
            <TaskRow
              key={t.task_id}
              task={t}
              people={people}
              canManage={!!canManage}
              canStatus={
                !!canWrite && (!!canManage || (!!t.assignee && t.assignee === personKey))
              }
              busy={busy}
              onStatus={onStatus}
              onAssign={onAssign}
              onArchive={onArchive}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
