import { useCallback, useEffect, useState } from 'react'
import { Button, FilterChips } from '../jm'
import { ErrorLine, Field, Sheet } from '../jm/shared/primitives'
import { Unauthorized, getLead, logLeadTouch } from '../lib/api'
import type { LeadDetailResponse, LeadTouch, LeadTouchKind } from '../lib/types'
import { dueLabel } from '../lib/leadDates'

/**
 * One lead: how to reach them, what has happened, and "Log contact".
 *
 * The next follow-up is left blank on purpose: the server picks it from the
 * cadence (hot tomorrow, warm in two days, then 7, 14, 30). Typing a date
 * overrides it.
 */

const KINDS: { value: LeadTouchKind; label: string }[] = [
  { value: 'call', label: 'Call' },
  { value: 'text', label: 'Text' },
  { value: 'email', label: 'Email' },
  { value: 'met', label: 'Met' },
  { value: 'showing', label: 'Showing' },
  { value: 'note', label: 'Note' },
]

const KIND_WORD: Record<LeadTouch['kind'], string> = {
  call: 'Called',
  text: 'Texted',
  email: 'Emailed',
  met: 'Met',
  showing: 'Showed a property',
  note: 'Note',
  created: 'Added',
  imported: 'Imported from the spreadsheet',
}

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function LeadSheet({
  leadId,
  canEdit,
  onClose,
  onChanged,
  onUnauthorized,
}: {
  leadId: string
  canEdit: boolean
  onClose: () => void
  onChanged: () => void
  onUnauthorized: () => void
}) {
  const [data, setData] = useState<LeadDetailResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [kind, setKind] = useState<LeadTouchKind>('call')
  const [missed, setMissed] = useState(false)
  const [note, setNote] = useState('')
  const [next, setNext] = useState('')

  const load = useCallback(async () => {
    try {
      setData(await getLead(leadId))
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError('Could not open this lead.')
    }
  }, [leadId, onUnauthorized])

  useEffect(() => {
    void load()
  }, [load])

  const save = async () => {
    setBusy(true)
    try {
      await logLeadTouch(leadId, {
        kind,
        note: note.trim() || undefined,
        reached: kind === 'note' ? undefined : !missed,
        next_touch_at: next || undefined,
      })
      setNote('')
      setNext('')
      setMissed(false)
      await load()
      onChanged()
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError('That did not save.')
    } finally {
      setBusy(false)
    }
  }

  const lead = data?.lead
  const due = lead ? dueLabel(lead.next_follow_up, data?.today, lead.status) : null

  return (
    <Sheet title={lead?.name ?? 'Lead'} subtitle={[lead?.property, lead?.agent].filter(Boolean).join(' · ') || undefined} onClose={onClose}>
      {error && <ErrorLine>{error}</ErrorLine>}
      {!data && !error && <p className="m-0 text-jm-body-sm text-jm-fg-secondary">Loading…</p>}
      {lead && (
        <div className="grid gap-jm-4" data-testid="lead-sheet">
          <div className="grid gap-jm-1 text-jm-body-sm">
            {lead.phone && (
              <a className="text-jm-accent-text" href={`tel:${lead.phone}`}>
                {lead.phone}
              </a>
            )}
            {lead.email && (
              <a className="text-jm-accent-text" href={`mailto:${lead.email}`}>
                {lead.email}
              </a>
            )}
            <span className="text-jm-fg-secondary">
              {[lead.status || 'New', lead.temperature, lead.price, lead.source].filter(Boolean).join(' · ')}
            </span>
            {due && <span className={due.now ? 'font-medium text-jm-accent-text' : 'text-jm-fg-secondary'}>{due.text}</span>}
            {lead.notes && <p className="m-0 whitespace-pre-wrap text-jm-fg">{lead.notes}</p>}
          </div>

          {canEdit && (
            <form
              className="grid gap-jm-3 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-4"
              onSubmit={(e) => {
                e.preventDefault()
                if (!busy) void save()
              }}
            >
              <span className="text-jm-label text-jm-fg">Log contact</span>
              <FilterChips
                label="What"
                options={KINDS}
                active={kind}
                hideAll
                onChange={(v) => v && setKind(v as LeadTouchKind)}
              />
              {kind !== 'note' && (
                <label className="flex items-center gap-jm-2 text-jm-body-sm text-jm-fg-secondary">
                  <input type="checkbox" checked={missed} onChange={(e) => setMissed(e.target.checked)} />
                  Didn’t reach them
                </label>
              )}
              <Field value={note} onChange={(e) => setNote(e.target.value)} placeholder="What happened (optional)" aria-label="Note" />
              {kind !== 'note' && (
                <label className="flex flex-wrap items-center gap-jm-2 text-jm-body-sm text-jm-fg-secondary">
                  Next follow-up
                  <Field type="date" value={next} onChange={(e) => setNext(e.target.value)} aria-label="Next follow-up" />
                  <span className="text-jm-caption">Leave blank and Jarvis picks it.</span>
                </label>
              )}
              <div>
                <Button type="submit" variant="primary" disabled={busy}>
                  {kind === 'note' ? 'Save note' : 'Log contact'}
                </Button>
              </div>
            </form>
          )}

          <div className="grid gap-jm-2">
            <span className="text-jm-label text-jm-fg">Timeline</span>
            {data.timeline.length === 0 ? (
              <p className="m-0 text-jm-body-sm text-jm-fg-secondary">Nothing logged yet.</p>
            ) : (
              <ol className="m-0 grid list-none gap-jm-2 p-0" data-testid="lead-timeline">
                {[...data.timeline].reverse().map((t) => (
                  <li key={t.touch_id} className="grid gap-[2px] border-b border-jm-border pb-jm-2 last:border-b-0">
                    <span className="text-jm-body-sm text-jm-fg">
                      {KIND_WORD[t.kind] ?? t.kind}
                      {t.reached === false && ' · no answer'}
                    </span>
                    {t.note && <span className="whitespace-pre-wrap text-jm-body-sm text-jm-fg-secondary">{t.note}</span>}
                    <span className="text-jm-caption text-jm-fg-secondary">
                      {[when(t.at), t.by_name, t.next_touch_at && `next ${t.next_touch_at}`].filter(Boolean).join(' · ')}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      )}
    </Sheet>
  )
}
