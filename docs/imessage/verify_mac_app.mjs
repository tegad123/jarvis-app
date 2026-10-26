#!/usr/bin/env node
// Jarvis for Mac, the parts that only a Mac runs, run here with stand-ins.
//
// The installer (Contents/MacOS/Jarvis) runs for real under bash with
// osacompile, plutil, codesign, tccutil, launchctl, pkill, ditto, xattr, open
// and osascript replaced by recording stubs. Checked: it builds the applet
// from helper.js, names it com.jarvis.messages, puts server.txt in, and signs
// only after the last change to the bundle; it installs to Applications and
// opens the installed copy; it never runs helper.js through osascript.
// Canary inside: osacompile fails, so the person is told and nothing installs.
//
// The app (helper.js makeCore) runs with a stand-in macOS that has no Full
// Disk Access: opening it shows one dialog and returns without waiting (the
// spinning wheel was a 5-minute wait loop), the LaunchAgent runs the app's
// own executable every 2 minutes, and the "no access" notice is not repeated
// every 2 minutes.
//
//   node docs/imessage/verify_mac_app.mjs
//   node docs/imessage/verify_mac_app.mjs --helper OLD_helper.js --launcher OLD_Jarvis   (canary: must FAIL)
import { execFileSync, spawnSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const arg = (name, dflt) => {
  const i = process.argv.indexOf(name)
  return i > 0 ? process.argv[i + 1] : dflt
}
const HELPER = resolve(arg('--helper', join(REPO, 'mac-helper/Jarvis.app/Contents/Resources/helper.js')))
const LAUNCHER = resolve(arg('--launcher', join(REPO, 'mac-helper/Jarvis.app/Contents/MacOS/Jarvis')))

let pass = 0
let total = 0
function ck(name, ok, detail = '') {
  total++
  if (ok) pass++
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok || !detail ? '' : ` — ${detail}`}`)
}

// ── the installer ────────────────────────────────────────────────────────────

const TOOLS = ['osacompile', 'plutil', 'codesign', 'tccutil', 'launchctl', 'pkill', 'ditto', 'xattr', 'open', 'osascript']

function runInstaller({ osacompileFails = false, root = mkdtempSync(join(tmpdir(), 'jv-install-')), keep = false, helperExtra = '' } = {}) {
  const home = join(root, 'home')
  const stubs = join(root, 'stubs')
  const log = join(root, 'calls.log')
  mkdirSync(home, { recursive: true })
  rmSync(stubs, { recursive: true, force: true })
  rmSync(log, { force: true })
  mkdirSync(stubs)
  const dl = join(root, 'Downloads/Jarvis.app')
  mkdirSync(join(dl, 'Contents/MacOS'), { recursive: true })
  mkdirSync(join(dl, 'Contents/Resources'), { recursive: true })
  writeFileSync(join(dl, 'Contents/Resources/helper.js'), readFileSync(HELPER, 'utf8') + helperExtra)
  writeFileSync(join(dl, 'Contents/Resources/server.txt'), 'https://acct.example\n')
  for (const t of TOOLS) {
    let body = `echo "${t} $*" >> '${log}'\n`
    if (t === 'osacompile') {
      body += osacompileFails
        ? 'exit 1\n'
        : 'out="$4"; mkdir -p "$out/Contents/MacOS" "$out/Contents/Resources"; echo plist > "$out/Contents/Info.plist"; ' +
          'echo applet > "$out/Contents/MacOS/applet"; chmod +x "$out/Contents/MacOS/applet"; cp "$5" "$out/Contents/Resources/main.js"; ' +
          `echo "  built from $(head -c 60 "$5" | tr -d '\\n')" >> '${log}'\n`
    }
    if (t === 'ditto') body += 'cp -R "$1" "$2"\n'
    writeFileSync(join(stubs, t), `#!/bin/bash\n${body}`)
    chmodSync(join(stubs, t), 0o755)
  }
  // The installer names tools by absolute path; point those paths at the stubs.
  const src = readFileSync(LAUNCHER, 'utf8').replace(/\/usr\/bin\/(\w+)|\/bin\/(launchctl)/g, (m, a, b) =>
    TOOLS.includes(a || b) ? join(stubs, a || b) : m)
  writeFileSync(join(dl, 'Contents/MacOS/Jarvis'), src)
  chmodSync(join(dl, 'Contents/MacOS/Jarvis'), 0o755)
  const r = spawnSync('bash', [join(dl, 'Contents/MacOS/Jarvis')], { env: { PATH: process.env.PATH, HOME: home, TMPDIR: root } })
  const calls = existsSync(log) ? readFileSync(log, 'utf8').split('\n').filter(Boolean) : []
  const installed = join(home, 'Applications/Jarvis.app')
  const out = {
    status: r.status,
    calls,
    installed: existsSync(installed),
    server: existsSync(join(installed, 'Contents/Resources/server.txt'))
      ? readFileSync(join(installed, 'Contents/Resources/server.txt'), 'utf8').trim() : null,
    installLog: existsSync(join(home, 'Library/Logs/Jarvis-install.log'))
      ? readFileSync(join(home, 'Library/Logs/Jarvis-install.log'), 'utf8') : '',
    installedPath: installed,
  }
  if (!keep) rmSync(root, { recursive: true, force: true })
  return out
}

console.log('\nthe installer (Contents/MacOS/Jarvis), run with stand-ins for the macOS tools')
const inst = runInstaller()
const idx = (re) => inst.calls.findIndex((c) => re.test(c))
const lastIdx = (re) => inst.calls.map((c, i) => (re.test(c) ? i : -1)).filter((i) => i >= 0).pop() ?? -1
ck('it exits 0 and installs Jarvis', inst.status === 0 && inst.installed, `rc=${inst.status} ${inst.installLog.split('\n').filter((l) => /FAILED/.test(l)).join(' ')}`)
ck('   it builds the app with osacompile from helper.js (JavaScript)',
  idx(/^osacompile -l JavaScript -o .*\/Jarvis\.app .*Resources\/helper\.js$/) >= 0 && inst.calls.some((c) => c.includes('Jarvis for Mac: reads your Messages')),
  inst.calls.filter((c) => c.startsWith('osacompile')).join(' | '))
ck('   it names the app com.jarvis.messages and hides it from the Dock',
  idx(/^plutil -replace CFBundleIdentifier -string com\.jarvis\.messages /) >= 0 && idx(/^plutil -replace LSUIElement -bool true /) >= 0)
ck('   it signs the app, then checks the signature',
  idx(/^codesign --force --deep --sign - --identifier com\.jarvis\.messages /) >= 0 && idx(/^codesign --verify --deep /) > idx(/^codesign --force/))
ck('   signing comes after the last change to the app (otherwise the signature is broken)',
  idx(/^codesign --force/) > lastIdx(/^(plutil|osacompile) /))
ck('   the installed app carries this account’s address', inst.server === 'https://acct.example', String(inst.server))
ck('   it stops the old background sync before replacing the app', idx(/^launchctl bootout gui\/\d+ .*com\.jarvis\.messages\.plist$/) >= 0)
ck('   it clears the stale Full Disk Access entry, and says whether that worked',
  idx(/^tccutil reset SystemPolicyAllFiles com\.jarvis\.messages$/) >= 0 && /cleared the old Full Disk Access entry|WARNING: tccutil/.test(inst.installLog))
ck('   it opens the installed app, not the download', inst.calls[inst.calls.length - 1] === `open ${inst.installedPath}`, inst.calls[inst.calls.length - 1])
ck('   nothing runs helper.js through osascript any more', !inst.calls.some((c) => /^osascript .*helper\.js/.test(c)) && !/osascript[^\n]*helper\.js/.test(readFileSync(LAUNCHER, 'utf8')))

const again = mkdtempSync(join(tmpdir(), 'jv-again-'))
const first = runInstaller({ root: again, keep: true })
const second = runInstaller({ root: again, keep: true })
ck('opening the same download again does not rebuild, re-sign or reset Full Disk Access (that switched access off)',
  first.status === 0 && second.status === 0 && !second.calls.some((c) => /^(osacompile|codesign|tccutil) /.test(c)),
  second.calls.filter((c) => /^(osacompile|codesign|tccutil) /.test(c)).join(' | '))
ck('   it opens the installed Jarvis instead', second.calls[second.calls.length - 1] === `open ${second.installedPath}`, second.calls.join(' | '))
const updated = runInstaller({ root: again, helperExtra: '\n// a newer build\n' })
ck('   presence twin: a download with a changed app does rebuild and re-sign',
  updated.status === 0 && updated.calls.some((c) => /^osacompile /.test(c)) && updated.calls.some((c) => /^codesign --force /.test(c)))

const bad = runInstaller({ osacompileFails: true })
ck('canary: when osacompile fails, it exits non-zero and installs nothing', bad.status !== 0 && !bad.installed, `rc=${bad.status} installed=${bad.installed}`)
ck('   and the person is told in a dialog, with where the details are',
  /^osascript .*couldn't finish setting up: osacompile[\s\S]*Jarvis-install\.log/m.test(bad.calls.join('\n')))
ck('   and the install log says FAILED', /FAILED: osacompile/.test(bad.installLog))

// ── the app, with no Full Disk Access ───────────────────────────────────────

console.log('\nthe app (helper.js) on a Mac where Full Disk Access is off')
const home = mkdtempSync(join(tmpdir(), 'jv-home-'))
const calls = []
const dialogs = []
const notes = []
let sleeps = 0
const sys = {
  home,
  server: 'https://acct.example',
  sh: (cmd) => {
    calls.push(cmd)
    if (cmd.startsWith('/usr/bin/sqlite3 ')) throw new Error('authorization denied')
    if (/^\/usr\/bin\/id -u/.test(cmd)) return '501\n'
    if (/launchctl|^\/usr\/bin\/open /.test(cmd)) return ''
    return execFileSync('/bin/sh', ['-c', cmd]).toString()
  },
  read: (p) => (existsSync(p) ? readFileSync(p, 'utf8') : ''),
  write: (p, t) => writeFileSync(p, t),
  tmp: (name) => join(home, `${name}-${Date.now()}.json`),
  sleep: () => {
    sleeps++
    if (sleeps > 1000) throw new Error('waited forever')
  },
  computerName: () => 'Fixture Mac',
  dialog: (text, buttons) => {
    dialogs.push(text)
    if (buttons.includes('Open Settings')) return 'Open Settings'
    return buttons.includes('Sync now') ? 'Sync now' : 'OK'
  },
  ask: () => null,
  notify: (t) => notes.push(t),
}
const core = createRequire(import.meta.url)(HELPER).makeCore(sys)
mkdirSync(dirname(core.configPath), { recursive: true })
writeFileSync(core.configPath, JSON.stringify({ server: 'https://acct.example', token: 'fixture-token', paired_at: new Date().toISOString() }))

const EXE = '/Applications/Jarvis.app/Contents/MacOS/applet'
let err = ''
const t0 = Date.now()
try {
  core.status(EXE)
} catch (e) {
  err = e.message
}
ck('opening Jarvis without access returns at once (no waiting loop, no spinning wheel)', !err && sleeps === 0, err || `sleep() called ${sleeps} times`)
ck('   presence twin: it did show the Full Disk Access dialog, once', dialogs.length === 1 && /Full Disk Access/.test(dialogs[0] || ''), `${dialogs.length} dialogs`)
ck('   it opens the Full Disk Access pane when asked', calls.some((c) => /^\/usr\/bin\/open 'x-apple\.systempreferences:.*Privacy_AllFiles'$/.test(c)))
ck('   it tells the person how to fix an entry that is on but stale', /click –, then add it again/.test(dialogs[0] || ''))
ck('   it schedules the background sync even before access is on', existsSync(core.agentPath) && calls.some((c) => /launchctl bootstrap gui\/501 /.test(c)))

const plist = existsSync(core.agentPath) ? readFileSync(core.agentPath, 'utf8') : ''
ck('the background sync runs the app’s own executable, every 2 minutes',
  plist.includes(`<array><string>${EXE}</string></array>`) && plist.includes('<integer>120</integer>'), plist.slice(0, 400))
ck('   in sync mode (JARVIS_MODE=sync), not through open or osascript',
  /<key>JARVIS_MODE<\/key><string>sync<\/string>/.test(plist) && !plist.includes('/usr/bin/open') && !plist.includes('osascript'))

core.background()
core.background()
core.background()
ck('three background runs without access notify once, not every 2 minutes', notes.length === 1, `${notes.length} notices`)
ck('   presence twin: that one notice names Full Disk Access', /Full Disk Access/.test(notes[0] || ''))
rmSync(home, { recursive: true, force: true })

console.log(`\n${pass}/${total} passed`)
process.exit(pass === total ? 0 : 1)
