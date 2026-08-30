/**
 * Push-to-talk recording.
 *
 * MediaRecorder + getUserMedia, wrapped so the screen deals in four honest
 * states rather than a spinner: recording -> uploading -> transcribing -> done.
 * Each is a real phase with a different failure mode, and naming them is the
 * difference between "it's working" and "is it stuck?".
 *
 * The transcript lands in the composer EDITABLE and is never auto-sent. Speech
 * recognition is wrong often enough that sending unread would eventually put a
 * mistranscribed instruction in front of the model — the user reviews first.
 */

export type VoiceState =
  | { phase: 'idle' }
  | { phase: 'requesting' }
  | { phase: 'recording'; seconds: number; level: number }
  | { phase: 'uploading' }
  | { phase: 'transcribing' }
  | { phase: 'denied' }
  | { phase: 'error'; message: string }

/** Server cap is 120s; stop a little under so the upload is never wasted. */
export const MAX_RECORD_SECONDS = 115

/** Safari does not do webm; let the browser pick what it can actually encode. */
function pickMimeType(): string | undefined {
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4',
    'audio/ogg;codecs=opus',
  ]
  for (const t of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported?.(t)) return t
  }
  return undefined
}

export function voiceSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'
  )
}

export interface Recorder {
  stop: () => Promise<Blob>
  cancel: () => void
}

/**
 * Start recording. Resolves once the mic is live (permission granted).
 *
 * `onTick` fires ~10x/sec with elapsed seconds and a 0..1 level so the UI can
 * show that something is actually being heard — a timer alone does not prove
 * the mic works, and a dead mic with a running timer is the exact failure this
 * is meant to make visible.
 */
export async function startRecording(
  onTick: (seconds: number, level: number) => void,
  onAutoStop: () => void,
): Promise<Recorder> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  })

  const mimeType = pickMimeType()
  const rec = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
  const chunks: BlobPart[] = []
  rec.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) chunks.push(e.data)
  }

  // Cheap level meter. An AnalyserNode is far less work than it looks and
  // costs nothing next to the encoder already running.
  let audioCtx: AudioContext | null = null
  let analyser: AnalyserNode | null = null
  let buf: Uint8Array<ArrayBuffer> | null = null
  try {
    const Ctx = window.AudioContext || (window as any).webkitAudioContext
    audioCtx = new Ctx()
    const src = audioCtx.createMediaStreamSource(stream)
    analyser = audioCtx.createAnalyser()
    analyser.fftSize = 512
    src.connect(analyser)
    buf = new Uint8Array(new ArrayBuffer(analyser.frequencyBinCount))
  } catch {
    // Level meter is a nicety; recording proceeds without it.
  }

  const started = Date.now()
  let stopped = false
  const timer = window.setInterval(() => {
    if (stopped) return
    const seconds = (Date.now() - started) / 1000
    let level = 0
    if (analyser && buf) {
      analyser.getByteTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128
        sum += v * v
      }
      level = Math.min(1, Math.sqrt(sum / buf.length) * 4)
    }
    onTick(seconds, level)
    if (seconds >= MAX_RECORD_SECONDS) onAutoStop()
  }, 100)

  const teardown = () => {
    stopped = true
    clearInterval(timer)
    stream.getTracks().forEach((t) => t.stop())
    audioCtx?.close().catch(() => {})
  }

  rec.start()

  return {
    stop: () =>
      new Promise<Blob>((resolve) => {
        rec.onstop = () => {
          teardown()
          resolve(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }))
        }
        if (rec.state !== 'inactive') rec.stop()
        else {
          teardown()
          resolve(new Blob(chunks, { type: rec.mimeType || 'audio/webm' }))
        }
      }),
    cancel: () => {
      try {
        if (rec.state !== 'inactive') rec.stop()
      } catch {
        /* already stopped */
      }
      teardown()
    },
  }
}

/** Chrome and Safari word permission refusal differently; both mean "denied". */
export function isPermissionDenied(err: unknown): boolean {
  const name = (err as { name?: string })?.name || ''
  return name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError'
}
