import { useCallback, useEffect, useMemo, useState } from 'react'
import Page from '../components/Page'
import { Unauthorized, getLeads, createLead, patchLead, archiveLead } from '../lib/api'
import { Button, EmptyState, ErrorState, Field, FilterChips, LeadRow, List, type LeadRowProps } from '../jm'
import type { LeadsResponse } from '../lib/types'

/**
 * Leads. Sheet-backed tenants are read-only here — the sheet stays the place
 * you edit; app-native tenants edit in place.
 *
 * 'Status Of Lead' (col H) has a small, stable vocabulary, so it is the status
 * control. 'Follow-up Status' (col G) is free text per agent ('Emailed 6/15')
 * so it feeds the last-activity line instead.
 */

type FilterId = 'all' | 'needs_touch' | 'hot' | 'new'

const FILTERS: { id: FilterId; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'needs_touch', label: 'Needs touch' },
  { id: 'hot', label: 'Hot' },
  { id: 'new', label: 'New' },
]

interface Props {
  /** Null on a break-glass operator session. Editing needs a person — see
      auth.require_person; this only REFLECTS that, the API enforces it. */
  personKey: string | null
  onUnauthorized: () => void
}

export default function Leads({ personKey, onUnauthorized }: Props) {
  const [data, setData] = useState<LeadsResponse | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [filter, setFilter] = useState<FilterId>('all')
  const [busy, setBusy] = useState(false)
  const [composing, setComposing] = useState(false)
  const [newName, setNewName] = useState('')

  // TWO CONDITIONS, and they fail for different reasons.
  //   SOURCE: writes exist only against the app-native store. A sheet-backed
  //     tenant gets the read-only screen — controls absent rather than
  //     present-and-409ing, because a button that always fails is worse.
  //   PERSON: a break-glass operator has no person key, and a lead edit is a
  //     claim about the business that needs someone to have made it.
  const canEdit = data?.source === 'store' && personKey !== null

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

  const visible = useMemo(() => {
    const all = data?.leads ?? []
    switch (filter) {
      case 'needs_touch':
        return all.filter((l) => l.needs_touch)
      case 'hot':
        return all.filter((l) => l.is_hot)
      case 'new':
        return all.filter((l) => l.is_new)
      default:
        return all
    }
  }, [data, filter])

  // The subtitle tells the truth about where leads LIVE, which differs per tenant.
  const subtitle = !data
    ? 'From your open-house tracker'
    : canEdit
      ? `${data.count} lead${data.count === 1 ? '' : 's'} · tap a status or temperature to change it`
      : `${data.count} from ${data.tabs.length} agent tabs · the sheet stays the place you edit`

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
          onSubmit={(e) => {
            e.preventDefault()
            const name = newName.trim()
            if (!name) return
            setComposing(false)
            setNewName('')
            void mutate(() => createLead({ name, source: 'Added by hand' }))
          }}
          className="grid gap-jm-3 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-4"
        >
          <Field autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Name" aria-label="New lead" />
          <div className="flex gap-jm-2">
            <Button type="submit" variant="primary" disabled={busy || !newName.trim()}>
              Add
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setComposing(false)
                setNewName('')
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

      <FilterChips
        label="Show"
        options={FILTERS.filter((f) => f.id !== 'all').map((f) => f.id)}
        active={filter === 'all' ? null : filter}
        onChange={(v) => setFilter((v as FilterId) ?? 'all')}
        format={(id) => FILTERS.find((f) => f.id === id)?.label ?? id}
      />

      {data === null && !error ? (
        <p className="m-0 py-jm-8 text-center text-jm-body-sm text-jm-fg-secondary">Loading…</p>
      ) : visible.length === 0 && !error ? (
        <EmptyState icon="leads">
          {filter === 'all' ? 'No leads in the tracker yet.' : `No leads match ${FILTERS.find((f) => f.id === filter)?.label}.`}
        </EmptyState>
      ) : (
        <List>
          {visible.map((l, i) => (
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
              byJarvis={l.created_by === 'jarvis'}
              canEdit={canEdit}
              onChange={(fields) => void mutate(() => patchLead(l.lead_id, fields as Record<string, string>))}
              onArchive={() => void mutate(() => archiveLead(l.lead_id))}
              busy={busy}
            />
          ))}
        </List>
      )}
    </Page>
  )
}
