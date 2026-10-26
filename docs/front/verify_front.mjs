/**
 * The front door, Google-only sign-in, invite sign-up and Jarvis for Mac,
 * end to end on the sandbox. Ledger, not screens: every claim is checked in
 * the front door's SQLite, the account's counters or its message table, and
 * each "refused" has a twin that shows the same thing allowed elsewhere.
 *
 *   VERIFY_OAUTH_FRONT=http://join.front.test:8719 VERIFY_TENANT_SLUG=verify VERIFY_SIGNIN=google \
 *   VERIFY_PUBLIC_ORIGIN=http://verify.front.test:8719 bash docs/tasks/tasks_sandbox.sh start
 *   bash docs/front/front_sandbox.sh start
 *   node docs/front/verify_front.mjs [--canary]
 *
 * Canary: start the sandbox WITHOUT VERIFY_SIGNIN and VERIFY_OAUTH_FRONT (keep
 * VERIFY_PUBLIC_ORIGIN) and pass --canary, which also points the Mac app at a
 * path that is not Jarvis. The Google-only, callback and pairing checks must FAIL.
 */
import { chromium } from '/usr/local/lib/hermes-agent/node_modules/playwright/index.mjs'
import { execFileSync, spawnSync } from 'node:child_process'
import { mkdtempSync, readFileSync, writeFileSync, existsSync, statSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')
const CANARY = process.argv.includes('--canary')
const PY = '/opt/jarvis/venv/bin/python'
const PORT = 8719
const ACCT = `http://verify.front.test:${PORT}`
const JOIN = `http://join.front.test:${PORT}`
const DIRECT = 'http://127.0.0.1:8711'
const SB = process.env.TASKS_SANDBOX || '/tmp/claude-0/-root/tasks-sandbox'
const FRONT_DB = '/tmp/front-verify/front.db'
const RESOLVE = ['verify', 'join', 'nope'].flatMap((h) => ['--resolve', `${h}.front.test:${PORT}:127.0.0.1`])
const PINS = JSON.parse(readFileSync('/opt/jarvis-verify-fixture/SEEDED_PINS.json', 'utf8'))
const pinOf = (who) => (typeof PINS[who] === 'object' ? PINS[who].pin : PINS[who])
const DANA_EMAIL = 'dana@testco.example.com'

const results = []
const ck = (name, cond, extra = '') => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`)
  results.push(!!cond)
}

function sqlite(db, query, params = []) {
  const out = execFileSync('python3', ['-c', `
import json, sqlite3, sys
c = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)
print(json.dumps(c.execute(sys.argv[2], json.loads(sys.argv[3])).fetchall()))`, db, query, JSON.stringify(params)])
  return JSON.parse(out.toString())
}
const counter = (name) => (sqlite(join(SB, 'threads.db'), 'SELECT value FROM counters WHERE name=?', [name])[0] || [0])[0]
const frontCounter = (name) => (sqlite(FRONT_DB, 'SELECT n FROM counters WHERE name=?', [name])[0] || [0])[0]
const danaMessages = () => sqlite(join(SB, 'accounts.db'), "SELECT count(*) FROM imessages WHERE person_key='dana'")[0][0]

function curl(url, { method = 'GET', headers = {}, body, jar } = {}) {
  const args = ['-s', ...RESOLVE, '-o', '-', '-w', '\n%{http_code} %{redirect_url}', '-X', method]
  for (const [k, v] of Object.entries(headers)) args.push('-H', `${k}: ${v}`)
  if (body !== undefined) args.push('-H', 'Content-Type: application/json', '--data-binary', JSON.stringify(body))
  if (jar) args.push('-b', jar, '-c', jar)
  args.push(url)
  const out = execFileSync('curl', args, { maxBuffer: 20e6 }).toString('latin1')
  const cut = out.lastIndexOf('\n')
  const [status, redirect] = out.slice(cut + 1).split(' ')
  return { status: Number(status), redirect: redirect || '', text: out.slice(0, cut) }
}
const json = (r) => {
  try {
    return JSON.parse(r.text)
  } catch {
    return null
  }
}


// Same-origin calls from inside the page: the page's resolver maps *.front.test.
async function api(page, path, { method = 'GET', body } = {}) {
  return page.evaluate(async ([path, method, body]) => {
    const r = await fetch(path, { method, credentials: 'same-origin', headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined })
    const buf = new Uint8Array(await r.arrayBuffer())
    let bin = ''
    for (const b of buf) bin += String.fromCharCode(b)
    return { status: r.status, type: r.headers.get('content-type') || '', b64: btoa(bin) }
  }, [path, method, body])
}
const apiJson = async (page, path, opts) => {
  const r = await api(page, path, opts)
  try {
    return { status: r.status, body: JSON.parse(Buffer.from(r.b64, 'base64').toString()) }
  } catch {
    return { status: r.status, body: null }
  }
}
// The mock Google pages stop and ask which account (as accounts.google.com
// would); the test answers by adding it to the URL, then the chain runs on.
async function googleAs(page, email, extra = {}) {
  await page.waitForURL(/mock\/authorize/, { timeout: 8000 })
  const u = new URL(page.url())
  u.searchParams.set('email', email)
  for (const [k, v] of Object.entries(extra)) u.searchParams.set(k, v)
  await page.goto(u.toString(), { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
}

const browser = await chromium.launch({ args: [`--host-resolver-rules=MAP *.front.test 127.0.0.1`] })
try {
  console.log('front door: routing')
  let n0 = frontCounter('front_unknown_host')
  let r = curl(`http://nope.front.test:${PORT}/`)
  ck('an address with no account is a 404 page', r.status === 404 && r.text.includes('No Jarvis at this address.'), `HTTP ${r.status}`)
  ck('   and is counted', frontCounter('front_unknown_host') === n0 + 1)
  r = curl(`${ACCT}/api/ping`)
  ck("the account's address reaches its app", r.status === 200, `HTTP ${r.status}`)
  n0 = frontCounter('front_internal_refused')
  const internal = [curl(`${ACCT}/api/broker/counters`, { method: 'POST', body: {} }), curl(`${ACCT}/api/model/anthropic/v1/messages`, { method: 'POST', body: {} })]
  ck('agent-only routes are refused from the public side', internal.every((x) => x.status === 404), internal.map((x) => x.status).join(','))
  ck('   and counted', frontCounter('front_internal_refused') === n0 + 2)
  r = curl(`${DIRECT}/api/broker/counters`, { method: 'POST', body: {} })
  ck('   twin: the same route on loopback is the app answering (not 404)', r.status !== 404, `HTTP ${r.status}`)

  console.log('\nGoogle-only on the public side')
  const pub = json(curl(`${ACCT}/api/auth/methods`))
  const loc = json(curl(`${DIRECT}/api/auth/methods`))
  ck('public: Google only', pub && pub.pin === false && pub.passphrase === false && pub.google === true, JSON.stringify(pub))
  ck('   twin, loopback: PIN and passphrase too', loc && loc.pin === true && loc.passphrase === true, JSON.stringify(loc))
  n0 = counter('signin_secret_refused_public')
  r = curl(`${ACCT}/api/login`, { method: 'POST', body: { person_key: 'dana', pin: pinOf('dana') } })
  ck('a right PIN from the public side is refused', r.status === 403 && (json(r) || {}).detail === 'Sign in with Google on this address.', `HTTP ${r.status}`)
  ck('   counted', counter('signin_secret_refused_public') === n0 + 1)
  r = curl(`${ACCT}/api/login`, { method: 'POST', body: { passphrase: readFileSync('/tmp/vpg-operator.pass', 'utf8').trim() } })
  ck('the operator passphrase too', r.status === 403, `HTTP ${r.status}`)
  r = curl(`${ACCT}/api/login`, { method: 'POST', headers: { 'X-Forwarded-For': '' }, body: { person_key: 'dana', pin: pinOf('dana') } })
  ck('   (an empty X-Forwarded-For from outside does not make it local)', r.status === 403, `HTTP ${r.status}`)
  r = curl(`${DIRECT}/api/login`, { method: 'POST', body: { person_key: 'dana', pin: pinOf('dana') } })
  ck('   twin: the same PIN on loopback signs in', r.status === 200, `HTTP ${r.status}`)
  ck('the public sign-in screen lists nobody', JSON.stringify(json(curl(`${ACCT}/api/people`))) === '{"people":[]}')
  ck('   twin: loopback lists the team', ((json(curl(`${DIRECT}/api/people`)) || {}).people || []).length >= 3)

  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await ctx.newPage()
  const seen = []
  page.on('request', (q) => seen.push(q.url()))
  await page.goto(ACCT, { waitUntil: 'networkidle' })
  ck('the sign-in screen offers Google', (await page.getByRole('button', { name: 'Continue with Google' }).count()) === 1)
  ck('   and no PIN or operator access', (await page.getByRole('button', { name: /Sign in with PIN|Operator access/ }).count()) === 0)
  await page.screenshot({ path: join(HERE, 'shots', 'account-signin.png') })

  n0 = frontCounter('front_callback_forwarded')
  await page.getByRole('button', { name: 'Continue with Google' }).click()
  await googleAs(page, DANA_EMAIL)
  const bounced = seen.find((u) => u.startsWith(`${JOIN}/oauth/callback?`))
  ck("Google answers the front door's one callback", !!bounced, bounced ? 'join.front.test/oauth/callback' : 'never went there')
  ck("   with the account's state prefix", !!bounced && new URL(bounced).searchParams.get('state').startsWith('verify.'))
  const hop = seen.findIndex((u) => u.startsWith(`${JOIN}/oauth/callback?`))
  ck('   and the front door sends it on to the account', hop >= 0 && seen.slice(hop + 1).some((u) => u.startsWith(`${ACCT}/oauth/callback?`)))
  ck('   (counted)', frontCounter('front_callback_forwarded') === n0 + 1)
  const sess = await apiJson(page, '/api/session')
  const sbody = sess.body || {}
  ck('signed in as dana, owner, through the front door', sbody.person_key === 'dana' && sbody.role === 'owner', `HTTP ${sess.status}`)
  r = curl(`${ACCT}/api/session`)
  ck('   and a stranger with no cookie, right after, is not (the front door keeps no cookies)', r.status === 401, `HTTP ${r.status}`)

  console.log('\nJarvis for Mac')
  const zipPath = join(mkdtempSync(join(tmpdir(), 'jv-mac-')), 'Jarvis.zip')
  const dl = await api(page, '/api/imessage/mac-app')
  writeFileSync(zipPath, Buffer.from(dl.b64, 'base64'))
  ck('a signed-in person can download the app', dl.status === 200 && dl.type === 'application/zip', `HTTP ${dl.status}`)
  ck('   twin: nobody signed in cannot', curl(`${ACCT}/api/imessage/mac-app`).status === 401)
  const zinfo = JSON.parse(execFileSync('python3', ['-c', `
import json, sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
out = {i.filename: oct((i.external_attr >> 16) & 0o777) for i in z.infolist()}
print(json.dumps({"modes": out, "server": z.read("Jarvis.app/Contents/Resources/server.txt").decode(),
                  "plist": z.read("Jarvis.app/Contents/Info.plist").decode(),
                  "all": b"".join(z.read(n) for n in z.namelist() if not n.endswith("/")).decode("utf-8", "replace")}))`, zipPath]).toString())
  ck('   the launcher is executable', zinfo.modes['Jarvis.app/Contents/MacOS/Jarvis'] === '0o755', zinfo.modes['Jarvis.app/Contents/MacOS/Jarvis'])
  ck("   it points at this account's address", zinfo.server.trim() === ACCT, zinfo.server.trim())
  ck('   it is an app bundle', zinfo.plist.includes('<string>com.jarvis.messages</string>') && zinfo.plist.includes('<key>CFBundleExecutable</key><string>Jarvis</string>'))
  ck('   nothing secret in it (no token, no session, no Cloudflare)', !/jarvis_session|CF-Access|cf_secret|Bearer [A-Za-z0-9_-]{20}/.test(zinfo.all))

  const helperSrc = readFileSync(join(REPO, 'mac-helper/Jarvis.app/Contents/Resources/helper.js'), 'utf8')
  const paths = [...new Set(helperSrc.match(/\/api\/[a-z/_-]+/g))].sort()
  ck('the app only ever calls claim and messages', JSON.stringify(paths) === '["/api/mac/claim","/api/mac/messages"]', paths.join(' '))
  ck('   and never scripts the Messages app (cannot send)', !/Application\(['"]Messages['"]\)|tell application "Messages"/.test(helperSrc))
  ck('   it parses as JavaScript', spawnSync('node', ['--check', join(REPO, 'mac-helper/Jarvis.app/Contents/Resources/helper.js')]).status === 0)
  ck('   the launcher parses as bash', spawnSync('bash', ['-n', join(REPO, 'mac-helper/Jarvis.app/Contents/MacOS/Jarvis')]).status === 0)

  // The same helper.js, with a stand-in for macOS: a fixture chat.db, the
  // sqlite3 stand-in, real curl (through the front door), stubbed launchctl.
  const home = mkdtempSync(join(tmpdir(), 'jv-home-'))
  const expected = JSON.parse(execFileSync('python3', [join(HERE, 'mac_fixture.py'), 'make', home]).toString()).expected_stored
  const calls = []
  const notes = []
  const sys = {
    home,
    server: CANARY ? 'http://verify.front.test:8719/never' : ACCT,
    sh: (cmd) => {
      calls.push(cmd)
      let real = cmd
      if (cmd.startsWith('/usr/bin/sqlite3 ')) real = `python3 ${join(HERE, 'mac_fixture.py')} sqlite3 ` + cmd.slice('/usr/bin/sqlite3 '.length)
      else if (cmd.startsWith('/usr/bin/curl ')) real = `/usr/bin/curl ${RESOLVE.map((x) => `'${x}'`).join(' ')} ` + cmd.slice('/usr/bin/curl '.length)
      else if (/^\/usr\/bin\/id -u/.test(cmd)) return '501\n'
      else if (/launchctl|^\/usr\/bin\/open /.test(cmd)) return ''
      return execFileSync('/bin/sh', ['-c', real], { maxBuffer: 20e6 }).toString()
    },
    read: (p) => (existsSync(p) ? readFileSync(p, 'utf8') : ''),
    write: (p, t) => writeFileSync(p, t),
    tmp: (name) => join(home, `${name}-${Date.now()}.json`),
    sleep: () => {},
    computerName: () => 'Fixture Mac',
    dialog: () => 'OK',
    ask: () => null,
    notify: (t) => notes.push(t),
  }
  const { makeCore } = createRequire(import.meta.url)(join(REPO, 'mac-helper/Jarvis.app/Contents/Resources/helper.js'))
  const core = makeCore(sys)

  let threw = ''
  try {
    core.pair('ZZZZ-ZZZZ', 'Fixture Mac')
  } catch (e) {
    threw = e.message
  }
  ck('a wrong code is refused, in words', /wrong, used or expired/.test(threw), threw.slice(0, 80))
  ck('   and nothing is saved', !existsSync(core.configPath))

  const code = (await apiJson(page, '/api/imessage/pair', { method: 'POST' })).body || {}
  const paired0 = counter('imessage_device_paired')
  const before = danaMessages()
  const macCard = async () => {
    await page.goto(`${ACCT}/`, { waitUntil: 'networkidle' })
    await page.getByRole('button', { name: 'Settings', exact: true }).first().click()
    const card = page.getByRole('region', { name: 'iMessage' })
    await card.waitFor({ timeout: 10000 })
    return { status: (await card.getByRole('status').first().innerText().catch(() => '')).trim(), text: await card.innerText() }
  }
  let synced = -1
  let paired = null
  try {
    core.pair(code.code, 'Fixture Mac')
    paired = await macCard()
    synced = core.sync()
  } catch (e) {
    threw = e.message
  }
  ck('the app pairs with the code from Settings, through the front door, no Cloudflare token', counter('imessage_device_paired') === paired0 + 1, threw && synced < 0 ? threw.slice(0, 80) : '')
  ck('   its token file is private (600)', existsSync(core.configPath) && (statSync(core.configPath).mode & 0o777) === 0o600)
  ck(`the first sync stores the ${expected} readable messages (not the reaction, not the 40-day-old one)`, synced === expected, `stored=${synced}`)
  ck('   the ledger agrees: dana has that many more', danaMessages() === before + expected, `${before} -> ${danaMessages()}`)
  const again = (() => {
    try {
      return core.sync()
    } catch {
      return -1
    }
  })()
  const afterSync = await macCard()
  ck('Settings says "Waiting for the Mac", not Connected, while a paired Mac has never synced', paired?.status === 'Waiting for the Mac' && /hasn’t synced yet/.test(paired?.text || ''), JSON.stringify(paired?.status))
  ck('   and "Syncing" once a sync has landed', afterSync.status === 'Syncing' && /synced just now|synced \d/.test(afterSync.text), JSON.stringify(afterSync.status))
  ck('a second sync stores nothing new', again === 0 && danaMessages() === before + expected, `stored=${again}`)
  const sqls = calls.filter((c) => c.startsWith('/usr/bin/sqlite3 '))
  ck('every database read is sqlite3 -readonly', sqls.length > 0 && sqls.every((c) => c.startsWith('/usr/bin/sqlite3 -readonly -json ')), `${sqls.length} reads`)
  ck("   the body-only message was decoded (the attributedBody path)", core.bodyText(Buffer.from(execFileSync('python3', ['-c', `import sys; sys.path.insert(0, '${HERE}'); import mac_fixture; sys.stdout.buffer.write(mac_fixture.typedstream('abc é'))`])).toString('hex')) === 'abc é')
  const plist = core.agentPlist('/Applications/Jarvis.app/Contents/MacOS/applet')
  ck("the background sync runs the app's own executable every 2 minutes", plist.includes('<string>/Applications/Jarvis.app/Contents/MacOS/applet</string>') && plist.includes('<integer>120</integer>') && !plist.includes('/usr/bin/open'))

  const dev = (((await apiJson(page, '/api/imessage/status')).body || {}).devices || []).find((d) => d.name === 'Fixture Mac')
  if (dev) await api(page, `/api/imessage/devices/${dev.device_id}`, { method: 'DELETE' })
  core.background()
  ck('after Disconnect in Settings, the app forgets itself and says so', !existsSync(core.configPath) && notes.some((t) => /isn't connected any more/.test(t)))
  ck('   and the synced texts are deleted', danaMessages() === 0)
  rmSync(home, { recursive: true, force: true })

  await page.goto(`${ACCT}/`, { waitUntil: 'networkidle' })
  await ctx.close()

  console.log('\nan unknown Google account')
  const ctx2 = await browser.newContext()
  const p2 = await ctx2.newPage()
  await p2.goto(ACCT, { waitUntil: 'networkidle' })
  await p2.getByRole('button', { name: 'Continue with Google' }).click()
  await googleAs(p2, 'stranger@example.com')
  ck('is back on sign-in with one plain sentence', (await p2.getByText("That Google account isn't on this Jarvis.").count()) === 1)
  ck('   and has no session', (await apiJson(p2, '/api/session')).status === 401)
  await ctx2.close()
  r = curl(`${JOIN}/oauth/callback?state=ghost.abc&code=x`)
  ck("a callback for an account that isn't here goes nowhere", r.status === 400 && !r.redirect, `HTTP ${r.status}`)

  console.log('\ninvite and sign-up')
  const env = { ...process.env, FRONT_DB, FRONT_BASE_DOMAIN: 'front.test', FRONT_JOIN_HOST: 'join.front.test', FRONT_SCHEME: 'http', FRONT_URL_PORT: `:${PORT}`, FRONT_OAUTH_MOCK: '1', FRONT_SECRET: readFileSync('/tmp/front-verify/secret', 'utf8') }
  const link = execFileSync(PY, ['-m', 'front.cli', 'invite', '--note', 'verify'], { cwd: REPO, env }).toString().trim()
  ck('the CLI makes an invite link on the join address', link.startsWith(`${JOIN}/i/`), link.replace(/\/i\/.*/, '/i/…'))
  const signups0 = sqlite(FRONT_DB, 'SELECT count(*) FROM signups')[0][0]
  const ctx3 = await browser.newContext({ viewport: { width: 420, height: 860 } })
  const p3 = await ctx3.newPage()
  await p3.goto(link)
  await p3.getByRole('link', { name: 'Continue with Google' }).click()
  await googleAs(p3, 'riley@fixture.example', { name: 'Riley Fixture', verified: 'false' })
  ck('an unverified Google address is not let in', p3.url().startsWith(`${JOIN}/?error=failed`), p3.url().replace(/\?.*/, ''))
  await p3.goto(link)
  ck('the invite page says what it is', (await p3.getByText("You're invited to your own Jarvis.").count()) === 1)
  await p3.screenshot({ path: join(HERE, 'shots', 'invite.png') })
  await p3.getByRole('link', { name: 'Continue with Google' }).click()
  await googleAs(p3, 'riley@fixture.example', { name: 'Riley Fixture' })
  ck('after Google, the form knows who you are', (await p3.getByText('riley@fixture.example').count()) === 1 && (await p3.getByLabel('Your name').inputValue()) === 'Riley Fixture')
  await p3.getByLabel('Business name').fill('Ravenscourt Fixture Realty')
  await p3.screenshot({ path: join(HERE, 'shots', 'signup-details.png') })
  await p3.getByRole('button', { name: 'Create my Jarvis' }).click()
  await p3.waitForLoadState('networkidle')
  const su = sqlite(FRONT_DB, 'SELECT id, slug, status, email FROM signups ORDER BY created_at DESC LIMIT 1')[0] || []
  ck('one sign-up is queued in the ledger', sqlite(FRONT_DB, 'SELECT count(*) FROM signups')[0][0] === signups0 + 1 && su[2] === 'queued', `status=${su[2]}`)
  ck('   for the Google address, not a typed one', su[3] === 'riley@fixture.example')
  ck('   with an address made from the business name', su[1] === 'ravenscourtfix', su[1])
  const PG_SLUG = /^[a-z][a-z0-9]{0,18}$/ // scripts/pg_tenant.py SLUG, the strictest consumer
  const names = ['34 Dev', 'Dev 34', 'Smith & Co. Realty', "O'Neil Homes", '123', 'ab', 'Ravenscourt Fixture Realty']
  const made = JSON.parse(execFileSync(PY, ['-c', `
import json, sqlite3, sys
from front import store
c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row
c.executescript("CREATE TABLE routes (slug TEXT); CREATE TABLE signups (slug TEXT);")
print(json.dumps([store.suggest_slug(c, n) for n in json.loads(sys.argv[1])]))`, JSON.stringify(names)], { cwd: REPO, env }).toString())
  ck('every suggested address is one the database setup accepts', made.every((s) => PG_SLUG.test(s)), made.join(' '))
  ck('   "34 Dev" becomes dev34', made[0] === 'dev34', made[0])
  ck('   presence twin: the database rule does refuse a hyphen', !PG_SLUG.test('dev-34'))
  ck('the waiting page shows that address', (await p3.getByText(`http://${su[1]}.front.test:${PORT}/`).count()) === 1)
  await p3.screenshot({ path: join(HERE, 'shots', 'signup-wait.png') })
  ck('the invite is now used', sqlite(FRONT_DB, 'SELECT used_at IS NOT NULL FROM invites WHERE signup_id=?', [su[0]])[0]?.[0] === 1)
  await p3.goto(link)
  ck('   so the link no longer works', (await p3.getByText('This invite link has been used or has expired.').count()) === 1)
  r = curl(`${JOIN}/i/not-a-real-invite`)
  ck('a made-up invite is refused', r.status === 410)
  ck("someone else can't read the sign-up's status", curl(`${JOIN}/api/signup/${su[0]}`).status === 404)

  const opt0 = existsSync(`/opt/${su[1]}`)
  const w = spawnSync(PY, [join(REPO, 'scripts/signup_worker.py'), '--once'], { cwd: REPO, env: { ...env, SIGNUP_MAX_ACCOUNTS: '0' } })
  const after = sqlite(FRONT_DB, 'SELECT status FROM signups WHERE id=?', [su[0]])[0][0]
  ck('with no room, the worker makes nothing and the sign-up waits as full', w.status === 0 && after === 'full' && !opt0 && !existsSync(`/opt/${su[1]}`), `rc=${w.status} status=${after}`)
  ck('   counted', frontCounter('signup_full') >= 1)
  execFileSync(PY, ['-c', `
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("INSERT INTO signups (id, invite_hash, email, owner_name, business_name, slug, status, created_at, updated_at) "
          "VALUES ('su_badslug', 'h-badslug', 'bad@fixture.example', 'Bad', 'Bad', 'bad-slug', 'queued', '2000-01-01', '2000-01-01')")
c.commit()`, FRONT_DB])
  const bad0 = frontCounter('signup_bad_slug')
  const wb = spawnSync(PY, [join(REPO, 'scripts/signup_worker.py'), '--once'], { cwd: REPO, env: { ...env, SIGNUP_MAX_ACCOUNTS: '0' } })
  const badRow = sqlite(FRONT_DB, "SELECT status FROM signups WHERE id='su_badslug'")[0]?.[0]
  ck('a hyphenated account name is refused before anything is made', wb.status === 0 && badRow === 'failed' && !existsSync('/opt/bad-slug'), `rc=${wb.status} status=${badRow}`)
  ck('   counted (+1)', frontCounter('signup_bad_slug') === bad0 + 1, `${bad0} -> ${frontCounter('signup_bad_slug')}`)
  // The worker's reading of the provisioner, with subprocess stubbed: only the lock's HELD line is "try later".
  const rc = JSON.parse(execFileSync(PY, ['-c', `
import json, os, subprocess, sys, tempfile, types
sys.path.insert(0, "scripts")
os.environ["SIGNUP_STATE"] = tempfile.mkdtemp()
import signup_worker as w
def fake(rc, err):
    return lambda *a, **k: types.SimpleNamespace(returncode=rc, stdout="", stderr=err)
out = {}
for name, rc, err in (("held", 2, "HELD: /root/.jarvis-agent-lock is held by pid 1"), ("refused", 2, "REFUSED: structural collision"), ("ok", 0, "")):
    w.subprocess.run = fake(rc, err)
    out[name] = w.provision("verifyrc", w.Path("/nonexistent.yaml"))
su = {"slug": "verifyrc", "owner_name": "Riley Fixture", "business_name": "Verify RC", "email": "riley@fixture.example"}
out["first"] = w.manifest_ports(w.write_manifest(su))
out["retry"] = w.manifest_ports(w.write_manifest(su))
out["fresh"] = w.pick_ports()
print(json.dumps(out))`], { cwd: REPO, env }).toString())
  ck('a provisioner refusal is a failure, not "lock busy"', rc.refused === 2 && rc.held === -2 && rc.ok === 0, JSON.stringify(rc))
  ck('a retry keeps the ports in its own manifest', JSON.stringify(rc.first) === JSON.stringify(rc.retry), `${JSON.stringify(rc.first)} -> ${JSON.stringify(rc.retry)}`)
  ck('   presence twin: a fresh pick would choose other ports', rc.fresh.app !== rc.first.app, JSON.stringify(rc.fresh))
  await p3.goto(`${JOIN}/signup/${su[0]}`)
  await p3.waitForTimeout(1500)
  ck('   and the page says so in words', (await p3.getByText("We're full right now.", { exact: false }).count()) === 1)
  await ctx3.close()

  console.log('\nfind my Jarvis')
  const ctx4 = await browser.newContext()
  const p4 = await ctx4.newPage()
  await p4.goto(JOIN)
  await p4.getByRole('link', { name: 'Sign in with Google' }).click()
  await googleAs(p4, 'nobody@fixture.example')
  ck('a Google account with no Jarvis gets one sentence', (await p4.getByText('No Jarvis is set up for that Google account.', { exact: false }).count()) === 1)
  await p4.goto(JOIN)
  await p4.getByRole('link', { name: 'Sign in with Google' }).click()
  await googleAs(p4, DANA_EMAIL)
  await googleAs(p4, DANA_EMAIL)
  const s4 = await apiJson(p4, '/api/session')
  ck("the owner's Google account lands signed in on their own Jarvis", p4.url().startsWith(ACCT) && s4.status === 200 && (s4.body || {}).person_key === 'dana', p4.url().replace(/\?.*/, ''))
  await ctx4.close()
} finally {
  await browser.close()
}
const pass = results.filter(Boolean).length
console.log(`\n${pass}/${results.length} passed${CANARY ? ' (canary: failures expected)' : ''}`)
process.exit(pass === results.length ? 0 : 1)
