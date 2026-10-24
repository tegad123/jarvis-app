import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, Unauthorized, transcribeAudio } from './api'
import { isPermissionDenied, startRecording, voiceSupported, type Recorder, type VoiceState } from './voice'

/**
 * Push-to-talk for the composer: tap to start, tap to stop.
 *
 * The phases are named ("Uploading…", "Transcribing…") because they fail
 * differently. The transcript lands in the composer editable and is never
 * auto-sent.
 */
export function useMic(onTranscript: (text: string) => void) {
  const [state, setState] = useState<VoiceState>({ phase: 'idle' })
  const recorderRef = useRef<Recorder | null>(null)
  const onTranscriptRef = useRef(onTranscript)
  onTranscriptRef.current = onTranscript

  // Never leave the mic hot if the screen goes away mid-recording.
  useEffect(() => () => recorderRef.current?.cancel(), [])

  const finish = useCallback(async (blob: Blob) => {
    recorderRef.current = null
    if (blob.size < 1200) {
      setState({ phase: 'error', message: 'That was too short to hear. Hold it a moment longer.' })
      return
    }
    setState({ phase: 'transcribing' })
    try {
      const r = await transcribeAudio(blob)
      onTranscriptRef.current(r.text)
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
  }, [])

  const stop = useCallback(async () => {
    const rec = recorderRef.current
    if (!rec) return
    await finish(await rec.stop())
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
      setState(isPermissionDenied(err) ? { phase: 'denied' } : { phase: 'error', message: 'No microphone available on this device.' })
    }
  }, [stop])

  const toggle = useCallback(() => {
    if (state.phase === 'recording') void stop()
    else void start()
  }, [state.phase, start, stop])

  return {
    state,
    supported: voiceSupported(),
    toggle,
    dismiss: () => setState({ phase: 'idle' }),
  }
}
