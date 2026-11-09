/**
 * Follow-up dates are YYYY-MM-DD on the tenant's calendar, and `today` comes
 * from the server in the same zone — compared as strings, never through the
 * browser's clock, so an agent travelling west doesn't see tomorrow's list.
 */

function short(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  if (!y || !m || !d) return day
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function isDue(next: string | undefined, today: string | undefined, status?: string): boolean {
  return !!next && !!today && status !== 'Not Interested' && next.slice(0, 10) <= today
}

export function dueLabel(
  next: string | undefined,
  today: string | undefined,
  status?: string,
): { text: string; now: boolean } | null {
  if (!next || status === 'Not Interested') return null
  const day = next.slice(0, 10)
  if (!today) return { text: `Follow up ${short(day)}`, now: false }
  if (day < today) return { text: `Overdue · was due ${short(day)}`, now: true }
  if (day === today) return { text: 'Follow up today', now: true }
  return { text: `Next follow-up · ${short(day)}`, now: false }
}
