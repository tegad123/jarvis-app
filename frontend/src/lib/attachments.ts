/**
 * Client-side attachment prep.
 *
 * WHY DOWNSCALE IN THE BROWSER
 *   A modern phone photo is 3-6 MB and 4000px wide. The gateway caps a request
 *   body at 10 MB and base64 inflates by 4/3, so a couple of raw photos would
 *   blow the budget for the whole turn. Resizing to a longest edge of ~2000px
 *   and re-encoding as JPEG lands a typical photo around 300-600 KB, which is
 *   invisible to the user and comfortably inside the budget. It also converts
 *   HEIC (which Anthropic does not accept) into JPEG (which it does), as long
 *   as the browser can decode it — Safari can.
 *
 * Documents pass through untouched: they are stored, never sent to the model.
 */

export const MAX_EDGE = 2000
export const JPEG_QUALITY = 0.85
/** Matches the server's per-file cap so the UI can refuse before uploading. */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

export const IMAGE_MIMES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif']
const DOCUMENT_MIMES = [
  'application/pdf',
  'text/plain',
  'text/csv',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
]

/** What the file picker offers. HEIC is accepted because we re-encode it. */
export const ACCEPT_ATTR = [
  ...IMAGE_MIMES,
  'image/heic',
  'image/heif',
  ...DOCUMENT_MIMES,
].join(',')

export function humanSize(n: number): string {
  return n >= 1024 * 1024 ? `${(n / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`
}

export function looksLikeImage(file: File): boolean {
  return file.type.startsWith('image/')
}

function loadImage(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new Error('decode-failed'))
    }
    img.src = url
  })
}

/**
 * Returns a File ready to upload.
 *
 * Non-images and undecodable images pass through unchanged — better to upload
 * the original and let the server's allowlist speak than to silently mangle it.
 * An already-small image is also passed through when re-encoding would make it
 * bigger, which happens with flat-colour PNGs and screenshots.
 */
export async function prepareForUpload(file: File): Promise<File> {
  if (!looksLikeImage(file)) return file

  let img: HTMLImageElement
  try {
    img = await loadImage(file)
  } catch {
    return file
  }

  const longest = Math.max(img.naturalWidth, img.naturalHeight)
  const scale = longest > MAX_EDGE ? MAX_EDGE / longest : 1
  const w = Math.max(1, Math.round(img.naturalWidth * scale))
  const h = Math.max(1, Math.round(img.naturalHeight * scale))

  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  if (!ctx) return file
  ctx.drawImage(img, 0, 0, w, h)

  const blob: Blob | null = await new Promise((res) =>
    canvas.toBlob(res, 'image/jpeg', JPEG_QUALITY),
  )
  if (!blob) return file

  // Keep the original when it was already smaller AND already within budget:
  // re-encoding a 40 KB flat PNG into a 90 KB JPEG helps nobody.
  const alreadyFine = scale === 1 && IMAGE_MIMES.includes(file.type)
  if (alreadyFine && file.size <= blob.size) return file

  const base = file.name.replace(/\.[^.]+$/, '') || 'photo'
  return new File([blob], `${base}.jpg`, { type: 'image/jpeg', lastModified: Date.now() })
}
