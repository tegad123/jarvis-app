import { useRef, useState, type KeyboardEvent } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { AttachmentChip, Dots, LiveDot, Tag, cx, type ChatAttachment } from '../shared/primitives'

export interface VoiceState {
  phase: 'idle' | 'requesting' | 'recording' | 'uploading' | 'transcribing' | 'denied' | 'error'
  seconds?: number
  /** 0–1 input level while recording. */
  level?: number
  message?: string
}

export interface ComposerProps {
  value?: string
  onChange?: (value: string) => void
  onSubmit?: (text: string) => void
  placeholder?: string
  label?: string
  disabled?: boolean
  attachments?: ChatAttachment[]
  onRemoveAttachment?: (a: ChatAttachment) => void
  uploading?: boolean
  /** Renders a hidden file input and passes the picked files. */
  onFiles?: (files: FileList | null) => void
  accept?: string
  /** Alternative to onFiles when the consumer opens its own picker. */
  onAttach?: () => void
  voice?: VoiceState
  onMic?: () => void
  onDismissVoice?: () => void
  maxSeconds?: number
  /** While a reply streams; with onStop the send button becomes stop. */
  streaming?: boolean
  onStop?: () => void
  /** Channels: who an @ can reach. Typing @ opens the list; Enter or Tab
   *  puts "@Name " in the message. */
  mentionables?: Mentionable[]
  className?: string
}

export interface Mentionable {
  id: string
  /** What gets typed after the @. */
  name: string
  /** A second line: "Assistant", a title. */
  hint?: string
}

/** The @word the caret is in, if any: where it starts and what is typed so far. */
function mentionAt(value: string, caret: number): { start: number; query: string } | null {
  const m = value.slice(0, caret).match(/(^|[\s(])@([^\s@]*)$/)
  return m ? { start: caret - m[2].length - 1, query: m[2] } : null
}

const clock = (sec = 0) => {
  const s = Math.max(0, sec)
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}

const strip = 'box-border flex min-h-[44px] items-center gap-jm-3 rounded-jm-lg border bg-jm-panel px-jm-4 py-jm-2 text-jm-fg'

function VoiceStrip({ voice: v, maxSeconds, onDismiss }: { voice: VoiceState; maxSeconds?: number; onDismiss?: () => void }) {
  if (v.phase === 'idle') return null
  if (v.phase === 'denied') {
    return (
      <div role="alert" className={cx(strip, 'items-start border-jm-accent py-jm-3')}>
        <div className="grid min-w-0 flex-1 justify-items-start gap-jm-2">
          <Tag tone="action">Microphone blocked</Tag>
          <p className="m-0 text-jm-body-sm text-jm-fg-secondary">
            Your browser is blocking microphone access for this site. Allow it in the address-bar permissions, then tap the mic
            again. You can keep typing meanwhile.
          </p>
        </div>
        {onDismiss && (
          <Button variant="ghost" size="sm" onClick={onDismiss}>
            Dismiss
          </Button>
        )}
      </div>
    )
  }
  if (v.phase === 'error') {
    return (
      <div role="alert" className={cx(strip, 'border-jm-border')}>
        <span className="text-jm-error">
          <Icon name="alert" size={16} />
        </span>
        <p className="m-0 min-w-0 flex-1 text-jm-body-sm text-jm-fg">{v.message}</p>
        {onDismiss && (
          <Button variant="ghost" size="sm" onClick={onDismiss}>
            OK
          </Button>
        )}
      </div>
    )
  }
  const recording = v.phase === 'recording'
  const label =
    v.phase === 'requesting' ? 'Waiting for the microphone…' : recording ? 'Recording' : v.phase === 'uploading' ? 'Uploading…' : 'Transcribing…'
  return (
    <div role="status" className={cx(strip, 'border-jm-border')}>
      {recording ? <LiveDot /> : <Dots />}
      <span className="text-jm-label">{label}</span>
      {recording && (
        <span className="flex h-[15px] items-end gap-0.5" aria-hidden="true">
          {[0.15, 0.35, 0.55, 0.75].map((t, i) => (
            <i
              key={i}
              className={cx('block w-[3px] rounded-sm', (v.level ?? 0) > t ? 'bg-jm-fg' : 'bg-jm-border-strong')}
              style={{ height: 6 + i * 3 }}
            />
          ))}
        </span>
      )}
      {recording && (
        <span className="ml-auto text-jm-caption tabular-nums text-jm-fg-secondary">
          {clock(v.seconds)}
          {maxSeconds ? ` / ${clock(maxSeconds)}` : ''}
        </span>
      )}
    </div>
  )
}

export function Composer(p: ComposerProps) {
  const [internal, setInternal] = useState('')
  const controlled = p.value !== undefined
  const value = controlled ? (p.value as string) : internal
  const fileRef = useRef<HTMLInputElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const [caret, setCaret] = useState(0)
  const [picked, setPicked] = useState(0)
  const [closedAt, setClosedAt] = useState<number | null>(null)
  const atts = p.attachments ?? []
  const voice = p.voice ?? { phase: 'idle' as const }
  const recording = voice.phase === 'recording'
  const voiceBusy = voice.phase === 'requesting' || voice.phase === 'uploading' || voice.phase === 'transcribing'
  const canSend = !p.disabled && !p.uploading && !p.streaming && (value.trim().length > 0 || atts.length > 0)

  const setValue = (v: string) => {
    p.onChange?.(v)
    if (!controlled) setInternal(v)
  }

  const at = p.mentionables?.length ? mentionAt(value, caret) : null
  const q = at?.query.toLowerCase() ?? ''
  const options =
    at && closedAt !== at.start
      ? (p.mentionables ?? [])
          .filter((m) => !q || m.name.toLowerCase().split(/\s+/).some((w) => w.startsWith(q)) || m.name.toLowerCase().startsWith(q))
          .slice(0, 6)
      : []
  const choice = Math.min(picked, Math.max(options.length - 1, 0))

  const insert = (m: Mentionable) => {
    if (!at) return
    const text = `@${m.name} `
    const next = value.slice(0, at.start) + text + value.slice(caret)
    const pos = at.start + text.length
    setValue(next)
    setCaret(pos)
    setPicked(0)
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.setSelectionRange(pos, pos)
    })
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!options.length) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setPicked((choice + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length)
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      insert(options[choice])
    } else if (e.key === 'Escape') {
      e.preventDefault()
      setClosedAt(at!.start)
    }
  }
  const track = () => setCaret(inputRef.current?.selectionStart ?? value.length)

  return (
    <form
      className={cx('grid gap-jm-2', p.className)}
      onSubmit={(e) => {
        e.preventDefault()
        if (!canSend) return
        p.onSubmit?.(value.trim())
        if (!controlled) setInternal('')
      }}
    >
      <VoiceStrip voice={voice} maxSeconds={p.maxSeconds} onDismiss={p.onDismissVoice} />
      <div className="relative box-border grid min-h-jm-composer gap-jm-2 rounded-jm-composer border border-jm-border bg-jm-panel p-jm-2 transition-colors duration-jm-press focus-within:border-jm-accent motion-reduce:transition-none">
        {options.length > 0 && (
          <ul
            id="jm-mention-list"
            role="listbox"
            aria-label="People to mention"
            className="absolute bottom-[calc(100%+var(--jm-space-2))] left-0 z-20 m-0 grid w-full max-w-[320px] list-none gap-0.5 rounded-jm-md border border-jm-border bg-jm-panel-raised p-jm-1 shadow-jm-float"
          >
            {options.map((m, i) => (
              <li
                key={m.id}
                id={`jm-mention-${m.id}`}
                role="option"
                aria-selected={i === choice}
                onPointerDown={(e) => {
                  e.preventDefault()
                  insert(m)
                }}
                onPointerEnter={() => setPicked(i)}
                className={cx(
                  'grid cursor-pointer rounded-jm-sm px-jm-3 py-jm-2',
                  i === choice ? 'bg-jm-accent-soft text-jm-accent-soft-text' : 'text-jm-fg',
                )}
              >
                <span className="text-jm-label">@{m.name}</span>
                {m.hint && <span className="text-jm-caption text-jm-fg-secondary">{m.hint}</span>}
              </li>
            ))}
          </ul>
        )}
        {(atts.length > 0 || p.uploading) && (
          <div className="flex flex-wrap items-center gap-jm-3 px-jm-2 pt-jm-2">
            {atts.map((a, i) => (
              <AttachmentChip key={a.id ?? i} attachment={a} onRemove={p.onRemoveAttachment ? () => p.onRemoveAttachment!(a) : undefined} />
            ))}
            {p.uploading && (
              <span role="status" className="inline-flex items-center gap-jm-2 text-jm-caption text-jm-fg-secondary">
                <Dots />
                Uploading…
              </span>
            )}
          </div>
        )}
        <div className="flex items-center gap-jm-1">
          <Button
            variant="ghost"
            icon="plus"
            aria-label="Add a photo or file"
            disabled={p.disabled || p.streaming || p.uploading}
            onClick={() => (p.onFiles && fileRef.current ? fileRef.current.click() : p.onAttach?.())}
          />
          {p.onFiles && (
            <input
              ref={fileRef}
              type="file"
              multiple
              accept={p.accept}
              hidden
              onChange={(e) => {
                p.onFiles!(e.target.files)
                e.target.value = ''
              }}
            />
          )}
          <input
            ref={inputRef}
            value={value}
            placeholder={p.placeholder ?? 'Ask Jarvis…'}
            aria-label={p.label ?? 'Message Jarvis'}
            disabled={p.disabled || p.streaming}
            role={p.mentionables?.length ? 'combobox' : undefined}
            aria-expanded={p.mentionables?.length ? options.length > 0 : undefined}
            aria-controls={options.length ? 'jm-mention-list' : undefined}
            aria-activedescendant={options.length ? `jm-mention-${options[choice].id}` : undefined}
            aria-autocomplete={p.mentionables?.length ? 'list' : undefined}
            onKeyDown={onKeyDown}
            onKeyUp={track}
            onClick={track}
            onSelect={track}
            onChange={(e) => {
              setValue(e.target.value)
              setCaret(e.target.selectionStart ?? e.target.value.length)
              setPicked(0)
              setClosedAt(null)
            }}
            className="h-jm-control min-w-0 flex-1 border-0 bg-transparent px-jm-2 font-jm text-jm-body text-jm-fg outline-none placeholder:text-jm-fg-secondary"
          />
          {p.onMic && (
            <Button
              variant={recording ? 'primary' : 'ghost'}
              icon={recording ? 'stop' : 'mic'}
              aria-label={recording ? 'Stop recording' : 'Record a voice note'}
              disabled={p.disabled || p.streaming || p.uploading || voiceBusy}
              onClick={p.onMic}
            />
          )}
          {p.streaming && p.onStop ? (
            <Button variant="primary" icon="stop" aria-label="Stop the answer" onClick={p.onStop} />
          ) : (
            <Button type="submit" variant="primary" icon="send" aria-label="Send" disabled={!canSend} />
          )}
        </div>
      </div>
    </form>
  )
}
