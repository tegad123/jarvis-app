#!/usr/bin/env node
// Serves the built web app (../../../static) with a stub /api so the screens
// can be photographed without a tenant. No dependencies; Node 20+.
//
//   node ios/reference/web/server.mjs [port]
//
// It binds 127.0.0.1 only, reads nothing but static/ and fixtures.mjs, and
// writes nothing. POST /__stub/set {"role":"owner|admin|member","signedIn":bool}
// switches who the stub says is signed in ("emptyHistory": true empties every
// conversation, for the empty states).
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import * as fx from './fixtures.mjs'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const STATIC = resolve(HERE, '../../../static')
const PORT = Number(process.argv[2] || 8799)

const state = { role: 'owner', signedIn: false, emptyHistory: false }

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.woff2': 'font/woff2',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webmanifest': 'application/manifest+json',
}

function json(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify(body))
}

async function body(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const text = Buffer.concat(chunks).toString('utf8')
  try {
    return text ? JSON.parse(text) : {}
  } catch {
    return {}
  }
}

async function api(req, res, url) {
  const path = url.pathname
  const method = req.method ?? 'GET'

  // Open routes.
  if (path === '/api/auth/methods') return json(res, 200, { pin: true, passphrase: true, google: true })
  if (path === '/api/people') return json(res, 200, { people: fx.PEOPLE })
  if (path === '/api/login' && method === 'POST') {
    const b = await body(req)
    state.signedIn = true
    state.role = b.person_key === 'omar' ? 'member' : b.person_key === 'priya' ? 'admin' : 'owner'
    return json(res, 200, { ok: true, session: { iat: 0 }, person_key: b.person_key ?? null, role: state.role, expires_in: 43200 })
  }
  if (path === '/api/logout') {
    state.signedIn = false
    return json(res, 200, { ok: true })
  }

  if (!state.signedIn) return json(res, 401, { detail: 'Not authenticated.' })

  if (path === '/api/session') return json(res, 200, fx.session(state.role))
  if (path === '/api/team/directory') return json(res, 200, { people: fx.PEOPLE })
  if (path === '/api/threads') return json(res, 200, fx.threads(state.role))
  if (path === '/api/chat/history') return json(res, 200, fx.history(state.emptyHistory ? '' : url.searchParams.get('thread_id') ?? ''))
  if (/^\/api\/threads\/[^/]+\/members$/.test(path)) return json(res, 200, fx.members(decodeURIComponent(path.split('/')[3])))
  if (/^\/api\/threads\/[^/]+\/read$/.test(path)) return json(res, 200, { ok: true, last_read_id: null })
  if (path === '/api/feed') return json(res, 200, fx.feed())
  if (path === '/api/feed/summary') return json(res, 200, fx.feedSummary())
  if (path === '/api/approvals') return json(res, 200, fx.approvals())
  if (path === '/api/inbox') return json(res, 200, fx.inbox())
  if (path === '/api/tasks') return json(res, 200, fx.tasks())
  if (path === '/api/leads') return json(res, 200, fx.leads(state.role))
  if (/^\/api\/leads\/[^/]+$/.test(path)) return json(res, 200, fx.lead(decodeURIComponent(path.split('/')[3])))
  if (path === '/api/google/connection') return json(res, 200, fx.googleConnection())
  if (path === '/api/browser/status') return json(res, 200, { enabled: true, running: false, taken_over: false, access: [] })
  if (path === '/api/browser/screen') return json(res, 200, { running: false, taken_over: false })
  if (path === '/api/browser/logins') return json(res, 200, fx.websiteLogins())
  if (path === '/api/imessage/status') return json(res, 200, fx.macStatus())
  if (path === '/api/team') return json(res, 200, fx.team())
  if (path === '/api/channels/settings') return json(res, 200, fx.channelSettings())
  if (path.startsWith('/api/counters/')) return json(res, 200, { ok: true })

  if (path === '/api/chat' && method === 'POST') {
    // One short stubbed turn, as NDJSON, so the streaming state can be seen.
    res.writeHead(200, { 'Content-Type': 'application/x-ndjson', 'Cache-Control': 'no-store' })
    const lines = [
      { type: 'tool', status: 'running', tool: 'jarvis', label: 'Checking your calendar', toolCallId: 's1' },
      { type: 'tool', status: 'completed', tool: 'jarvis', toolCallId: 's1' },
      { type: 'delta', text: 'You have a walkthrough at 40 Marsh Lane on Friday at 2:00 PM, ' },
      { type: 'delta', text: 'and nothing else booked this week.' },
      { type: 'done', finish_reason: 'stop' },
    ]
    for (const l of lines) {
      res.write(JSON.stringify(l) + '\n')
      await new Promise((r) => setTimeout(r, 150))
    }
    return res.end()
  }

  return json(res, 404, { detail: `The stub has no ${method} ${path}.` })
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${PORT}`)
  try {
    if (url.pathname === '/__stub/set' && req.method === 'POST') {
      Object.assign(state, await body(req))
      return json(res, 200, state)
    }
    if (url.pathname.startsWith('/api/')) return await api(req, res, url)

    const rel = normalize(url.pathname).replace(/^(\.\.[/\\])+/, '')
    let file = join(STATIC, rel === '/' ? 'index.html' : rel)
    if (!file.startsWith(STATIC)) file = join(STATIC, 'index.html')
    let data
    try {
      data = await readFile(file)
    } catch {
      file = join(STATIC, 'index.html')
      data = await readFile(file)
    }
    // sw.js is left out on purpose: a cached shell would outlive the stub.
    if (url.pathname === '/sw.js') {
      res.writeHead(404)
      return res.end()
    }
    res.writeHead(200, { 'Content-Type': TYPES[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' })
    res.end(data)
  } catch (err) {
    json(res, 500, { detail: String(err) })
  }
}).listen(PORT, '127.0.0.1', () => {
  console.log(`web reference stub on http://127.0.0.1:${PORT} (static: ${STATIC})`)
})
