import { kindStyle, needsYou } from './kinds'

/**
 * The day's summary line, from the server's per-kind counts for the whole day
 * (not the filtered view, not the rendered page — a filter should not change
 * what "needs you", and neither should how many events fit on a page).
 */
export interface DaySummary {
  todayCount: number
  needsYouCount: number
  /** "3 emails sent · 1 needs approval", most frequent first. */
  items: string[]
}

/** Words that read wrong with an s: "3 done", not "3 dones". */
const NO_PLURAL = new Set(['done'])

function pluralise(word: string, n: number): string {
  const lower = word.toLowerCase()
  if (n === 1 || NO_PLURAL.has(lower)) return lower
  const [noun, ...rest] = lower.split(' ')
  const plural = /(s|x|z|ch|sh)$/.test(noun) ? `${noun}es` : `${noun}s`
  return [plural, ...rest].join(' ')
}

export function summarise(byKind: Record<string, number>): DaySummary {
  const counts = new Map<string, number>()
  let needsYouCount = 0
  let todayCount = 0

  for (const [kind, n] of Object.entries(byKind)) {
    const { word, semantic } = kindStyle(kind)
    counts.set(word, (counts.get(word) ?? 0) + n)
    if (needsYou(semantic)) needsYouCount += n
    todayCount += n
  }

  const items = [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([word, n]) => `${n} ${pluralise(word, n)}`)

  return { todayCount, needsYouCount, items }
}
