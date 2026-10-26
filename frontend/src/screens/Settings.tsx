import { useCallback, useEffect, useState } from 'react'
import Page from '../components/Page'
import {
  ApiError,
  Unauthorized,
  deleteWebsiteLogin,
  disconnectGoogleConnection,
  disconnectMac,
  sendReplyReminder,
  getBrowserStatus,
  getGoogleConnection,
  getMacStatus,
  getTeam,
  getWebsiteLogins,
  invitePerson,
  newMacCode,
  patchPerson,
  saveWebsiteLogin,
} from '../lib/api'
import { Button, ErrorState, Field, PairingCode, SettingsSection, type SettingsSectionProps } from '../jm'
import { Select, cx, focus } from '../jm/shared/primitives'
import { canManageTeam, canSeeSettings, ROLE_LABEL } from '../lib/roles'
import { absoluteTime, relativeTime } from '../lib/time'
import type { Feature, GoogleConnectionStatus, MacDevice, MacStatus, ReplyReminder, Role, TeamPerson, TeamResponse, WebsiteLogin } from '../lib/types'

/**
 * Settings — the signed-in person's own accounts. Any person; never the
 * operator.
 *
 * The API enforces require_person and only ever reads, connects or
 * disconnects the caller's own row. Hiding this screen from the operator is a
 * courtesy on top of that, never instead of it.
 *
 * Status GET returns status / google_email / connected_at / refreshed_at only.
 * This page never asks for a token, refresh token, or client secret.
 */

interface Props {
  role: Role
  personKey?: string | null
  features?: Partial<Record<Feature, boolean>>
  onUnauthorized: () => void
}

type Phase = 'idle' | 'disconnecting' | 'disconnected'
type Status = NonNullable<SettingsSectionProps['status']>

function statusCopy(conn: GoogleConnectionStatus | null, phase: Phase): { status: Status; time: string | null; timeAbs: string | null } {
  if (phase === 'disconnecting') return { status: { tone: 'busy', label: 'Disconnecting…' }, time: null, timeAbs: null }
  if (phase === 'disconnected' || conn?.status === 'disconnected') {
    return {
      status: { tone: 'neutral', label: phase === 'disconnected' ? 'Disconnected' : 'Never connected' },
      time: null,
      timeAbs: null,
    }
  }
  if (conn?.status === 'needs_reauth') return { status: { tone: 'action', label: 'Needs reauth' }, time: null, timeAbs: null }
  if (conn?.status === 'connected') {
    const ts = conn.refreshed_at || conn.connected_at
    if (!ts) return { status: { tone: 'done', label: 'Connected' }, time: null, timeAbs: null }
    return {
      status: { tone: 'done', label: 'Connected' },
      time: conn.refreshed_at ? `Last refreshed ${relativeTime(conn.refreshed_at)}` : `Connected ${relativeTime(conn.connected_at as string)}`,
      timeAbs: ts,
    }
  }
  return { status: { tone: 'neutral', label: 'Never connected' }, time: null, timeAbs: null }
}

export default function Settings({ role, personKey, features = {}, onUnauthorized }: Props) {
  const [conn, setConn] = useState<GoogleConnectionStatus | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [confirm, setConfirm] = useState(false)

  const load = useCallback(async () => {
    try {
      const next = await getGoogleConnection()
      setConn(next)
      setError(null)
      if (next.status !== 'disconnected') setPhase('idle')
    } catch (err) {
      if (err instanceof Unauthorized) {
        onUnauthorized()
        return
      }
      if (err instanceof ApiError) {
        setError({
          message: 'Jarvis could not read the Google connection.',
          detail: err.detail ?? (err.status ? `HTTP ${err.status}` : undefined),
        })
      } else {
        setError({ message: 'Something went wrong loading Settings.' })
      }
    }
  }, [onUnauthorized])

  useEffect(() => {
    if (!canSeeSettings(role)) return
    void load()
  }, [load, role])

  if (!canSeeSettings(role)) return null

  const copy = statusCopy(conn, phase)
  const mailbox = conn?.google_email
  const connected = conn?.status === 'connected' || conn?.status === 'needs_reauth'
  const showConnect = !connected && phase !== 'disconnecting'
  const showDisconnect = connected && phase !== 'disconnecting'

  async function onDisconnect() {
    if (!confirm) {
      setConfirm(true)
      return
    }
    setPhase('disconnecting')
    setConfirm(false)
    try {
      await disconnectGoogleConnection()
      setPhase('disconnected')
      setConn({ status: 'disconnected', google_email: null, connected_at: null, refreshed_at: null })
    } catch (err) {
      setPhase('idle')
      if (err instanceof Unauthorized) {
        onUnauthorized()
        return
      }
      setError({
        message: 'Could not disconnect Google.',
        detail: err instanceof ApiError ? (err.detail ?? (err.status ? `HTTP ${err.status}` : undefined)) : undefined,
      })
    }
  }

  return (
    <Page title="Settings" subtitle="Your own accounts. Your assistant uses them for you; nobody else on the team can.">
      {error && <ErrorState label="Settings unavailable" message={error.message} detail={error.detail} onRetry={() => void load()} />}

      <SettingsSection
        title="Your Google account"
        description="Your assistant uses it to check your calendar and email."
        status={conn || phase !== 'idle' ? copy.status : undefined}
        rows={
          mailbox && phase !== 'disconnected'
            ? [
                {
                  title: mailbox,
                  detail: copy.time && copy.timeAbs ? <span title={absoluteTime(copy.timeAbs)}>{copy.time}</span> : undefined,
                },
              ]
            : undefined
        }
        actions={
          <>
            {showConnect && (
              <Button
                variant="primary"
                onClick={() => {
                  window.location.href = '/api/google/connect/start'
                }}
              >
                Connect
              </Button>
            )}
            {showDisconnect && (
              <>
                <Button onClick={() => void onDisconnect()}>{confirm ? 'Confirm disconnect' : 'Disconnect'}</Button>
                {confirm && (
                  <Button variant="ghost" onClick={() => setConfirm(false)}>
                    Cancel
                  </Button>
                )}
              </>
            )}
          </>
        }
      />

      {role !== 'operator' && features.browser !== false && <WebsiteLogins onUnauthorized={onUnauthorized} />}
      {role !== 'operator' && features.imessage !== false && <MacMessages onUnauthorized={onUnauthorized} />}
      {canManageTeam(role) && <Team role={role} personKey={personKey ?? null} onUnauthorized={onUnauthorized} />}
    </Page>
  )
}

/** Settings → Team. Who may change whom is the API's (app/team.py); this
 *  only hides controls the API would refuse. */
function Team({ role, personKey, onUnauthorized }: { role: Role; personKey: string | null; onUnauthorized: () => void }) {
  const [team, setTeam] = useState<TeamResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [newRole, setNewRole] = useState<'member' | 'admin'>('member')
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setTeam(await getTeam())
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not read the team.')
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
  }, [load])

  if (!team) return error ? <SettingsSection title="Team" error={error} /> : null

  const owner = role === 'owner'
  const manageable = (p: TeamPerson) =>
    p.person_key !== personKey && p.role !== 'owner' && (owner || p.role === 'member')

  async function change(p: TeamPerson, patch: Parameters<typeof patchPerson>[1]) {
    setBusy(p.person_key)
    setError(null)
    setNotice(null)
    try {
      const next = await patchPerson(p.person_key, patch)
      setTeam((t) => (t ? { ...t, people: t.people.map((x) => (x.person_key === next.person_key ? next : x)) } : t))
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'That change did not save.')
    } finally {
      setBusy(null)
    }
  }

  async function invite(e: React.FormEvent) {
    e.preventDefault()
    setBusy('invite')
    setError(null)
    setNotice(null)
    try {
      const p = await invitePerson({ name: name.trim(), email: email.trim(), role: newRole })
      setName('')
      setEmail('')
      setNewRole('member')
      setNotice(`${p.name} is on the team. Send them ${window.location.origin}: they sign in with Google using ${p.email}.`)
      await load()
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not invite them.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <SettingsSection
      title="Team"
      description="Invite people by email. They sign in with Google using that address. Switch a feature off for one person and both the app and Jarvis stop offering it to them."
      error={error ?? undefined}
    >
      <ul className="m-0 grid list-none gap-jm-4 p-0" aria-label="Team members">
        {team.people.map((p) => {
          const can = manageable(p)
          const off = p.disabled
          return (
            <li key={p.person_key} className="grid gap-jm-2 border-b border-jm-border pb-jm-4 last:border-b-0" aria-label={p.name}>
              <div className="flex flex-wrap items-center gap-jm-2">
                <span className="min-w-0 flex-1">
                  <span className="block text-jm-label text-jm-fg">
                    {p.full_name || p.name}
                    {p.person_key === personKey ? ' (you)' : ''}
                  </span>
                  <span className="block text-jm-caption text-jm-fg-secondary">
                    {[p.email, off ? 'Access off' : p.last_login ? `Signed in ${relativeTime(p.last_login)}` : 'Hasn’t signed in yet']
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </span>
                {owner && can ? (
                  <Select
                    label={`Role for ${p.name}`}
                    value={p.role}
                    disabled={busy !== null}
                    options={[
                      { value: 'member', label: 'Member' },
                      { value: 'admin', label: 'Admin' },
                    ]}
                    onChange={(v) => void change(p, { role: v as 'admin' | 'member' })}
                  />
                ) : (
                  <span className="text-jm-caption text-jm-fg-secondary">{ROLE_LABEL[p.role]}</span>
                )}
                {can && (
                  <Button variant="ghost" disabled={busy !== null} onClick={() => void change(p, { disabled: !off })}>
                    {off ? 'Turn access back on' : 'Turn off access'}
                  </Button>
                )}
              </div>
              {p.role !== 'owner' && (
                <fieldset className="m-0 flex flex-wrap gap-jm-2 border-0 p-0" disabled={!can || busy !== null}>
                  <legend className="sr-only">Features for {p.name}</legend>
                  {team.features.map((f) => (
                    <label
                      key={f}
                      className={cx(
                        'inline-flex cursor-pointer items-center gap-jm-1 rounded-jm-pill border px-jm-3 py-1 text-jm-caption',
                        p.features[f] ? 'border-transparent bg-jm-accent-soft text-jm-accent-soft-text' : 'border-jm-border text-jm-fg-secondary line-through',
                        !can && 'cursor-default opacity-jm-disabled',
                      )}
                    >
                      <input
                        type="checkbox"
                        className={cx('m-0', focus)}
                        aria-label={`${team.feature_labels[f]} for ${p.name}`}
                        checked={p.features[f]}
                        onChange={(e) => void change(p, { features: { [f]: e.target.checked } })}
                      />
                      {team.feature_labels[f]}
                    </label>
                  ))}
                </fieldset>
              )}
            </li>
          )
        })}
      </ul>

      {team.invites_ready ? (
        <form onSubmit={(e) => void invite(e)} className="mt-jm-4 grid gap-jm-2 sm:grid-cols-[1fr_1fr_auto]" aria-label="Invite someone">
          <Field value={name} onChange={(e) => setName(e.target.value)} placeholder="Their name" aria-label="Their name" required />
          <Field
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Their Google email"
            aria-label="Their Google email"
            autoComplete="off"
            required
          />
          <Select
            label="Role"
            value={newRole}
            disabled={!team.can_invite_admins}
            options={[{ value: 'member', label: 'Member' }, ...(team.can_invite_admins ? [{ value: 'admin', label: 'Admin' }] : [])]}
            onChange={(v) => setNewRole(v as 'admin' | 'member')}
          />
          <Button type="submit" variant="primary" disabled={busy !== null} className="sm:col-span-3 sm:justify-self-start">
            {busy === 'invite' ? 'Inviting…' : 'Invite'}
          </Button>
          {notice && <p className="m-0 text-jm-body-sm text-jm-fg-secondary sm:col-span-3">{notice}</p>}
        </form>
      ) : (
        <p className="m-0 mt-jm-4 text-jm-body-sm text-jm-fg-secondary">Inviting isn’t set up on this account yet.</p>
      )}
    </SettingsSection>
  )
}

/** The read-only iMessage helper. Pairing is a one-time code the Mac claims. */
/** The Mac syncs every two minutes; well past that, it has stopped. */
const MAC_STALE_MS = 10 * 60 * 1000

function macSyncing(lastSync: string | null): boolean {
  return !!lastSync && Date.now() - Date.parse(lastSync) < MAC_STALE_MS
}

function macStatus(devices: MacDevice[]): { tone: 'done' | 'action' | 'neutral'; label: string } {
  if (devices.length === 0) return { tone: 'neutral', label: 'Not connected' }
  if (devices.some((d) => macSyncing(d.last_sync_at))) return { tone: 'done', label: 'Syncing' }
  if (devices.every((d) => !d.last_sync_at)) return { tone: 'action', label: 'Waiting for the Mac' }
  return { tone: 'action', label: 'Not syncing' }
}

function macDetail(lastSync: string | null): string {
  if (!lastSync) return 'paired, but the Mac hasn’t synced yet: open Jarvis on it and turn on Full Disk Access'
  if (macSyncing(lastSync)) return `synced ${relativeTime(lastSync)}`
  return `last synced ${relativeTime(lastSync)}: open Jarvis on the Mac to see what’s wrong`
}

function MacMessages({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [st, setSt] = useState<MacStatus | null>(null)
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setSt(await getMacStatus())
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not read the Mac connection.')
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (!code) return
    const t = window.setInterval(() => void load(), 4000)
    return () => window.clearInterval(t)
  }, [code, load])

  useEffect(() => {
    if (code && st && st.devices.length > 0) setCode(null)
  }, [code, st])

  async function pair() {
    setBusy(true)
    setError(null)
    try {
      setCode(await newMacCode())
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not make a code.')
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    setBusy(true)
    try {
      await disconnectMac(id)
      await load()
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not disconnect that Mac.')
    } finally {
      setBusy(false)
    }
  }

  if (!st) return error ? <SettingsSection title="iMessage" error={error} /> : null

  return (
    <SettingsSection
      title="iMessage"
      description={
        <>
          A small helper on your Mac lets Jarvis read your texts from the last {st.retention_days} days. Reading only: Jarvis can’t send
          a text. Disconnecting deletes everything it synced.
        </>
      }
      status={macStatus(st.devices)}
      rows={st.devices.map((d) => ({
        id: d.device_id,
        title: d.name,
        detail: `${st.messages} messages · ${macDetail(d.last_sync_at)}`,
        action: { label: 'Disconnect', disabled: busy, onClick: () => void remove(d.device_id) },
      }))}
      error={error ?? undefined}
      actions={
        !code && (
          <>
            <Button variant="primary" disabled={busy} onClick={() => void pair()}>
              {st.devices.length > 0 ? 'Connect another Mac' : 'Connect a Mac'}
            </Button>
            {st.devices.length > 0 && st.mac_app && (
              <Button variant="secondary" onClick={() => (window.location.href = '/api/imessage/mac-app')}>
                Update the Mac app
              </Button>
            )}
          </>
        )
      }
    >
      {code && st.mac_app && (
        <ol className="m-0 grid gap-jm-3 pl-jm-5 text-jm-body-sm text-jm-fg-secondary" data-testid="mac-steps">
          <li>
            <span className="text-jm-fg">Download Jarvis for Mac</span> and open it.
            <div className="mt-jm-2">
              <Button variant="primary"onClick={() => (window.location.href = '/api/imessage/mac-app')}>
                Download for Mac
              </Button>
            </div>
          </li>
          <li>
            The first time, macOS asks whether to open it: choose <span className="text-jm-fg">Open</span>, or go to System Settings →
            Privacy &amp; Security and click <span className="text-jm-fg">Open Anyway</span>.
          </li>
          <li>
            Type this code when Jarvis asks:
            <div className="mt-jm-2">
              <PairingCode code={code.code} expires={absoluteTime(code.expires_at)} instruction={null} />
            </div>
          </li>
          <li>Turn on Full Disk Access for Jarvis when it shows you where. That’s it: it keeps up in the background.</li>
        </ol>
      )}
      {code && !st.mac_app && <PairingCode code={code.code} expires={absoluteTime(code.expires_at)} />}
      {st.reminder?.enabled && st.devices.length > 0 && (
        <ReplyReminderNote reminder={st.reminder} onUnauthorized={onUnauthorized} />
      )}
    </SettingsSection>
  )
}

function clockLabel(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number)
  return `${h % 12 || 12}${m ? `:${String(m).padStart(2, '0')}` : ''} ${h < 12 ? 'AM' : 'PM'}`
}

function listLabel(items: string[]): string {
  return items.length <= 1 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`
}

/** The twice-daily "texts waiting for your reply" email. It goes from the
 *  person's own Gmail to that same address, so without one it can't go. */
function ReplyReminderNote({ reminder, onUnauthorized }: { reminder: ReplyReminder; onUnauthorized: () => void }) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const when = listLabel(reminder.times_local.map(clockLabel))

  async function sendNow() {
    setBusy(true)
    setNote(null)
    try {
      const r = await sendReplyReminder()
      setNote(r.waiting ? `Sent: ${r.waiting} waiting for your reply.` : 'Sent: nothing is waiting for your reply.')
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setNote(err instanceof ApiError ? err.message : 'The email could not be sent.')
    } finally {
      setBusy(false)
    }
  }

  let text: string
  if (!reminder.send_on) text = `Reply reminders are paused: sending is switched off for you.`
  else if (!reminder.to) text = `Connect your Google account above and Jarvis will email you at ${when} with the texts still waiting for your reply.`
  else text = `At ${when}, Jarvis emails ${reminder.to} the texts still waiting for your reply.`

  return (
    <div className="grid gap-jm-2 text-jm-body-sm text-jm-fg-secondary" data-testid="reply-reminder">
      <p className="m-0">{text}</p>
      {reminder.to && reminder.send_on && (
        <div className="flex flex-wrap items-center gap-jm-3">
          <Button variant="secondary" disabled={busy} onClick={() => void sendNow()}>
            Email me the list now
          </Button>
          {note && <span role="status">{note}</span>}
        </div>
      )}
    </div>
  )
}

/** Saved website logins for Jarvis's browser. The API never returns a
 *  password; the browser types it into the site's own form. */
function WebsiteLogins({ onUnauthorized }: { onUnauthorized: () => void }) {
  const [enabled, setEnabled] = useState(false)
  const [logins, setLogins] = useState<WebsiteLogin[]>([])
  const [site, setSite] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const st = await getBrowserStatus()
      setEnabled(st.enabled)
      if (st.enabled) setLogins((await getWebsiteLogins()).logins)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not read the saved logins.')
    }
  }, [onUnauthorized])

  useEffect(() => {
    void load()
  }, [load])

  if (!enabled) return null

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await saveWebsiteLogin(site.trim(), username.trim(), password)
      setSite('')
      setUsername('')
      setPassword('')
      await load()
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not save that login.')
    } finally {
      setBusy(false)
    }
  }

  async function remove(s: string) {
    setBusy(true)
    try {
      await deleteWebsiteLogin(s)
      await load()
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError ? err.message : 'Could not remove that login.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <SettingsSection
      title="Website logins"
      description="Jarvis signs in with these when you approve a task on that site. It never sees the password: the browser types it into the site’s own sign-in form."
      rows={logins.map((l) => ({
        id: l.site,
        title: l.site,
        detail: `${l.username}${l.last_used_at ? ` · last used ${relativeTime(l.last_used_at)}` : ' · not used yet'}`,
        action: { label: 'Remove', disabled: busy, onClick: () => void remove(l.site) },
      }))}
      error={error ?? undefined}
    >
      <form onSubmit={(e) => void add(e)} className="grid gap-jm-2 sm:grid-cols-3">
        <Field value={site} onChange={(e) => setSite(e.target.value)} placeholder="Site, e.g. zillow.com" aria-label="Site" required />
        <Field
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Email or username"
          aria-label="Email or username"
          autoComplete="off"
          required
        />
        <Field
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          aria-label="Password"
          autoComplete="new-password"
          required
        />
        <Button type="submit" variant="primary" disabled={busy} className="sm:col-span-3 sm:justify-self-start">
          Save login
        </Button>
      </form>
    </SettingsSection>
  )
}
