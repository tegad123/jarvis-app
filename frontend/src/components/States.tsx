import AsteriskMark from './AsteriskMark'

/**
 * Empty and error states.
 *
 * The error state is deliberately loud about what went wrong. The spec is
 * explicit: "if the feed file is unreadable, say so plainly — no silent empty
 * list". A blank list that means "broken" is the failure mode being designed
 * out here, so this never renders as nothing.
 */

export function EmptyState() {
  return (
    <div className="relative mt-6 grid place-items-center py-10">
      <div aria-hidden="true" className="ink-halo absolute h-48 w-48 rounded-full" />
      <div className="glass-moment relative grid place-items-center gap-3 rounded-card px-6 py-7 text-center">
        <AsteriskMark
          size={28}
          className="text-light-accent dark:text-dark-accent"
        />
        <p className="text-body text-light-text-secondary dark:text-dark-text-secondary">
          Jarvis is watching. Nothing needs you right now.
        </p>
      </div>
    </div>
  )
}

export function ErrorState({
  message,
  detail,
  onRetry,
  stale,
  label = 'Unavailable',
  staleLabel = 'Not updating',
}: {
  message: string
  detail?: string
  onRetry: () => void
  /** True when we are still showing the last good data underneath. */
  stale?: boolean
  /** What is unavailable. Every screen using this must say its own name —
   *  a Leads failure labelled "Feed unavailable" is a lie about what broke. */
  label?: string
  staleLabel?: string
}) {
  return (
    <div
      role="alert"
      className="mt-4 rounded-list border border-light-alert/30 bg-light-surface p-card-x
                 shadow-card dark:border-dark-alert/40 dark:bg-dark-surface"
    >
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="block h-[7px] w-[7px] shrink-0 rounded-full bg-light-alert dark:bg-dark-alert"
        />
        <span className="font-mono text-label-mono uppercase text-light-alert dark:text-dark-alert">
          {stale ? staleLabel : label}
        </span>
      </div>

      <p className="mt-2 text-body text-light-text-primary dark:text-dark-text-primary">
        {message}
      </p>

      {detail && (
        <p className="mt-1 break-words font-mono text-caption text-light-text-muted dark:text-dark-text-muted">
          {detail}
        </p>
      )}

      {stale && (
        <p className="mt-2 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
          Showing the last events Jarvis sent. They may be out of date.
        </p>
      )}

      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-btn bg-light-accent px-4 py-2 text-caption text-white
                   dark:bg-dark-accent-fill"
      >
        Try again
      </button>
    </div>
  )
}
