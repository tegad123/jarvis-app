import { useCallback, useEffect, useState } from 'react'
import { Unauthorized, getOutbox, type OutboxRow } from '../lib/api'
import { absoluteTime, relativeTime } from '../lib/time'

/**
 * The capture outbox, read-only, for the operator session. Every row is a
 * mail the outbound boundary CAUGHT instead of sending — on a test tenant
 * this is the only evidence a send happened, so the operator watching a
 * tester's run needs to see it without a shell. Same opaque-card language as
 * the feed rows; the body is behind the same chevron affordance.
 */
interface Props {
  onUnauthorized: () => void
  limit?: number
}

const POLL_MS = 15_000

export default function OutboxList({ onUnauthorized, limit = 20 }: Props) {
  const [rows, setRows] = useState<OutboxRow[] | null>(null)
  const [active, setActive] = useState<boolean | null>(null)
  const [open, setOpen] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await getOutbox(limit)
      setRows([...r.rows].reverse())   // newest first on screen; the API is file order
      setActive(r.active)
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) { onUnauthorized(); return }
      setError("Couldn't read the outbox.")
    }
  }, [limit, onUnauthorized])

  useEffect(() => {
    void load()
    const id = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(id)
  }, [load])

  return (
    <section aria-labelledby="outbox-heading" className="mt-2">
      <header className="mb-3 flex items-baseline gap-3">
        <h2 id="outbox-heading" className="text-row-title">Outbox</h2>
        <span className="font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] text-light-text-muted dark:text-dark-text-muted">
          {active === false ? 'boundary off — nothing is captured here' : 'captured, not sent'}
          {rows ? ` · ${rows.length}` : ''}
        </span>
      </header>

      {error && (
        <p className="text-body-s text-light-alert dark:text-dark-alert">{error}</p>
      )}
      {rows && rows.length === 0 && !error && (
        <p className="text-body-s text-light-text-muted dark:text-dark-text-muted">Nothing captured yet.</p>
      )}

      {rows && rows.length > 0 && (
        <ol className="grid gap-3">
          {rows.map((r, i) => {
            const isOpen = open === i
            return (
              <li
                key={`${r.ts}-${i}`}
                className="hover-lift min-w-0 rounded-card border border-light-border bg-light-surface
                           px-6 py-4 shadow-lift lg:hover:shadow-glass
                           dark:border-dark-border dark:bg-dark-surface dark:shadow-lift-dark dark:lg:hover:shadow-glass-dark"
              >
                <div className="flex items-center gap-3">
                  <span className="chip chip-info">Captured</span>
                  <span className="min-w-0 truncate font-mono text-[10px] uppercase tracking-[0.06em] text-light-text-muted dark:text-dark-text-muted">
                    to {r.to.join(', ')}{r.cc.length ? ` · cc ${r.cc.join(', ')}` : ''}
                  </span>
                  <time dateTime={r.ts} title={absoluteTime(r.ts)}
                        className="ml-auto shrink-0 text-[11.5px] font-medium text-light-text-muted dark:text-dark-text-muted">
                    {relativeTime(r.ts)}
                  </time>
                </div>
                <p className="mt-2 text-[13.9px] font-semibold leading-[1.3] tracking-[-0.006em]">{r.subject}</p>
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : i)}
                  aria-expanded={isOpen}
                  className="mt-2 flex items-center gap-1 text-[12px] font-medium text-light-text-muted
                             transition-colors duration-[120ms] hover:text-light-text-primary
                             dark:text-dark-text-muted dark:hover:text-dark-text-primary"
                >
                  <svg aria-hidden="true" viewBox="0 0 24 24"
                       className={`h-3.5 w-3.5 transition-transform duration-150 ${isOpen ? 'rotate-90' : ''}`}
                       fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                  {isOpen ? 'Hide body' : 'Body'}
                </button>
                {isOpen && (
                  <pre className="mt-2 max-h-[320px] overflow-auto whitespace-pre-wrap break-words border-l-2
                                  border-light-border pl-3 font-mono text-caption text-light-text-secondary
                                  dark:border-dark-border dark:text-dark-text-secondary">
                    {r.body}
                  </pre>
                )}
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
