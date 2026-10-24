import { useCallback, useEffect, useState } from 'react'
import Page from '../components/Page'
import {
  ApiError,
  Unauthorized,
  deleteWebsiteLogin,
  disconnectGoogleConnection,
  disconnectMac,
  getBrowserStatus,
  getGoogleConnection,
  getMacStatus,
  getWebsiteLogins,
  newMacCode,
  saveWebsiteLogin,
} from '../lib/api'
import { Button, ErrorState, Field, PairingCode, SettingsSection, type SettingsSectionProps } from '../jm'
import { canSeeSettings } from '../lib/roles'
import { absoluteTime, relativeTime } from '../lib/time'
import type { GoogleConnectionStatus, MacStatus, Role, WebsiteLogin } from '../lib/types'

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

export default function Settings({ role, onUnauthorized }: Props) {
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

      {role !== 'operator' && <WebsiteLogins onUnauthorized={onUnauthorized} />}
      {role !== 'operator' && <MacMessages onUnauthorized={onUnauthorized} />}
    </Page>
  )
}

/** The read-only iMessage helper. Pairing is a one-time code the Mac claims. */
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
      status={st.devices.length > 0 ? { tone: 'done', label: 'Connected' } : { tone: 'neutral', label: 'Not connected' }}
      rows={st.devices.map((d) => ({
        id: d.device_id,
        title: d.name,
        detail: `${st.messages} messages${d.last_sync_at ? ` · synced ${relativeTime(d.last_sync_at)}` : ' · waiting for the first sync'}`,
        action: { label: 'Disconnect', disabled: busy, onClick: () => void remove(d.device_id) },
      }))}
      error={error ?? undefined}
      actions={
        !code && (
          <Button variant="primary" disabled={busy} onClick={() => void pair()}>
            {st.devices.length > 0 ? 'Connect another Mac' : 'Connect a Mac'}
          </Button>
        )
      }
    >
      {code && <PairingCode code={code.code} expires={absoluteTime(code.expires_at)} />}
    </SettingsSection>
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
