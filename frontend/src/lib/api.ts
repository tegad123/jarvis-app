/**
 * The ONLY module in this app that talks to the network.
 *
 * Rules this file enforces so no component has to think about them:
 *
 *  - Relative '/api/...' paths only. The app is served same-origin by the same
 *    FastAPI process, and the public cloudflared hostname rotates on every
 *    restart — a baked-in base URL would break on the next deploy.
 *  - credentials:'include'. Auth is an httponly + samesite=lax cookie that JS
 *    cannot read; the browser carries it, and the only way to ask "am I logged
 *    in" is to call getSession() and see whether it 401s.
 *  - Non-2xx bodies are preserved, not discarded. /api/health deliberately
 *    answers 503 WITH the full report; throwing on !res.ok would bin exactly
 *    the data the caller needs.
 *  - 401 is the single logged-out signal, surfaced as a typed Unauthorized so
 *    App can flip to the login screen from anywhere.
 */
import type {
  Approval,
  ApprovalsResponse,
  ChatHistoryResponse,
  ChatStreamEvent,
  FeedResponse,
  FeedSummary,
  GoogleConnectionStatus,
  HealthResponse,
  LeadsError,
  LeadsResponse,
  Task,
  TasksResponse,
  LoginResponse,
  SessionResponse,
  Attachment,
  Thread,
  ThreadMember,
  ThreadsResponse,
} from './types'
import { DIRECT_THREAD } from './types'

/** Thrown on any 401. Callers catch this to route back to Login. */
export class Unauthorized extends Error {
  constructor(message = 'Not authenticated.') {
    super(message)
    this.name = 'Unauthorized'
  }
}

/** Thrown for transport failures and unexpected statuses. Carries what we know. */
export class ApiError extends Error {
  status: number
  detail?: string
  constructor(message: string, status: number, detail?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

interface RawResult<T> {
  status: number
  ok: boolean
  body: T | null
}

function detailOf(body: unknown): string | undefined {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
    // FastAPI validation errors arrive as a list of {loc,msg,type}.
    if (Array.isArray(d) && d.length && typeof d[0] === 'object') {
      const msg = (d[0] as { msg?: unknown }).msg
      if (typeof msg === 'string') return msg
    }
  }
  return undefined
}

/**
 * Low-level fetch. Returns status + parsed body without throwing on non-2xx,
 * except for 401 which is always Unauthorized.
 */
async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<RawResult<T>> {
  let res: Response
  try {
    res = await fetch(path, {
      ...init,
      credentials: 'include',
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...(init.headers ?? {}),
      },
    })
  } catch (e) {
    // Network-level failure: server down, tunnel dropped, offline.
    throw new ApiError(
      'Could not reach Jarvis.',
      0,
      e instanceof Error ? e.message : undefined,
    )
  }

  let body: T | null = null
  const text = await res.text()
  if (text) {
    try {
      body = JSON.parse(text) as T
    } catch {
      body = null
    }
  }

  if (res.status === 401) {
    throw new Unauthorized(detailOf(body) ?? 'Not authenticated.')
  }

  return { status: res.status, ok: res.ok, body }
}

/** Throwing variant for endpoints where any non-2xx is a genuine failure. */
async function requestOk<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { ok, status, body } = await request<T>(path, init)
  if (!ok || body === null) {
    throw new ApiError(detailOf(body) ?? 'Request failed.', status, detailOf(body))
  }
  return body
}

/* ------------------------------------------------------------------ session */

/** Sign-in roster for the picker. Unauthenticated by design. */
export interface Person { person_key: string; name: string; title?: string }

export async function getPeople(): Promise<Person[]> {
  try {
    const { ok, body } = await request<{ people: Person[] }>('/api/people')
    return ok && body ? body.people : []
  } catch {
    // A roster we cannot read is not fatal — the operator break-glass field
    // still works, which is the whole point of keeping it.
    return []
  }
}

/** Per-person sign-in. `person_key` empty means the break-glass passphrase. */
export async function login(
  secret: string,
  personKey?: string,
): Promise<LoginResponse> {
  const payload = personKey ? { person_key: personKey, pin: secret } : { passphrase: secret }
  const { ok, status, body } = await request<LoginResponse>('/api/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  if (ok && body) return body

  // 401 already became Unauthorized upstream; on the login form that is a
  // wrong passphrase rather than an expired session, so it never reaches here.
  if (status === 429) {
    throw new ApiError(
      'Too many attempts. Wait a few minutes and try again.',
      status,
    )
  }
  if (status === 422) {
    throw new ApiError('Enter your passphrase.', status)
  }
  throw new ApiError(
    detailOf(body) ?? 'Could not sign in.',
    status,
    detailOf(body),
  )
}

export async function logout(): Promise<void> {
  await request('/api/logout', { method: 'POST' })
}

/** Resolves when signed in; throws Unauthorized when not. */
export function getSession(): Promise<SessionResponse> {
  return requestOk<SessionResponse>('/api/session')
}

/* ------------------------------------------------------- google connection */

export function getGoogleConnection(): Promise<GoogleConnectionStatus> {
  return requestOk<GoogleConnectionStatus>('/api/google/connection')
}

export function disconnectGoogleConnection(): Promise<{ ok: boolean; status: string }> {
  return requestOk('/api/google/connection/disconnect', { method: 'POST' })
}

/* --------------------------------------------------------------------- feed */

export interface FeedQuery {
  limit?: number
  skill?: string
  kind?: string
  destination?: string
  /** ISO-8601 timestamp; returns events strictly newer. Unused today — we do a
   *  full refetch each poll because the dataset is small and a full page can't
   *  drift out of sync the way an incremental merge can. Kept for when it is. */
  since?: string
}

/** One captured (not sent) mail from the tenant's outbox. Operator only. */
export interface OutboxRow {
  ts: string
  to: string[]
  cc: string[]
  subject: string
  body: string
}
export interface OutboxResponse { rows: OutboxRow[]; count: number; limit: number; active: boolean }

export function getOutbox(limit = 20): Promise<OutboxResponse> {
  return requestOk<OutboxResponse>(`/api/operator/outbox?limit=${limit}`)
}

export function getFeed(q: FeedQuery = {}): Promise<FeedResponse> {
  const params = new URLSearchParams()
  params.set('limit', String(q.limit ?? 50))
  if (q.skill) params.set('skill', q.skill)
  if (q.kind) params.set('kind', q.kind)
  if (q.destination) params.set('destination', q.destination)
  if (q.since) params.set('since', q.since)
  return requestOk<FeedResponse>(`/api/feed?${params.toString()}`)
}

/**
 * Today's counts for the hero.
 *
 * Sends the browser's own IANA zone: "today" needs a timezone, there is no
 * tenant timezone in config, and the device holding the app is the best
 * available authority on what day it is for the person reading it.
 */
export function getFeedSummary(): Promise<FeedSummary> {
  const params = new URLSearchParams()
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (tz) params.set('tz', tz)
  } catch {
    // No Intl or no zone — the server falls back to UTC and says so.
  }
  return requestOk<FeedSummary>(`/api/feed/summary?${params.toString()}`)
}

/* -------------------------------------------------------------- lead writes */
/** App-native leads only. The server answers 409 when the tenant is still
 *  sheet-backed, which the screen prevents by hiding the controls. */
export interface LeadInput {
  name?: string
  email?: string
  phone?: string
  property?: string
  price_point?: string
  source?: string
  notes?: string
  status?: string
  temperature?: string
  follow_up?: string
  next_touch_at?: string
}

export function createLead(input: LeadInput): Promise<{ ok: boolean }> {
  return requestOk('/api/leads', { method: 'POST', body: JSON.stringify(input) })
}

export function patchLead(id: string, input: LeadInput): Promise<{ ok: boolean }> {
  return requestOk('/api/leads/' + encodeURIComponent(id), {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function archiveLead(id: string): Promise<{ ok: boolean }> {
  return requestOk('/api/leads/' + encodeURIComponent(id) + '/archive', { method: 'POST' })
}

/* ------------------------------------------------------------------- health */

/**
 * Returns the report even when the app answers 503 — "degraded" and "fail" are
 * states to render, not errors to swallow. Only 401 throws.
 */
/**
 * Tasks. Every write goes through requestOk, so the server's own sentence
 * arrives as ApiError.message and the screen can show what it actually said —
 * a 403 that reads "That change did not save" tells the person nothing about
 * why, and the backend already wrote a better sentence than we would.
 */
export interface TaskInput {
  title?: string
  detail?: string | null
  assignee?: string | null
  priority?: string
  deadline?: string | null
}

export function getTasks(): Promise<TasksResponse> {
  return requestOk<TasksResponse>('/api/tasks')
}

export function createTask(input: TaskInput): Promise<{ ok: boolean; task: Task }> {
  return requestOk('/api/tasks', { method: 'POST', body: JSON.stringify(input) })
}

export function patchTask(id: string, input: TaskInput): Promise<{ ok: boolean; task: Task }> {
  return requestOk('/api/tasks/' + encodeURIComponent(id), {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function assignTask(id: string, assignee: string | null): Promise<{ ok: boolean; task: Task }> {
  return requestOk('/api/tasks/' + encodeURIComponent(id) + '/assign', {
    method: 'POST',
    body: JSON.stringify({ assignee }),
  })
}

export function setTaskStatus(id: string, status: string): Promise<{ ok: boolean; task: Task }> {
  return requestOk('/api/tasks/' + encodeURIComponent(id) + '/status', {
    method: 'POST',
    body: JSON.stringify({ status }),
  })
}

export function archiveTask(id: string): Promise<{ ok: boolean; task: Task }> {
  return requestOk('/api/tasks/' + encodeURIComponent(id) + '/archive', { method: 'POST' })
}

export async function getHealth(): Promise<HealthResponse> {
  const { body, status } = await request<HealthResponse>('/api/health')
  if (!body) throw new ApiError('Health check unavailable.', status)
  return body
}

/* ---------------------------------------------------------------- approvals */

export function getApprovals(): Promise<ApprovalsResponse> {
  return requestOk<ApprovalsResponse>('/api/approvals')
}

/** Records a decision. 409 means someone already resolved it. */
export async function decideApproval(
  id: string,
  decision: 'approve' | 'reject',
): Promise<Approval> {
  const { ok, status, body } = await request<{ ok: boolean; approval: Approval }>(
    `/api/approvals/${encodeURIComponent(id)}`,
    { method: 'POST', body: JSON.stringify({ decision }) },
  )
  if (ok && body?.approval) return body.approval
  if (status === 409) {
    throw new ApiError('That request has already been decided.', status)
  }
  if (status === 404) {
    throw new ApiError('That request no longer exists.', status)
  }
  throw new ApiError(detailOf(body) ?? 'Could not record that decision.', status)
}

/* -------------------------------------------------------------------- leads */

/**
 * Leads, or a typed failure.
 *
 * The backend answers 503 WITH a body when the sheet is unreachable, the same
 * contract /api/health uses — an empty list would read as "no leads", which is
 * a different and much worse claim than "we couldn't look".
 */
export async function getLeads(): Promise<
  { ok: true; data: LeadsResponse } | { ok: false; error: LeadsError }
> {
  const { ok, status, body } = await request<LeadsResponse & LeadsError>('/api/leads')
  if (ok && body && Array.isArray(body.leads) && !('error' in body)) {
    return { ok: true, data: body as LeadsResponse }
  }
  if (body && 'error' in body) {
    return { ok: false, error: body as LeadsError }
  }
  throw new ApiError('Could not load leads.', status)
}

/* ------------------------------------------------------------------ threads */

export function getThreads(includeArchived = false): Promise<ThreadsResponse> {
  return requestOk<ThreadsResponse>(
    `/api/threads${includeArchived ? '?include_archived=true' : ''}`,
  )
}

export async function createThread(name: string): Promise<Thread> {
  const { ok, status, body } = await request<{ ok: boolean; thread: Thread }>(
    '/api/threads',
    { method: 'POST', body: JSON.stringify({ name }) },
  )
  if (ok && body?.thread) return body.thread
  throw new ApiError(detailOf(body) ?? 'Could not create that channel.', status)
}

export async function renameThread(id: string, name: string): Promise<Thread> {
  const { ok, status, body } = await request<{ ok: boolean; thread: Thread }>(
    `/api/threads/${encodeURIComponent(id)}`,
    { method: 'PATCH', body: JSON.stringify({ name }) },
  )
  if (ok && body?.thread) return body.thread
  // 409 is the direct line refusing; it is a rule, not a failure.
  throw new ApiError(detailOf(body) ?? 'Could not rename that channel.', status)
}

export async function archiveThread(id: string, archived = true): Promise<Thread> {
  const { ok, status, body } = await request<{ ok: boolean; thread: Thread }>(
    `/api/threads/${encodeURIComponent(id)}/archive?archived=${archived}`,
    { method: 'POST' },
  )
  if (ok && body?.thread) return body.thread
  throw new ApiError(detailOf(body) ?? 'Could not archive that channel.', status)
}

/* -------------------------------------------------------------- attachments */

/**
 * Upload one file to a thread.
 *
 * Multipart, so no Content-Type header is set by hand — the browser must
 * generate the boundary. Errors carry the server's own wording (too large,
 * unsupported type), which is written to be shown verbatim.
 */
export async function uploadAttachment(
  threadId: string,
  file: File,
  signal?: AbortSignal,
): Promise<Attachment> {
  const form = new FormData()
  form.append('file', file, file.name)

  let res: Response
  try {
    res = await fetch(`/api/threads/${encodeURIComponent(threadId)}/attachments`, {
      method: 'POST',
      credentials: 'include',
      body: form,
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError('Upload failed — could not reach Jarvis.', 0)
  }

  if (res.status === 401) throw new Unauthorized()

  let body: unknown = null
  const text = await res.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  if (res.ok && body && (body as { attachment?: Attachment }).attachment) {
    return (body as { attachment: Attachment }).attachment
  }
  throw new ApiError(detailOf(body) ?? 'That file could not be uploaded.', res.status)
}

/* ----------------------------------------------------------------- counters */

/**
 * Fire-and-forget usage counter. Never throws and never blocks the UI —
 * telemetry must not be able to break a feature. Deliberately not an activity
 * feed event: the feed is the customer's record of what Jarvis did.
 */
export function bumpCounter(name: 'tts_toggle_on' | 'tts_utterance' | 'ptt_utterance'): void {
  void fetch(`/api/counters/${name}`, { method: 'POST', credentials: 'include' }).catch(() => {})
}

/* --------------------------------------------------------------- transcribe */

export interface TranscriptResult {
  text: string
  duration_s: number
  model: string
  transcribe_s: number
}

/**
 * Push-to-talk: audio in, text out. The server does NOT retain the audio.
 * Errors carry the server's wording (too long, undecodable, no speech), which
 * is written to be shown verbatim.
 */
export async function transcribeAudio(
  blob: Blob,
  signal?: AbortSignal,
): Promise<TranscriptResult> {
  const form = new FormData()
  const ext = blob.type.includes('mp4') ? 'm4a' : blob.type.includes('ogg') ? 'ogg' : 'webm'
  form.append('audio', blob, `note.${ext}`)

  let res: Response
  try {
    res = await fetch('/api/transcribe', {
      method: 'POST',
      credentials: 'include',
      body: form,
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new ApiError('Could not reach Jarvis to transcribe that.', 0)
  }

  if (res.status === 401) throw new Unauthorized()

  let body: unknown = null
  const text = await res.text()
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = null
    }
  }
  if (res.ok && body && typeof (body as TranscriptResult).text === 'string') {
    return body as TranscriptResult
  }
  throw new ApiError(detailOf(body) ?? 'That recording could not be transcribed.', res.status)
}

/* --------------------------------------------------------------------------- chat */

/* ---------------------------------------------------------------- members */

export async function getThreadMembers(threadId: string): Promise<ThreadMember[]> {
  return (
    await requestOk<{ members: ThreadMember[] }>(
      `/api/threads/${encodeURIComponent(threadId)}/members`,
    )
  ).members
}

export async function addThreadMember(
  threadId: string,
  personKey: string,
): Promise<ThreadMember[]> {
  const { ok, status, body } = await request<{ members: ThreadMember[] }>(
    `/api/threads/${encodeURIComponent(threadId)}/members`,
    { method: 'POST', body: JSON.stringify({ person_key: personKey }) },
  )
  if (ok && body?.members) return body.members
  throw new ApiError(detailOf(body) ?? 'Could not add them.', status)
}

export async function removeThreadMember(
  threadId: string,
  personKey: string,
): Promise<ThreadMember[]> {
  const { ok, status, body } = await request<{ members: ThreadMember[] }>(
    `/api/threads/${encodeURIComponent(threadId)}/members/${encodeURIComponent(personKey)}`,
    { method: 'DELETE' },
  )
  if (ok && body?.members) return body.members
  throw new ApiError(detailOf(body) ?? 'Could not remove them.', status)
}

export function getChatHistory(
  threadId: string = DIRECT_THREAD,
): Promise<ChatHistoryResponse> {
  return requestOk<ChatHistoryResponse>(
    `/api/chat/history?thread_id=${encodeURIComponent(threadId)}`,
  )
}

export interface ChatHandlers {
  onDelta?: (text: string) => void
  onTool?: (e: Extract<ChatStreamEvent, { type: 'tool' }>) => void
  onDone?: (e: Extract<ChatStreamEvent, { type: 'done' }>) => void
  onError?: (e: Extract<ChatStreamEvent, { type: 'error' }>) => void
  /** Something was deliberately withheld from the model. */
  onNotice?: (e: Extract<ChatStreamEvent, { type: 'notice' }>) => void
  onVerified?: (e: Extract<ChatStreamEvent, { type: 'verified' }>) => void
  onAction?: (e: Extract<ChatStreamEvent, { type: 'action' }>) => void
}

/**
 * Stream one chat turn.
 *
 * The response is NDJSON — one JSON object per line — rather than SSE, because
 * this has to be a POST (EventSource cannot) and line-delimited JSON avoids
 * SSE's comment and multi-line-data parsing rules on the client. Lines can be
 * split across network chunks, so the tail is buffered until a newline lands.
 *
 * Pass an AbortSignal to cancel. Aborting drops the connection, which the
 * gateway treats as a hard interrupt of the agent run — the right behaviour
 * when the user navigates away mid-answer.
 */
export async function streamChat(
  threadId: string,
  message: string,
  handlers: ChatHandlers,
  signal?: AbortSignal,
  attachmentIds: string[] = [],
): Promise<void> {
  let res: Response
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/x-ndjson' },
      body: JSON.stringify({ thread_id: threadId, message, attachments: attachmentIds }),
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    throw new ApiError(
      'Could not reach Jarvis.',
      0,
      e instanceof Error ? e.message : undefined,
    )
  }

  if (res.status === 401) throw new Unauthorized()
  if (!res.ok || !res.body) {
    let detail: string | undefined
    try {
      detail = detailOf(await res.json())
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail ?? 'Jarvis could not answer.', res.status, detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatch = (line: string) => {
    const trimmed = line.trim()
    if (!trimmed) return
    let evt: ChatStreamEvent
    try {
      evt = JSON.parse(trimmed) as ChatStreamEvent
    } catch {
      return // a truncated line is not worth killing the stream over
    }
    switch (evt.type) {
      case 'delta':
        handlers.onDelta?.(evt.text)
        break
      case 'tool':
        handlers.onTool?.(evt)
        break
      case 'done':
        handlers.onDone?.(evt)
        break
      case 'error':
        handlers.onError?.(evt)
        break
      case 'notice':
        handlers.onNotice?.(evt)
        break
      case 'verified':
        handlers.onVerified?.(evt)
        break
      case 'action':
        handlers.onAction?.(evt)
        break
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let nl: number
      while ((nl = buffer.indexOf('\n')) !== -1) {
        const line = buffer.slice(0, nl)
        buffer = buffer.slice(nl + 1)
        dispatch(line)
      }
    }
    dispatch(buffer) // trailing line with no newline
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') return
    throw e
  }
}
