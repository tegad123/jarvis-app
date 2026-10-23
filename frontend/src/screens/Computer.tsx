import { useCallback, useEffect, useRef, useState } from 'react'
import { ErrorState } from '../components/States'
import {
  ApiError,
  Unauthorized,
  browserInput,
  closeBrowser,
  getBrowserScreen,
  getBrowserStatus,
  setBrowserTakeover,
} from '../lib/api'
import { DOT_BG } from '../lib/kinds'
import { absoluteTime } from '../lib/time'
import type { BrowserInput, BrowserScreen, BrowserStatus } from '../lib/types'

/**
 * Computer — the owner watching Jarvis's browser, and taking it over.
 *
 * Owner-only server-side (app/browser.py require_owner). While the owner holds
 * the browser the API refuses every agent action, so taking over is how a
 * two-factor code or a CAPTCHA gets past: the owner types it, then hands back.
 * The picture is a still refreshed every POLL_MS, not a stream.
 */

interface Props {
  onUnauthorized: () => void
}

const POLL_MS = 1500
const KEYS = ['Enter', 'Tab', 'Backspace', 'Escape'] as const

export default function Computer({ onUnauthorized }: Props) {
  const [status, setStatus] = useState<BrowserStatus | null>(null)
  const [screen, setScreen] = useState<BrowserScreen | null>(null)
  const [error, setError] = useState<{ message: string; detail?: string } | null>(null)
  const [text, setText] = useState('')
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const img = useRef<HTMLImageElement>(null)

  const fail = useCallback(
    (err: unknown, message: string) => {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError({ message, detail: err instanceof ApiError ? err.detail ?? `HTTP ${err.status}` : undefined })
    },
    [onUnauthorized],
  )

  const refresh = useCallback(async () => {
    try {
      const st = await getBrowserStatus()
      setStatus(st)
      if (st.enabled) setScreen(await getBrowserScreen())
      setError(null)
    } catch (err) {
      fail(err, 'Jarvis could not show the browser.')
    }
  }, [fail])

  useEffect(() => {
    void refresh()
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, POLL_MS)
    return () => window.clearInterval(t)
  }, [refresh])

  const takeover = screen?.taken_over ?? status?.taken_over ?? false

  async function send(input: BrowserInput) {
    setBusy(true)
    try {
      const r = await browserInput(input)
      if (!r.ok && r.error) setError({ message: r.error })
      await refresh()
    } catch (err) {
      fail(err, 'That did not reach the browser.')
    } finally {
      setBusy(false)
    }
  }

  async function toggle(on: boolean) {
    setBusy(true)
    try {
      await setBrowserTakeover(on)
      await refresh()
    } catch (err) {
      fail(err, on ? 'Could not take over the browser.' : 'Could not hand the browser back.')
    } finally {
      setBusy(false)
    }
  }

  function onPicture(e: React.MouseEvent<HTMLImageElement>) {
    if (!takeover || !img.current || !screen?.width || !screen?.height) return
    const box = img.current.getBoundingClientRect()
    void send({
      kind: 'click',
      x: ((e.clientX - box.left) / box.width) * screen.width,
      y: ((e.clientY - box.top) / box.height) * screen.height,
    })
  }

  if (status && !status.enabled) {
    return (
      <section>
        <h2 className="text-screen-title">Computer</h2>
        <p className="mt-2 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          The browser isn’t switched on for this assistant.
        </p>
      </section>
    )
  }

  const btn =
    'rounded-pill border border-light-border px-4 py-2 text-body-s font-semibold ' +
    'text-light-text-primary disabled:opacity-50 dark:border-dark-border dark:text-dark-text-primary'

  return (
    <section className="grid min-w-0 gap-4">
      <header>
        <h2 className="text-screen-title">Computer</h2>
        <p className="mt-0.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          Jarvis’s web browser. Take over to type a code or sign in yourself; Jarvis waits until you hand it back.
        </p>
      </header>

      {error && <ErrorState message={error.message} detail={error.detail} onRetry={() => void refresh()} />}

      <div className="glass-featured min-w-0 rounded-card p-3 lg:p-4">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`block h-[8px] w-[8px] shrink-0 rounded-full ${
              DOT_BG[takeover ? 'warning' : screen?.running ? 'success' : 'neutral']
            }`}
            aria-hidden="true"
          />
          <p className="min-w-0 truncate text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            {takeover
              ? 'You have the browser'
              : screen?.running
                ? screen.title || screen.url
                : 'Closed. It opens when Jarvis needs it.'}
          </p>
        </div>
        {screen?.url && (
          <p className="mt-1 truncate font-mono text-label-mono text-light-text-muted dark:text-dark-text-muted">
            {screen.url}
          </p>
        )}

        {screen?.running && screen.jpeg && (
          <img
            ref={img}
            alt={screen.title ? `The page: ${screen.title}` : 'The browser'}
            src={`data:image/jpeg;base64,${screen.jpeg}`}
            onClick={onPicture}
            className={`mt-3 w-full rounded-list border border-light-border dark:border-dark-border ${
              takeover ? 'cursor-crosshair' : ''
            }`}
          />
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void toggle(!takeover)}
            className={
              takeover
                ? btn
                : 'rounded-pill bg-light-accent px-4 py-2 text-body-s font-semibold text-white ' +
                  'shadow-accent-btn disabled:opacity-50 dark:bg-dark-accent-fill'
            }
          >
            {takeover ? 'Hand back to Jarvis' : 'Take over'}
          </button>
          {screen?.running && (
            <button type="button" disabled={busy} onClick={() => void closeBrowser().then(refresh)} className={btn}>
              Close browser
            </button>
          )}
        </div>

        {takeover && (
          <div className="mt-3 grid gap-2">
            <form
              className="flex min-w-0 gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (!address.trim()) return
                const url = /^https?:\/\//.test(address.trim()) ? address.trim() : `https://${address.trim()}`
                void send({ kind: 'open', url })
              }}
            >
              <input
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Go to an address"
                className="min-w-0 flex-1 rounded-btn border border-light-border bg-transparent px-3 py-2 text-body-s
                           dark:border-dark-border"
              />
              <button type="submit" disabled={busy} className={btn}>
                Go
              </button>
            </form>
            <form
              className="flex min-w-0 gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                if (!text) return
                void send({ kind: 'type', text }).then(() => setText(''))
              }}
            >
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Click a box in the picture, then type here"
                autoComplete="off"
                className="min-w-0 flex-1 rounded-btn border border-light-border bg-transparent px-3 py-2 text-body-s
                           dark:border-dark-border"
              />
              <button type="submit" disabled={busy} className={btn}>
                Type
              </button>
            </form>
            <div className="flex flex-wrap gap-2">
              {KEYS.map((k) => (
                <button key={k} type="button" disabled={busy} onClick={() => void send({ kind: 'key', key: k })} className={btn}>
                  {k}
                </button>
              ))}
              <button type="button" disabled={busy} onClick={() => void send({ kind: 'scroll' })} className={btn}>
                Scroll
              </button>
              <button type="button" disabled={busy} onClick={() => void send({ kind: 'back' })} className={btn}>
                Back
              </button>
            </div>
          </div>
        )}
      </div>

      {!!status?.access?.length && (
        <div className="glass-featured rounded-card px-4 py-3">
          <h3 className="text-row-title">Jarvis can act on</h3>
          <ul className="mt-1 grid gap-1">
            {status.access.map((a) => (
              <li key={a.site} className="text-body-s text-light-text-secondary dark:text-dark-text-secondary">
                {a.site}
                {a.until && <span title={absoluteTime(a.until)}> until {new Date(a.until).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
