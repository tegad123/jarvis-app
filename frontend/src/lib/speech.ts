/**
 * Reply playback via the browser's SpeechSynthesis.
 *
 * WHY THE BROWSER AND NOT A SERVER ENGINE
 *   docs/tts-investigation.md: local Kokoro on this 2-core droplet generates
 *   speech SLOWER than it plays (RTF 1.14-1.41x), so server-side audio would
 *   start after ~2.8s and then stall mid-sentence. Browser synthesis moves the
 *   work to the phone, where the core count of the droplet is irrelevant, and
 *   costs nothing. Quality is the OS default — good on iOS, variable on
 *   Android — which is the accepted trade for v1 while we find out whether
 *   anyone actually uses playback.
 *
 * The counter this feeds (see MicButton's sibling call in Ask.tsx) exists so
 * the "resize to 4 cores and run Kokoro" decision can be made from observed
 * usage rather than a guess.
 */

const PREF_KEY = 'jarvis-speak-replies'

/* ----------------------------------------------------------------- support */

export function speechSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Voices load asynchronously in Chrome — the first call often returns [].
 * Resolves once they arrive, or after a timeout so a browser that genuinely
 * has none does not hang the caller.
 */
export function loadVoices(timeoutMs = 2000): Promise<SpeechSynthesisVoice[]> {
  return new Promise((resolve) => {
    if (!speechSupported()) return resolve([])
    const now = window.speechSynthesis.getVoices()
    if (now.length) return resolve(now)

    let done = false
    const finish = () => {
      if (done) return
      done = true
      window.speechSynthesis.onvoiceschanged = null
      resolve(window.speechSynthesis.getVoices())
    }
    window.speechSynthesis.onvoiceschanged = finish
    setTimeout(finish, timeoutMs)
  })
}

/**
 * Pick the best English voice available.
 *
 * Ordering is deliberate: named high-quality engines first, then any local
 * (on-device, no network) English voice, then any English voice at all.
 * "compact" is pushed down because iOS ships low-bitrate compact variants
 * alongside the good ones and they sound noticeably worse.
 */
export function pickVoice(voices: SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const en = voices.filter((v) => /^en(-|_|$)/i.test(v.lang))
  if (!en.length) return null

  const score = (v: SpeechSynthesisVoice): number => {
    const n = v.name.toLowerCase()
    let s = 0
    if (/(google|microsoft|natural|neural|enhanced|premium)/.test(n)) s += 40
    if (/(samantha|daniel|karen|moira|serena|aaron|allison)/.test(n)) s += 25
    if (v.localService) s += 10
    if (/^en-(gb|us)/i.test(v.lang)) s += 8
    if (v.default) s += 3
    if (/compact/.test(n)) s -= 30
    return s
  }
  return [...en].sort((a, b) => score(b) - score(a))[0] ?? null
}

/* -------------------------------------------------------------- preference */

export function speakEnabled(): boolean {
  try {
    return localStorage.getItem(PREF_KEY) === '1'
  } catch {
    return false
  }
}

export function setSpeakEnabled(on: boolean): void {
  try {
    localStorage.setItem(PREF_KEY, on ? '1' : '0')
  } catch {
    /* private mode — the preference just will not persist */
  }
}

/* ------------------------------------------------------------------ speech */

/**
 * Strip markdown so the synthesiser reads prose, not punctuation.
 *
 * Without this the voice says "asterisk asterisk" around bold text and reads
 * URLs character by character, which is the difference between a feature and
 * a joke. Code fences are dropped entirely rather than spelled out.
 */
export function stripMarkdown(text: string): string {
  return (text || '')
    .replace(/```[\s\S]*?```/g, ' . ')            // fenced code: skip, don't read
    .replace(/`([^`]+)`/g, '$1')                  // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')        // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')      // links -> label only
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')           // headings
    .replace(/^\s{0,3}>\s?/gm, '')                // blockquotes
    .replace(/^\s*[-*+]\s+/gm, '')                // bullets
    .replace(/^\s*\d+\.\s+/gm, '')                // numbered lists
    .replace(/(\*\*|__)(.*?)\1/g, '$2')           // bold
    .replace(/(\*|_)(.*?)\1/g, '$2')              // italic
    .replace(/~~(.*?)~~/g, '$2')                  // strikethrough
    .replace(/^\s*([-*_]\s*){3,}$/gm, ' ')        // horizontal rules
    .replace(/\|/g, ' ')                          // table pipes
    .replace(/https?:\/\/\S+/g, 'a link')         // don't spell out URLs
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .trim()
}

let currentVoice: SpeechSynthesisVoice | null = null

export function setVoice(v: SpeechSynthesisVoice | null): void {
  currentVoice = v
}

export function isSpeaking(): boolean {
  return speechSupported() && window.speechSynthesis.speaking
}

export function stopSpeaking(): void {
  if (!speechSupported()) return
  try {
    window.speechSynthesis.cancel()
  } catch {
    /* nothing to cancel */
  }
}

/**
 * Speak one reply. Cancels anything already in flight — a newer answer always
 * supersedes an older one rather than queueing behind it.
 */
export function speak(
  text: string,
  handlers: { onStart?: () => void; onEnd?: () => void } = {},
): void {
  if (!speechSupported()) return
  const clean = stripMarkdown(text)
  if (!clean) return

  stopSpeaking()
  const u = new SpeechSynthesisUtterance(clean)
  if (currentVoice) {
    u.voice = currentVoice
    u.lang = currentVoice.lang
  }
  u.rate = 1.02
  u.pitch = 1.0
  u.onstart = () => handlers.onStart?.()
  u.onend = () => handlers.onEnd?.()
  u.onerror = () => handlers.onEnd?.()
  window.speechSynthesis.speak(u)
}
