// Jarvis for Mac: reads your Messages, never sends.
//
// Opened by you: asks for the code from Jarvis Settings, walks you to Full
// Disk Access, then syncs every two minutes in the background (a LaunchAgent
// that runs this app's own executable). Opened again: shows the connection,
// Sync now, Disconnect. Only what ships with macOS: osacompile and codesign
// (at install), sqlite3 (read-only), curl. It talks to one Jarvis
// (Resources/server.txt) and only ever calls
// /api/mac/claim and /api/mac/messages.
//
// The logic below takes a `sys` (shell, files, dialogs), so the same file runs
// under the verifier with a stand-in for macOS.

const DAYS = 30
const BATCH = 500
const LABEL = 'com.jarvis.messages'
const MODE_VAR = 'JARVIS_MODE'
const NO_ACCESS_NOTICE_MS = 6 * 3600 * 1000
const APPLE_EPOCH_MS = Date.UTC(2001, 0, 1)

function q(s) {
  return "'" + String(s).replace(/'/g, "'\\''") + "'"
}

function makeCore(sys) {
  const home = sys.home
  const supportDir = home + '/Library/Application Support/Jarvis'
  const configPath = supportDir + '/messages.json'
  const chatDb = home + '/Library/Messages/chat.db'
  const contactsRoot = home + '/Library/Application Support/AddressBook'
  const agentPath = home + '/Library/LaunchAgents/' + LABEL + '.plist'

  function loadConfig() {
    const raw = sys.read(configPath)
    if (!raw) return {}
    try {
      return JSON.parse(raw)
    } catch (e) {
      return {}
    }
  }

  function saveConfig(cfg) {
    sys.sh('/bin/mkdir -p ' + q(supportDir) + ' && /bin/chmod 700 ' + q(supportDir))
    sys.write(configPath, JSON.stringify(cfg, null, 1))
    sys.sh('/bin/chmod 600 ' + q(configPath))
  }

  // ── reading Messages ──────────────────────────────────────────────────────

  function sql(db, query) {
    const out = sys.sh('/usr/bin/sqlite3 -readonly -json ' + q(db) + ' ' + q(query))
    return out.trim() ? JSON.parse(out) : []
  }

  function canRead() {
    try {
      sql(chatDb, 'SELECT 1 AS ok FROM message LIMIT 1')
      return true
    } catch (e) {
      return false
    }
  }

  // message.date: nanoseconds since 2001-01-01 (seconds on old macOS).
  function appleTime(v) {
    v = Number(v || 0)
    const secs = v > 1e11 ? v / 1e9 : v
    return new Date(APPLE_EPOCH_MS + secs * 1000).toISOString()
  }

  function appleValue(date) {
    return Math.floor((date.getTime() - APPLE_EPOCH_MS) / 1000) * 1e9
  }

  function utf8(bytes) {
    let s = ''
    for (const b of bytes) s += '%' + (b < 16 ? '0' : '') + b.toString(16)
    try {
      return decodeURIComponent(s)
    } catch (e) {
      return bytes.map((b) => (b < 128 ? String.fromCharCode(b) : '\uFFFD')).join('')
    }
  }

  // Recent macOS keeps the text only in attributedBody (an NSArchiver
  // typedstream): after the NSString class name, a '+' marker, then the
  // length (one byte, or 0x81 + two bytes, 0x82 + three bytes, little-endian).
  function bodyText(hex) {
    if (!hex) return ''
    const b = []
    for (let i = 0; i + 1 < hex.length; i += 2) b.push(parseInt(hex.substr(i, 2), 16))
    const tag = [0x4e, 0x53, 0x53, 0x74, 0x72, 0x69, 0x6e, 0x67]
    let at = -1
    for (let i = 0; i + tag.length <= b.length && at < 0; i++) {
      if (tag.every((c, j) => b[i + j] === c)) at = i
    }
    if (at < 0) return ''
    const plus = b.indexOf(0x2b, at + 8)
    if (plus < 0 || plus + 1 >= b.length) return ''
    let i = plus + 1
    let n = b[i]
    if (n === 0x81) {
      n = b[i + 1] | (b[i + 2] << 8)
      i += 3
    } else if (n === 0x82) {
      n = b[i + 1] | (b[i + 2] << 8) | (b[i + 3] << 16)
      i += 4
    } else {
      i += 1
    }
    return utf8(b.slice(i, i + n))
  }

  function phoneKey(v) {
    const d = String(v || '').replace(/\D/g, '')
    return d.length >= 7 ? d.slice(-10) : ''
  }

  function contactNames() {
    const names = {}
    const files = sys
      .sh('/bin/ls -1 ' + q(contactsRoot) + '/Sources/*/AddressBook-v22.abcddb ' + q(contactsRoot) + '/AddressBook-v22.abcddb 2>/dev/null || true')
      .split('\n')
      .filter(Boolean)
    for (const f of files) {
      try {
        const people = {}
        for (const r of sql(f, 'SELECT Z_PK AS pk, ZFIRSTNAME AS first, ZLASTNAME AS last, ZORGANIZATION AS org FROM ZABCDRECORD')) {
          people[r.pk] = [r.first, r.last].filter(Boolean).join(' ') || r.org || ''
        }
        for (const r of sql(f, 'SELECT ZOWNER AS owner, ZFULLNUMBER AS number FROM ZABCDPHONENUMBER')) {
          const k = phoneKey(r.number)
          if (people[r.owner] && k && !(k in names)) names[k] = people[r.owner]
        }
        for (const r of sql(f, 'SELECT ZOWNER AS owner, ZADDRESS AS email FROM ZABCDEMAILADDRESS')) {
          const k = String(r.email || '').trim().toLowerCase()
          if (people[r.owner] && k && !(k in names)) names[k] = people[r.owner]
        }
      } catch (e) {
        // A Contacts file we can't read means numbers instead of names, not a failed sync.
      }
    }
    return names
  }

  function label(handle, names) {
    if (!handle) return ''
    const name = names[String(handle).trim().toLowerCase()] || names[phoneKey(handle)]
    return name ? name + ' (' + handle + ')' : handle
  }

  function newestRowid() {
    return Number((sql(chatDb, 'SELECT COALESCE(MAX(ROWID), 0) AS n FROM message')[0] || {}).n || 0)
  }

  function readMessages(afterRowid, since, names) {
    const rows = sql(
      chatDb,
      'SELECT m.ROWID AS rowid, m.guid AS guid, m.text AS text, hex(m.attributedBody) AS body, m.is_from_me AS from_me, ' +
        'm.date AS date, h.id AS handle, c.display_name AS chat_name, c.chat_identifier AS chat_id ' +
        'FROM message m LEFT JOIN handle h ON h.ROWID = m.handle_id ' +
        'LEFT JOIN chat_message_join j ON j.message_id = m.ROWID LEFT JOIN chat c ON c.ROWID = j.chat_id ' +
        'WHERE m.ROWID > ' + Number(afterRowid) + ' AND m.date >= ' + appleValue(since) +
        ' AND COALESCE(m.associated_message_type, 0) = 0 AND COALESCE(m.item_type, 0) = 0 ' +
        'GROUP BY m.ROWID ORDER BY m.ROWID LIMIT ' + BATCH,
    )
    const last = Number((sql(chatDb, 'SELECT COALESCE(MAX(ROWID), 0) AS n FROM message')[0] || {}).n || 0)
    return {
      last,
      batch: rows.map((r) => ({
        rowid: Number(r.rowid),
        guid: r.guid,
        text: r.text || bodyText(r.body),
        is_from_me: Boolean(r.from_me),
        sent_at: appleTime(r.date),
        sender: r.from_me ? '' : label(r.handle, names),
        chat: r.chat_name || label(r.chat_id, names),
      })),
    }
  }

  // ── talking to Jarvis ─────────────────────────────────────────────────────

  function post(cfg, path, body, token) {
    const file = sys.tmp('jarvis-body')
    sys.write(file, JSON.stringify(body))
    let cmd = '/usr/bin/curl -sS --max-time 60 -X POST -H ' + q('Content-Type: application/json') +
      ' -H ' + q('User-Agent: jarvis-mac/1') + ' --data-binary @' + q(file) + " -w '\\n%{http_code}'"
    if (token) cmd += ' -H ' + q('Authorization: Bearer ' + token)
    cmd += ' ' + q(cfg.server.replace(/\/+$/, '') + path)
    let out
    try {
      out = sys.sh(cmd)
    } catch (e) {
      throw new Error("Can't reach your Jarvis. Check the internet connection and try again.")
    } finally {
      sys.sh('/bin/rm -f ' + q(file))
    }
    const cut = out.lastIndexOf('\n')
    const status = Number(out.slice(cut + 1).trim())
    let data = {}
    try {
      data = JSON.parse(out.slice(0, cut) || '{}')
    } catch (e) {
      throw new Error("Your Jarvis didn't answer as expected (HTTP " + status + ').')
    }
    if (status === 401) {
      const err = new Error("This Mac isn't connected any more. Open Jarvis to connect it again.")
      err.unpaired = true
      throw err
    }
    if (status < 200 || status >= 300) throw new Error((data && data.detail) || 'Jarvis said no (HTTP ' + status + ').')
    return data
  }

  function pair(code, name) {
    const cfg = { server: sys.server }
    const got = post(cfg, '/api/mac/claim', { code: code, name: name || 'Mac' })
    const days = got.history_days === undefined ? DAYS : Math.max(0, Math.min(DAYS, Number(got.history_days) || 0))
    // No history: everything already in Messages counts as seen; only what arrives from now on is sent.
    const start = days === 0 && canRead() ? newestRowid() : 0
    const saved = { server: sys.server, token: got.token, device_id: got.device_id, last_rowid: start,
                    history_days: days, paired_at: new Date().toISOString() }
    saveConfig(saved)
    return saved
  }

  function sync() {
    const cfg = loadConfig()
    if (!cfg.token) throw new Error("This Mac isn't connected yet. Open Jarvis and type the code from Settings.")
    if (!canRead()) {
      const err = new Error('Jarvis needs Full Disk Access to read your Messages.')
      err.noAccess = true
      throw err
    }
    const names = contactNames()
    const days = cfg.history_days === undefined ? DAYS : Number(cfg.history_days)
    const since = cfg.paired_at
      ? new Date(Math.max(Date.now() - DAYS * 86400000, Date.parse(cfg.paired_at) - days * 86400000))
      : new Date(Date.now() - DAYS * 86400000)
    let after = Number(cfg.last_rowid || 0)
    let sent = 0
    let posted = false
    for (;;) {
      const got = readMessages(after, since, names)
      if (got.batch.length === 0) {
        after = Math.max(after, got.last)
        break
      }
      const msgs = got.batch.filter((m) => String(m.text || '').trim()).map((m) => {
        const o = Object.assign({}, m)
        delete o.rowid
        return o
      })
      if (msgs.length) {
        sent += Number(post(cfg, '/api/mac/messages', { messages: msgs }, cfg.token).stored || 0)
        posted = true
      }
      after = got.batch[got.batch.length - 1].rowid
      cfg.last_rowid = after
      saveConfig(cfg)
    }
    if (!posted) post(cfg, '/api/mac/messages', { messages: [] }, cfg.token)
    cfg.last_rowid = after
    cfg.last_sync = new Date().toISOString()
    saveConfig(cfg)
    return sent
  }

  // launchd runs the signed app's own executable, not `open` or osascript,
  // so Full Disk Access is checked against Jarvis itself.
  function agentPlist(executable) {
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    return '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n' +
      '<plist version="1.0"><dict>' +
      '<key>Label</key><string>' + LABEL + '</string>' +
      '<key>ProgramArguments</key><array><string>' + esc(executable) + '</string></array>' +
      '<key>EnvironmentVariables</key><dict><key>' + MODE_VAR + '</key><string>sync</string></dict>' +
      '<key>RunAtLoad</key><true/><key>StartInterval</key><integer>120</integer>' +
      '<key>StandardErrorPath</key><string>' + esc(home + '/Library/Logs/Jarvis-sync.log') + '</string>' +
      '</dict></plist>\n'
  }

  function installAgent(executable) {
    sys.sh('/bin/mkdir -p ' + q(home + '/Library/LaunchAgents'))
    sys.write(agentPath, agentPlist(executable))
    const uid = sys.sh('/usr/bin/id -u').trim()
    sys.sh('/bin/launchctl bootout gui/' + uid + ' ' + q(agentPath) + ' >/dev/null 2>&1 || true')
    sys.sh('/bin/launchctl bootstrap gui/' + uid + ' ' + q(agentPath))
  }

  function forget() {
    const uid = sys.sh('/usr/bin/id -u').trim()
    sys.sh('/bin/launchctl bootout gui/' + uid + ' ' + q(agentPath) + ' >/dev/null 2>&1 || true')
    sys.sh('/bin/rm -f ' + q(agentPath) + ' ' + q(configPath))
  }

  // ── what you see ──────────────────────────────────────────────────────────

  // One dialog, then out of the way: macOS may quit Jarvis when the switch is
  // turned on, and the background sync picks up within two minutes either way.
  // Never wait here; a script waiting on access is a spinning wheel.
  function askForAccess() {
    if (canRead()) return true
    const b = sys.dialog(
      'Jarvis needs Full Disk Access to read your Messages.\n\n' +
        'Click Open Settings, then switch on Jarvis in the list. If Jarvis isn’t there, click +, choose Applications, then Jarvis. ' +
        'If Jarvis is already switched on and you still see this, select it, click –, then add it again with +.\n\n' +
        'You don’t need to reopen Jarvis: it starts syncing on its own within two minutes.',
      ['Not now', 'Open Settings'],
      'Open Settings',
    )
    if (b === 'Open Settings') sys.sh("/usr/bin/open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'")
    return false
  }

  function firstRun(executable) {
    let code = sys.ask('Type the code shown in Jarvis Settings → iMessage.', '')
    if (code === null) return
    for (;;) {
      try {
        pair(code.trim(), sys.computerName())
        break
      } catch (e) {
        code = sys.ask(e.message + '\n\nType the code again (make a new one in Settings if it expired).', '')
        if (code === null) return
      }
    }
    installAgent(executable)
    if (!askForAccess()) return
    try {
      const n = sync()
      sys.dialog('All set. Jarvis read ' + n + ' message' + (n === 1 ? '' : 's') + ' from the last ' + DAYS +
        ' days, and keeps up in the background. Jarvis can read your texts; it can’t send one.', ['OK'], 'OK')
    } catch (e) {
      sys.dialog('Connected, but the first sync didn’t finish: ' + e.message + '\nIt will try again in two minutes.', ['OK'], 'OK')
    }
  }

  function status(executable) {
    installAgent(executable)
    if (!askForAccess()) return
    const cfg = loadConfig()
    const when = cfg.last_sync ? new Date(cfg.last_sync).toLocaleString() : 'not yet'
    const b = sys.dialog('This Mac is connected to ' + cfg.server + '.\nLast sync: ' + when + '.',
      ['Disconnect this Mac', 'Sync now', 'OK'], 'OK')
    if (b === 'Sync now') {
      try {
        const n = sync()
        sys.dialog('Synced ' + n + ' new message' + (n === 1 ? '' : 's') + '.', ['OK'], 'OK')
      } catch (e) {
        sys.dialog(e.message, ['OK'], 'OK')
      }
    } else if (b === 'Disconnect this Mac') {
      forget()
      sys.dialog('This Mac stopped syncing. To delete the texts Jarvis already has, tap Disconnect in Jarvis Settings.', ['OK'], 'OK')
    }
  }

  function background() {
    try {
      sync()
    } catch (e) {
      if (e.unpaired) {
        forget()
        sys.notify(e.message)
      } else if (e.noAccess) {
        const cfg = loadConfig()
        const last = Date.parse(cfg.no_access_notice_at || '') || 0
        if (Date.now() - last >= NO_ACCESS_NOTICE_MS) {
          sys.notify('Jarvis can’t read your Messages: turn on Full Disk Access for Jarvis in System Settings.')
          cfg.no_access_notice_at = new Date().toISOString()
          saveConfig(cfg)
        }
      }
    }
  }

  return { q, bodyText, appleTime, phoneKey, label, pair, sync, loadConfig, agentPlist, firstRun, status, background,
    configPath, agentPath }
}

if (typeof module !== 'undefined') module.exports = { makeCore, q }

// ── macOS ───────────────────────────────────────────────────────────────────

// Built into an applet by the installer (Contents/MacOS/Jarvis). Opened by
// you, it shows the dialogs; run by the LaunchAgent, JARVIS_MODE=sync makes it
// sync quietly and quit.
function run() {
  ObjC.import('Foundation')
  const app = Application.currentApplication()
  app.includeStandardAdditions = true
  const bundle = $.NSBundle.mainBundle
  const executable = bundle.executablePath.js
  const resources = bundle.resourcePath.js
  const readFile = (p) => {
    const s = $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null)
    return s.isNil() ? '' : s.js
  }
  const sys = {
    home: $.NSHomeDirectory().js,
    server: readFile(resources + '/server.txt').trim(),
    sh: (cmd) => app.doShellScript(cmd, { alteringLineEndings: false }),
    read: readFile,
    write: (p, text) => $.NSString.stringWithString(text).writeToFileAtomicallyEncodingError(p, true, $.NSUTF8StringEncoding, null),
    tmp: (name) => $.NSTemporaryDirectory().js + name + '-' + Date.now() + '.json',
    computerName: () => app.doShellScript('/usr/sbin/scutil --get ComputerName || hostname'),
    dialog: (text, buttons, def) => {
      app.activate()
      try {
        return app.displayDialog(text, { buttons: buttons, defaultButton: def, withTitle: 'Jarvis' }).buttonReturned
      } catch (e) {
        return null
      }
    },
    ask: (text, answer) => {
      app.activate()
      try {
        return app.displayDialog(text, { defaultAnswer: answer, buttons: ['Cancel', 'Connect'], defaultButton: 'Connect',
          withTitle: 'Jarvis' }).textReturned
      } catch (e) {
        return null
      }
    },
    notify: (text) => app.displayNotification(text, { withTitle: 'Jarvis' }),
  }
  const core = makeCore(sys)
  const env = $.NSProcessInfo.processInfo.environment.objectForKey(MODE_VAR)
  if (!env.isNil() && env.js === 'sync') return core.background()
  if (core.loadConfig().token) return core.status(executable)
  return core.firstRun(executable)
}
