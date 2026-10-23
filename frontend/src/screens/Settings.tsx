import { useCallback, useEffect, useState } from 'react'
import { ErrorState } from '../components/States'
import { canSeeSettings } from '../components/TabBar'
import {
  ApiError,
  Unauthorized,
  deleteWebsiteLogin,
  disconnectGoogleConnection,
  getBrowserStatus,
  getGoogleConnection,
  getWebsiteLogins,
  saveWebsiteLogin,
} from '../lib/api'
import { absoluteTime, relativeTime } from '../lib/time'
import type { GoogleConnectionStatus, Role, WebsiteLogin } from '../lib/types'

/**
 * Settings — the signed-in person's own Google account. Any person; never the
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

function statusCopy(
  conn: GoogleConnectionStatus | null,
  phase: Phase,
): { status: string; time: string | null; timeAbs: string | null } {
  if (phase === 'disconnecting') {
    return { status: 'Disconnecting…', time: null, timeAbs: null }
  }
  if (phase === 'disconnected' || conn?.status === 'disconnected') {
    return {
      status: phase === 'disconnected' ? 'Disconnected' : 'Never connected',
      time: null,
      timeAbs: null,
    }
  }
  if (conn?.status === 'needs_reauth') {
    return { status: 'Needs reauth', time: null, timeAbs: null }
  }
  if (conn?.status === 'connected') {
    const ts = conn.refreshed_at || conn.connected_at
    if (!ts) return { status: 'Connected', time: null, timeAbs: null }
    return {
      status: 'Connected',
      time: conn.refreshed_at
        ? `Last refreshed ${relativeTime(conn.refreshed_at)}`
        : `Connected ${relativeTime(conn.connected_at as string)}`,
      timeAbs: ts,
    }
  }
  return { status: 'Never connected', time: null, timeAbs: null }
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
      if (err instanceof ApiError) {
        setError({
          message: 'Could not disconnect Google.',
          detail: err.detail ?? (err.status ? `HTTP ${err.status}` : undefined),
        })
      } else {
        setError({ message: 'Could not disconnect Google.' })
      }
    }
  }

  return (
    <section>
      <h2 className="text-screen-title">Settings</h2>
      <p className="mt-1 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
        Your own Google account. Your assistant uses it to check your calendar and email; nobody else on the team can.
      </p>

      {error && (
        <ErrorState
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
        />
      )}

      <div className="glass-featured mt-5 rounded-card px-4 py-4 lg:px-6 lg:py-5">
        <h3 className="text-row-title">Your Google account</h3>
        <p className="mt-2 text-body-s text-light-text-primary dark:text-dark-text-primary">
          {copy.status}
        </p>
        {mailbox && phase !== 'disconnected' && (
          <p className="mt-1 truncate text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            {mailbox}
          </p>
        )}
        {copy.time && copy.timeAbs && (
          <p
            className="mt-1 text-caption text-light-text-muted dark:text-dark-text-muted"
            title={absoluteTime(copy.timeAbs)}
          >
            {copy.time}
          </p>
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          {showConnect && (
            <button
              type="button"
              onClick={() => {
                window.location.href = '/api/google/connect/start'
              }}
              className="rounded-pill bg-light-accent px-5 py-2.5 text-body font-semibold
                         text-white shadow-accent-btn dark:bg-dark-accent-fill
                         lg:transition-[opacity,box-shadow,transform] lg:duration-[120ms]
                         lg:hover:-translate-y-px lg:hover:shadow-glass"
            >
              Connect
            </button>
          )}
          {showDisconnect && (
            <>
              <button
                type="button"
                onClick={() => void onDisconnect()}
                className="rounded-pill border border-light-border px-5 py-2.5 text-body
                           font-semibold text-light-text-primary
                           dark:border-dark-border dark:text-dark-text-primary"
              >
                {confirm ? 'Confirm disconnect' : 'Disconnect'}
              </button>
              {confirm && (
                <button
                  type="button"
                  onClick={() => setConfirm(false)}
                  className="text-caption text-light-text-muted underline-offset-2 hover:underline
                             dark:text-dark-text-muted"
                >
                  Cancel
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {role !== 'operator' && <WebsiteLogins onUnauthorized={onUnauthorized} />}
    </section>
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

  const field =
    'min-w-0 rounded-btn border border-light-border bg-transparent px-3 py-2 text-body-s dark:border-dark-border'

  return (
    <div className="glass-featured mt-5 rounded-card px-4 py-4 lg:px-6 lg:py-5">
      <h3 className="text-row-title">Website logins</h3>
      <p className="mt-1 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
        Jarvis signs in with these when you approve a task on that site. It never sees the password: the browser
        types it into the site’s own sign-in form.
      </p>
      {error && <p className="mt-2 text-body-s text-light-alert dark:text-dark-alert">{error}</p>}
      {logins.length > 0 && (
        <ul className="mt-3 grid gap-2">
          {logins.map((l) => (
            <li key={l.site} className="flex min-w-0 items-center gap-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-body-s text-light-text-primary dark:text-dark-text-primary">{l.site}</p>
                <p className="truncate text-caption text-light-text-muted dark:text-dark-text-muted">
                  {l.username}
                  {l.last_used_at ? ` · last used ${relativeTime(l.last_used_at)}` : ' · not used yet'}
                </p>
              </div>
              <button
                type="button"
                disabled={busy}
                onClick={() => void remove(l.site)}
                className="text-caption text-light-text-muted underline-offset-2 hover:underline dark:text-dark-text-muted"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={(e) => void add(e)} className="mt-4 grid gap-2 sm:grid-cols-3">
        <input value={site} onChange={(e) => setSite(e.target.value)} placeholder="Site, e.g. zillow.com" required className={field} />
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          placeholder="Email or username"
          autoComplete="off"
          required
          className={field}
        />
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoComplete="new-password"
          required
          className={field}
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-pill bg-light-accent px-5 py-2.5 text-body font-semibold text-white shadow-accent-btn
                     disabled:opacity-50 dark:bg-dark-accent-fill sm:col-span-3 sm:justify-self-start"
        >
          Save login
        </button>
      </form>
    </div>
  )
}
