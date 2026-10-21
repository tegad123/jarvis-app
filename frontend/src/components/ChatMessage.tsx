/**
 * What the assistant says, and the app's own controls under it.
 *
 * The model's words are plain text, always: no markdown, no links, nothing
 * clickable, whatever it writes. A control in the chat comes only from a
 * server `action` event, and its destination is fixed here, never taken from
 * the model or the event.
 */

export const CONNECT_GOOGLE_START = '/api/google/connect/start'

export function AssistantText({ content }: { content: string }) {
  return (
    <p className="mt-1.5 whitespace-pre-wrap text-chat text-light-text-primary dark:text-dark-text-primary">
      {content}
    </p>
  )
}

export type ChatAction = 'connect_google'

const ACTION_LABEL: Record<ChatAction, string> = {
  connect_google: 'Connect your Google account',
}

const ACTION_TARGET: Record<ChatAction, string> = {
  connect_google: CONNECT_GOOGLE_START,
}

export function isChatAction(a: unknown): a is ChatAction {
  return typeof a === 'string' && Object.prototype.hasOwnProperty.call(ACTION_LABEL, a)
}

export function ChatActionButton({
  action,
  go = (url: string) => window.location.assign(url),
}: {
  action: ChatAction
  go?: (url: string) => void
}) {
  return (
    <button
      type="button"
      data-testid="chat-action"
      data-action={action}
      onClick={() => go(ACTION_TARGET[action])}
      className="mt-3 rounded-card bg-light-accent px-4 py-2.5 text-chat text-white dark:bg-dark-accent-fill"
    >
      {ACTION_LABEL[action]}
    </button>
  )
}

/** Controls saved under a reply in history. Only known actions draw, with this file's own label and target. */
export function SavedActions({
  actions,
  go,
}: {
  actions?: { action: string }[]
  go?: (url: string) => void
}) {
  const known = [...new Set((actions ?? []).map((a) => a.action).filter(isChatAction))]
  return (
    <>
      {known.map((a) => (
        <ChatActionButton key={a} action={a} go={go} />
      ))}
    </>
  )
}
