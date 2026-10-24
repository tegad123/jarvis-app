import { Button, MessageBubble, type MessageBubbleProps } from '../jm'

/**
 * What the assistant says, and the app's own controls under it.
 *
 * The model's words are plain text, always: no markdown, no links, nothing
 * clickable, whatever it writes. A control in the chat comes only from a
 * server `action` event, and its destination is fixed here, never taken from
 * the model or the event. docs/frontend/verify_chat_links.mjs renders these.
 */

export const CONNECT_GOOGLE_START = '/api/google/connect/start'

export function AssistantText({ content, ...rest }: { content: string } & Omit<MessageBubbleProps, 'role' | 'text' | 'children' | 'actions'>) {
  return <MessageBubble role="assistant" text={content} {...rest} />
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
    <Button variant="primary" data-testid="chat-action" data-action={action} onClick={() => go(ACTION_TARGET[action])}>
      {ACTION_LABEL[action]}
    </Button>
  )
}

/** The buttons under one reply: this turn's live actions plus any saved with it. Only known actions draw. */
export function SavedActions({
  actions,
  live = [],
  go,
}: {
  actions?: { action: string }[]
  live?: ChatAction[]
  go?: (url: string) => void
}) {
  const known = [...new Set([...live, ...(actions ?? []).map((a) => a.action)].filter(isChatAction))]
  if (!known.length) return null
  return (
    <div className="flex flex-wrap gap-jm-2">
      {known.map((a) => (
        <ChatActionButton key={a} action={a} go={go} />
      ))}
    </div>
  )
}
