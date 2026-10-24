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
import { ComputerCard, type ComputerInput } from '../jm'
import type { Approval, BrowserInput, BrowserScreen, BrowserStatus } from '../lib/types'

/**
 * Jarvis's web browser, docked above the composer. A small live picture while
 * Jarvis uses the browser; tap it to make it big. Taking over is how a
 * sign-in, a code or a CAPTCHA gets past: what you type here goes to the page,
 * never into the chat, and Jarvis waits until you hand it back. Asking to act
 * on a site shows up here too, so you approve it without leaving the chat.
 *
 * Each person's own browser (app/browser.py); the server refuses an operator.
 * The picture is a still refreshed every few seconds, not a stream.
 */

interface Props {
  /** Poll faster while Jarvis is answering. */
  busy: boolean
  hidden: boolean
  onHiddenChange: (hidden: boolean) => void
  /** Whether there is anything to show — the chat header offers it back when hidden. */
  onAvailableChange: (available: boolean) => void
  onUnauthorized: () => void
}

function isBrowserAsk(a: Approval): boolean {
  return a.status === 'pending' && String(a.payload?.type ?? '').startsWith('browser') && a.can_approve !== false
}

export default function BrowserWindow({ busy, hidden, onHiddenChange, onAvailableChange, onUnauthorized }: Props) {
  const [status, setStatus] = useState<BrowserStatus | null>(null)
  const [screen, setScreen] = useState<BrowserScreen | null>(null)
  const [asks, setAsks] = useState<Approval[]>([])
  const [big, setBig] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [working, setWorking] = useState(false)
  const lastUrl = useRef<string | null>(null)
  const onHiddenRef = useRef(onHiddenChange)
  onHiddenRef.current = onHiddenChange

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
      if ((sc.running && sc.url && sc.url !== lastUrl.current) || mine.length) onHiddenRef.current(false)
      lastUrl.current = sc.url ?? null
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorized()
    }
  }, [onUnauthorized])

  const takeover = screen?.taken_over ?? status?.taken_over ?? false
  const running = !!screen?.running
  const available = !!status?.enabled && (running || takeover || asks.length > 0)

  useEffect(() => {
    onAvailableChange(available)
  }, [available, onAvailableChange])

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

  function onInput(evt: ComputerInput) {
    if (evt.kind === 'click') {
      // The card reports where on the picture; the page wants its own pixels.
      if (!screen?.width || !screen?.height) return
      void send({ kind: 'click', x: evt.x * screen.width, y: evt.y * screen.height })
      return
    }
    void send(evt)
  }

  if (!available || hidden) return null

  return (
    <ComputerCard
      state={takeover ? 'yours' : asks.length ? 'needs-you' : running && screen?.jpeg ? 'working' : 'starting'}
      screenshot={running && screen?.jpeg ? `data:image/jpeg;base64,${screen.jpeg}` : undefined}
      title={screen?.title ?? undefined}
      notice={running ? screen?.site_refused ?? undefined : undefined}
      url={screen?.url ?? undefined}
      asks={asks.map((a) => ({
        id: a.id,
        kind: a.payload?.type === 'browser_task' ? 'task' : 'once',
        summary: a.summary,
        onAllow: () => void act(() => decideApproval(a.id, 'approve'), 'Could not approve that.'),
        onNotNow: () => void act(() => decideApproval(a.id, 'reject'), 'Could not decline that.'),
      }))}
      expanded={big}
      onToggleExpand={() => setBig(!big)}
      onHide={() => onHiddenChange(true)}
      onTakeOver={() => {
        setBig(true)
        void act(() => setBrowserTakeover(true), 'Could not take over the browser.')
      }}
      onDone={() => void act(() => setBrowserTakeover(false), 'Could not hand the browser back.')}
      onClose={running ? () => void act(() => closeBrowser(), 'Could not close the browser.') : undefined}
      onInput={onInput}
      busy={working}
      error={error ?? undefined}
      className={big ? 'w-full' : 'justify-self-end'}
    />
  )
}
