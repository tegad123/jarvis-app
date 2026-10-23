import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ApiError,
  Unauthorized,
  browserInput,
  closeBrowser,
  decideApproval,
  getApprovals,
  getBrowserScreen,
  getBrowserStatus,
  setBrowserTakeover,
} from '../lib/api'
import { DOT_BG } from '../lib/kinds'
import type { Approval, BrowserInput, BrowserScreen, BrowserStatus } from '../lib/types'

/**
 * Jarvis's web browser, inside the chat. A small live picture floats over the
 * conversation while Jarvis uses the browser; tap it to make it big. Taking
 * over is how a sign-in, a code or a CAPTCHA gets past: what you type here
 * goes to the page, never into the chat, and Jarvis waits until you hand it
 * back. Asking to act on a site shows up here too, so you approve it without
 * leaving the conversation.
 *
 * Each person's own browser (app/browser.py); the server refuses an operator.
 * The picture is a still refreshed every few seconds, not a stream.
 */

interface Props {
  /** Poll faster while Jarvis is answering. */
  busy: boolean
  onUnauthorized: () => void
}

const KEYS = ['Enter', 'Tab', 'Backspace', 'Escape'] as const

function isBrowserAsk(a: Approval): boolean {
  return a.status === 'pending' && String(a.payload?.type ?? '').startsWith('browser') && a.can_approve !== false
}

export default function BrowserWindow({ busy, onUnauthorized }: Props) {
  const [status, setStatus] = useState<BrowserStatus | null>(null)
  const [screen, setScreen] = useState<BrowserScreen | null>(null)
  const [asks, setAsks] = useState<Approval[]>([])
  const [big, setBig] = useState(false)
  const [hidden, setHidden] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [address, setAddress] = useState('')
  const [working, setWorking] = useState(false)
  const img = useRef<HTMLImageElement>(null)
  const lastUrl = useRef<string | null>(null)

  const fail = useCallback(
    (err: unknown, message: string) => {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError(err instanceof ApiError && err.detail ? `${message} ${err.detail}` : message)
    },
    [onUnauthorized],
  )

  const refresh = useCallback(async () => {
    try {
      const st = await getBrowserStatus()
      setStatus(st)
      if (!st.enabled) return
      const [sc, ap] = await Promise.all([getBrowserScreen(), getApprovals()])
      setScreen(sc)
      const mine = ap.approvals.filter(isBrowserAsk)
      setAsks(mine)
      // Something new to look at brings the window back.
      if ((sc.running && sc.url && sc.url !== lastUrl.current) || mine.length) setHidden(false)
      lastUrl.current = sc.url ?? null
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorized()
    }
  }, [onUnauthorized])

  const takeover = screen?.taken_over ?? status?.taken_over ?? false
  const running = !!screen?.running

  useEffect(() => {
    void refresh()
    const ms = busy || takeover ? 1500 : running ? 3000 : 6000
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refresh()
    }, ms)
    return () => window.clearInterval(t)
  }, [refresh, busy, takeover, running])

  async function act(fn: () => Promise<unknown>, message: string) {
    setWorking(true)
    setError(null)
    try {
      await fn()
      await refresh()
    } catch (err) {
      fail(err, message)
    } finally {
      setWorking(false)
    }
  }

  function send(input: BrowserInput) {
    return act(async () => {
      const r = await browserInput(input)
      if (!r.ok && r.error) setError(r.error)
    }, 'That did not reach the browser.')
  }

  function onPicture(e: React.MouseEvent<HTMLImageElement>) {
    if (!big) return setBig(true)
    if (!takeover || !img.current || !screen?.width || !screen?.height) return
    const box = img.current.getBoundingClientRect()
    void send({
      kind: 'click',
      x: ((e.clientX - box.left) / box.width) * screen.width,
      y: ((e.clientY - box.top) / box.height) * screen.height,
    })
  }

  if (!status?.enabled || hidden || (!running && !takeover && asks.length === 0)) return null

  const btn =
    'whitespace-nowrap rounded-pill border border-light-border px-3 py-1.5 text-caption font-semibold ' +
    'text-light-text-primary disabled:opacity-50 dark:border-dark-border dark:text-dark-text-primary'
  const primary =
    'whitespace-nowrap rounded-pill bg-light-accent px-3 py-1.5 text-caption font-semibold text-white ' +
    'shadow-accent-btn disabled:opacity-50 dark:bg-dark-accent-fill'
  const input =
    'min-w-0 flex-1 rounded-btn border border-light-border bg-transparent px-3 py-1.5 text-body-s ' +
    'dark:border-dark-border'

  return (
    <aside
      aria-label="Jarvis's browser"
      className={`glass-featured fixed z-30 overflow-y-auto rounded-card shadow-card
                  right-3 bottom-[calc(140px+env(safe-area-inset-bottom))]
                  max-h-[calc(100dvh-230px-env(safe-area-inset-bottom))]
                  ${big ? 'left-3 md:left-auto md:w-[560px]' : 'w-[240px]'}`}
    >
      <div className="flex min-w-0 items-center gap-2 px-3 pt-2.5">
        <span
          aria-hidden="true"
          className={`block h-[7px] w-[7px] shrink-0 rounded-full ${
            DOT_BG[takeover ? 'warning' : running ? 'success' : 'neutral']
          }`}
        />
        <p className="min-w-0 flex-1 truncate text-caption text-light-text-secondary dark:text-dark-text-secondary">
          {takeover ? 'You have the browser' : running ? screen?.title || screen?.url : 'Jarvis’s browser'}
        </p>
        <button
          type="button"
          onClick={() => setBig(!big)}
          aria-label={big ? 'Make the browser small' : 'Make the browser big'}
          className="grid h-7 w-7 place-items-center rounded-btn text-light-text-secondary dark:text-dark-text-secondary"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
               strokeLinecap="round" aria-hidden="true">
            {big ? <path d="M5 19 19 5M5 19v-6M5 19h6" /> : <path d="M19 5 5 19M19 5v6M19 5h-6" />}
          </svg>
        </button>
        <button
          type="button"
          onClick={() => setHidden(true)}
          aria-label="Hide the browser"
          className="grid h-7 w-7 place-items-center rounded-btn text-light-text-secondary dark:text-dark-text-secondary"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
               strokeLinecap="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </button>
      </div>

      {asks.map((a) => (
        <div key={a.id} className="mx-3 mt-2 rounded-list border border-light-border p-2.5 dark:border-dark-border">
          <p className="text-body-s text-light-text-primary dark:text-dark-text-primary">{a.summary}</p>
          <div className="mt-2 flex gap-2">
            <button type="button" disabled={working} className={primary}
                    onClick={() => void act(() => decideApproval(a.id, 'approve'), 'Could not approve that.')}>
              {a.payload?.type === 'browser_task' ? 'Allow for 30 min' : 'Allow once'}
            </button>
            <button type="button" disabled={working} className={btn}
                    onClick={() => void act(() => decideApproval(a.id, 'reject'), 'Could not decline that.')}>
              Not now
            </button>
          </div>
        </div>
      ))}

      {running && screen?.jpeg && (
        <img
          ref={img}
          alt={screen.title ? `The page: ${screen.title}` : 'The browser'}
          src={`data:image/jpeg;base64,${screen.jpeg}`}
          onClick={onPicture}
          className={`mx-3 mt-2 block w-[calc(100%-1.5rem)] rounded-list border border-light-border
                      dark:border-dark-border ${takeover && big ? 'cursor-crosshair' : 'cursor-pointer'}`}
        />
      )}
      {big && screen?.url && (
        <p className="mx-3 mt-1 truncate font-mono text-label-mono text-light-text-muted dark:text-dark-text-muted">
          {screen.url}
        </p>
      )}

      {error && <p role="alert" className="mx-3 mt-2 text-caption text-light-alert dark:text-dark-alert">{error}</p>}

      <div className="flex flex-wrap items-center gap-2 px-3 py-2.5">
        <button type="button" disabled={working} className={takeover ? btn : primary}
                onClick={() => {
                  setBig(true)
                  void act(() => setBrowserTakeover(!takeover),
                           takeover ? 'Could not hand the browser back.' : 'Could not take over the browser.')
                }}>
          {takeover ? 'Hand back to Jarvis' : 'Take over'}
        </button>
        {big && running && (
          <button type="button" disabled={working} className={btn}
                  onClick={() => void act(() => closeBrowser(), 'Could not close the browser.')}>
            Close browser
          </button>
        )}
      </div>

      {big && takeover && (
        <div className="grid gap-2 px-3 pb-3">
          <p className="text-caption text-light-text-muted dark:text-dark-text-muted">
            Tap a box in the picture, then type. What you type goes to the page, not the chat.
          </p>
          <form className="flex min-w-0 gap-2" onSubmit={(e) => {
            e.preventDefault()
            if (text) void send({ kind: 'type', text }).then(() => setText(''))
          }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type into the page"
                   autoComplete="off" className={input} />
            <button type="submit" disabled={working} className={btn}>Type</button>
          </form>
          <form className="flex min-w-0 gap-2" onSubmit={(e) => {
            e.preventDefault()
            const a = address.trim()
            if (a) void send({ kind: 'open', url: /^https?:\/\//.test(a) ? a : `https://${a}` })
          }}>
            <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Go to an address"
                   className={input} />
            <button type="submit" disabled={working} className={btn}>Go</button>
          </form>
          <div className="flex flex-wrap gap-2">
            {KEYS.map((k) => (
              <button key={k} type="button" disabled={working} className={btn}
                      onClick={() => void send({ kind: 'key', key: k })}>{k}</button>
            ))}
            <button type="button" disabled={working} className={btn} onClick={() => void send({ kind: 'scroll' })}>
              Scroll
            </button>
            <button type="button" disabled={working} className={btn} onClick={() => void send({ kind: 'back' })}>
              Back
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}
