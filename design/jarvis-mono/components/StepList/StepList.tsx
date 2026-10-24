import { useState } from 'react'
import { Icon } from '../shared/Icon'
import { Dots, LiveDot, Tick, cx, focus, srOnly, truncate } from '../shared/primitives'

export interface Step {
  id?: string
  label: string
  detail?: string
  status?: 'waiting' | 'running' | 'done' | 'error'
}

export interface StepListProps {
  steps: Step[]
  /** Default: "Working" / "Done · N steps" / "Stopped". */
  title?: string
  open?: boolean
  onOpenChange?: (open: boolean) => void
  className?: string
}

const SPOKEN = { done: ' (done)', running: ' (in progress)', error: ' (failed)', waiting: ' (waiting)' }

export function StepList({ steps, title, open: openProp, onOpenChange, className }: StepListProps) {
  const [internal, setInternal] = useState(true)
  const controlled = openProp !== undefined
  const open = controlled ? !!openProp : internal
  const running = steps.some((s) => s.status === 'running')
  const failed = steps.some((s) => s.status === 'error')
  const doneCount = steps.filter((s) => s.status === 'done').length
  const heading = title ?? (running ? 'Working' : failed ? 'Stopped' : `Done · ${doneCount} ${doneCount === 1 ? 'step' : 'steps'}`)

  const toggle = () => {
    onOpenChange?.(!open)
    if (!controlled) setInternal(!open)
  }

  return (
    <div aria-busy={running || undefined} className={cx('box-border max-w-jm-bubble rounded-jm-lg border border-jm-border px-jm-4 py-jm-3', className)}>
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className={cx('flex w-full cursor-pointer appearance-none items-center gap-jm-2 rounded-jm-sm border-0 bg-transparent p-0 text-left font-jm text-jm-fg-secondary', focus)}
      >
        {running ? (
          <Dots />
        ) : failed ? (
          <span className="text-jm-error">
            <Icon name="alert" size={16} />
          </span>
        ) : (
          <Tick />
        )}
        <span className="min-w-0 flex-1 text-jm-label text-jm-fg">{heading}</span>
        <span className={cx('transition-transform duration-jm-press motion-reduce:transition-none', open && 'rotate-90')}>
          <Icon name="chevron" size={16} />
        </span>
      </button>
      {open && (
        <ol className="m-0 mt-jm-3 grid list-none gap-jm-2 p-0">
          {steps.map((st, i) => {
            const status = st.status ?? 'waiting'
            return (
              <li key={st.id ?? i} className="flex items-start gap-jm-3 animate-jm-rise motion-reduce:animate-none">
                {/* Keyed by status, so the check mounts (and draws) when a step finishes. */}
                <span key={status} aria-hidden="true" className={cx('grid h-5 w-4 shrink-0 place-items-center', status === 'error' ? 'text-jm-error' : 'text-jm-fg-secondary')}>
                  {status === 'done' ? (
                    <Tick />
                  ) : status === 'error' ? (
                    <Icon name="alert" size={16} />
                  ) : status === 'running' ? (
                    <LiveDot />
                  ) : (
                    <span className="box-border block h-2.5 w-2.5 rounded-full border-[1.5px] border-jm-border-strong" />
                  )}
                </span>
                <span className="grid min-w-0 flex-1">
                  <span className={cx('text-jm-body-sm', status === 'done' || status === 'waiting' ? 'text-jm-fg-secondary' : 'text-jm-fg')}>
                    {st.label}
                    <span className={srOnly}>{SPOKEN[status]}</span>
                  </span>
                  {st.detail && <span className={cx('text-jm-caption text-jm-fg-secondary', truncate)}>{st.detail}</span>}
                </span>
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}
