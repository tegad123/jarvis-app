import { useCallback, useEffect, useMemo, useState } from 'react'
import SparkMark from '../components/SparkMark'
import FilterChips from '../components/FilterChips'
import { ErrorState } from '../components/States'
import { Unauthorized, getLeads, createLead, patchLead, archiveLead } from '../lib/api'
import { DOT_BG, WORD_TEXT, type KindSemantic } from '../lib/kinds'
import type { Lead, LeadsResponse } from '../lib/types'

/**
 * Leads — read-only. The sheet stays the write surface in v1.
 *
 * Status chip is driven by 'Status Of Lead' (col H), which has a small, stable
 * vocabulary that maps onto the tokens' kind semantics. 'Follow-up Status'
 * (col G) is free text per agent ('Emailed 6/15', 'Called on 7/9') so it feeds
 * the last-activity line instead of a chip.
 */

type FilterId = 'all' | 'needs_touch' | 'hot' | 'new'

const FILTERS: { id: FilterId; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'needs_touch', label: 'Needs touch' },
  { id: 'hot', label: 'Hot' },
  { id: 'new', label: 'New' },
]

function statusSemantic(status: string): KindSemantic {
  const s = status.toLowerCase()
  if (s === 'responded') return 'success'
  if (s === 'no response') return 'warning'
  if (s === 'not interested') return 'neutral'
  return 'neutral'
}

/**
 * Temperature bars: 3 = Hot, 2 = Warm, 1 = Cold, 0 = blank.
 * Blank is common in this sheet, so an all-empty meter has to look deliberate
 * rather than broken — hence the visible unfilled track.
 */
function TemperatureBars({ rank, temperature }: { rank: number; temperature: string }) {
  const semantic: KindSemantic = rank >= 3 ? 'alert' : rank === 2 ? 'warning' : 'info'
  return (
    <span
      className="flex items-center gap-[3px]"
      title={temperature || 'No temperature set'}
      aria-label={temperature ? `Temperature ${temperature}` : 'No temperature set'}
    >
      {[1, 2, 3].map((i) => (
        <span
          key={i}
          aria-hidden="true"
          className={[
            'block h-3 w-[3px] rounded-full',
            i <= rank
              ? DOT_BG[semantic]
              : 'bg-light-neutral/40 dark:bg-dark-neutral/40',
          ].join(' ')}
        />
      ))}
    </span>
  )
}

const STATUS_CYCLE = ['New', 'No Response', 'Responded', 'Not Interested']
const TEMP_CYCLE = ['', 'Cold', 'Warm', 'Hot']

function LeadRow({
  lead,
  canEdit,
  onPatch,
  onArchive,
  busy,
}: {
  lead: Lead
  canEdit: boolean
  onPatch: (id: string, fields: Record<string, string>) => void
  onArchive: (id: string) => void
  busy: boolean
}) {
  const semantic = statusSemantic(lead.status)
  // Most recent signal we have about this lead, in preference order.
  const activity =
    lead.follow_up || (lead.last_nudge ? `Nudged ${lead.last_nudge}` : '') || 'No activity yet'

  return (
    <li
      // min-w-0 on the GRID ITEM. A grid item defaults to min-width:auto, so a
      // row whose content cannot shrink expands past its own track — the ul
      // measures 350px while the li measures 371px. Same failure as the
      // step-2 filter chips; same fix. The parent already has min-w-0 and that
      // is NOT sufficient: the constraint has to be on the item.
      className="min-w-0 rounded-list border border-light-border bg-light-surface p-card-x shadow-card
                 dark:border-dark-border dark:bg-dark-surface"
    >
      <div className="flex items-center gap-2">
        {/* The temperature bars and status chip were already the design; making
            them CONTROLS is the whole change. Tapping advances to the next
            value — the vocabularies are short and closed, so a cycle beats a
            picker for the common case (nudging a lead warmer after a call).
            Read-only tenants get the identical markup without the handlers, so
            a sheet-backed screen looks exactly as it did. */}
        {canEdit ? (
          <button
            type="button"
            disabled={busy}
            aria-label={`Temperature: ${lead.temperature || 'unset'} — tap to change`}
            onClick={() =>
              onPatch(lead.lead_id, {
                temperature:
                  TEMP_CYCLE[(TEMP_CYCLE.indexOf(lead.temperature) + 1) % TEMP_CYCLE.length],
              })
            }
            className="shrink-0 rounded-btn disabled:opacity-50"
          >
            <TemperatureBars rank={lead.temperature_rank} temperature={lead.temperature} />
          </button>
        ) : (
          <TemperatureBars rank={lead.temperature_rank} temperature={lead.temperature} />
        )}

        <span className="min-w-0 flex-1 truncate text-row-title">{lead.name}</span>

        {lead.created_by === 'jarvis' && (
          <SparkMark size={11} className="shrink-0 text-ink-300" />
        )}

        {lead.status &&
          (canEdit ? (
            <button
              type="button"
              disabled={busy}
              aria-label={`Status: ${lead.status} — tap to change`}
              onClick={() =>
                onPatch(lead.lead_id, {
                  status:
                    STATUS_CYCLE[(STATUS_CYCLE.indexOf(lead.status) + 1) % STATUS_CYCLE.length],
                })
              }
              className="flex shrink-0 items-center gap-1.5 rounded-chip disabled:opacity-50"
            >
              <span
                aria-hidden="true"
                className={`block h-[7px] w-[7px] rounded-full ${DOT_BG[semantic]}`}
              />
              <span className={`font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] ${WORD_TEXT[semantic]}`}>
                {lead.status}
              </span>
            </button>
          ) : (
            <span className="flex shrink-0 items-center gap-1.5">
              <span
                aria-hidden="true"
                className={`block h-[7px] w-[7px] rounded-full ${DOT_BG[semantic]}`}
              />
              <span className={`font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] ${WORD_TEXT[semantic]}`}>
                {lead.status}
              </span>
            </span>
          ))}
      </div>

      {(lead.property || lead.price) && (
        <p className="mt-1 truncate text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          {lead.property}
          {lead.property && lead.price ? ' · ' : ''}
          {lead.price}
        </p>
      )}

      {/* min-w-0 on BOTH the row and the truncating span: without it the
          truncate child reports its full text as min-content, the flex line
          refuses to shrink, and the whole card grows past the gutter. Adding
          the Archive button is what surfaced this — it gave the line something
          that could not shrink to absorb the overflow. */}
      <p className="mt-1.5 flex min-w-0 flex-wrap items-baseline gap-x-2 text-caption text-light-text-muted dark:text-dark-text-muted">
        <span className="min-w-0 truncate">{activity}</span>
        <span>· {lead.agent}</span>
        {lead.date && <span>· {lead.date}</span>}
        {canEdit && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onArchive(lead.lead_id)}
            className="ml-auto shrink-0 rounded-btn text-light-text-muted underline-offset-2
                       hover:underline disabled:opacity-50 dark:text-dark-text-muted"
          >
            Archive
          </button>
        )}
      </p>
    </li>
  )
}

interface Props {
  onUnauthorized: () => void
}

export default function Leads({ onUnauthorized }: Props) {
  const [data, setData] = useState<LeadsResponse | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [filter, setFilter] = useState<FilterId>('all')

  const [busy, setBusy] = useState(false)
  const [composing, setComposing] = useState(false)
  const [newName, setNewName] = useState('')

  // Writes exist only against the app-native store. A sheet-backed tenant gets
  // the identical read-only screen — the controls are absent rather than
  // present-and-failing, because a button that always 409s is worse than none.
  const canEdit = data?.source === 'store'

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

  const onPatch = useCallback(
    (id: string, fields: Record<string, string>) => void mutate(() => patchLead(id, fields)),
    [mutate],
  )
  const onArchive = useCallback(
    (id: string) => void mutate(() => archiveLead(id)),
    [mutate],
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

  return (
    <div className="grid min-w-0 gap-4">
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-screen-title">Leads</h2>
          {/* The subtitle tells the truth about where leads LIVE, which differs
              per tenant during the migration: a sheet-backed tenant still edits
              in the sheet, an app-native one edits here. */}
          <p className="mt-0.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            {!data
              ? 'From your open-house tracker'
              : canEdit
                ? `${data.count} lead${data.count === 1 ? '' : 's'} · tap a status or temperature to change it`
                : `${data.count} from ${data.tabs.length} agent tabs · the sheet stays the place you edit`}
          </p>
        </div>
        {canEdit && (
          <button
            type="button"
            disabled={busy}
            onClick={() => setComposing(true)}
            className="shrink-0 rounded-btn bg-light-accent px-3 py-1.5 text-[12px] font-semibold
                       text-white shadow-accent-btn disabled:opacity-50
                       dark:bg-dark-accent-fill"
          >
            New lead
          </button>
        )}
      </header>

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
          className="glass-featured grid gap-2 rounded-card p-3.5"
        >
          <label className="font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] text-light-text-secondary dark:text-dark-text-secondary">
            New lead
          </label>
          <input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="Name"
            className="w-full rounded-btn border border-light-border bg-light-surface px-3 py-2
                       text-body-s text-light-text-primary outline-none
                       dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-primary"
          />
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={busy || !newName.trim()}
              className="rounded-btn bg-light-accent px-3 py-1.5 text-[12px] font-semibold text-white
                         disabled:opacity-50 dark:bg-dark-accent-fill"
            >
              Add
            </button>
            <button
              type="button"
              onClick={() => { setComposing(false); setNewName('') }}
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
          stale={(data?.leads.length ?? 0) > 0}
          label="Leads unavailable"
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
        <p className="py-8 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
          Loading…
        </p>
      ) : visible.length === 0 && !error ? (
        <p className="rounded-list border border-light-border bg-light-surface p-card-x
                      text-body text-light-text-secondary shadow-card
                      dark:border-dark-border dark:bg-dark-surface dark:text-dark-text-secondary">
          {filter === 'all'
            ? 'No leads in the tracker yet.'
            : `No leads match ${FILTERS.find((f) => f.id === filter)?.label}.`}
        </p>
      ) : (
        <ul className="grid min-w-0 gap-3">
          {visible.map((l, i) => (
            <LeadRow
              key={`${l.lead_id || l.name}-${i}`}
              lead={l}
              canEdit={canEdit}
              busy={busy}
              onPatch={onPatch}
              onArchive={onArchive}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
