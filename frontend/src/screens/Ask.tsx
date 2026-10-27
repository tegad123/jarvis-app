import { useCallback, useEffect, useRef, useState } from 'react'
import BrowserWindow from '../components/BrowserWindow'
import { BackButton } from '../components/Page'
import {
  ApiError,
  Unauthorized,
  addThreadMember,
  bumpCounter,
  getChatHistory,
  getThreadMembers,
  markThreadRead,
  removeThreadMember,
  streamChat,
  uploadAttachment,
  type Person,
} from '../lib/api'
import { ACCEPT_ATTR, MAX_UPLOAD_BYTES, humanSize, prepareForUpload } from '../lib/attachments'
import { AssistantText, SavedActions, isChatAction, type ChatAction } from '../components/ChatMessage'
import { stepName, truncateLabel } from '../lib/steps'
import { silence, useSpeaker } from '../lib/useSpeaker'
import { useMic } from '../lib/useMic'
import { MAX_RECORD_SECONDS } from '../lib/voice'
import { Button, Composer, MembersSheet, MessageBubble, StepList, SystemLine, type ChatAttachment, type Mentionable, type Step } from '../jm'
import type { Attachment, ChatHistoryResponse, ChatMessage, JarvisReplies, Role, Thread, ThreadMember } from '../lib/types'

const EXAMPLE_PROMPTS = ['Which emails need me today?', "What's on the calendar this week?", 'Any warm leads I should chase?']

/** How often an open channel asks for what the others wrote. */
const POLL_MS = 3_000

type Stored = ChatHistoryResponse['messages'][number]

/** Server rows into the list by id: new ones in id order, nothing twice, and
 *  what this screen is still sending (no id yet) left where it is. */
function mergeById(cur: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const have = new Set(cur.map((m) => m.id).filter((id): id is number => id !== undefined))
  const add = incoming.filter((m) => m.id !== undefined && !have.has(m.id)).sort((a, b) => a.id! - b.id!)
  if (!add.length) return cur
  const out = [...cur]
  for (const m of add) {
    const at = out.findIndex((x) => x.id !== undefined && x.id > m.id!)
    if (at < 0) out.push(m)
    else out.splice(at, 0, m)
  }
  return out
}

interface Props {
  thread: Thread
  /** From /api/session. Decides what this screen OFFERS; the API decides what
      it allows. Every gate below has a server-side twin. */
  role: Role
  personKey: string | null
  people: Person[]
  /** Settings → Channels: when Jarvis answers in a channel. */
  jarvisReplies: JarvisReplies
  /** This thread has been read up to its newest message. */
  onRead: (threadId: string) => void
  /** Membership changed: the sidebar's rows carry it. */
  onThreadsChanged: () => void
  onUnauthorized: () => void
}

function chip(a: Attachment): ChatAttachment {
  return { id: a.id, name: a.filename, size: a.size_human, isImage: a.is_image, src: a.url, href: a.url, readable: a.readable }
}

function AssistantMessage({
  m,
  isLatest,
  completedAt,
  streaming,
  stopped,
  actions,
  from,
}: {
  m: ChatMessage
  isLatest: boolean
  completedAt?: number
  streaming: boolean
  stopped: boolean
  actions: ChatAction[]
  from?: string
}) {
  const { speech, onSpeak } = useSpeaker(m.content, isLatest, completedAt, bumpCounter)
  return (
    <>
      <AssistantText
        content={m.content}
        from={from}
        streaming={streaming}
        partial={m.partial || stopped}
        partialLabel={stopped ? 'You stopped this answer.' : undefined}
        speech={m.content && !streaming ? speech : undefined}
        onSpeak={onSpeak}
      />
      <SavedActions actions={m.actions} live={actions} />
    </>
  )
}

export default function Ask({ thread, role, personKey, people, jarvisReplies, onRead, onThreadsChanged, onUnauthorized }: Props) {
  // An operator reaches every channel's structure and none of its content.
  // app/auth.py:require_bodies is what actually enforces this; the flag here
  // exists so the screen shows an honest explanation instead of a 403.
  const structureOnly = role === 'operator'
  const canManageMembers = role === 'owner' || role === 'admin'
  // A team channel: people talk to each other here and Jarvis answers when
  // called (app/channels.py). The direct line is the person and Jarvis only.
  const channel = !thread.is_direct
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  // The current turn's steps, shown above its reply; replaced by the next turn.
  const [steps, setSteps] = useState<Step[]>([])
  const [stepsOpen, setStepsOpen] = useState(true)
  // This turn's question and reply, by localId: other people's messages can
  // land between them, so a position in the list would not stay true.
  const [turn, setTurn] = useState<{ q: string; a: string; jarvis: boolean } | null>(null)
  const [stoppedKey, setStoppedKey] = useState<string | null>(null)
  // Whom Jarvis is answering in this channel for someone else, from the poll.
  const [answering, setAnswering] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  // Uploaded and waiting to be sent with the next message.
  const [pending, setPending] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // The turn's own check against the store. Server-generated: a receipt
  // built from the saved row, or a correction when the reply claimed a
  // task that was never written.
  const [verified, setVerified] = useState<{ ok: boolean; message: string } | null>(null)
  const [chatActions, setChatActions] = useState<ChatAction[]>([])
  const [members, setMembers] = useState<ThreadMember[] | null>(null)
  const [membersOpen, setMembersOpen] = useState(false)
  const [membersBusy, setMembersBusy] = useState(false)
  const [membersError, setMembersError] = useState<string | null>(null)
  const [browserHidden, setBrowserHidden] = useState(false)
  const [browserAvailable, setBrowserAvailable] = useState(false)
  // Bumped when a reply finishes; read-aloud keys auto-speak off it.
  const [replyDoneAt, setReplyDoneAt] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const stoppedRef = useRef(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const contentRef = useRef<HTMLDivElement | null>(null)
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  const pinnedRef = useRef(true)
  const onUnauthorizedRef = useRef(onUnauthorized)
  onUnauthorizedRef.current = onUnauthorized
  const onReadRef = useRef(onRead)
  onReadRef.current = onRead
  // Where the next poll starts, and whether this screen's own turn is running
  // (its reply arrives through the stream, so the poll leaves it alone).
  const lastIdRef = useRef(0)
  const streamingRef = useRef(false)
  const readRef = useRef(0)
  // Rows already on screen another way: a turn's receipt is the verified line.
  const shownElsewhere = useRef(new Set<number>())

  const mic = useMic((text) => setDraft((cur) => (cur.trim() ? `${cur.trim()} ${text}` : text)))

  // Load THIS thread. Histories are per-thread server-side; the shell remounts
  // this screen per thread, so nothing from another conversation carries over.
  useEffect(() => {
    // Membership is structure, so it loads for every role including operator.
    if (!thread.is_direct) {
      void getThreadMembers(thread.thread_id)
        .then(setMembers)
        .catch((err) => {
          if (err instanceof Unauthorized) onUnauthorizedRef.current()
          setMembers([])
        })
    }
    // The one thing an operator does NOT fetch. Not asking says what we intend,
    // rather than the UI's rule and the server's being only accidentally the same.
    if (structureOnly) return
    void getChatHistory(thread.thread_id)
      .then((r) => {
        lastIdRef.current = r.last_id ?? 0
        setAnswering(r.jarvis_answering ?? [])
        setMessages(r.messages.map((m) => ({ ...m, thread_id: thread.thread_id })))
      })
      .catch((err) => {
        // An empty thread is the normal case; only auth failures matter here.
        if (err instanceof Unauthorized) onUnauthorizedRef.current()
      })
  }, [thread.thread_id, thread.is_direct, structureOnly])

  // CHANNELS: what the others wrote, every few seconds while you are looking.
  // While your own turn streams, its reply and your own message come through
  // the stream; the poll still brings everyone else's.
  const poll = useCallback(async () => {
    try {
      const r = await getChatHistory(thread.thread_id, lastIdRef.current)
      const fresh = r.messages
        .map((m: Stored) => ({ ...m, thread_id: thread.thread_id }))
        .filter((m) => !shownElsewhere.current.has(m.id))
        .filter((m) => !streamingRef.current || (m.role === 'user' && m.author !== personKey))
      if (!streamingRef.current) lastIdRef.current = Math.max(lastIdRef.current, r.last_id ?? 0)
      setAnswering(r.jarvis_answering ?? [])
      if (fresh.length) setMessages((cur) => mergeById(cur, fresh))
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorizedRef.current()
    }
  }, [thread.thread_id, personKey])

  useEffect(() => {
    if (!channel || structureOnly) return
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void poll()
    }, POLL_MS)
    return () => window.clearInterval(t)
  }, [channel, structureOnly, poll])

  // Read as it arrives: the newest saved id goes to the server while the
  // screen is in front of you, and the sidebar's dot goes out.
  const markRead = useCallback(() => {
    if (structureOnly || document.visibilityState !== 'visible') return
    const newest = messagesRef.current.reduce((n, m) => (m.id !== undefined && m.id > n ? m.id : n), 0)
    if (newest <= readRef.current) return
    readRef.current = newest
    void markThreadRead(thread.thread_id, newest)
      .then(() => onReadRef.current(thread.thread_id))
      .catch((err) => {
        if (err instanceof Unauthorized) onUnauthorizedRef.current()
      })
  }, [thread.thread_id, structureOnly])

  useEffect(() => {
    markRead()
  }, [messages, markRead])

  useEffect(() => {
    document.addEventListener('visibilitychange', markRead)
    return () => document.removeEventListener('visibilitychange', markRead)
  }, [markRead])

  // Abort any in-flight answer on leaving — dropping the connection tells the
  // gateway to interrupt the agent rather than keep working — and never let
  // one thread's reply keep talking over another.
  useEffect(
    () => () => {
      abortRef.current?.abort()
      silence()
    },
    [],
  )

  // Follow the bottom whenever the transcript grows — new text, a late image,
  // a control that appears — unless the person has scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current
    const content = contentRef.current
    if (!el || !content) return
    const follow = () => {
      if (pinnedRef.current) el.scrollTop = el.scrollHeight
    }
    follow()
    const ro = new ResizeObserver(follow)
    ro.observe(content)
    return () => ro.disconnect()
  }, [])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }, [])

  const changeMembers = useCallback(
    async (fn: () => Promise<ThreadMember[]>, failure: string) => {
      setMembersBusy(true)
      setMembersError(null)
      try {
        setMembers(await fn())
        onThreadsChanged()
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorizedRef.current()
        setMembersError(err instanceof ApiError ? err.message : failure)
      } finally {
        setMembersBusy(false)
      }
    },
    [onThreadsChanged],
  )

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      // An attachment with no caption is a legitimate turn.
      if ((!message && pending.length === 0) || streaming) return
      const sending = pending

      setError(null)
      setNotice(null)
      setDraft('')
      setPending([])
      silence() // a new question supersedes the previous answer being read
      setStreaming(true)
      streamingRef.current = true
      setSteps([])
      setStepsOpen(true)
      setStoppedKey(null)
      stoppedRef.current = false
      pinnedRef.current = true
      setVerified(null)
      setChatActions([])

      // This turn's question and reply, by handle. In a channel Jarvis may not
      // be asked at all; the server says so in its first line ("posted").
      const stamp = `${Date.now()}`
      const q = `q-${stamp}`
      const a = `a-${stamp}`
      setMessages((m) => [
        ...m,
        { localId: q, thread_id: thread.thread_id, role: 'user', author: personKey, content: message, ts: Date.now() / 1000, attachments: sending },
      ])
      setTurn({ q, a, jarvis: !channel })

      let assistant = ''
      const ensureAssistant = () => {
        setMessages((m) => {
          const i = m.findIndex((x) => x.localId === a)
          if (i >= 0) return m.map((x, j) => (j === i ? { ...x, content: assistant } : x))
          // Right under its question, even if someone else wrote since.
          const at = m.findIndex((x) => x.localId === q)
          const reply = { localId: a, thread_id: thread.thread_id, role: 'assistant' as const, content: assistant, ts: Date.now() / 1000 }
          return at < 0 ? [...m, reply] : [...m.slice(0, at + 1), reply, ...m.slice(at + 1)]
        })
      }
      const settle = (status: 'done' | 'error') =>
        setSteps((cur) => cur.map((s) => (s.status === 'running' ? { ...s, status } : s)))

      const controller = new AbortController()
      abortRef.current = controller

      try {
        await streamChat(
          thread.thread_id,
          message,
          {
            onDelta: (t) => {
              assistant += t
              ensureAssistant()
            },
            onTool: (e) => {
              if (e.status === 'running') {
                const id = e.toolCallId ?? `step-${Date.now()}`
                // The server already turns the agent's tool into a plain step ("jarvis" + label).
                const label = e.tool === 'jarvis' && e.label ? e.label : stepName(e.tool)
                const detail = e.tool === 'jarvis' ? undefined : truncateLabel(e.label) || undefined
                setSteps((cur) => [...cur.filter((s) => s.id !== id), { id, label, detail, status: 'running' }])
              } else {
                setSteps((cur) => {
                  const i = e.toolCallId ? cur.findIndex((s) => s.id === e.toolCallId) : cur.map((s) => s.status).lastIndexOf('running')
                  return i < 0 ? cur : cur.map((s, j) => (j === i ? { ...s, status: 'done' } : s))
                })
              }
            },
            onVerified: (e) => setVerified({ ok: e.ok, message: e.message }),
            onAction: (e) => {
              const a = e.action
              if (isChatAction(a)) setChatActions((cur) => (cur.includes(a) ? cur : [...cur, a]))
            },
            onDone: () => {
              settle('done')
              setStepsOpen(false)
              setReplyDoneAt(Date.now())
            },
            onError: (e) => {
              setError(e.message)
              settle('error')
              if (assistant) ensureAssistant()
            },
            // Something was deliberately withheld (a document). Say so rather
            // than letting the user assume Jarvis read it.
            onNotice: (e) => setNotice(e.message),
            // Channels: the message is saved and the others can see it.
            onPosted: (e) => {
              setMessages((m) => m.map((x) => (x.localId === q ? { ...x, id: e.message.id, author: e.message.author, author_name: e.message.author_name } : x)))
              setTurn((t) => (t && t.q === q ? { ...t, jarvis: e.jarvis } : t))
            },
            // The rows this turn wrote: the reply gets its id, and a receipt
            // (already on screen as the verified line) is not merged in again.
            onSaved: (e) => {
              const [replyId, ...rest] = e.ids
              if (replyId !== undefined) setMessages((m) => m.map((x) => (x.localId === a ? { ...x, id: replyId } : x)))
              rest.forEach((id) => shownElsewhere.current.add(id))
            },
          },
          controller.signal,
          sending.map((a) => a.id),
        )
        if (stoppedRef.current) {
          settle('error')
          if (assistant) setStoppedKey(a)
          else setNotice('You stopped this answer.')
        }
      } catch (err) {
        if (err instanceof Unauthorized) {
          onUnauthorizedRef.current()
          return
        }
        settle('error')
        setError(err instanceof ApiError && err.status === 0 ? "Can't reach Jarvis right now." : 'Something went wrong sending that.')
        setDraft(message)
        // Put the attachments back so a retry does not lose them.
        setPending(sending)
      } finally {
        setStreaming(false)
        streamingRef.current = false
        abortRef.current = null
        // Whatever the poll held back while this turn ran (someone else's
        // Jarvis reply, say) comes in now, in order.
        if (channel) void poll()
      }
    },
    [thread.thread_id, streaming, pending, channel, personKey, poll],
  )

  const stop = useCallback(() => {
    stoppedRef.current = true
    abortRef.current?.abort()
  }, [])

  const onPick = useCallback(
    async (files: FileList | null) => {
      if (!files || !files.length) return
      setError(null)
      setNotice(null)
      setUploading(true)
      try {
        for (const file of Array.from(files)) {
          // Refuse before spending the upload when it cannot possibly fit.
          if (file.size > MAX_UPLOAD_BYTES * 4) {
            setError(`${file.name} is ${humanSize(file.size)} — too large to send.`)
            continue
          }
          try {
            // Downscale/re-encode photos so a 5 MB phone shot fits the budget
            // invisibly; documents pass through untouched.
            const prepared = await prepareForUpload(file)
            if (prepared.size > MAX_UPLOAD_BYTES) {
              setError(`${file.name} is ${humanSize(prepared.size)} after compression — the limit is ${humanSize(MAX_UPLOAD_BYTES)}.`)
              continue
            }
            const att = await uploadAttachment(thread.thread_id, prepared)
            setPending((cur) => [...cur, att])
          } catch (err) {
            if (err instanceof Unauthorized) return onUnauthorizedRef.current()
            // Server wording is written to be shown verbatim. Never a silent drop.
            setError(err instanceof ApiError ? err.message : `${file.name} could not be uploaded.`)
          }
        }
      } finally {
        setUploading(false)
      }
    },
    [thread.thread_id],
  )

  const lastAssistant = messages.map((m) => m.role).lastIndexOf('assistant')
  const stepNode =
    steps.length > 0 ? <StepList steps={steps} open={stepsOpen} onOpenChange={setStepsOpen} /> : null
  const hasReply = turn !== null && messages.some((m) => m.localId === turn.a)
  // Dots while Jarvis works on THIS person's turn; in a channel only once the
  // server has said Jarvis is answering it.
  const waiting = streaming && turn !== null && turn.jarvis && !hasReply

  // Channels: who an @ can reach, and how each person's names read.
  const named = new Map(people.map((p) => [p.person_key, p]))
  const me = personKey ? named.get(personKey) : undefined
  const namesOf = (name?: string) => (name ? [name, name.split(/\s+/)[0]] : [])
  const mentionNames = {
    names: ['jarvis', 'assistant', ...(members ?? []).flatMap((m) => namesOf(named.get(m.person_key)?.name))],
    self: namesOf(me?.name),
  }
  const mentionables: Mentionable[] = channel
    ? [
        { id: 'jarvis', name: 'jarvis', hint: 'Ask Jarvis' },
        ...(members ?? [])
          .filter((m) => m.person_key !== personKey && named.has(m.person_key))
          .map((m) => ({ id: m.person_key, name: named.get(m.person_key)!.name, hint: named.get(m.person_key)!.title || undefined })),
      ]
    : []
  const othersWaiting = channel && !streaming && answering.length > 0

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex min-h-[56px] items-center gap-jm-2 border-b border-jm-border px-jm-4">
        <BackButton label="Back to conversations" />
        <h1 className="m-0 min-w-0 flex-1 truncate text-jm-heading text-jm-fg">{thread.name}</h1>
        {browserAvailable && browserHidden && !structureOnly && (
          <Button variant="ghost" size="sm" icon="globe" onClick={() => setBrowserHidden(false)}>
            Browser
          </Button>
        )}
        {/* Members live on the channel header: who can read a deal channel is
            something you want to check at a glance. Direct lines have none. */}
        {!thread.is_direct && (
          <Button variant="ghost" size="sm" icon="members" aria-label={`Members of ${thread.name}`} onClick={() => setMembersOpen(true)}>
            {members === null ? '—' : members.length}
          </Button>
        )}
      </header>

      <div ref={scrollRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        <div ref={contentRef} className="mx-auto grid w-full max-w-jm-chat gap-jm-5 px-jm-4 py-jm-6">
          {structureOnly ? (
            // OPERATOR: STRUCTURE, NOT BODIES. The channel and its membership
            // are here; the conversation is not, and the screen says why.
            <SystemLine
              detail="Reading messages needs the account owner to sign in. If that is genuinely required, reset their PIN with them on the call."
              action={thread.is_direct ? undefined : { label: 'See who is in this channel', onClick: () => setMembersOpen(true) }}
            >
              Operator access shows this channel and who is in it — not what was said in it.
            </SystemLine>
          ) : messages.length === 0 && !streaming && channel ? (
            <div className="grid gap-jm-2 py-jm-8">
              <p className="m-0 text-jm-title text-jm-fg">This is the start of {thread.name}.</p>
              <p className="m-0 text-jm-body text-jm-fg-secondary">
                Everyone in this channel reads what is written here.{' '}
                {jarvisReplies === 'mention' ? 'Type @jarvis when you want Jarvis to answer.' : 'Jarvis answers every message here.'}
              </p>
            </div>
          ) : messages.length === 0 && !streaming ? (
            <div className="grid gap-jm-4 py-jm-8">
              <p className="m-0 text-jm-title text-jm-fg">Ask Jarvis anything about your leads, email, or schedule.</p>
              <div className="flex flex-wrap gap-jm-2">
                {EXAMPLE_PROMPTS.map((p) => (
                  <Button key={p} onClick={() => void send(p)}>
                    {p}
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((m, i) => {
                const live = turn !== null && m.localId === turn.a
                // In a channel someone else's message carries their name; one
                // saved before authors were recorded says so instead of guessing.
                const mine = !channel || m.author === personKey
                return (
                  <div key={m.id ?? m.localId ?? `i${i}`} className="grid gap-jm-3">
                    {live && stepNode}
                    {m.role === 'user' ? (
                      <MessageBubble
                        role="user"
                        text={m.content || undefined}
                        attachments={(m.attachments ?? []).map(chip)}
                        from={mine ? undefined : m.author_name || 'Earlier message'}
                        mentions={channel ? mentionNames : undefined}
                      />
                    ) : (
                      <AssistantMessage
                        m={m}
                        from={channel ? 'Jarvis' : undefined}
                        isLatest={i === messages.length - 1}
                        completedAt={i === messages.length - 1 ? replyDoneAt : undefined}
                        streaming={streaming && live}
                        stopped={stoppedKey !== null && m.localId === stoppedKey}
                        actions={i === lastAssistant ? chatActions : []}
                      />
                    )}
                  </div>
                )
              })}
              {waiting && (stepNode ?? <MessageBubble role="assistant" typing />)}
              {!streaming && !waiting && turn !== null && !hasReply && stepNode}
              {othersWaiting && (
                <MessageBubble role="assistant" typing from="Jarvis" typingLabel={`Jarvis is answering ${answering.join(' and ')}`} />
              )}
            </>
          )}

          {error && <SystemLine tone="error">{error}</SystemLine>}
          {notice && <SystemLine>{notice}</SystemLine>}

          {/* THE TURN'S RECEIPT, and it is not the model talking. A confirmation
              comes from the row that was saved; a correction appears when the
              reply claimed a task that does not exist. Kept apart from the
              bubble so it never reads as more prose from the same source. */}
          {verified && (
            <div data-testid="verified" data-ok={verified.ok ? 'true' : 'false'} className="whitespace-pre-line">
              <SystemLine tone={verified.ok ? 'done' : 'error'}>{verified.message}</SystemLine>
            </div>
          )}

          {/* A live action with no reply to sit under still gets its button. */}
          {lastAssistant < 0 && <SavedActions live={chatActions} />}
        </div>
      </div>

      {/* No composer and no browser for an operator: break-glass looks, it does
          not speak. The API refuses the POST regardless (require_bodies). */}
      {!structureOnly && (
        <div className="mx-auto grid w-full max-w-jm-chat gap-jm-3 px-jm-4 pb-[calc(16px+env(safe-area-inset-bottom))] pt-jm-2">
          <BrowserWindow
            busy={streaming}
            hidden={browserHidden}
            onHiddenChange={setBrowserHidden}
            onAvailableChange={setBrowserAvailable}
            onUnauthorized={onUnauthorized}
          />
          <Composer
            value={draft}
            onChange={setDraft}
            onSubmit={(t) => void send(t)}
            placeholder={
              !channel ? 'Ask Jarvis…' : jarvisReplies === 'mention' ? `Message ${thread.name} · @jarvis to ask Jarvis` : `Message ${thread.name}`
            }
            label={channel ? `Message ${thread.name}` : 'Message Jarvis'}
            mentionables={mentionables}
            attachments={pending.map(chip)}
            onRemoveAttachment={(a) => setPending((cur) => cur.filter((x) => x.id !== a.id))}
            uploading={uploading}
            onFiles={(f) => void onPick(f)}
            accept={ACCEPT_ATTR}
            // The transcript lands in the field EDITABLE — it never auto-sends.
            voice={mic.state}
            onMic={mic.supported ? mic.toggle : undefined}
            onDismissVoice={mic.dismiss}
            maxSeconds={MAX_RECORD_SECONDS}
            streaming={streaming}
            onStop={stop}
          />
        </div>
      )}

      {membersOpen && (
        <MembersSheet
          channelName={thread.name}
          members={(members ?? []).map((m) => m.person_key)}
          people={people.map((p) => ({ id: p.person_key, name: p.name, title: p.title }))}
          personKey={personKey}
          canManage={canManageMembers}
          busy={membersBusy}
          error={membersError}
          onAdd={(k) => void changeMembers(() => addThreadMember(thread.thread_id, k), 'Could not add them.')}
          onRemove={(k) => void changeMembers(() => removeThreadMember(thread.thread_id, k), 'Could not remove them.')}
          onClose={() => {
            setMembersOpen(false)
            setMembersError(null)
          }}
        />
      )}
    </div>
  )
}
