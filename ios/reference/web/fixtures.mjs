// Stub data for the web reference screenshots. Everything here is invented:
// no tenant, person, address, phone number or message is real. Addresses use
// the reserved .example domain and phone numbers the 555-01xx fiction range.
//
// The shapes follow frontend/src/lib/types.ts, which is the contract the web
// app reads. The SwiftUI previews (ios/JarvisApp/JarvisApp/Preview) carry the
// same cast and the same rows, so a web screenshot and a preview line up.

const ago = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString()
const day = (offset) => {
  const d = new Date(Date.now() + offset * 86_400_000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export const PEOPLE = [
  { person_key: 'dana', name: 'Dana Whitfield', title: 'Broker (owner)' },
  { person_key: 'priya', name: 'Priya Raman', title: 'Operations' },
  { person_key: 'omar', name: 'Omar Haddad', title: 'Agent' },
]

const FEATURES = { ask: true, leads: true, tasks: true, send: true, browser: true, imessage: true }

export function session(role) {
  const who = role === 'member' ? PEOPLE[2] : role === 'admin' ? PEOPLE[1] : PEOPLE[0]
  return {
    ok: true,
    authenticated: true,
    issued_at: Math.floor(Date.now() / 1000),
    display_name: who.name,
    person_key: who.person_key,
    role,
    features: FEATURES,
  }
}

export function threads(role) {
  const all = [
    {
      thread_id: 'direct-dana',
      name: 'Ask Jarvis',
      created_by: 'user',
      created_at: ago(60 * 24 * 30),
      last_message_at: ago(12),
      archived: false,
      is_direct: true,
      preview: 'Two need you today: the Alder Ave inspection reply and the Marsh counter-offer.',
      preview_role: 'assistant',
      unread: false,
      members: [],
      category_id: null,
    },
    {
      thread_id: 'ch-alder',
      name: '128 Alder Ave',
      created_by: 'jarvis',
      created_at: ago(60 * 24 * 6),
      last_message_at: ago(34),
      archived: false,
      is_direct: false,
      preview: '@Dana the inspector can do Thursday at 10.',
      preview_role: 'user',
      preview_mine: false,
      preview_author_name: 'Omar',
      unread: true,
      unread_count: 2,
      mentions: 1,
      members: ['dana', 'priya', 'omar'],
      category_id: 'cat-listings',
    },
    {
      thread_id: 'ch-marsh',
      name: '40 Marsh Lane',
      created_by: 'user',
      created_at: ago(60 * 24 * 12),
      last_message_at: ago(60 * 5),
      archived: false,
      is_direct: false,
      preview: 'Counter-offer drafted. It is waiting in Approvals.',
      preview_role: 'assistant',
      unread: true,
      unread_count: 1,
      mentions: 0,
      members: ['dana', 'omar'],
      category_id: 'cat-listings',
    },
    {
      thread_id: 'ch-buyers',
      name: 'Okafor family',
      created_by: 'user',
      created_at: ago(60 * 24 * 20),
      last_message_at: ago(60 * 24 * 4),
      archived: false,
      is_direct: false,
      preview: 'Sent them the three listings near the school.',
      preview_role: 'user',
      preview_mine: true,
      unread: false,
      members: ['dana', 'priya'],
      category_id: 'cat-buyers',
    },
    {
      thread_id: 'ch-team',
      name: 'Team',
      created_by: 'user',
      created_at: ago(60 * 24 * 40),
      last_message_at: ago(60 * 26),
      archived: false,
      is_direct: false,
      preview: 'Open house sign-in sheet is in the drive.',
      preview_role: 'user',
      preview_mine: false,
      preview_author_name: 'Priya',
      unread: false,
      members: ['dana', 'priya', 'omar'],
      category_id: null,
    },
  ]
  const mine = role === 'member' ? all.filter((t) => t.is_direct || (t.members ?? []).includes('omar')) : all
  return {
    threads: mine,
    count: mine.length,
    counts: { active: mine.length, archived: 0, channels: mine.length - 1, messages: 48 },
    bodies_visible: true,
    categories: [
      { category_id: 'cat-listings', name: 'Listings', position: 0 },
      { category_id: 'cat-buyers', name: 'Buyers', position: 1 },
    ],
    jarvis_replies: 'mention',
  }
}

export function history(threadId) {
  if (threadId === 'ch-alder') {
    const messages = [
      { id: 201, role: 'user', author: 'priya', author_name: 'Priya', content: 'Seller signed the disclosure. Uploading it now.', ts: Date.now() / 1000 - 3 * 3600 },
      { id: 202, role: 'user', author: 'dana', author_name: 'Dana', content: '@jarvis when is the inspection window?', ts: Date.now() / 1000 - 2.5 * 3600 },
      {
        id: 203,
        role: 'assistant',
        content: 'The contract gives ten days from acceptance, so the window closes on the 21st. Nothing is booked yet.',
        ts: Date.now() / 1000 - 2.4 * 3600,
      },
      { id: 204, role: 'user', author: 'omar', author_name: 'Omar', content: '@Dana the inspector can do Thursday at 10.', ts: Date.now() / 1000 - 34 * 60 },
    ]
    return { thread_id: threadId, messages, count: messages.length, last_id: 204, jarvis_answering: [] }
  }
  if (threadId === 'direct-dana') {
    const messages = [
      { id: 101, role: 'user', author: 'dana', author_name: 'Dana', content: 'Which emails need me today?', ts: Date.now() / 1000 - 14 * 60 },
      {
        id: 102,
        role: 'assistant',
        content:
          'Two need you today: the Alder Ave inspection reply and the Marsh counter-offer.\n\nThe rest are filed. I drafted a reply to the inspector and left it in Approvals.',
        ts: Date.now() / 1000 - 13 * 60,
      },
      { id: 103, role: 'user', author: 'dana', author_name: 'Dana', content: 'Remind me to call the Okafors tomorrow at 9.', ts: Date.now() / 1000 - 12 * 60 },
      { id: 104, role: 'assistant', content: 'Done. It is on your list for tomorrow at 9:00 AM.', ts: Date.now() / 1000 - 12 * 60 },
    ]
    return { thread_id: threadId, messages, count: messages.length, last_id: 104, jarvis_answering: [] }
  }
  return { thread_id: threadId, messages: [], count: 0, last_id: 0, jarvis_answering: [] }
}

export function members(threadId) {
  const t = threads('owner').threads.find((x) => x.thread_id === threadId)
  return { members: (t?.members ?? []).map((k) => ({ person_key: k, added_by: 'dana', added_at: ago(60 * 24 * 6) })) }
}

export function feed() {
  const events = [
    {
      ts: ago(8),
      tenant: 'sample',
      skill: 'email-triage',
      destination: 'emails',
      kind: 'approval.requested',
      message: 'A reply to the inspector is waiting for you.',
      headline: 'A reply to the inspector is waiting for your approval.',
      detail: 'Draft reply · thread "Inspection times for 128 Alder Ave"',
      attachments: [{ type: 'email', subject: 'Inspection times for 128 Alder Ave' }],
    },
    {
      ts: ago(31),
      tenant: 'sample',
      skill: 'email-triage',
      destination: 'emails',
      kind: 'email.classified',
      message: 'Filed 5 emails.',
      headline: 'Filed five emails from this morning. Two need you.',
      attachments: [],
    },
    {
      ts: ago(74),
      tenant: 'sample',
      skill: 'tasks',
      destination: 'tasks',
      kind: 'task.created',
      message: 'Task created.',
      headline: 'Added "Call the Okafors" to your list for tomorrow.',
      attachments: [{ type: 'task', title: 'Call the Okafors' }],
    },
    {
      ts: ago(60 * 5),
      tenant: 'sample',
      skill: 'sweeps',
      destination: 'ops',
      kind: 'lead.quiet',
      message: 'Lead going quiet.',
      headline: 'Renee Castillo has not heard from anyone in nine days.',
      detail: 'Last contact: a text on the 3rd. No reply logged.',
      attachments: [],
    },
    {
      ts: ago(60 * 9),
      tenant: 'sample',
      skill: 'tasks',
      destination: 'tasks',
      kind: 'task.completed',
      message: 'Task completed.',
      headline: 'Priya finished "Order the Marsh Lane sign".',
      attachments: [],
    },
    {
      ts: ago(60 * 27),
      tenant: 'sample',
      skill: 'meeting-intake',
      destination: 'ops',
      kind: 'meeting.filed',
      message: 'Meeting filed.',
      headline: 'Filed the notes from Monday\'s team meeting. Three tasks came out of it.',
      attachments: [],
    },
  ]
  return { events, count: events.length, total: events.length, order: 'newest_first', feed_path: '' }
}

export function feedSummary() {
  return {
    date: day(0),
    timezone: 'America/Chicago',
    total: 9,
    counts: { 'email.classified': 5, 'task.created': 2, 'approval.requested': 1, 'task.completed': 1 },
    feed_total: 64,
    generated_at: ago(0),
  }
}

export function approvals() {
  const list = [
    {
      id: 'apr-1',
      kind: 'action',
      ts: ago(8),
      tenant: 'sample',
      skill: 'email-triage',
      summary: 'Reply to the inspector about Thursday',
      payload: { type: 'email_send' },
      display: {
        to: 'inspections@harborhome.example',
        subject: 'Re: Inspection times for 128 Alder Ave',
        message: 'Thursday at 10 works for us. The lockbox code will be sent the morning of.\n\nThanks,\nDana',
      },
      status: 'pending',
      can_approve: true,
      can_reject: true,
    },
    {
      id: 'apr-2',
      kind: 'config-change',
      ts: ago(60 * 3),
      tenant: 'sample',
      skill: 'digest',
      summary: 'Move the morning digest to 8 AM',
      payload: {
        changes: [{ key: 'digest.times', label: 'Digest times', current: ['5:00 AM', '3:00 PM'], proposed: ['8:00 AM', '3:00 PM'] }],
      },
      status: 'pending',
      can_approve: true,
      can_reject: true,
    },
    {
      id: 'apr-3',
      kind: 'action',
      ts: ago(60 * 30),
      tenant: 'sample',
      skill: 'calendar',
      summary: 'Add the Marsh Lane walkthrough to your calendar',
      payload: {},
      display: { title: 'Walkthrough · 40 Marsh Lane', starts: 'Friday 2:00 PM' },
      status: 'approved',
      decided_by: 'Dana',
      decided_at: ago(60 * 29),
    },
  ]
  return {
    approvals: list,
    count: list.length,
    counts: { pending: 2, approved: 1, rejected: 0 },
    requests_path: '',
  }
}

export function inbox() {
  const messages = [
    {
      id: 'm1',
      from: 'Harbor Home Inspections',
      subject: 'Inspection times for 128 Alder Ave',
      date: ago(42),
      unread: true,
      snippet: 'We have Thursday at 10 or Friday at 2 open. Let us know which works.',
      handling: 'Drafted a reply. It is waiting in Approvals.',
    },
    {
      id: 'm2',
      from: 'Renee Castillo',
      subject: 'Still thinking about Marsh Lane',
      date: ago(60 * 4),
      unread: true,
      snippet: 'Could we see it once more this weekend before we decide?',
    },
    {
      id: 'm3',
      from: 'County Recorder',
      subject: 'Document recorded',
      date: ago(60 * 26),
      unread: false,
      snippet: 'Your document has been recorded. A copy is attached.',
      handling: 'Filed under 40 Marsh Lane.',
    },
  ]
  return { status: 'connected', mailbox: 'jarvis@sample-brokerage.example', unread: 2, messages, notice: null }
}

export function tasks() {
  const list = [
    {
      task_id: 't1',
      title: 'Reply to the inspector about Thursday',
      detail: 'Draft is in Approvals.',
      assignee: 'dana',
      assignee_name: 'Dana',
      created_by: 'jarvis',
      created_by_name: 'Jarvis',
      source: 'email',
      source_ref: null,
      priority: 'high',
      deadline: day(-1),
      status: 'open',
      stale: false,
      is_overdue: true,
      days_open: 2,
      due_label: 'Due yesterday',
      created_at: ago(60 * 48),
      updated_at: ago(60 * 3),
      completed_at: null,
    },
    {
      task_id: 't2',
      title: 'Call the Okafors',
      detail: null,
      assignee: 'dana',
      assignee_name: 'Dana',
      created_by: 'jarvis',
      created_by_name: 'Jarvis',
      source: 'chat',
      source_ref: null,
      priority: 'medium',
      deadline: day(1),
      status: 'open',
      stale: false,
      is_overdue: false,
      days_open: 0,
      due_label: 'Due tomorrow',
      created_at: ago(74),
      updated_at: ago(74),
      completed_at: null,
    },
    {
      task_id: 't3',
      title: 'Photograph 40 Marsh Lane',
      detail: 'Twilight shots for the listing.',
      assignee: 'omar',
      assignee_name: 'Omar',
      created_by: 'dana',
      created_by_name: 'Dana',
      source: 'manual',
      source_ref: null,
      priority: 'medium',
      deadline: null,
      status: 'in_progress',
      stale: true,
      is_overdue: false,
      days_open: 9,
      due_label: '',
      created_at: ago(60 * 24 * 9),
      updated_at: ago(60 * 24 * 6),
      completed_at: null,
    },
    {
      task_id: 't4',
      title: 'Order the Marsh Lane sign',
      detail: null,
      assignee: 'priya',
      assignee_name: 'Priya',
      created_by: 'dana',
      created_by_name: 'Dana',
      source: 'meeting',
      source_ref: null,
      priority: 'low',
      deadline: null,
      status: 'done',
      stale: false,
      is_overdue: false,
      days_open: 3,
      due_label: '',
      created_at: ago(60 * 24 * 3),
      updated_at: ago(60 * 9),
      completed_at: ago(60 * 9),
    },
  ]
  return {
    tasks: list,
    count: list.length,
    counts: { open: 2, in_progress: 1, done: 1, stale: 1, overdue: 1, archived: 0 },
    source: 'store',
    bodies_visible: true,
    timezone: 'America/Chicago',
  }
}

const LEADS = [
  {
    lead_id: 'ld-1',
    name: 'Renee Castillo',
    property: '40 Marsh Lane',
    price: '$640k',
    date: 'Nov 2',
    temperature: 'Hot',
    temperature_rank: 3,
    status: 'No Response',
    pipeline: 'Contacted',
    follow_up: 'Texted 11/3',
    next_follow_up: day(-2),
    last_nudge: '',
    agent: 'Omar',
    tab: 'omar',
    is_new: false,
    is_hot: true,
    needs_touch: true,
    created_by: 'user',
  },
  {
    lead_id: 'ld-2',
    name: 'Marcus Bell',
    property: '128 Alder Ave',
    price: '$515k',
    date: 'Nov 9',
    temperature: 'Warm',
    temperature_rank: 2,
    status: 'Responded',
    pipeline: 'Touring',
    follow_up: '',
    next_follow_up: day(0),
    last_nudge: '',
    agent: 'Dana',
    tab: 'dana',
    is_new: false,
    is_hot: false,
    needs_touch: false,
    created_by: 'user',
  },
  {
    lead_id: 'ld-3',
    name: 'Ife Okafor',
    property: 'Near Lincoln Elementary',
    price: '$450–500k',
    date: 'Nov 11',
    temperature: 'Warm',
    temperature_rank: 2,
    status: 'Scheduled',
    pipeline: 'New',
    follow_up: '',
    next_follow_up: day(3),
    last_nudge: '',
    agent: 'Dana',
    tab: 'dana',
    is_new: true,
    is_hot: false,
    needs_touch: false,
    created_by: 'jarvis',
  },
  {
    lead_id: 'ld-4',
    name: 'Tom Albright',
    property: '40 Marsh Lane',
    price: '',
    date: 'Oct 28',
    temperature: 'Cold',
    temperature_rank: 1,
    status: 'No Response',
    pipeline: 'Contacted',
    follow_up: 'Emailed 10/29',
    next_follow_up: day(0),
    last_nudge: '',
    agent: 'Omar',
    tab: 'omar',
    is_new: false,
    is_hot: false,
    needs_touch: true,
    created_by: 'user',
  },
]

export function leads(role) {
  const list = role === 'member' ? LEADS.filter((l) => l.tab === 'omar') : LEADS
  return {
    leads: list,
    count: list.length,
    source: 'store',
    tabs: ['dana', 'omar'],
    sheet_id: '',
    sheet_title: '',
    counts: { all: list.length, needs_touch: 2, hot: 1, new: 1 },
    today: day(0),
    cadence: { hot: 1, warm: 2, other: 3, later: [7, 14, 30] },
  }
}

export function lead(id) {
  const base = LEADS.find((l) => l.lead_id === id) ?? LEADS[0]
  return {
    lead: {
      ...base,
      email: 'renee.castillo@mail.example',
      phone: '(555) 010-0142',
      source: 'Open house',
      owner_person: base.tab,
      notes: 'Wants a yard for the dog. Pre-approved, flexible on closing date.',
    },
    timeline: [
      { touch_id: 'tc-1', lead_id: id, at: ago(60 * 24 * 11), by_person: 'omar', by_name: 'Omar', kind: 'created', note: null, reached: null, next_touch_at: null },
      { touch_id: 'tc-2', lead_id: id, at: ago(60 * 24 * 10), by_person: 'omar', by_name: 'Omar', kind: 'call', note: 'Left a voicemail.', reached: false, next_touch_at: day(-9) },
      { touch_id: 'tc-3', lead_id: id, at: ago(60 * 24 * 9), by_person: 'omar', by_name: 'Omar', kind: 'text', note: 'Sent the floor plan. She asked about the HOA fee.', reached: true, next_touch_at: day(-2) },
    ],
    today: day(0),
  }
}

export function googleConnection() {
  return {
    status: 'connected',
    google_email: 'dana@sample-brokerage.example',
    connected_at: ago(60 * 24 * 20),
    refreshed_at: ago(60 * 2),
  }
}

export function websiteLogins() {
  return {
    logins: [
      { site: 'mls.example', username: 'dana@sample-brokerage.example', updated_at: ago(60 * 24 * 14), last_used_at: ago(60 * 24 * 2) },
      { site: 'showings.example', username: 'dwhitfield', updated_at: ago(60 * 24 * 3), last_used_at: null },
    ],
  }
}

export function macStatus() {
  return {
    devices: [{ device_id: 'mac-1', name: "Dana's MacBook Air", created_at: ago(60 * 24 * 9), last_sync_at: ago(1) }],
    messages: 1284,
    retention_days: 30,
    mac_app: true,
    reminder: {
      enabled: true,
      times_local: ['09:00', '16:30'],
      older_than_hours: 4,
      lookback_days: 7,
      to: 'dana@sample-brokerage.example',
      google_status: 'connected',
      send_on: true,
      last: null,
    },
  }
}

export function team() {
  const f = (over = {}) => ({ ...FEATURES, ...over })
  return {
    people: [
      {
        person_key: 'dana',
        name: 'Dana',
        full_name: 'Dana Whitfield',
        title: 'Broker',
        role: 'owner',
        email: 'dana@sample-brokerage.example',
        disabled: false,
        invited: false,
        has_account: true,
        last_login: ago(12),
        features: f(),
      },
      {
        person_key: 'priya',
        name: 'Priya',
        full_name: 'Priya Raman',
        title: 'Operations',
        role: 'admin',
        email: 'priya@sample-brokerage.example',
        disabled: false,
        invited: false,
        has_account: true,
        last_login: ago(60 * 20),
        features: f(),
      },
      {
        person_key: 'omar',
        name: 'Omar',
        full_name: 'Omar Haddad',
        title: 'Agent',
        role: 'member',
        email: 'omar@sample-brokerage.example',
        disabled: false,
        invited: true,
        has_account: true,
        last_login: null,
        features: f({ send: false, browser: false }),
      },
    ],
    features: ['ask', 'leads', 'tasks', 'send', 'browser', 'imessage'],
    feature_labels: { ask: 'Ask Jarvis', leads: 'Leads', tasks: 'Tasks', send: 'Sending', browser: 'Browser', imessage: 'iMessage' },
    can_invite_admins: true,
    invites_ready: true,
  }
}

export function channelSettings() {
  return { jarvis_replies: 'mention', updated_by: 'dana', updated_at: ago(60 * 24 * 5), can_change: true }
}
