import { useCallback, useEffect, useMemo, useState } from 'react'
import Page from '../components/Page'
import LeadSheet from '../components/LeadSheet'
import { type Person, Unauthorized, getLeads, createLead, patchLead, archiveLead } from '../lib/api'
import { Button, EmptyState, ErrorState, Field, FilterChips, LeadRow, List, type LeadRowProps } from '../jm'
import { Select } from '../jm/shared/primitives'
import { dueLabel, isDue } from '../lib/leadDates'
import { canManageTeam } from '../lib/roles'
import type { Lead, LeadsResponse, Role } from '../lib/types'

/**
 * Leads. Sheet-backed tenants are read-only here — the sheet stays the place
 * you edit; app-native tenants edit in place.
 *
 * TODAY IS THE DEFAULT on an app-native tenant: the leads whose follow-up date
 * has come, overdue first. A member sees their own (the API scopes the list);
 * an owner or admin sees everyone's, with each agent's due count on the Who
 * chips.
 *
 * 'Status Of Lead' (col H) has a small, stable vocabulary, so it is the status
 * control. 'Follow-up Status' (col G) is free text per agent ('Emailed 6/15')
 * so it feeds the last-activity line instead.
 */

type FilterId = 'due' | 'all' | 'needs_touch' | 'hot' | 'new'

const FILTERS: { id: FilterId; label: string }[] = [
  { id: 'due', label: 'Today' },
  { id: 'needs_touch', label: 'Needs touch' },
  { id: 'hot', label: 'Hot' },
  { id: 'new', label: 'New' },
]

const TEMPS = ['Hot', 'Warm', 'Cold']

interface Props {
  role: Role
  /** Null on a break-glass operator session. Editing needs a person — see
      auth.require_person; this only REFLECTS that, the API enforces it. */
  personKey: string | null
  people: Person[]
  onUnauthorized: () => void
}

const EMPTY_DRAFT = { name: '', phone: '', email: '', property: '', temperature: '', notes: '', owner: '' }

export default function Leads({ role, personKey, people, onUnauthorized }: Props) {
  const [data, setData] = useState<LeadsResponse | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [filter, setFilter] = useState<FilterId>('due')
  const [agent, setAgent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [composing, setComposing] = useState(false)
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [open, setOpen] = useState<string | null>(null)

  // TWO CONDITIONS, and they fail for different reasons.
  //   SOURCE: writes exist only against the app-native store. A sheet-backed
  //     tenant gets the read-only screen — controls absent rather than
  //     present-and-409ing, because a button that always fails is worse.
  //   PERSON: a break-glass operator has no person key, and a lead edit is a
  //     claim about the business that needs someone to have made it.
  const store = data?.source === 'store'
  const canEdit = store && personKey !== null
  const manager = canManageTeam(role) || role === 'operator'
  const today = data?.today

  const load = useCallback(async () => {
    try {
      const r = await getLeads()
      if (r.ok) {
        setData(r.data)
        setError(null)
      } else {
        // Sheet unreachable: say so. Never an empty table, which would read as
        // "you have no leads".
        setError({ message: r.error.error, detail: r.error.detail })
      }
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError({ message: 'Could not load leads.' })
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
        await load()
      } catch (err) {
        if (err instanceof Unauthorized) onUnauthorized()
        else setError({ message: 'That change did not save.' })
      } finally {
        setBusy(false)
      }
    },
    [load, onUnauthorized],
  )

  const due = (l: Lead) => isDue(l.next_follow_up, today, l.status)
  const active: FilterId = filter === 'due' && !store ? 'all' : filter

  const agents = useMemo(() => {
    const m = new Map<string, { name: string; due: number }>()
    for (const l of data?.leads ?? []) {
      if (!l.tab) continue
      const a = m.get(l.tab) ?? { name: l.agent || l.tab, due: 0 }
      if (isDue(l.next_follow_up, today, l.status)) a.due += 1
      m.set(l.tab, a)
    }
    return m
  }, [data, today])

  const visible = useMemo(() => {
    let all = data?.leads ?? []
    if (agent) all = all.filter((l) => l.tab === agent)
    switch (active) {
      case 'due':
        return all
          .filter((l) => isDue(l.next_follow_up, today, l.status))
          .sort((a, b) => a.next_follow_up.localeCompare(b.next_follow_up) || b.temperature_rank - a.temperature_rank)
      case 'needs_touch':
        return all.filter((l) => l.needs_touch)
      case 'hot':
        return all.filter((l) => l.is_hot)
      case 'new':
        return all.filter((l) => l.is_new)
      default:
        return all
    }
  }, [data, active, agent, today])

  const dueCount = (data?.leads ?? []).filter(due).length

  // The subtitle tells the truth about where leads LIVE, which differs per tenant.
  const subtitle = !data
    ? 'From your open-house tracker'
    : store
      ? `${data.count} lead${data.count === 1 ? '' : 's'} · ${dueCount} to follow up today`
      : `${data.count} from ${data.tabs.length} agent tabs · the sheet stays the place you edit`

  const roster = people.map((p) => ({ value: p.person_key, label: p.name || p.person_key }))
  const set = (k: keyof typeof EMPTY_DRAFT) => (e: { target: { value: string } }) => setDraft((d) => ({ ...d, [k]: e.target.value }))

  return (
    <Page
      title="Leads"
      subtitle={subtitle}
      actions={
        canEdit && (
          <Button variant="primary" size="sm" icon="plus" disabled={busy} onClick={() => setComposing(true)}>
            New lead
          </Button>
        )
      }
    >
      {composing && (
        <form
          data-testid="quick-add"
          onSubmit={(e) => {
            e.preventDefault()
            const name = draft.name.trim()
            if (!name) return
            const d = draft
            setComposing(false)
            setDraft(EMPTY_DRAFT)
            void mutate(() =>
              createLead({
                name,
                phone: d.phone.trim() || undefined,
                email: d.email.trim() || undefined,
                property: d.property.trim() || undefined,
                temperature: d.temperature || undefined,
                notes: d.notes.trim() || undefined,
                source: 'Open house',
                ...(manager && d.owner ? { owner_person: d.owner } : {}),
              }),
            )
          }}
          className="grid gap-jm-3 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-4"
        >
          <Field autoFocus value={draft.name} onChange={set('name')} placeholder="Name" aria-label="Name" />
          <div className="grid gap-jm-2 sm:grid-cols-2">
            <Field value={draft.phone} onChange={set('phone')} placeholder="Phone" aria-label="Phone" inputMode="tel" />
            <Field value={draft.email} onChange={set('email')} placeholder="Email" aria-label="Email" inputMode="email" />
          </div>
          <Field value={draft.property} onChange={set('property')} placeholder="Property they saw" aria-label="Property" />
          <Field value={draft.notes} onChange={set('notes')} placeholder="What they want (optional)" aria-label="Notes" />
          <FilterChips
            label="How hot"
            options={TEMPS}
            active={draft.temperature || null}
            allLabel="Not sure"
            onChange={(v) => setDraft((d) => ({ ...d, temperature: v ?? '' }))}
          />
          {manager && roster.length > 0 && (
            <label className="flex items-center justify-between gap-jm-3 text-jm-body-sm text-jm-fg-secondary">
              Agent
              <Select
                label="Agent"
                value={draft.owner}
                options={[{ value: '', label: 'Me' }, ...roster.filter((r) => r.value !== personKey)]}
                onChange={(v) => setDraft((d) => ({ ...d, owner: v }))}
              />
            </label>
          )}
          <p className="m-0 text-jm-caption text-jm-fg-secondary">
            Jarvis sets the first follow-up: tomorrow if hot, in two days if warm, otherwise in three.
          </p>
          <div className="flex gap-jm-2">
            <Button type="submit" variant="primary" disabled={busy || !draft.name.trim()}>
              Add
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setComposing(false)
                setDraft(EMPTY_DRAFT)
              }}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}

      {error && (
        <ErrorState
          label="Leads unavailable"
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={(data?.leads.length ?? 0) > 0}
        />
      )}

      <div className="grid gap-jm-2">
        <FilterChips
          label="Show"
          options={FILTERS.filter((f) => store || f.id !== 'due').map((f) => ({
            value: f.id,
            label: f.id === 'due' ? `Today (${dueCount})` : f.label,
          }))}
          active={active === 'all' ? null : active}
          onChange={(v) => setFilter((v as FilterId) ?? 'all')}
        />
        {manager && store && agents.size > 1 && (
          <FilterChips
            label="Who"
            allLabel="All agents"
            options={[...agents.entries()].map(([key, a]) => ({ value: key, label: `${a.name} · ${a.due}` }))}
            active={agent}
            onChange={setAgent}
          />
        )}
      </div>

      {data === null && !error ? (
        <p className="m-0 py-jm-8 text-center text-jm-body-sm text-jm-fg-secondary">Loading…</p>
      ) : visible.length === 0 && !error ? (
        <EmptyState icon="leads">
          {active === 'due'
            ? 'Nobody to follow up with today.'
            : active === 'all'
              ? 'No leads in the tracker yet.'
              : `No leads match ${FILTERS.find((f) => f.id === active)?.label}.`}
        </EmptyState>
      ) : (
        <List>
          {visible.map((l, i) => {
            const d = store ? dueLabel(l.next_follow_up, today, l.status) : null
            return (
              <LeadRow
                key={`${l.lead_id || l.name}-${i}`}
                name={l.name}
                property={l.property || undefined}
                price={l.price || undefined}
                temperature={l.temperature as LeadRowProps['temperature']}
                temperatureRank={l.temperature_rank}
                status={l.status as LeadRowProps['status']}
                // Most recent signal we have about this lead, in preference order.
                activity={l.follow_up || (l.last_nudge ? `Nudged ${l.last_nudge}` : '') || undefined}
                agent={l.agent || undefined}
                date={l.date || undefined}
                due={d?.text}
                dueNow={d?.now}
                byJarvis={l.created_by === 'jarvis'}
                canEdit={canEdit}
                onOpen={store && l.lead_id ? () => setOpen(l.lead_id) : undefined}
                onChange={(fields) => void mutate(() => patchLead(l.lead_id, fields as Record<string, string>))}
                onArchive={() => void mutate(() => archiveLead(l.lead_id))}
                busy={busy}
              />
            )
          })}
        </List>
      )}

      {open && (
        <LeadSheet
          leadId={open}
          canEdit={canEdit}
          onClose={() => setOpen(null)}
          onChanged={() => void load()}
          onUnauthorized={onUnauthorized}
        />
      )}
    </Page>
  )
}
