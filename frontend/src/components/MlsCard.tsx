import { useEffect, useState } from 'react'
import { Unauthorized, getMlsRun } from '../lib/api'
import type { MlsListing, MlsRun } from '../lib/types'

/**
 * An MLS search's results, drawn from the stored run (GET /api/mls/runs/{id}),
 * never from the model's words. Only the person who ran it gets the run; for
 * anyone else the server answers 404 and nothing is drawn. Plain text only:
 * no listing links, no agent contact columns (the server never reads them).
 */

const POLL_MS = 4_000

const STATUS: Record<string, string> = { active: 'Active', pending: 'Pending', sold: 'Sold' }

const STOPPED: Record<string, string> = {
  needs_person: 'Matrix needs you before Jarvis can search.',
  blocked: 'Matrix refused the search for now.',
  unreadable: 'Jarvis could not read the Matrix page.',
  ambiguous: 'Matrix matched more than one place.',
  failed: 'The search did not finish.',
}

const money = (n?: number | null) => (n == null ? '—' : `$${n.toLocaleString('en-US')}`)
const num = (n?: number | null) => (n == null ? '—' : n.toLocaleString('en-US'))

function priceOf(l: MlsListing) {
  return l.status === 'sold' ? l.sold_price : l.list_price
}

function dateOf(l: MlsListing) {
  return (l.status === 'sold' ? l.sold_date : l.status === 'pending' ? l.pending_date : l.list_date) ?? '—'
}

export function MlsResults({ run }: { run: MlsRun | null | 'loading' }) {
  const frame = 'flex flex-col gap-jm-2 rounded-jm-lg border border-jm-border bg-jm-panel p-jm-3 text-jm-body-sm text-jm-fg'
  if (run === 'loading') {
    return <div data-testid="mls-card" data-state="loading" className={frame}>Loading the search results…</div>
  }
  if (run === null) return null
  if (run.state === 'running') {
    return <div data-testid="mls-card" data-state="running" className={frame}>Searching Matrix…</div>
  }
  if (run.expired) {
    return (
      <div data-testid="mls-card" data-state="expired" className={frame}>
        These results have expired. Ask again to search Matrix now.
      </div>
    )
  }
  if (run.state !== 'done') {
    return (
      <div data-testid="mls-card" data-state={run.state} className={frame}>
        <div>{STOPPED[run.state] ?? STOPPED.failed}</div>
        {run.detail && <div className="text-jm-fg-secondary">{run.detail}</div>}
        {run.choices && run.choices.length > 0 && (
          <ul className="list-disc pl-jm-4 text-jm-fg-secondary">
            {run.choices.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        )}
      </div>
    )
  }
  const rows = run.listings ?? []
  const s = run.summary
  return (
    <div data-testid="mls-card" data-state="done" className={frame}>
      <div className="text-jm-heading">
        {run.count ?? rows.length} listing{(run.count ?? rows.length) === 1 ? '' : 's'} from Matrix
      </div>
      {s && (
        <div className="flex flex-col gap-jm-1 text-jm-fg-secondary">
          <div>{s.note}</div>
          {s.median_price != null && (
            <div>
              Median {money(s.median_price)} (middle half {money(s.price_low)}–{money(s.price_high)})
              {s.median_price_per_sqft != null && `, ${money(s.median_price_per_sqft)}/sq ft`}
              {s.median_days_on_market != null && `, ${s.median_days_on_market} days on market`}
            </div>
          )}
          {s.suggested_low != null && s.suggested_high != null && (
            <div data-testid="mls-suggested">
              Suggested range {money(s.suggested_low)}–{money(s.suggested_high)}
            </div>
          )}
        </div>
      )}
      {rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-jm-caption">
            <thead className="text-jm-fg-secondary">
              <tr>
                <th className="pr-jm-2 font-normal">Address</th>
                <th className="pr-jm-2 font-normal">Status</th>
                <th className="pr-jm-2 font-normal">Price</th>
                <th className="pr-jm-2 font-normal">Beds</th>
                <th className="pr-jm-2 font-normal">Baths</th>
                <th className="pr-jm-2 font-normal">Sq ft</th>
                <th className="pr-jm-2 font-normal">$/sq ft</th>
                <th className="pr-jm-2 font-normal">Date</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((l, i) => (
                <tr key={`${l.mls_number ?? ''}-${l.address}-${i}`} data-testid="mls-row" className="border-t border-jm-border">
                  <td className="pr-jm-2">{l.address}</td>
                  <td className="pr-jm-2">{STATUS[l.status ?? ''] ?? '—'}</td>
                  <td className="pr-jm-2">{money(priceOf(l))}</td>
                  <td className="pr-jm-2">{num(l.beds)}</td>
                  <td className="pr-jm-2">{num(l.baths)}</td>
                  <td className="pr-jm-2">{num(l.sqft)}</td>
                  <td className="pr-jm-2">{money(l.price_per_sqft)}</td>
                  <td className="pr-jm-2">{dateOf(l)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {(run.truncated || (run.unverified ?? 0) > 0) && (
        <div className="text-jm-caption text-jm-fg-secondary">
          {run.truncated && 'More results were on Matrix than were read. '}
          {(run.unverified ?? 0) > 0 && `${run.unverified} row${run.unverified === 1 ? '' : 's'} could not be checked against every filter.`}
        </div>
      )}
    </div>
  )
}

export default function MlsCard({ runId, onUnauthorized }: { runId: number; onUnauthorized?: () => void }) {
  const [run, setRun] = useState<MlsRun | null | 'loading'>('loading')

  useEffect(() => {
    let alive = true
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const r = await getMlsRun(runId)
        if (!alive) return
        setRun(r)
        if (r && r.state === 'running') timer = setTimeout(() => void load(), POLL_MS)
      } catch (err) {
        if (!alive) return
        if (err instanceof Unauthorized) onUnauthorized?.()
        setRun(null)
      }
    }
    void load()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [runId, onUnauthorized])

  return <MlsResults run={run} />
}
