/**
 * KIND → SEMANTIC MAP
 *
 * The feed's `kind` field is an open-ended string, not an enum:
 * _shared/notify.build_event() defaults it to 'info' and every caller is free
 * to invent its own. Real values on disk today are 'task.created',
 * 'task.completed' and 'email.classified'. (The ok/degraded/fail vocabulary
 * belongs to /api/health — a different axis entirely; don't conflate them.)
 *
 * The design tokens define five semantics, quoted verbatim from the token
 * file's KIND COLOR SEMANTICS block:
 *
 *   info    = filings, updates        (Email filed, Update)
 *   success = completions, hot leads  (Task created, Done word uses text-secondary, Hot)
 *   warning = waiting on the user     (Needs approval, Pending, Warm)
 *   alert   = deadlines, failures     (Alert, Going quiet, connection errors)
 *   neutral = past/settled dots
 *
 * This module is the single documented bridge between the two. Rules are
 * applied most-specific first; anything unrecognised degrades to info styling
 * with a prettified label. An unknown kind must never crash the feed or cause
 * an event to be hidden — silently dropping an event is worse than showing it
 * with a generic dot.
 */

export type KindSemantic = 'info' | 'success' | 'warning' | 'alert' | 'neutral'

export interface KindStyle {
  semantic: KindSemantic
  /** The word shown beside the dot. Dot + word, never dot alone. */
  word: string
}

/** Exact-match table for the kinds we know by name. */
const EXACT: Record<string, KindStyle> = {
  'email.classified': { semantic: 'info', word: 'Email filed' },
  'task.created': { semantic: 'success', word: 'Task created' },
  // Settled/past — the tokens list 'Done' under success but specify the WORD
  // renders in text-secondary; the dot itself is the neutral past/settled dot.
  'task.completed': { semantic: 'neutral', word: 'Done' },
  // Delegation. 'info' because a handover is a filing, not something waiting
  // on the reader — the person it went TO sees it in their own list.
  'task.assigned': { semantic: 'info', word: 'Assigned' },
  // The tokens name this one exactly: alert = "Going quiet".
  'task.stale': { semantic: 'alert', word: 'Going quiet' },
  'lead.quiet': { semantic: 'alert', word: 'Going quiet' },
  'meeting.filed': { semantic: 'info', word: 'Meeting filed' },
  'digest.sent': { semantic: 'info', word: 'Digest sent' },
  // Operator fixture injection — a test input the operator pushed in.
  'operator.inject': { semantic: 'info', word: 'Test input' },
  info: { semantic: 'info', word: 'Update' },
  warning: { semantic: 'warning', word: 'Warning' },
  alert: { semantic: 'alert', word: 'Alert' },
  error: { semantic: 'alert', word: 'Alert' },
}

/**
 * Turn an unrecognised kind string into something readable:
 * 'deal.channel_created' → 'Deal Channel Created'
 */
export function prettifyKind(kind: string): string {
  const words = kind
    .replace(/[._-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (!words.length) return 'Update'
  return words
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(' ')
}

export function kindStyle(rawKind: string | undefined | null): KindStyle {
  const kind = (rawKind ?? '').trim()
  if (!kind) return { semantic: 'info', word: 'Update' }

  const lower = kind.toLowerCase()

  // 1. Exact match wins.
  const exact = EXACT[lower]
  if (exact) return exact

  // 2. Anything the user is being waited on for.
  if (lower.includes('approval') || lower.includes('needs_')) {
    return { semantic: 'warning', word: prettifyKind(kind) }
  }

  // 3. Failure-shaped kinds.
  if (
    lower.endsWith('.failed') ||
    lower.includes('alert') ||
    lower.includes('error')
  ) {
    return { semantic: 'alert', word: prettifyKind(kind) }
  }

  // 4. Family fallbacks, so a new sibling kind still lands sensibly.
  if (lower.includes('warning')) {
    return { semantic: 'warning', word: prettifyKind(kind) }
  }
  if (lower.endsWith('.completed') || lower.endsWith('.done')) {
    return { semantic: 'neutral', word: prettifyKind(kind) }
  }
  if (lower.endsWith('.created')) {
    return { semantic: 'success', word: prettifyKind(kind) }
  }

  // 5. Unknown — visible, never hidden.
  return { semantic: 'info', word: prettifyKind(kind) }
}

/** Semantics that mean "a human still has to do something about this". */
export function needsYou(semantic: KindSemantic): boolean {
  return semantic === 'warning' || semantic === 'alert'
}
