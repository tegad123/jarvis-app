import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import OutboxList from '../components/OutboxList'
import Page from '../components/Page'
import { ApiError, Unauthorized, getFeed, getFeedSummary } from '../lib/api'
import { ActivityRow, EmptyState, ErrorState, FilterChips, Rows, TodaySummary } from '../jm'
import { kindStyle, type KindSemantic } from '../lib/kinds'
import { summarise } from '../lib/summary'
import { absoluteTime, relativeTime } from '../lib/time'
import type { FeedEvent, FeedSummary, Role } from '../lib/types'

const POLL_MS = 15_000

interface Props {
  role?: Role
  onUnauthorized: () => void
}

const TONE: Record<KindSemantic, 'neutral' | 'needs-you' | 'done' | 'error'> = {
  info: 'neutral',
  neutral: 'neutral',
  success: 'done',
  warning: 'needs-you',
  alert: 'error',
}

function attachmentLabel(a: Record<string, unknown>): string | null {
  for (const key of ['title', 'subject', 'task_id', 'message_id', 'name']) {
    const v = a[key]
    if (typeof v === 'string' && v.trim()) return v
  }
  const t = a.type
  return typeof t === 'string' ? t : null
}

/** Attachment labels worth a chip: not ones the headline already says. */
function chips(event: FeedEvent): string[] {
  const body = (event.headline || event.message || '').toLowerCase()
  const rawSubject = event.subject?.trim().toLowerCase()
  return event.attachments
    .map(attachmentLabel)
    .filter((l): l is string => !!l)
    .filter((l) => {
      const t = l.trim().toLowerCase()
      if (event.headline && rawSubject && t === rawSubject) return false
      return !body.includes(t.slice(0, 40))
    })
}

export default function Feed({ role, onUnauthorized }: Props) {
  const [events, setEvents] = useState<FeedEvent[] | null>(null)
  const [summary, setSummary] = useState<FeedSummary | null>(null)
  const [summaryLoaded, setSummaryLoaded] = useState(false)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [kindFilter, setKindFilter] = useState<string | null>(null)
  const [skillFilter, setSkillFilter] = useState<string | null>(null)

  // Held in a ref so the polling effect never has to re-subscribe when data
  // changes — the interval is set up once and keeps calling the latest load().
  const onUnauthorizedRef = useRef(onUnauthorized)
  onUnauthorizedRef.current = onUnauthorized

  const load = useCallback(async () => {
    try {
      // A whole page refetched rather than a `since` merge: a page can't drift
      // out of order or miss an edit the way a merged tail can. Settled rather
      // than awaited as a pair, so a summary failure never blanks the list.
      const [feedRes, summaryRes] = await Promise.allSettled([getFeed({ limit: 50 }), getFeedSummary()])

      if (feedRes.status === 'rejected') throw feedRes.reason
      setEvents(feedRes.value.events)
      setError(null)

      if (summaryRes.status === 'fulfilled') {
        setSummary(summaryRes.value)
      } else {
        // 401 on the summary means the session is gone for the feed too.
        // Anything else: the honest "Summary unavailable" rather than stale counts.
        if (summaryRes.reason instanceof Unauthorized) throw summaryRes.reason
        setSummary(null)
      }
      setSummaryLoaded(true)
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorizedRef.current()
        return
      }
      // Keep whatever we already have on screen — never blank the list.
      if (err instanceof ApiError) {
        setError({
          message: err.status === 0 ? "Can't reach Jarvis right now." : 'Jarvis could not read the activity feed.',
          detail: err.detail ?? (err.status ? `HTTP ${err.status}` : undefined),
        })
      } else {
        setError({ message: 'Something went wrong loading the feed.' })
      }
    }
  }, [])

  useEffect(() => {
    void load()
    const id = setInterval(() => void load(), POLL_MS)
    // Catch up immediately when the phone comes back from the background,
    // where timers are throttled or suspended.
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [load])

  const all = events ?? []
  const kindOptions = useMemo(() => [...new Set(all.map((e) => e.kind))].sort(), [all])
  const skillOptions = useMemo(() => [...new Set(all.map((e) => e.skill))].sort(), [all])
  const visible = useMemo(
    () =>
      all.filter(
        (e) => (kindFilter === null || e.kind === kindFilter) && (skillFilter === null || e.skill === skillFilter),
      ),
    [all, kindFilter, skillFilter],
  )
  const day = summary ? summarise(summary.counts) : null

  return (
    <Page title="Activity">
      {summaryLoaded && <TodaySummary items={day?.items} needsYou={day?.needsYouCount} unavailable={!day} />}

      {error && (
        <ErrorState
          label="Feed unavailable"
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={all.length > 0}
        />
      )}

      <div className="grid gap-jm-2">
        <FilterChips label="Kind" options={kindOptions} active={kindFilter} onChange={setKindFilter} format={(k) => kindStyle(k).word} />
        <FilterChips label="Skill" options={skillOptions} active={skillFilter} onChange={setSkillFilter} />
      </div>

      {events === null && !error ? (
        <p className="m-0 py-jm-8 text-center text-jm-body-sm text-jm-fg-secondary">Loading…</p>
      ) : visible.length === 0 && !error ? (
        <EmptyState icon="activity">Jarvis is watching. Nothing needs you right now.</EmptyState>
      ) : (
        <Rows>
          {visible.map((e) => {
            const { semantic, word } = kindStyle(e.kind)
            return (
              <ActivityRow
                key={`${e.ts}-${e.skill}-${e.kind}`}
                kind={word}
                tone={TONE[semantic]}
                skill={e.skill}
                time={relativeTime(e.ts)}
                timeTitle={absoluteTime(e.ts)}
                dateTime={e.ts}
                // `message` is the fallback for events written before the humanised schema.
                headline={e.headline || e.message}
                detail={e.detail || undefined}
                attachments={chips(e)}
              />
            )
          })}
        </Rows>
      )}

      {/* OPERATOR OUTBOX — what the boundary caught instead of sending. Desktop only. */}
      {role === 'operator' && (
        <div className="hidden lg:block">
          <OutboxList onUnauthorized={onUnauthorized} />
        </div>
      )}
    </Page>
  )
}
