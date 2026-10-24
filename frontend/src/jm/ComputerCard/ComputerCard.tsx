import { useState, type MouseEvent } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { Dots, ErrorLine, Field, LiveDot, Tag, cx, focus, truncate } from '../shared/primitives'

/** Matches lib/types BrowserInput, except click x/y are 0–1 fractions of the picture. */
export type ComputerInput =
  | { kind: 'click'; x: number; y: number }
  | { kind: 'type'; text: string }
  | { kind: 'open'; url: string }
  | { kind: 'key'; key: 'Enter' | 'Tab' | 'Backspace' | 'Escape' }
  | { kind: 'scroll' }
  | { kind: 'back' }

export interface ComputerAsk {
  id?: string
  /** 'task' reads "Allow for 30 min"; anything else "Allow once". */
  kind?: 'task' | 'once'
  summary: string
  onAllow?: () => void
  onNotNow?: () => void
}

export interface ComputerCardProps {
  state: 'starting' | 'working' | 'needs-you' | 'yours'
  /** Image URL of the latest still (e.g. data:image/jpeg;base64,…). */
  screenshot?: string
  title?: string
  url?: string
  asks?: ComputerAsk[]
  /** Why it needs you when there is no approval (a sign-in, a code). */
  reason?: string
  /** The site refused the page (HTTP 4xx/5xx); shown under the picture. */
  notice?: string
  expanded?: boolean
  onToggleExpand?: () => void
  onHide?: () => void
  onTakeOver?: () => void
  /** "I’m done": hand the browser back to Jarvis. */
  onDone?: () => void
  onClose?: () => void
  onInput?: (input: ComputerInput) => void
  busy?: boolean
  error?: string
  /** Adds shadow-float while docked over the transcript. */
  floating?: boolean
  className?: string
}

const KEYS = ['Enter', 'Tab', 'Backspace', 'Escape'] as const

export function ComputerCard(p: ComputerCardProps) {
  const { state, expanded = false, asks = [] } = p
  const [typed, setTyped] = useState('')
  const [addr, setAddr] = useState('')
  const input = (evt: ComputerInput) => p.onInput?.(evt)

  const onPicture = (e: MouseEvent<HTMLButtonElement>) => {
    if (!expanded) return p.onToggleExpand?.()
    if (state !== 'yours') return
    const box = e.currentTarget.getBoundingClientRect()
    input({ kind: 'click', x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height })
  }

  /* The state sits on the picture's corner, on a solid bg ground, so the
     header keeps its room for the page title and the pill reads over any page. */
  const status = (
    <span className="pointer-events-none absolute left-jm-2 top-jm-2 flex rounded-jm-pill bg-jm-bg">
      {state === 'needs-you' ? (
        <Tag tone="action">Action needed</Tag>
      ) : (
        <Tag className="!bg-jm-panel-raised !text-jm-fg">
          {state === 'yours' ? (
            'You have the browser'
          ) : (
            <>
              <LiveDot />
              {state === 'starting' ? 'Starting' : 'Live'}
            </>
          )}
        </Tag>
      )}
    </span>
  )

  const screenBox = 'relative box-border block aspect-[16/10] w-full overflow-hidden rounded-jm-sm border border-jm-border bg-jm-bg p-0'
  const screen = p.screenshot ? (
    <button
      type="button"
      onClick={onPicture}
      aria-label={!expanded ? 'Make the browser big' : state === 'yours' ? 'Click on the page' : 'The page'}
      className={cx(screenBox, 'appearance-none', !expanded ? 'cursor-zoom-in' : state === 'yours' ? 'cursor-crosshair' : 'cursor-default', focus)}
    >
      <img src={p.screenshot} alt={p.title ? `The page: ${p.title}` : 'The browser'} className="block h-full w-full object-cover object-top" />
      {status}
    </button>
  ) : (
    <div className={cx(screenBox, 'grid content-center place-items-center gap-jm-2 text-jm-fg-secondary')}>
      <Dots />
      <span className="text-jm-caption">Starting the browser…</span>
      {status}
    </div>
  )

  const close =
    expanded && p.onClose ? (
      <Button variant="ghost" disabled={p.busy} onClick={p.onClose}>
        Close browser
      </Button>
    ) : null

  return (
    <section
      aria-label="Jarvis’s browser"
      className={cx(
        'box-border grid max-w-full gap-jm-3 rounded-jm-lg border bg-jm-panel p-jm-3',
        expanded ? 'w-jm-computer-lg' : 'w-jm-computer-sm',
        state === 'needs-you' ? 'border-jm-accent' : 'border-jm-border',
        p.floating && 'shadow-jm-float',
        p.className,
      )}
    >
      <div className="flex min-w-0 items-center gap-jm-2">
        <span className="text-jm-fg-secondary">
          <Icon name="globe" size={16} />
        </span>
        <p className={cx('m-0 min-w-0 flex-1 text-jm-body-sm text-jm-fg-secondary', truncate)}>{p.title || p.url || 'Jarvis’s browser'}</p>
        {p.onToggleExpand && (
          <Button
            variant="ghost"
            size="sm"
            icon={expanded ? 'shrink' : 'expand'}
            aria-label={expanded ? 'Make the browser small' : 'Make the browser big'}
            onClick={p.onToggleExpand}
          />
        )}
        {p.onHide && <Button variant="ghost" size="sm" icon="close" aria-label="Hide the browser" onClick={p.onHide} />}
      </div>

      {screen}
      {p.notice && (
        <p role="status" data-testid="browser-refused" className="m-0 text-jm-body-sm text-jm-fg">
          {p.notice}
        </p>
      )}
      {expanded && p.url && <p className={cx('m-0 -mt-jm-1 text-jm-caption text-jm-fg-secondary', truncate)}>{p.url}</p>}
      {state === 'needs-you' && !asks.length && p.reason && <p className="m-0 text-jm-body-sm text-jm-fg">{p.reason}</p>}

      {asks.map((a, i) => (
        <div key={a.id ?? i} className="grid gap-jm-3">
          <p className="m-0 text-jm-body-sm text-jm-fg">{a.summary}</p>
          <div className="flex flex-wrap gap-jm-2">
            <Button variant="primary" disabled={p.busy} onClick={a.onAllow}>
              {a.kind === 'task' ? 'Allow for 30 min' : 'Allow once'}
            </Button>
            <Button variant="secondary" disabled={p.busy} onClick={a.onNotNow}>
              Not now
            </Button>
          </div>
        </div>
      ))}

      {state === 'yours' ? (
        <div className="flex flex-wrap gap-jm-2">
          <Button variant="primary" disabled={p.busy} onClick={p.onDone}>
            I’m done
          </Button>
          {close}
        </div>
      ) : !asks.length ? (
        <div className="flex flex-wrap gap-jm-2">
          <Button variant={state === 'needs-you' ? 'primary' : 'secondary'} disabled={p.busy || state === 'starting'} onClick={p.onTakeOver}>
            Take over
          </Button>
          {close}
        </div>
      ) : null}

      {state === 'yours' && expanded && (
        <div className="grid gap-jm-2 border-t border-jm-border pt-jm-3">
          <p className="m-0 text-jm-caption text-jm-fg-secondary">
            Tap a box in the picture, then type. What you type goes to the page, not the chat.
          </p>
          <form
            className="flex min-w-0 gap-jm-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (!typed) return
              input({ kind: 'type', text: typed })
              setTyped('')
            }}
          >
            <Field className="flex-1" value={typed} autoComplete="off" placeholder="Type into the page" aria-label="Type into the page" onChange={(e) => setTyped(e.target.value)} />
            <Button type="submit" disabled={p.busy}>
              Type
            </Button>
          </form>
          <form
            className="flex min-w-0 gap-jm-2"
            onSubmit={(e) => {
              e.preventDefault()
              const a = addr.trim()
              if (a) input({ kind: 'open', url: /^https?:\/\//.test(a) ? a : `https://${a}` })
            }}
          >
            <Field className="flex-1" value={addr} placeholder="Go to an address" aria-label="Go to an address" onChange={(e) => setAddr(e.target.value)} />
            <Button type="submit" disabled={p.busy}>
              Go
            </Button>
          </form>
          <div className="flex flex-wrap gap-jm-2">
            {KEYS.map((k) => (
              <Button key={k} size="sm" disabled={p.busy} onClick={() => input({ kind: 'key', key: k })}>
                {k}
              </Button>
            ))}
            <Button size="sm" disabled={p.busy} onClick={() => input({ kind: 'scroll' })}>
              Scroll
            </Button>
            <Button size="sm" disabled={p.busy} onClick={() => input({ kind: 'back' })}>
              Back
            </Button>
          </div>
        </div>
      )}

      {p.error && <ErrorLine small>{p.error}</ErrorLine>}
    </section>
  )
}
