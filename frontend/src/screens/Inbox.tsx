import { useCallback, useEffect, useState } from 'react'
import Page from '../components/Page'
import { ApiError, Unauthorized, getInbox } from '../lib/api'
import { EmptyState, ErrorState, List } from '../jm'
import { absoluteTime, relativeTime } from '../lib/time'
import type { InboxMessage, InboxResponse } from '../lib/types'

/**
 * The log of Jarvis's own inbox. Unread mail stays marked so a forward or a
 * CC is not something the list can quietly drop. The server is owner/admin
 * only, and this screen only renders what that call returns.
 */

interface Props {
  onUnauthorized: () => void
}

const POLL_MS = 30_000

function Row({ message }: { message: InboxMessage }) {
  const when = message.date ? relativeTime(message.date) : ''
  return (
    <li className="grid min-w-0 gap-jm-1 border-b border-jm-border py-jm-3 last:border-b-0">
      <div className="flex min-w-0 items-center gap-jm-2 text-jm-caption text-jm-fg-secondary">
        {message.unread ? (
          <span className="shrink-0 rounded-jm-pill bg-jm-accent-soft px-jm-2 py-0.5 text-jm-accent-soft-text">Unread</span>
        ) : (
          <span className="shrink-0">Read</span>
        )}
        <span className="min-w-0 truncate">{message.from || 'Unknown sender'}</span>
        {when && (
          <time dateTime={message.date} title={absoluteTime(message.date)} className="ml-auto shrink-0">
            {when}
          </time>
        )}
      </div>
      <p className={`m-0 text-jm-body-sm text-jm-fg ${message.unread ? 'font-medium' : ''}`}>{message.subject}</p>
      {message.snippet && <p className="m-0 text-jm-caption text-jm-fg-secondary">{message.snippet}</p>}
      {message.handling && <p className="m-0 text-jm-caption text-jm-fg">{message.handling}</p>}
    </li>
  )
}

export default function Inbox({ onUnauthorized }: Props) {
  const [data, setData] = useState<InboxResponse | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const next = await getInbox()
      setData(next)
      setError(null)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError({
        message: 'Jarvis could not read his inbox.',
        detail: err instanceof ApiError ? (err.detail ?? `HTTP ${err.status}`) : undefined,
      })
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
    const id = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, POLL_MS)
    return () => window.clearInterval(id)
  }, [load])

  const messages = data?.messages ?? []
  const needsConnect = data?.status === 'disconnected' || data?.status === 'needs_reauth'
  const waiting = data?.status === 'connected' ? data.unread : 0

  return (
    <Page
      title="Inbox"
      subtitle={
        data?.mailbox
          ? waiting > 0
            ? `${data.mailbox} · ${waiting} unread`
            : data.mailbox
          : 'Mail sent to Jarvis'
      }
    >
      {error && (
        <ErrorState
          label="Inbox unavailable"
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={messages.length > 0}
        />
      )}

      {needsConnect && (
        <EmptyState
          icon="inbox"
          action={{
            label: "Connect Jarvis's mailbox",
            primary: true,
            onClick: () => {
              window.location.href = '/api/google/assistant/connect/start'
            },
          }}
        >
          {data?.notice ?? "Jarvis's mailbox is not connected, so this list is empty."}
        </EmptyState>
      )}

      {data?.status === 'unavailable' && !error && (
        <EmptyState icon="inbox">{data.notice ?? 'Jarvis could not read his inbox just now.'}</EmptyState>
      )}

      {data?.status === 'connected' && messages.length === 0 && !error && (
        <EmptyState icon="inbox">Nothing is in Jarvis's inbox.</EmptyState>
      )}

      {messages.length > 0 && (
        <List>
          {messages.map((m) => (
            <Row key={m.id} message={m} />
          ))}
        </List>
      )}
    </Page>
  )
}
