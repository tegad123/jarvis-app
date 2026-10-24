import { useCallback, useEffect, useState } from 'react'
import { Unauthorized, getOutbox, type OutboxRow as Row } from '../lib/api'
import { OutboxRow, Rows } from '../jm'
import { absoluteTime, relativeTime } from '../lib/time'

/**
 * What the send boundary caught instead of sending. A support surface for the
 * break-glass session only; the server decides who may read it.
 */

interface Props {
  onUnauthorized: () => void
  limit?: number
}

const POLL_MS = 15_000

export default function OutboxList({ onUnauthorized, limit = 20 }: Props) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [active, setActive] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const r = await getOutbox(limit)
      setRows([...r.rows].reverse()) // newest first on screen; the API is file order
      setActive(r.active)
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorized()
        return
      }
      setError("Couldn't read the outbox.")
    }
  }, [limit, onUnauthorized])

  useEffect(() => {
    void load()
    const id = setInterval(() => void load(), POLL_MS)
    return () => clearInterval(id)
  }, [load])

  return (
    <section aria-labelledby="outbox-heading" className="grid gap-jm-3">
      <header className="flex items-baseline gap-jm-3">
        <h2 id="outbox-heading" className="m-0 text-jm-heading text-jm-fg">
          Outbox
        </h2>
        <span className="text-jm-caption text-jm-fg-secondary">
          {active === false ? 'Boundary off — nothing is captured here' : 'Captured, not sent'}
          {rows ? ` · ${rows.length}` : ''}
        </span>
      </header>

      {error && <p className="m-0 text-jm-body-sm text-jm-error">{error}</p>}
      {rows && rows.length === 0 && !error && <p className="m-0 text-jm-body-sm text-jm-fg-secondary">Nothing captured yet.</p>}

      {rows && rows.length > 0 && (
        <Rows>
          {rows.map((r, i) => (
            <OutboxRow
              key={`${r.ts}-${i}`}
              to={r.to}
              cc={r.cc}
              subject={r.subject}
              body={r.body}
              time={relativeTime(r.ts)}
              timeTitle={absoluteTime(r.ts)}
              dateTime={r.ts}
            />
          ))}
        </Rows>
      )}
    </section>
  )
}
