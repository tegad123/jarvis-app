import type { Attachment } from '../lib/types'

/**
 * Attachment chip, following the design's chip pattern (rounded-chip, mono
 * label, hairline border).
 *
 * Two states that matter:
 *   IMAGE     — thumbnail; the model saw this.
 *   DOCUMENT  — file glyph plus a subtle "saved, not readable yet" line. The
 *               gateway rejects document parts outright, so a PDF is stored and
 *               downloadable but never sent. Saying so is the honest thing;
 *               a chip that looked identical to an image would imply Jarvis had
 *               read it.
 */

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M14 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V7.5L14 3Z"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
      <path d="M13.5 3.2V8h4.8" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
    </svg>
  )
}

export default function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment
  /** Present only in the composer, before the turn is sent. */
  onRemove?: () => void
}) {
  const a = attachment

  if (a.is_image) {
    return (
      <span className="relative inline-block">
        <a
          href={a.url}
          target="_blank"
          rel="noreferrer"
          title={`${a.filename} · ${a.size_human}`}
          className="block overflow-hidden rounded-chip border border-light-border
                     dark:border-dark-border"
        >
          <img
            src={a.url}
            alt={a.filename}
            className="h-20 w-20 object-cover"
            loading="lazy"
          />
        </a>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            aria-label={`Remove ${a.filename}`}
            className="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center
                       rounded-full bg-ink-900 text-white shadow-card"
          >
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="3"
                    strokeLinecap="round" />
            </svg>
          </button>
        )}
      </span>
    )
  }

  return (
    <span className="relative inline-flex max-w-full">
      <a
        href={a.url}
        target="_blank"
        rel="noreferrer"
        download={a.filename}
        title={`${a.filename} · ${a.size_human} — download`}
        className="flex min-w-0 items-center gap-2 rounded-chip border border-light-border
                   bg-light-surface px-3 py-2 dark:border-dark-border dark:bg-dark-surface"
      >
        <span className="shrink-0 text-light-text-muted dark:text-dark-text-muted">
          <FileIcon />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-body-s text-light-text-primary dark:text-dark-text-primary">
            {a.filename}
          </span>
          <span className="block font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted">
            {a.size_human}
            {!a.readable && ' · saved, not readable yet'}
          </span>
        </span>
      </a>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${a.filename}`}
          className="absolute -right-1.5 -top-1.5 grid h-5 w-5 place-items-center
                     rounded-full bg-ink-900 text-white shadow-card"
        >
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="3"
                  strokeLinecap="round" />
          </svg>
        </button>
      )}
    </span>
  )
}
