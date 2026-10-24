import { useCallback, useEffect, useMemo, useState } from 'react'
import Page from '../components/Page'
import { ApiError, type Person, Unauthorized, archiveTask, assignTask, createTask, getTasks, setTaskStatus } from '../lib/api'
import { Button, EmptyState, ErrorState, Field, FilterChips, List, TaskRow, type TaskStatus } from '../jm'
import { Select } from '../jm/shared/primitives'
import { durationOpen } from '../lib/time'
import type { Role, TasksResponse } from '../lib/types'

/**
 * Tasks — the shared list.
 *
 * THE WHOLE TEAM SEES THE WHOLE LIST. That is deliberately not the Leads rule,
 * where a member sees only their own: a shared task list you can only half see
 * is not shared. What stays scoped is the FEED.
 *
 * WHAT EACH ROLE MAY DO IS ENFORCED BY THE API, NOT HERE. The `can*` flags
 * below only decide whether to render a control; every one of them has a
 * server-side twin, and a crafted request without the button gets the same 403.
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

interface Props {
  role: Role
  /** Null on a break-glass operator session. Acting on the tenant's data needs
      a person — see auth.require_person; this only REFLECTS that. */
  personKey: string | null
  people: Person[]
  onUnauthorized: () => void
}

export default function Tasks({ role, personKey, people, onUnauthorized }: Props) {
  const [data, setData] = useState<TasksResponse | null>(null)
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
        // THE SERVER'S SENTENCE, NOT OURS: it knows which rule refused this.
        else setError({ message: err instanceof ApiError ? err.message : 'That change did not save.' })
      } finally {
        setBusy(false)
      }
    },
    [load, onUnauthorized],
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

  const roster = people.map((p) => ({ id: p.person_key, name: p.name || p.person_key }))

  return (
    <Page
      title="Tasks"
      subtitle={subtitle}
      actions={
        canWrite && (
          <Button variant="primary" size="sm" icon="plus" disabled={busy} onClick={() => setComposing(true)}>
            New task
          </Button>
        )
      }
    >
      {composing && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            const title = newTitle.trim()
            if (!title) return
            setComposing(false)
            const input = { title, assignee: newAssignee || null, priority: newPriority, deadline: newDeadline || null }
            setNewTitle('')
            setNewAssignee('')
            setNewPriority('medium')
            setNewDeadline('')
            void mutate(() => createTask(input))
          }}
          className="grid gap-jm-3 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-4"
        >
          <Field autoFocus value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder="What needs doing" aria-label="New task" />
          <div className="flex min-w-0 flex-wrap items-center gap-jm-2">
            <Select
              label="Assignee"
              value={newAssignee}
              options={[{ value: '', label: 'Unassigned' }, ...roster.map((p) => ({ value: p.id, label: p.name }))]}
              onChange={setNewAssignee}
            />
            <Select
              label="Priority"
              value={newPriority}
              options={['high', 'medium', 'low'].map((p) => ({ value: p, label: PRIORITY_LABEL[p] }))}
              onChange={setNewPriority}
            />
            <Field type="date" aria-label="Deadline" value={newDeadline} onChange={(e) => setNewDeadline(e.target.value)} className="w-auto" />
          </div>
          <div className="flex gap-jm-2">
            <Button type="submit" variant="primary" disabled={busy || !newTitle.trim()}>
              Add
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setComposing(false)
                setNewTitle('')
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {error && (
        <ErrorState
          label="Tasks unavailable"
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={(data?.tasks.length ?? 0) > 0}
        />
      )}

      {data?.source === 'store' && (
        <div className="grid gap-jm-2">
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
            <FilterChips label="Who" options={[...assignees.keys()]} active={fAssignee} onChange={setFAssignee} format={(k) => assignees.get(k) ?? k} />
          )}
          {(counts?.stale ?? 0) > 0 && (
            <FilterChips label="Quiet" options={['stale']} active={fQuiet} onChange={setFQuiet} format={() => 'Gone quiet'} />
          )}
        </div>
      )}

      {data === null && !error ? (
        <p className="m-0 py-jm-8 text-center text-jm-body-sm text-jm-fg-secondary">Loading…</p>
      ) : data && data.source !== 'store' ? (
        <EmptyState icon="tasks">{data.notice ?? 'Tasks for this tenant live in the assistant’s own list.'}</EmptyState>
      ) : visible.length === 0 && !error ? (
        <EmptyState icon="tasks">
          {fStatus || fPriority || fAssignee || fQuiet ? 'No tasks match those filters.' : 'Nothing on the list yet.'}
        </EmptyState>
      ) : (
        <List>
          {visible.map((t) => (
            <TaskRow
              key={t.task_id}
              title={t.title}
              // Absent on an operator session because the SERVER withheld it.
              detail={t.detail}
              status={t.status as TaskStatus}
              priority={t.priority as 'high' | 'medium' | 'low'}
              overdue={t.is_overdue}
              stale={t.stale}
              dueLabel={t.due_label ?? undefined}
              openFor={durationOpen(t.days_open)}
              assigneeName={t.assignee_name ?? undefined}
              byJarvis={t.created_by === 'jarvis'}
              canStatus={!!canWrite && (!!canManage || (!!t.assignee && t.assignee === personKey))}
              onStatus={(next) => void mutate(() => setTaskStatus(t.task_id, next))}
              canManage={!!canManage}
              people={roster}
              assignee={t.assignee}
              onAssign={(a) => void mutate(() => assignTask(t.task_id, a))}
              onArchive={() => void mutate(() => archiveTask(t.task_id))}
              busy={busy}
            />
          ))}
        </List>
      )}
    </Page>
  )
}
