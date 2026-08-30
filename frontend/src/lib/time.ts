/**
 * Timestamp helpers.
 *
 * Feed timestamps come from Python's datetime.isoformat() on a UTC-aware
 * datetime, so they look like '2026-08-16T15:35:35.051992+00:00' — a +00:00
 * offset with microseconds, NOT a 'Z' suffix. Date parses that correctly;
 * string-matching on a trailing 'Z' does not. Always go through here.
 */

export function parseTs(ts: string): Date | null {
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? null : d
}

/** "just now" / "4m" / "3h" / "2d" / then an absolute date. */
export function relativeTime(ts: string, now: Date = new Date()): string {
  const d = parseTs(ts)
  if (!d) return ''

  const seconds = Math.round((now.getTime() - d.getTime()) / 1000)

  // Small clock skew between the droplet and the phone shows up as a future
  // timestamp; read that as "now" rather than printing a negative age.
  if (seconds < 45) return 'just now'

  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m`

  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h`

  const days = Math.round(hours / 24)
  if (days < 7) return `${days}d`

  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

/** Full timestamp for the title attribute / accessible label. */
export function absoluteTime(ts: string): string {
  const d = parseTs(ts)
  if (!d) return ts
  return d.toLocaleString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

/**
 * Is this event from today?
 *
 * "Today" is the *viewer's* local day, not the droplet's UTC day and not the
 * tenant's configured timezone. Someone opening the app at 8am sees the events
 * of the day they are living in. Documented because it is a real choice: the
 * tenant config carries America/Chicago, and a tenant-timezone reading would
 * be equally defensible if the hero is ever meant to match a nightly digest.
 */
export function isToday(ts: string, now: Date = new Date()): boolean {
  const d = parseTs(ts)
  if (!d) return false
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  )
}

/** "Saturday, 16 August" — the date line under the greeting. */
export function todayLabel(now: Date = new Date()): string {
  return now.toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  })
}


/**
 * A YYYY-MM-DD deadline as a LOCAL date.
 *
 * new Date('2026-09-30') parses as UTC midnight, which is the 29th anywhere
 * west of Greenwich for most of the day — so a deadline rendered that way is
 * off by one for every American tenant every evening. Splitting the parts and
 * building a local date is the only reading that agrees with the calendar the
 * person is looking at.
 */
export function parseLocalDate(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso || '').trim())
  if (!m) return null
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  return Number.isNaN(d.getTime()) ? null : d
}

/**
 * How long a task has been open, as a phrase.
 *
 * relativeTime() answers "how long ago did this happen" and renders '3d';
 * this answers "how long has this been sitting", which is a different
 * question and deserves words. The number comes from the server (days_open,
 * computed in the tenant's zone) — this only phrases it.
 */
export function durationOpen(days: number): string {
  if (!Number.isFinite(days) || days <= 0) return 'open today'
  if (days === 1) return 'open 1 day'
  if (days < 14) return `open ${days} days`
  const weeks = Math.round(days / 7)
  return `open ${weeks} weeks`
}
