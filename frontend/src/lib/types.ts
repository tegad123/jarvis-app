/**
 * Shapes returned by the Jarvis app backend.
 *
 * Hand-written on purpose: the FastAPI app is constructed with
 * `openapi_url=None`, so there is no schema to generate from. These types were
 * read off the actual responses and off app/feed.py + app/health.py. If the
 * backend changes shape, this file is the one place to update.
 */

/** One activity-feed event, as written by _shared/notify.build_event(). */
export interface FeedEvent {
  /** UTC ISO-8601 with a +00:00 offset and microseconds — NOT a 'Z' suffix. */
  ts: string
  tenant: string
  /** Skill directory name, or 'unknown' when not inferable. */
  skill: string
  /** Logical destination ('ops' | 'tasks' | 'emails' | ...), never a channel id. */
  destination: string
  /** Open-ended string, NOT an enum. See lib/kinds.ts for the semantic map. */
  kind: string
  /** Legacy free text. Present on every event; the ONLY field old events have. */
  message: string
  /** One plain sentence for the tenant. Absent on pre-2026-08-17 events. */
  headline?: string
  /** Technical specifics, shown on tap-to-expand. */
  detail?: string
  /** Display name only — never an address. */
  person?: string
  subject?: string
  attachments: FeedAttachment[]
}

/**
 * Attachments are heterogeneous and keyed loosely by `type`; the writer imposes
 * no schema, so everything past `type` is optional and unknown-typed.
 */
export interface FeedAttachment {
  type?: string
  [key: string]: unknown
}

export interface FeedResponse {
  events: FeedEvent[]
  count: number
  total: number
  order: 'newest_first'
  feed_path: string
}

/**
 * Today's counts, computed server-side over the WHOLE feed.
 *
 * The hero used to derive this from the 50-event page it rendered, which stopped
 * being "today" the moment the feed grew past 50. Counts are keyed by RAW kind;
 * lib/kinds.ts still owns the kind -> word and kind -> "needs you" mappings.
 */
export interface FeedSummary {
  /** The local date the counts cover, in `timezone`. */
  date: string
  /** The zone actually used — echoes the request, or 'UTC' if it was invalid. */
  timezone: string
  total: number
  counts: Record<string, number>
  /** Every event in the feed, all dates. Useful for "is the feed empty at all". */
  feed_total: number
  generated_at: string
}

export interface SessionResponse {
  ok: boolean
  authenticated: boolean
  issued_at: number
  /** Added in build step 2, sourced from the identity registry owner.
   *  Absent when the registry is unreadable — render "Hi there" then. */
  display_name?: string
  /** Who is signed in. Null on a break-glass operator session. */
  person_key?: string | null
  role?: Role
}

/** What a session may do. 'operator' is the shared break-glass passphrase:
 *  it reaches every channel's STRUCTURE and none of its message content. */
export type Role = 'owner' | 'admin' | 'member' | 'operator'

/** GET /api/google/connection. Exactly these keys — never token/refresh/cipher. */
export type GoogleConnectionStatusName = 'connected' | 'needs_reauth' | 'disconnected'

export interface GoogleConnectionStatus {
  status: GoogleConnectionStatusName
  google_email: string | null
  connected_at: string | null
  refreshed_at: string | null
}

export interface LoginResponse {
  ok: boolean
  /** Null for a break-glass operator session. */
  person_key?: string | null
  role?: Role
  session: { iat: number }
  expires_in: number
}

/** /api/health check bodies genuinely vary by branch; only these two are certain. */
export interface HealthCheck {
  status: 'ok' | 'degraded' | 'fail' | 'unknown'
  latency_ms: number
  [key: string]: unknown
}

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'fail' | 'unknown'
  checked_at: string
  checks: Record<string, HealthCheck>
}

/* ---------------------------------------------------------------------------
 * Conversation types — structured with thread_id from day one so the Ask tab
 * can become multi-channel without a data migration.
 *
 * NOTHING implements these yet. The backend exposes no conversation endpoint
 * (spec screen 2 is a later build step). They are declared here so that when
 * /api/chat lands, the client already speaks in threads rather than in a flat
 * message list that would have to be reshaped.
 * ------------------------------------------------------------------------- */

/**
 * A conversation. Implemented in Phase A — this replaces the speculative
 * placeholder shape declared in step 2, and now mirrors app/threads.py exactly.
 */
export interface Thread {
  thread_id: string
  name: string
  /** 'jarvis' channels show the spark mark; 'user' channels do not. */
  created_by: 'user' | 'jarvis'
  created_at: string
  last_message_at: string | null
  archived: boolean
  /** This person's own direct line: unrenamable, unarchivable, unshareable.
   *  Every signed-in person has exactly one; the UI pins it by THIS FLAG and
   *  never by a hardcoded thread id. */
  is_direct: boolean
  /** Last message snippet, supplied by GET /api/threads for the list rows. */
  preview?: string | null
  preview_role?: 'user' | 'assistant' | null
  /** Count chips for the row's badge line ("2 UPDATES"). NO PRODUCER YET —
   *  the backend never sets this, so the badge row collapses in production.
   *  Declared so the design exists and a future unread-counter has a target. */
  badges?: string[]
  /** Unread indicator. Same: no producer yet, dot simply does not render. */
  unread?: boolean
  /** Person keys in this channel, from GET /api/threads. Empty for direct
   *  lines — a direct line is not a one-member channel (app/threads.py). */
  members?: string[]
}

export interface ThreadsResponse {
  threads: Thread[]
  count: number
  counts: { active: number; archived: number; channels: number; messages: number }
  /** False for a break-glass operator session: rows and membership are listed,
   *  message content is not. The API enforces this; the UI reflects it. */
  bodies_visible?: boolean
}

export interface ThreadMember {
  person_key: string
  added_by: string | null
  added_at: string
}

export interface Message {
  thread_id: string
  message_id: string
  role: 'user' | 'assistant'
  body: string
  created_at: string
}

/* --------------------------------------------------------------------------
 * Chat — implemented as of build step 3.
 * ------------------------------------------------------------------------ */

/** The legacy direct-line id — now just the OWNER's line, kept because its
 *  messages point at it. Every person has their own; the UI finds it by
 *  `is_direct` on the thread list and never assumes this id. */
export const DIRECT_THREAD = 'direct'

/** As stored by the backend's in-memory history and replayed by /api/chat/history. */
export interface ChatMessage {
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  /** Unix seconds from the server; absent on messages created optimistically. */
  ts?: number
  /** True when the turn ended before the model finished. */
  partial?: boolean
  attachments?: Attachment[]
}

export interface ChatHistoryResponse {
  thread_id: string
  messages: Array<{
    role: 'user' | 'assistant'
    content: string
    ts?: number
    partial?: boolean
    attachments?: Attachment[]
  }>
  count: number
}

/* --------------------------------------------------------------- approvals */

export type ApprovalStatus = 'pending' | 'approved' | 'rejected'

/** v1.1. Absent on every pre-2026-08-18 row, which is why it is optional and
 *  why the UI treats an unknown value as 'action' rather than erroring. */
export type ApprovalKind = 'action' | 'config-change'

/** One key's before/after in a config-change request. `current` is the value
 *  the proposal was written against — the server refuses to apply if it moved. */
export interface ConfigChange {
  key: string
  current: unknown
  proposed: unknown
  /** Plain-language line for the owner, e.g. "Digest times: 5AM & 3PM → 8AM". */
  label?: string
}

export interface Approval {
  id: string
  kind?: ApprovalKind
  /** Recorded now, enforced when per-user auth lands (Workstream 3). */
  requires_role?: string | null
  ts: string
  tenant: string
  skill: string
  summary: string
  /** Free-form per producing skill; rendered as a key/value detail list. */
  payload: Record<string, unknown>
  status: ApprovalStatus
  decided_by?: string
  decided_at?: string
}

export interface ApprovalsResponse {
  approvals: Approval[]
  count: number
  counts: Record<ApprovalStatus, number>
  requests_path: string
}

/* ------------------------------------------------------------------- leads */

export interface Lead {
  lead_id: string
  name: string
  property: string
  price: string
  date: string
  /** 'Hot' | 'Warm' | 'Cold' | '' — blank is common. */
  temperature: string
  /** 3/2/1/0, for the temperature bars. */
  temperature_rank: number
  /** 'No Response' | 'Responded' | 'Not Interested' | '' */
  status: string
  /** Free text: 'New', 'Contacted', 'Emailed 6/15'. Never chip this. */
  follow_up: string
  next_follow_up: string
  last_nudge: string
  agent: string
  tab: string
  is_new: boolean
  is_hot: boolean
  needs_touch: boolean
  /** App-native only; absent on sheet-backed rows. */
  created_by?: "user" | "jarvis"
  thread_id?: string | null
  archived?: boolean
}

export type TaskStatus = 'open' | 'in_progress' | 'done' | 'archived'
export type TaskPriority = 'high' | 'medium' | 'low'
export type TaskSource = 'manual' | 'chat' | 'email' | 'meeting'

export interface Task {
  task_id: string
  title: string
  /** The body. NULL on an operator session — see TasksResponse.bodies_visible. */
  detail: string | null
  /** A person KEY, or null when nobody has it yet. */
  assignee: string | null
  /** Display name for `assignee`; '' when unassigned. */
  assignee_name: string
  created_by: string
  created_by_name: string
  source: TaskSource
  source_ref: string | null
  priority: TaskPriority
  /** YYYY-MM-DD in the tenant's own zone, or null. */
  deadline: string | null
  status: TaskStatus
  stale: boolean
  /** Server-computed, in the tenant's zone — never recompute these locally. */
  is_overdue: boolean
  days_open: number
  due_label: string
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface TasksResponse {
  tasks: Task[]
  count: number
  counts: {
    open: number
    in_progress: number
    done: number
    stale: number
    overdue: number
    archived: number
  }
  /** 'store' accepts writes; 'legacy' means this tenant's tasks live in the
   *  assistant's own list and the screen is a sentence, not a table. */
  source: 'store' | 'legacy'
  /** False on a break-glass operator session: every `detail` is null because
   *  the server withheld it, NOT because nobody wrote one. */
  bodies_visible: boolean
  timezone?: string
  notice?: string
}

export interface LeadsResponse {
  leads: Lead[]
  count: number
  /** Which store answered. 'sheet' is read-only; only 'store' accepts writes,
   *  so the UI shows editing controls solely when this says 'store'. */
  source?: "sheet" | "store"
  tabs: string[]
  sheet_id: string
  sheet_title: string
  counts: { all: number; needs_touch: number; hot: number; new: number }
}

/** 503 body when the sheet cannot be read. */
export interface LeadsError {
  error: string
  detail?: string
  leads: []
  count: 0
}

/** A stored attachment, as returned by the upload and history endpoints. */
export interface Attachment {
  id: string
  thread_id: string
  filename: string
  mime: string
  size: number
  size_human: string
  ts: string
  is_image: boolean
  /** False for documents: stored and downloadable, but the model never sees
   *  them. The UI renders that as "saved, not readable yet". */
  readable: boolean
  url: string
}

/** NDJSON events emitted by POST /api/chat. */
export type ChatStreamEvent =
  | { type: 'delta'; text: string }
  | {
      type: 'tool'
      status: 'running' | 'completed'
      tool?: string
      /** Only present on 'running' — cache it for the matching 'completed'. */
      label?: string
      toolCallId?: string
    }
  | { type: 'done'; finish_reason: string; usage?: Record<string, number> }
  | { type: 'error'; message: string; detail?: string; partial?: boolean }
  /** Something was deliberately withheld from the model (e.g. a document). */
  | { type: 'notice'; message: string }
  /**
   * The turn checked itself against the store. `ok: true` is a receipt built
   * from the saved row; `ok: false` means the reply claimed a task and none
   * was written. Generated by the server, never by the model.
   */
  | { type: 'verified'; ok: boolean; message: string; task_ids?: string[] }
  /** One of the app's own controls, asked for by the agent and drawn by the app. */
  | { type: 'action'; action: string; label?: string }
