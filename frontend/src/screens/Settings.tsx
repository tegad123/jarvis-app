import { useCallback, useEffect, useState } from 'react'
import { ErrorState } from '../components/States'
import { canSeeSettings } from '../components/TabBar'
import { ApiError, Unauthorized, disconnectGoogleConnection, getGoogleConnection } from '../lib/api'
import { absoluteTime, relativeTime } from '../lib/time'
import type { GoogleConnectionStatus, Role } from '../lib/types'

/**
 * Settings — Connect Google card. Owner and admin only.
 *
 * The API enforces require_admin + require_person. Hiding this screen is a
 * courtesy on top of that, never instead of it. Members and operators do not
 * see the tab and do not render this card.
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
        The team’s Google mailbox. Connecting here is the tenant grant, not a staff sign-in.
      </p>

      {error && (
        <ErrorState
          message={error.message}
          detail={error.detail}
          onRetry={() => void load()}
        />
      )}

      <div className="glass-featured mt-5 rounded-card px-4 py-4 lg:px-6 lg:py-5">
        <h3 className="text-row-title">Connect Google</h3>
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
    </section>
  )
}
