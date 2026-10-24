import { useId, useState } from 'react'
import { Disclose } from '../ActivityRow/ActivityRow'
import { Tag, cx, rowBase, truncate } from '../shared/primitives'

export interface OutboxRowProps {
  to: string[]
  cc?: string[]
  subject: string
  body: string
  time?: string
  timeTitle?: string
  dateTime?: string
  defaultOpen?: boolean
  className?: string
}

export function OutboxRow(p: OutboxRowProps) {
  const [open, setOpen] = useState(!!p.defaultOpen)
  const bodyId = useId()
  const cc = (p.cc ?? []).join(', ')
  const who = `to ${p.to.join(', ')}${cc ? ` · cc ${cc}` : ''}`
  return (
    <li className={cx(rowBase, p.className)}>
      <div className="flex min-w-0 items-center gap-jm-2 text-jm-fg-secondary">
        <Tag>Captured</Tag>
        <span title={who} className={cx('min-w-0 text-jm-caption', truncate)}>
          {who}
        </span>
        {p.time && (
          <time dateTime={p.dateTime} title={p.timeTitle} className="ml-auto shrink-0 text-jm-caption">
            {p.time}
          </time>
        )}
      </div>
      <p className="m-0 mt-0.5 text-jm-body-sm text-jm-fg">{p.subject}</p>
      <Disclose open={open} controls={bodyId} onToggle={() => setOpen(!open)}>
        {open ? 'Hide body' : 'Body'}
      </Disclose>
      {open && (
        <pre
          id={bodyId}
          className="m-0 mt-jm-1 max-h-[320px] overflow-auto whitespace-pre-wrap break-words rounded-jm-md border border-jm-border bg-jm-panel p-jm-3 font-jm text-jm-caption text-jm-fg-secondary"
        >
          {p.body}
        </pre>
      )}
    </li>
  )
}
