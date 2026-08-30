import { useEffect, useRef, useState, type FormEvent } from 'react'
import { createPortal } from 'react-dom'

/**
 * Minimal bottom sheet for creating a channel. One field, one button — the
 * brief calls for minimal and there is nothing else to collect yet (lead
 * linkage and the context strip are Phase 5).
 *
 * Also used for rename, which is the same shape with different copy.
 */
interface Props {
  title: string
  submitLabel: string
  initialValue?: string
  busy?: boolean
  error?: string | null
  onSubmit: (name: string) => void
  onClose: () => void
}

export default function NewChannelSheet({
  title,
  submitLabel,
  initialValue = '',
  busy,
  error,
  onSubmit,
  onClose,
}: Props) {
  const [name, setName] = useState(initialValue)
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const trimmed = name.trim()
    if (trimmed && !busy) onSubmit(trimmed)
  }

  // PORTALLED TO <body> ON PURPOSE. The screen renders inside <main>, which is
  // `relative z-10` and therefore opens a stacking context — a z-50 inside it
  // still paints BELOW the fixed tab bar (z-30), which is main's sibling. The
  // symptom is the tab bar swallowing taps on this sheet's submit button.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end" role="dialog" aria-modal="true"
         aria-label={title}>
      {/* Scrim. Tapping outside closes — nothing here is destructive. */}
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 bg-ink-900/40 backdrop-blur-[2px]"
      />

      <div className="glass-moment relative w-full rounded-t-screen px-screen-x pb-[max(20px,env(safe-area-inset-bottom))] pt-5">
        <div
          aria-hidden="true"
          className="mx-auto mb-4 h-1 w-9 rounded-full bg-light-border-strong dark:bg-dark-border-strong"
        />
        <h2 className="text-screen-title">{title}</h2>

        <form onSubmit={submit} className="mt-4 grid gap-3">
          <label htmlFor="channel-name" className="sr-only">
            Channel name
          </label>
          <input
            id="channel-name"
            ref={inputRef}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. 128 Alder Ave"
            maxLength={120}
            disabled={busy}
            className="w-full rounded-pill border border-light-border-strong bg-light-surface
                       px-5 py-3 text-body text-light-text-primary
                       placeholder:text-light-text-muted
                       dark:border-dark-border-strong dark:bg-dark-surface
                       dark:text-dark-text-primary dark:placeholder:text-dark-text-muted"
          />

          <button
            type="submit"
            disabled={busy || name.trim().length === 0}
            className="w-full rounded-pill bg-light-accent px-5 py-3 text-body font-semibold
                       text-white shadow-accent-btn transition-opacity disabled:opacity-50
                       dark:bg-dark-accent-fill"
          >
            {busy ? 'Working…' : submitLabel}
          </button>

          {error && (
            <p
              role="alert"
              className="flex items-center justify-center gap-2 text-caption
                         text-light-alert dark:text-dark-alert"
            >
              <span
                aria-hidden="true"
                className="block h-[7px] w-[7px] rounded-full bg-light-alert dark:bg-dark-alert"
              />
              {error}
            </p>
          )}
        </form>
      </div>
    </div>,
    document.body,
  )
}
