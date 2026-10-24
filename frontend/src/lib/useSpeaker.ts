import { useEffect, useState } from 'react'
import {
  isSpeaking,
  loadVoices,
  pickVoice,
  setSpeakEnabled,
  setVoice,
  speak,
  speakEnabled,
  speechSupported,
  stopSpeaking,
} from './speech'

/**
 * Read-aloud for one Jarvis reply. One control, three jobs:
 *   preference OFF     -> tap turns playback on AND speaks this reply
 *   ON and speaking    -> tap stops
 *   ON and silent      -> tap replays this reply
 *
 * Off by default, persisted client-side; playback is browser SpeechSynthesis.
 * A browser with no voices reports 'unavailable' rather than offering a
 * control that silently does nothing.
 */

/** Module-level so every reply agrees, without a context provider. */
let voicesChecked = false
let haveVoices = false

export type Speech = 'off' | 'on' | 'speaking' | 'unavailable'

export function useSpeaker(
  text: string,
  isLatest: boolean,
  completedAt: number | undefined,
  onCountEvent?: (name: 'tts_toggle_on' | 'tts_utterance') => void,
): { speech: Speech | undefined; onSpeak: () => void } {
  const [enabled, setEnabled] = useState(() => speakEnabled())
  const [speaking, setSpeaking] = useState(false)
  const [noVoices, setNoVoices] = useState(false)
  const [ready, setReady] = useState(voicesChecked)

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
    speak(text, { onStart: () => setSpeaking(true), onEnd: () => setSpeaking(false) })
    onCountEvent?.('tts_utterance')
  }, [completedAt, enabled, isLatest, ready, noVoices]) // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the control in sync if speech ends for a reason we did not initiate.
  useEffect(() => {
    if (!speaking) return
    const id = setInterval(() => {
      if (!isSpeaking()) setSpeaking(false)
    }, 400)
    return () => clearInterval(id)
  }, [speaking])

  const onSpeak = () => {
    if (noVoices) return
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

  const speech: Speech | undefined = !ready ? undefined : noVoices ? 'unavailable' : speaking ? 'speaking' : enabled ? 'on' : 'off'
  return { speech, onSpeak }
}

/** Turn playback off globally — used when navigating away. */
export function silence(): void {
  stopSpeaking()
}
