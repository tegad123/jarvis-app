import { useEffect, useState } from 'react'
import {
  isSpeaking,
  loadVoices,
  pickVoice,
  setVoice,
  speak,
  speakEnabled,
  speechSupported,
  setSpeakEnabled,
  stopSpeaking,
} from '../lib/speech'

/**
 * Speaker control on a Jarvis message.
 *
 * One button, three jobs, because they are the same intent at different
 * moments:
 *   preference OFF        -> tap turns playback on AND speaks this reply
 *   ON and speaking       -> tap stops
 *   ON and silent         -> tap replays this reply
 *
 * Off by default, persisted client-side. Playback is browser SpeechSynthesis:
 * zero cost, and the work happens on the phone rather than on a droplet whose
 * two cores cannot generate speech faster than it plays
 * (docs/tts-investigation.md).
 */
interface Props {
  text: string
  /** True for the newest assistant message — only that one auto-speaks. */
  isLatest: boolean
  /** Rises when a reply completes, so auto-speak fires once per reply. */
  completedAt?: number
  onCountEvent?: (name: 'tts_toggle_on' | 'tts_utterance') => void
}

/** Module-level so every instance agrees, without a context provider. */
let voicesChecked = false
let haveVoices = false

function SpeakerIcon({ muted }: { muted: boolean }) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M11 5 6.5 9H3v6h3.5L11 19V5Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      {muted ? (
        <path d="M16 9.5l5 5m0-5l-5 5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      ) : (
        <>
          <path d="M15.5 9a4 4 0 0 1 0 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          <path d="M18.5 6.5a8 8 0 0 1 0 11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </>
      )}
    </svg>
  )
}

export default function SpeakerToggle({ text, isLatest, completedAt, onCountEvent }: Props) {
  const [enabled, setEnabled] = useState(() => speakEnabled())
  const [speaking, setSpeaking] = useState(false)
  const [noVoices, setNoVoices] = useState(false)
  const [ready, setReady] = useState(voicesChecked)

  // Resolve the best English voice once per page load.
  useEffect(() => {
    if (voicesChecked) {
      setNoVoices(!haveVoices)
      setReady(true)
      return
    }
    if (!speechSupported()) {
      voicesChecked = true
      haveVoices = false
      setNoVoices(true)
      setReady(true)
      return
    }
    let cancelled = false
    void loadVoices().then((vs) => {
      if (cancelled) return
      const v = pickVoice(vs)
      setVoice(v)
      voicesChecked = true
      haveVoices = !!v
      setNoVoices(!v)
      setReady(true)
    })
    return () => {
      cancelled = true
    }
  }, [])

  // Auto-speak a completed reply, once, only the newest one.
  useEffect(() => {
    if (!enabled || !isLatest || !completedAt || !ready || noVoices) return
    speak(text, {
      onStart: () => setSpeaking(true),
      onEnd: () => setSpeaking(false),
    })
    onCountEvent?.('tts_utterance')
    // completedAt changes per reply, which is what makes this fire once each.
  }, [completedAt, enabled, isLatest, ready, noVoices]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the button in sync if speech ends for a reason we did not initiate.
  useEffect(() => {
    if (!speaking) return
    const id = setInterval(() => {
      if (!isSpeaking()) setSpeaking(false)
    }, 400)
    return () => clearInterval(id)
  }, [speaking])

  if (!ready) return null

  // HONEST DEGRADATION: a browser with no voices must say so, not present a
  // control that silently does nothing.
  if (noVoices) {
    return (
      <button
        type="button"
        onClick={() =>
          alert(
            'This browser has no speech voices installed, so Jarvis cannot read replies aloud here. ' +
              'It usually works on iOS Safari and desktop Chrome.',
          )
        }
        aria-label="Read aloud unavailable"
        title="No speech voices available in this browser"
        className="grid h-7 w-7 place-items-center rounded-full text-light-text-muted opacity-50
                   dark:text-dark-text-muted"
      >
        <SpeakerIcon muted />
      </button>
    )
  }

  const onTap = () => {
    if (speaking) {
      stopSpeaking()
      setSpeaking(false)
      return
    }
    if (!enabled) {
      setEnabled(true)
      setSpeakEnabled(true)
      onCountEvent?.('tts_toggle_on')
    }
    speak(text, { onStart: () => setSpeaking(true), onEnd: () => setSpeaking(false) })
    onCountEvent?.('tts_utterance')
  }

  return (
    <button
      type="button"
      onClick={onTap}
      aria-label={speaking ? 'Stop reading aloud' : enabled ? 'Read this reply aloud' : 'Turn on reading replies aloud'}
      aria-pressed={enabled}
      title={enabled ? 'Reading replies aloud is on' : 'Read replies aloud'}
      className={[
        'grid h-7 w-7 place-items-center rounded-full transition-colors',
        speaking
          ? 'bg-light-accent text-white dark:bg-dark-accent-fill'
          : enabled
            ? 'text-light-accent dark:text-dark-accent'
            : 'text-light-text-muted dark:text-dark-text-muted',
      ].join(' ')}
    >
      <SpeakerIcon muted={!enabled && !speaking} />
    </button>
  )
}

/** Turn playback off globally — used when navigating away. */
export function silence(): void {
  stopSpeaking()
}
