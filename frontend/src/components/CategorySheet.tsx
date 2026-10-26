import { useState } from 'react'
import { Button } from '../jm'
import { ErrorLine, Field, Select, Sheet, type SelectOption } from '../jm/shared/primitives'

/**
 * Admins' sidebar sections. Three jobs, one frame:
 *   new   — name a section
 *   edit  — rename it, or remove it (its channels stay, ungrouped)
 *   move  — put a channel in a section, or in none
 * The API refuses all three for members; this sheet only opens for admins.
 */
export type CategorySheetMode =
  | { kind: 'new' }
  | { kind: 'edit'; id: string; name: string }
  | { kind: 'move'; threadName: string; current: string }

export default function CategorySheet({
  mode,
  categories,
  busy,
  error,
  onSave,
  onDelete,
  onClose,
}: {
  mode: CategorySheetMode
  categories: SelectOption[]
  busy: boolean
  error?: string | null
  onSave: (value: string) => void
  onDelete?: () => void
  onClose: () => void
}) {
  const [value, setValue] = useState(mode.kind === 'edit' ? mode.name : mode.kind === 'move' ? mode.current : '')
  const [confirm, setConfirm] = useState(false)
  const title = mode.kind === 'new' ? 'New category' : mode.kind === 'edit' ? 'Edit category' : `Move ${mode.threadName}`

  return (
    <Sheet
      title={title}
      subtitle={mode.kind === 'move' ? 'Who can see the channel doesn’t change.' : undefined}
      onClose={onClose}
    >
      <form
        className="grid gap-jm-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (busy) return
          if (mode.kind !== 'move' && !value.trim()) return
          onSave(mode.kind === 'move' ? value : value.trim())
        }}
      >
        {mode.kind === 'move' ? (
          <label className="flex items-center justify-between gap-jm-3 text-jm-body-sm text-jm-fg-secondary">
            Category
            <Select
              label="Category"
              value={value}
              disabled={busy}
              options={[{ value: '', label: 'No category' }, ...categories]}
              onChange={setValue}
            />
          </label>
        ) : (
          <Field
            large
            autoFocus
            value={value}
            maxLength={60}
            disabled={busy}
            placeholder="e.g. Listings"
            aria-label="Category name"
            onChange={(e) => setValue(e.target.value)}
          />
        )}
        <Button
          type="submit"
          variant="primary"
          className="min-h-jm-touch w-full"
          disabled={busy || (mode.kind !== 'move' && !value.trim())}
        >
          {busy ? 'Working…' : mode.kind === 'new' ? 'Add' : mode.kind === 'move' ? 'Move' : 'Save'}
        </Button>
        {mode.kind === 'edit' && onDelete && (
          <Button
            type="button"
            variant="ghost"
            className="w-full"
            disabled={busy}
            onClick={() => (confirm ? onDelete() : setConfirm(true))}
          >
            {confirm ? 'Remove it — channels stay, ungrouped' : 'Remove category'}
          </Button>
        )}
        {error && <ErrorLine>{error}</ErrorLine>}
      </form>
    </Sheet>
  )
}
