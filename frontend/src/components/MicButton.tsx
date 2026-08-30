import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, Unauthorized, transcribeAudio } from '../lib/api'
import {
  MAX_RECORD_SECONDS,
  isPermissionDenied,
  startRecording,
  voiceSupported,
  type Recorder,
  type VoiceState,
} from '../lib/voice'

/**
 * Push-to-talk. Takes the slot reserved for it in the composer since step 3.
 *
 * Tap to start, tap to stop. The phases are NAMED — "Uploading…",
 * "Transcribing…" — never a bare spinner, because they fail differently and a
 * user staring at a dot cannot tell a slow model from a dead request.
 *
 * The transcript lands in the composer editable and is NEVER auto-sent.
 */
interface Props {
  disabled?: boolean
  onTranscript: (text: string) => void
  onStateChange?: (state: VoiceState) => void
}

function MicIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="2" />
      <path d="M5 11a7 7 0 0 0 14 0" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <path d="M12 18v3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}

function StopIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
    </svg>
  )
}

export default function MicButton({ disabled, onTranscript, onStateChange }: Props) {
  const [state, setState] = useState<VoiceState>({ phase: 'idle' })
  const recorderRef = useRef<Recorder | null>(null)
  const supported = voiceSupported()

  useEffect(() => {
    onStateChange?.(state)
  }, [state, onStateChange])

  // Never leave the mic hot if the screen goes away mid-recording.
  useEffect(() => () => recorderRef.current?.cancel(), [])

  const finish = useCallback(
    async (blob: Blob) => {
      recorderRef.current = null
      if (blob.size < 1200) {
        setState({ phase: 'error', message: 'That was too short to hear. Hold it a moment longer.' })
        return
      }
      setState({ phase: 'uploading' })
      try {
        setState({ phase: 'transcribing' })
        const r = await transcribeAudio(blob)
        onTranscript(r.text)
        setState({ phase: 'idle' })
      } catch (err) {
        if (err instanceof Unauthorized) {
          setState({ phase: 'error', message: 'Session expired — sign in again.' })
          return
        }
        setState({
          phase: 'error',
          message: err instanceof ApiError ? err.message : 'That recording could not be transcribed.',
        })
      }
    },
    [onTranscript],
  )

  const stop = useCallback(async () => {
    const rec = recorderRef.current
    if (!rec) return
    const blob = await rec.stop()
    await finish(blob)
  }, [finish])

  const start = useCallback(async () => {
    setState({ phase: 'requesting' })
    try {
      const rec = await startRecording(
        (seconds, level) => setState({ phase: 'recording', seconds, level }),
        () => void stop(),
      )
      recorderRef.current = rec
      setState({ phase: 'recording', seconds: 0, level: 0 })
    } catch (err) {
      if (isPermissionDenied(err)) {
        setState({ phase: 'denied' })
        return
      }
      setState({
        phase: 'error',
        message: 'No microphone available on this device.',
      })
    }
  }, [stop])

  if (!supported) return <span aria-hidden="true" className="h-9 w-9 shrink-0" />

  const recording = state.phase === 'recording'
  const busy = state.phase === 'uploading' || state.phase === 'transcribing' || state.phase === 'requesting'

  return (
    <button
      type="button"
      onClick={() => (recording ? void stop() : void start())}
      disabled={disabled || busy}
      aria-label={recording ? 'Stop recording' : 'Record a voice note'}
      className={[
        'grid h-9 w-9 shrink-0 place-items-center rounded-full transition-colors',
        recording
          ? 'bg-light-alert text-white dark:bg-dark-alert'
          : 'text-light-text-secondary dark:text-dark-text-secondary',
        'disabled:opacity-40',
      ].join(' ')}
    >
      {recording ? <StopIcon /> : <MicIcon />}
    </button>
  )
}

/**
 * The status strip shown above the composer while a voice note is in flight.
 * Separated from the button so the composer row keeps its fixed geometry.
 */
export function VoiceStatus({ state, onDismiss }: { state: VoiceState; onDismiss: () => void }) {
  if (state.phase === 'idle') return null

  if (state.phase === 'denied') {
    return (
      <div
        role="alert"
        className="mb-2 rounded-list border border-light-warning/40 bg-light-surface p-3
                   dark:border-dark-warning/40 dark:bg-dark-surface"
      >
        <p className="flex items-center gap-2 font-mono text-label-mono uppercase text-light-warning dark:text-dark-warning">
          <span aria-hidden="true" className="block h-[7px] w-[7px] rounded-full bg-light-warning dark:bg-dark-warning" />
          Microphone blocked
        </p>
        <p className="mt-1.5 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          Your browser is blocking microphone access for this site. Allow it in the
          address-bar permissions, then tap the mic again. You can keep typing meanwhile.
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="mt-2 font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted"
        >
          Dismiss
        </button>
      </div>
    )
  }

  if (state.phase === 'error') {
    return (
      <div
        role="alert"
        className="mb-2 flex items-start gap-2 rounded-list border border-light-alert/30
                   bg-light-surface p-3 dark:border-dark-alert/40 dark:bg-dark-surface"
      >
        <span aria-hidden="true" className="mt-1.5 block h-[7px] w-[7px] shrink-0 rounded-full bg-light-alert dark:bg-dark-alert" />
        <span className="min-w-0 flex-1 text-body-s text-light-text-primary dark:text-dark-text-primary">
          {state.message}
        </span>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted"
        >
          OK
        </button>
      </div>
    )
  }

  // Named working states — never a bare spinner.
  const label =
    state.phase === 'requesting'
      ? 'Waiting for the microphone…'
      : state.phase === 'recording'
        ? 'Recording'
        : state.phase === 'uploading'
          ? 'Uploading…'
          : 'Transcribing…'

  const seconds = state.phase === 'recording' ? state.seconds : 0
  const level = state.phase === 'recording' ? state.level : 0
  const mm = Math.floor(seconds / 60)
  const ss = Math.floor(seconds % 60)

  return (
    <div className="glass-featured mb-2 flex items-center gap-3 rounded-card px-4 py-2.5">
      <span
        aria-hidden="true"
        className={[
          'block h-[9px] w-[9px] shrink-0 rounded-full',
          state.phase === 'recording'
            ? 'animate-pulse bg-light-alert dark:bg-dark-alert'
            : 'bg-light-accent dark:bg-dark-accent',
        ].join(' ')}
      />
      <span className="font-mono text-label-mono uppercase text-light-text-secondary dark:text-dark-text-secondary">
        {label}
      </span>

      {state.phase === 'recording' && (
        <>
          {/* Level bars: proof the mic is actually hearing something. A timer
              alone ticks happily on a dead microphone. */}
          <span aria-hidden="true" className="flex items-end gap-[2px]">
            {[0.15, 0.35, 0.55, 0.75].map((t, i) => (
              <span
                key={i}
                className={[
                  'block w-[3px] rounded-full transition-all duration-100',
                  level > t
                    ? 'bg-light-alert dark:bg-dark-alert'
                    : 'bg-light-neutral/50 dark:bg-dark-neutral/50',
                ].join(' ')}
                style={{ height: `${6 + i * 3}px` }}
              />
            ))}
          </span>
          <span className="ml-auto font-mono text-label-mono tabular-nums text-light-text-muted dark:text-dark-text-muted">
            {mm}:{String(ss).padStart(2, '0')} / {Math.floor(MAX_RECORD_SECONDS / 60)}:
            {String(MAX_RECORD_SECONDS % 60).padStart(2, '0')}
          </span>
        </>
      )}
    </div>
  )
}
