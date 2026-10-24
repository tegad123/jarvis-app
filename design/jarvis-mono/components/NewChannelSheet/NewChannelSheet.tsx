import { useEffect, useRef, useState } from 'react'
import { Button } from '../Button/Button'
import { ErrorLine, Field, Sheet } from '../shared/primitives'

export interface NewChannelSheetProps {
  mode?: 'create' | 'rename'
  initialValue?: string
  onSubmit?: (name: string) => void
  onClose?: () => void
  busy?: boolean
  error?: string | null
  title?: string
  submitLabel?: string
  placeholder?: string
  /** Render in place instead of over the page. */
  inline?: boolean
}

export function NewChannelSheet(p: NewChannelSheetProps) {
  const rename = p.mode === 'rename'
  const [name, setName] = useState(p.initialValue ?? '')
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (!p.inline) ref.current?.focus()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Sheet title={p.title ?? (rename ? 'Rename channel' : 'New channel')} onClose={p.onClose} inline={p.inline}>
      <form
        className="grid gap-jm-3"
        onSubmit={(e) => {
          e.preventDefault()
          const t = name.trim()
          if (t && !p.busy) p.onSubmit?.(t)
        }}
      >
        <Field
          ref={ref}
          large
          value={name}
          maxLength={120}
          disabled={p.busy}
          placeholder={p.placeholder ?? 'e.g. 128 Alder Ave'}
          aria-label="Channel name"
          aria-invalid={p.error ? true : undefined}
          onChange={(e) => setName(e.target.value)}
        />
        <Button type="submit" variant="primary" className="min-h-jm-touch w-full" disabled={p.busy || !name.trim()}>
          {p.busy ? 'Working…' : (p.submitLabel ?? (rename ? 'Save' : 'Create'))}
        </Button>
        {p.error && <ErrorLine>{p.error}</ErrorLine>}
      </form>
    </Sheet>
  )
}
