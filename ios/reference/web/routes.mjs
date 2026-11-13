// What the stub answers for each signed-in GET, and the one stubbed chat
// turn. server.mjs serves these to the web app; export-swift-fixtures.mjs
// writes the same answers into the SwiftUI previews.
import * as fx from './fixtures.mjs'

/** The answer to a signed-in GET, or null when the stub has none. */
export function get(path, search, state) {
  if (path === '/api/session') return fx.session(state.role)
  if (path === '/api/auth/methods') return { pin: true, passphrase: true, google: true }
  if (path === '/api/people') return { people: fx.PEOPLE }
  if (path === '/api/team/directory') return { people: fx.PEOPLE }
  if (path === '/api/threads') return fx.threads(state.role)
  if (path === '/api/chat/history') return fx.history(state.emptyHistory ? '' : search.get('thread_id') ?? '')
  if (/^\/api\/threads\/[^/]+\/members$/.test(path)) return fx.members(decodeURIComponent(path.split('/')[3]))
  if (path === '/api/feed') return fx.feed()
  if (path === '/api/feed/summary') return fx.feedSummary()
  if (path === '/api/approvals') return fx.approvals()
  if (path === '/api/inbox') return fx.inbox()
  if (path === '/api/tasks') return fx.tasks()
  if (path === '/api/leads') return fx.leads(state.role)
  if (/^\/api\/leads\/[^/]+$/.test(path)) return fx.lead(decodeURIComponent(path.split('/')[3]))
  if (path === '/api/google/connection') return fx.googleConnection()
  if (path === '/api/browser/status') return { enabled: true, running: false, taken_over: false, access: [] }
  if (path === '/api/browser/screen') return { running: false, taken_over: false }
  if (path === '/api/browser/logins') return fx.websiteLogins()
  if (path === '/api/imessage/status') return fx.macStatus()
  if (path === '/api/team') return fx.team()
  if (path === '/api/channels/settings') return fx.channelSettings()
  return null
}

/** Every GET a screen makes, for the Swift export. */
export function paths(role) {
  const threads = fx.threads(role).threads
  const leads = fx.leads(role).leads
  return [
    '/api/session',
    '/api/auth/methods',
    '/api/people',
    '/api/team/directory',
    '/api/threads',
    ...threads.map((t) => `/api/chat/history?thread_id=${t.thread_id}`),
    ...threads.filter((t) => !t.is_direct).map((t) => `/api/threads/${t.thread_id}/members`),
    '/api/feed',
    '/api/feed/summary',
    '/api/approvals',
    '/api/inbox',
    '/api/tasks',
    '/api/leads',
    ...leads.map((l) => `/api/leads/${l.lead_id}`),
    '/api/google/connection',
    '/api/browser/status',
    '/api/browser/logins',
    '/api/imessage/status',
    '/api/team',
    '/api/channels/settings',
  ]
}

/** One short turn, so the streaming state can be seen. */
export const CHAT_LINES = [
  { type: 'tool', status: 'running', tool: 'jarvis', label: 'Checking your calendar', toolCallId: 's1' },
  { type: 'tool', status: 'completed', tool: 'jarvis', toolCallId: 's1' },
  { type: 'delta', text: 'You have a walkthrough at 40 Marsh Lane on Friday at 2:00 PM, ' },
  { type: 'delta', text: 'and nothing else booked this week.' },
  { type: 'done', finish_reason: 'stop' },
]
