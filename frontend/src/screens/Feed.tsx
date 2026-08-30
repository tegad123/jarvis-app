import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import FeedRow from '../components/FeedRow'
import FilterChips from '../components/FilterChips'
import HeroCard from '../components/HeroCard'
import { EmptyState, ErrorState } from '../components/States'
import OutboxList from '../components/OutboxList'
import { ApiError, Unauthorized, getFeed, getFeedSummary } from '../lib/api'
import { kindStyle } from '../lib/kinds'
import type { FeedEvent, FeedSummary, Role } from '../lib/types'

const POLL_MS = 15_000

interface Props {
  role?: Role
  onUnauthorized: () => void
}

export default function Feed({ role, onUnauthorized }: Props) {
  const [events, setEvents] = useState<FeedEvent[] | null>(null)
  const [summary, setSummary] = useState<FeedSummary | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [kindFilter, setKindFilter] = useState<string | null>(null)
  const [skillFilter, setSkillFilter] = useState<string | null>(null)

  // Held in a ref so the polling effect never has to re-subscribe when data
  // changes — the interval is set up once and keeps calling the latest load().
  const onUnauthorizedRef = useRef(onUnauthorized)
  onUnauthorizedRef.current = onUnauthorized

  const load = useCallback(async () => {
    try {
      // Deliberate full refetch rather than an incremental `since` merge: the
      // page is small, and a whole page can't drift out of order or miss an
      // edit the way a merged tail can. `since` stays available in the client.
      // Both in flight together: the summary counts the whole day server-side
      // while the page renders the newest 50. Settled rather than awaited as a
      // pair, so a summary failure never blanks the list (or vice versa).
      const [feedRes, summaryRes] = await Promise.allSettled([
        getFeed({ limit: 50 }),
        getFeedSummary(),
      ])

      if (feedRes.status === 'rejected') throw feedRes.reason
      setEvents(feedRes.value.events)
      setError(null)

      if (summaryRes.status === 'fulfilled') {
        setSummary(summaryRes.value)
      } else {
        // 401 on the summary means the session is gone for the feed too; the
        // feed call above would already have thrown. Anything else: drop to the
        // honest "Summary unavailable" hero rather than showing stale counts.
        if (summaryRes.reason instanceof Unauthorized) throw summaryRes.reason
        setSummary(null)
      }
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorizedRef.current()
        return
      }
      // Keep whatever we already have on screen — never blank the list.
      if (err instanceof ApiError) {
        setError({
          message:
            err.status === 0
              ? "Can't reach Jarvis right now."
              : 'Jarvis could not read the activity feed.',
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

  const kindOptions = useMemo(
    () => [...new Set(all.map((e) => e.kind))].sort(),
    [all],
  )
  const skillOptions = useMemo(
    () => [...new Set(all.map((e) => e.skill))].sort(),
    [all],
  )

  const visible = useMemo(
    () =>
      all.filter(
        (e) =>
          (kindFilter === null || e.kind === kindFilter) &&
          (skillFilter === null || e.skill === skillFilter),
      ),
    [all, kindFilter, skillFilter],
  )

  return (
    <div className="relative grid min-w-0 gap-3.5 lg:gap-6">
      {/* CANVAS BLOOM. The mockup's ground is not a flat fill — there is a
          faint ink glow behind the top of the content, and it is most of why a
          quiet day still looks like a designed screen rather than a blank
          sheet. Behind everything (-z-10) and unfocusable; the ambient blobs
          are a separate, stronger layer that lives on the app shell. */}
      <div aria-hidden="true" className="page-glow -z-10" />
      {/* Hero summarises the whole day from the server, not the filtered view
          and not the rendered page — a filter should not change what "needs
          you", and neither should how many events happen to fit on the page. */}
      <HeroCard summary={summary} />

      {error && (
        <ErrorState
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
          stale={all.length > 0}
          label="Feed unavailable"
        />
      )}

      <div className="grid gap-1.5 lg:gap-3">
        <FilterChips
          label="Kind"
          options={kindOptions}
          active={kindFilter}
          onChange={setKindFilter}
          format={(k) => kindStyle(k).word}
        />
        <FilterChips
          label="Skill"
          options={skillOptions}
          active={skillFilter}
          onChange={setSkillFilter}
        />
      </div>

      {events === null && !error ? (
        <p className="py-8 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
          Loading…
        </p>
      ) : visible.length === 0 && !error ? (
        <EmptyState />
      ) : (
        <ol className="relative mt-1 lg:mt-0">
          {/* The wire spine: a hairline the 13px dots sit on. Inset to the dot
              centre (13px column ÷ 2). At lg the rail is a fixed 32px gutter
              and the spine sits on its centre line; it runs from the first
              dot to the last so the connector never breaks between cards. */}
          <span
            aria-hidden="true"
            className="wire-spine absolute bottom-4 left-[7px] top-4 w-px
                       lg:bottom-[calc(16px+var(--dot-top))] lg:left-[15.5px] lg:top-[var(--dot-top)]"
          />
          {visible.map((e) => (
            <FeedRow key={`${e.ts}-${e.skill}-${e.kind}`} event={e} />
          ))}
        </ol>
      )}

      {/* OPERATOR OUTBOX — what the boundary caught instead of sending. A
          support surface for the break-glass session only, and desktop only
          (lg+): it is where an operator sits when checking a tester's run. */}
      {role === 'operator' && (
        <div className="hidden lg:block">
          <OutboxList onUnauthorized={onUnauthorized} />
        </div>
      )}
    </div>
  )
}
