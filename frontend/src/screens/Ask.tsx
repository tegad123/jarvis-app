import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import AsteriskMark from '../components/AsteriskMark'
import AttachmentChip from '../components/AttachmentChip'
import ConversationsList from '../components/ConversationsList'
import MicButton, { VoiceStatus } from '../components/MicButton'
import SpeakerToggle, { silence } from '../components/SpeakerToggle'
import NewChannelSheet from '../components/NewChannelSheet'
import MembersSheet from '../components/MembersSheet'
import {
  ApiError,
  Unauthorized,
  archiveThread,
  createThread,
  getChatHistory,
  getPeople,
  getThreadMembers,
  getThreads,
  addThreadMember,
  removeThreadMember,
  renameThread,
  streamChat,
  uploadAttachment,
  bumpCounter,
} from '../lib/api'
import { THINKING, stepName, truncateLabel } from '../lib/steps'
import { AssistantText, ChatActionButton, SavedActions, isChatAction, type ChatAction } from '../components/ChatMessage'
import { ACCEPT_ATTR, MAX_UPLOAD_BYTES, humanSize, prepareForUpload } from '../lib/attachments'
import type { VoiceState } from '../lib/voice'
import type { Attachment, ChatMessage, Role, Thread, ThreadMember } from '../lib/types'
import type { Person } from '../lib/api'

const EXAMPLE_PROMPTS = [
  'Which emails need me today?',
  "What's on the calendar this week?",
  'Any warm leads I should chase?',
]

interface Props {
  /** From /api/session. Decides what this screen OFFERS; the API decides what
      it allows. Every gate below has a server-side twin. */
  role: Role
  personKey: string | null
  onUnauthorized: () => void
}

interface Step {
  name: string
  label?: string
}

type Sheet =
  | { mode: 'create' }
  | { mode: 'rename'; thread: Thread }
  | null

export default function Ask({ role, personKey, onUnauthorized }: Props) {
  // An operator reaches every channel's structure and none of its content.
  // app/auth.py:require_bodies is what actually enforces this; the flag here
  // exists so the screen shows an honest explanation instead of a 403.
  const structureOnly = role === 'operator'
  const canManageMembers = role === 'owner' || role === 'admin'
  const [threads, setThreads] = useState<Thread[] | null>(null)
  const [openThread, setOpenThread] = useState<Thread | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [step, setStep] = useState<Step | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sheet, setSheet] = useState<Sheet>(null)
  const [sheetBusy, setSheetBusy] = useState(false)
  const [sheetError, setSheetError] = useState<string | null>(null)
  // Uploaded and waiting to be sent with the next message.
  const [pending, setPending] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // The turn's own check against the store. Server-generated: a receipt
  // built from the saved row, or a correction when the reply claimed a
  // task that was never written.
  const [verified, setVerified] = useState<{ ok: boolean; message: string } | null>(null)
  const [chatActions, setChatActions] = useState<ChatAction[]>([])
  const [voice, setVoice] = useState<VoiceState>({ phase: 'idle' })
  const [members, setMembers] = useState<ThreadMember[] | null>(null)
  const [membersOpen, setMembersOpen] = useState(false)
  const [membersBusy, setMembersBusy] = useState(false)
  const [membersError, setMembersError] = useState<string | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  // Bumped when a reply finishes; SpeakerToggle keys auto-speak off it.
  const [replyDoneAt, setReplyDoneAt] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const pinnedRef = useRef(true)
  const toolLabels = useRef<Map<string, string>>(new Map())
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const onUnauthorizedRef = useRef(onUnauthorized)
  onUnauthorizedRef.current = onUnauthorized
  const structureOnlyRef = useRef(structureOnly)
  structureOnlyRef.current = structureOnly

  // The roster is needed by ANYONE who can open the members sheet, not just
  // by someone who can edit it: without it the sheet renders raw person keys
  // ("spencer") instead of names, which is what an operator saw. It is the
  // same unauthenticated list the login picker uses, so this costs nothing.
  useEffect(() => {
    void getPeople().then(setPeople).catch(() => setPeople([]))
  }, [])

  const loadThreads = useCallback(async () => {
    try {
      const r = await getThreads()
      setThreads(r.threads)
      return r.threads
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorizedRef.current()
      else setError('Could not load your conversations.')
      return null
    }
  }, [])

  useEffect(() => {
    void loadThreads()
  }, [loadThreads])

  // Abort any in-flight answer on unmount — dropping the connection tells the
  // gateway to interrupt the agent rather than keep working.
  useEffect(
    () => () => {
      abortRef.current?.abort()
      silence()
    },
    [],
  )

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight
  }, [])
  useEffect(scrollToBottom, [messages, step, scrollToBottom])

  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }, [])

  /** Open a thread and load ITS history. Histories are per-thread server-side. */
  const open = useCallback(async (thread: Thread) => {
    abortRef.current?.abort()
    silence()   // never let one thread's reply keep talking over another
    setOpenThread(thread)
    setMessages([])
    setChatActions([])
    setError(null)
    setStep(null)
    setDraft('')
    setPending([])
    setNotice(null)
    setVoice({ phase: 'idle' })
    pinnedRef.current = true
    setMembers(null)
    setMembersError(null)

    // Membership is structure, so it loads for every role including operator.
    if (!thread.is_direct) {
      void getThreadMembers(thread.thread_id)
        .then(setMembers)
        .catch((err) => {
          if (err instanceof Unauthorized) onUnauthorizedRef.current()
          setMembers([])
        })
    }

    // The one thing an operator does NOT fetch. Asking and swallowing the 403
    // would work, but it would also mean the UI's rule and the server's rule
    // are only accidentally the same; not asking says what we intend.
    if (structureOnlyRef.current) return
    try {
      const r = await getChatHistory(thread.thread_id)
      setMessages(r.messages.map((m) => ({ thread_id: thread.thread_id, ...m })))
    } catch (err) {
      if (err instanceof Unauthorized) onUnauthorizedRef.current()
      // An empty thread is the normal case; only auth failures matter here.
    }
  }, [])

  const addMember = useCallback(
    async (key: string) => {
      if (!openThread) return
      setMembersBusy(true)
      setMembersError(null)
      try {
        setMembers(await addThreadMember(openThread.thread_id, key))
        await loadThreads()
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorizedRef.current()
        setMembersError(err instanceof ApiError ? err.message : 'Could not add them.')
      } finally {
        setMembersBusy(false)
      }
    },
    [openThread, loadThreads],
  )

  const dropMember = useCallback(
    async (key: string) => {
      if (!openThread) return
      setMembersBusy(true)
      setMembersError(null)
      try {
        setMembers(await removeThreadMember(openThread.thread_id, key))
        await loadThreads()
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorizedRef.current()
        setMembersError(err instanceof ApiError ? err.message : 'Could not remove them.')
      } finally {
        setMembersBusy(false)
      }
    },
    [openThread, loadThreads],
  )

  const back = useCallback(() => {
    abortRef.current?.abort()
    silence()
    setOpenThread(null)
    setMessages([])
    setStep(null)
    setError(null)
    void loadThreads()
  }, [loadThreads])

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      const thread = openThread
      // An attachment with no caption is a legitimate turn.
      if ((!message && pending.length === 0) || streaming || !thread) return
      const sending = pending

      setError(null)
      setNotice(null)
      setDraft('')
      setPending([])
      silence()   // a new question supersedes the previous answer being read
      setStreaming(true)
      setStep({ name: THINKING })
      toolLabels.current.clear()
      pinnedRef.current = true
      setVerified(null)
      setChatActions([])

      setMessages((m) => [
        ...m,
        {
          thread_id: thread.thread_id,
          role: 'user',
          content: message,
          ts: Date.now() / 1000,
          attachments: sending,
        },
      ])

      let assistant = ''
      const ensureAssistant = () => {
        setMessages((m) => {
          const last = m[m.length - 1]
          if (last && last.role === 'assistant') {
            return [...m.slice(0, -1), { ...last, content: assistant }]
          }
          return [
            ...m,
            {
              thread_id: thread.thread_id,
              role: 'assistant' as const,
              content: assistant,
              ts: Date.now() / 1000,
            },
          ]
        })
      }

      const controller = new AbortController()
      abortRef.current = controller

      try {
        await streamChat(
          thread.thread_id,
          message,
          {
            onDelta: (t) => {
              assistant += t
              setStep(null)
              ensureAssistant()
            },
            onTool: (e) => {
              if (e.status === 'running') {
                if (e.toolCallId && e.label) toolLabels.current.set(e.toolCallId, e.label)
                setStep(e.tool === 'jarvis' && e.label ? { name: e.label } : { name: stepName(e.tool), label: e.label })
              } else {
                setStep(assistant ? null : { name: THINKING })
              }
            },
            onVerified: (e) => setVerified({ ok: e.ok, message: e.message }),
            onAction: (e) => {
              const a = e.action
              if (isChatAction(a)) setChatActions((cur) => (cur.includes(a) ? cur : [...cur, a]))
            },
            onDone: () => {
              setStep(null)
              setReplyDoneAt(Date.now())
            },
            onError: (e) => {
              setError(e.message)
              setStep(null)
              if (assistant) ensureAssistant()
            },
            // Something was deliberately withheld (a document). Say so rather
            // than letting the user assume Jarvis read it.
            onNotice: (e) => setNotice(e.message),
          },
          controller.signal,
          sending.map((a) => a.id),
        )
      } catch (err) {
        if (err instanceof Unauthorized) {
          onUnauthorizedRef.current()
          return
        }
        setError(
          err instanceof ApiError && err.status === 0
            ? "Can't reach Jarvis right now."
            : 'Something went wrong sending that.',
        )
        setDraft(message)
        // Put the attachments back so a retry does not lose them.
        setPending(sending)
      } finally {
        setStreaming(false)
        setStep(null)
        abortRef.current = null
      }
    },
    [openThread, streaming, pending],
  )

  /* ---------------------------------------------------------- attachments */

  const onPick = useCallback(
    async (files: FileList | null) => {
      const thread = openThread
      if (!files || !files.length || !thread) return
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
              setError(
                `${file.name} is ${humanSize(prepared.size)} after compression — ` +
                  `the limit is ${humanSize(MAX_UPLOAD_BYTES)}.`,
              )
              continue
            }
            const att = await uploadAttachment(thread.thread_id, prepared)
            setPending((cur) => [...cur, att])
          } catch (err) {
            if (err instanceof Unauthorized) return onUnauthorizedRef.current()
            // Server wording is written to be shown verbatim (too large,
            // unsupported type). Never a silent drop.
            setError(err instanceof ApiError ? err.message : `${file.name} could not be uploaded.`)
          }
        }
      } finally {
        setUploading(false)
        if (fileInputRef.current) fileInputRef.current.value = ''
      }
    },
    [openThread],
  )

  /* --------------------------------------------------------------- sheets */

  const submitSheet = useCallback(
    async (name: string) => {
      if (!sheet) return
      setSheetBusy(true)
      setSheetError(null)
      try {
        if (sheet.mode === 'create') {
          const created = await createThread(name)
          setSheet(null)
          const list = await loadThreads()
          // Drop straight into the new channel — creating one is an intent to
          // use it, not to admire it in a list.
          void open(list?.find((t) => t.thread_id === created.thread_id) ?? created)
        } else {
          await renameThread(sheet.thread.thread_id, name)
          setSheet(null)
          await loadThreads()
        }
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorizedRef.current()
        setSheetError(err instanceof ApiError ? err.message : 'That did not work.')
      } finally {
        setSheetBusy(false)
      }
    },
    [sheet, loadThreads, open],
  )

  const archive = useCallback(
    async (thread: Thread) => {
      try {
        await archiveThread(thread.thread_id, true)
        await loadThreads()
      } catch (err) {
        if (err instanceof Unauthorized) return onUnauthorizedRef.current()
        setError(err instanceof ApiError ? err.message : 'Could not archive that channel.')
      }
    },
    [loadThreads],
  )

  const sheetNode = sheet && (
    <NewChannelSheet
      title={sheet.mode === 'create' ? 'New channel' : 'Rename channel'}
      submitLabel={sheet.mode === 'create' ? 'Create' : 'Save'}
      initialValue={sheet.mode === 'rename' ? sheet.thread.name : ''}
      busy={sheetBusy}
      error={sheetError}
      onSubmit={submitSheet}
      onClose={() => {
        setSheet(null)
        setSheetError(null)
      }}
    />
  )

  const membersNode = membersOpen && openThread && (
    <MembersSheet
      channelName={openThread.name}
      members={members ?? []}
      people={people}
      canManage={canManageMembers}
      personKey={personKey}
      busy={membersBusy}
      error={membersError}
      onAdd={(k) => void addMember(k)}
      onRemove={(k) => void dropMember(k)}
      onClose={() => {
        setMembersOpen(false)
        setMembersError(null)
      }}
    />
  )

  /* ----------------------------------------------------------------- list */

  if (!openThread) {
    const direct = threads?.find((t) => t.is_direct) ?? null
    const channels = (threads ?? []).filter((t) => !t.is_direct)
    return (
      <>
        {threads === null ? (
          <p className="py-8 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
            Loading…
          </p>
        ) : (
          <ConversationsList
            direct={direct}
            channels={channels}
            onOpen={(t) => void open(t)}
            onCompose={() => {
              setSheetError(null)
              setSheet({ mode: 'create' })
            }}
            onRename={(t) => {
              setSheetError(null)
              setSheet({ mode: 'rename', thread: t })
            }}
            onArchive={(t) => void archive(t)}
          />
        )}
        {error && (
          <p role="alert" className="mt-3 text-body-s text-light-alert dark:text-dark-alert">
            {error}
          </p>
        )}
        {sheetNode}
      </>
    )
  }

  /* ------------------------------------------------------------- interior */

  return (
    <div className="grid min-w-0 gap-3">
      <header className="flex items-center gap-2">
        <button
          type="button"
          onClick={back}
          aria-label="Back to conversations"
          className="-ml-2 grid h-9 w-9 shrink-0 place-items-center rounded-btn
                     text-light-text-secondary dark:text-dark-text-secondary"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M15 5 8 12l7 7"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h2 className="min-w-0 flex-1 truncate text-screen-title">{openThread.name}</h2>

        {/* Members live on the channel header rather than behind the overflow
            menu: who can read a deal channel is the kind of thing you want to
            be able to CHECK at a glance, not go looking for. Direct lines have
            no control because they have no members, by construction. */}
        {!openThread.is_direct && (
          <button
            type="button"
            onClick={() => setMembersOpen(true)}
            aria-label={`Members of ${openThread.name}`}
            className="flex shrink-0 items-center gap-1.5 rounded-pill border border-light-border
                       px-3 py-1.5 text-caption text-light-text-secondary
                       dark:border-dark-border dark:text-dark-text-secondary"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M16 19v-1a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v1" />
              <circle cx="9.5" cy="7" r="3.5" />
              <path d="M17 11a3 3 0 1 0 0-6M21 19v-1a3.6 3.6 0 0 0-2.5-3.4" />
            </svg>
            {members === null ? '—' : members.length}
          </button>
        )}
      </header>

      {/* OPERATOR: STRUCTURE, NOT BODIES. The channel, its name and its
          membership are all here; the conversation is not, and the screen says
          why rather than rendering an empty thread that reads like a bug. */}
      {structureOnly ? (
        <div className="grid gap-3 py-6">
          <p className="text-body text-light-text-primary dark:text-dark-text-primary">
            Operator access shows this channel and who is in it — not what was said
            in it.
          </p>
          <p className="text-body-s text-light-text-secondary dark:text-dark-text-secondary">
            Reading messages needs the account owner to sign in. If that is genuinely
            required, reset their PIN with them on the call.
          </p>
          <button
            type="button"
            onClick={() => setMembersOpen(true)}
            disabled={openThread.is_direct}
            className="justify-self-start rounded-pill border border-light-border px-4 py-2
                       text-body-s text-light-text-secondary disabled:opacity-40
                       dark:border-dark-border dark:text-dark-text-secondary"
          >
            {openThread.is_direct ? 'A private line has no members' : 'See who is in this channel'}
          </button>
        </div>
      ) : (
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-w-0 overflow-y-auto"
        style={{ maxHeight: 'calc(100dvh - 300px)' }}
      >
        {messages.length === 0 && !streaming ? (
          <div className="grid gap-4 py-6">
            <p className="text-hero text-light-text-primary dark:text-dark-text-primary">
              Ask Jarvis anything about your leads, email, or schedule.
            </p>
            <ul className="grid gap-2">
              {EXAMPLE_PROMPTS.map((p) => (
                <li key={p}>
                  <button
                    type="button"
                    onClick={() => void send(p)}
                    className="w-full rounded-list border border-light-border bg-light-surface
                               px-4 py-3 text-left text-body-s shadow-card
                               dark:border-dark-border dark:bg-dark-surface"
                  >
                    {p}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <ol className="grid min-w-0 gap-5 py-2">
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <li key={i} className="flex flex-col items-end gap-2">
                  {m.attachments && m.attachments.length > 0 && (
                    <div className="flex max-w-[80%] flex-wrap justify-end gap-2">
                      {m.attachments.map((a) => (
                        <AttachmentChip key={a.id} attachment={a} />
                      ))}
                    </div>
                  )}
                  {m.content && (
                    <p
                      className="max-w-[80%] rounded-card bg-light-accent px-4 py-2.5
                                 text-chat text-white dark:bg-dark-accent-fill"
                    >
                      {m.content}
                    </p>
                  )}
                </li>
              ) : (
                <li key={i} className="min-w-0">
                  {/* INK, not muted grey. The chat interior is white text, one
                      ink bubble and nothing else — this label appears above
                      every assistant turn, so it is the cheapest place to get
                      the accent onto the screen repeatedly without adding
                      chrome. */}
                  <p className="flex items-center gap-2 font-mono text-label-mono uppercase
                                text-light-accent/75 dark:text-dark-accent/80">
                    Jarvis
                    {m.content && !streaming && (
                      <SpeakerToggle
                        text={m.content}
                        isLatest={i === messages.length - 1}
                        completedAt={i === messages.length - 1 ? replyDoneAt : undefined}
                        onCountEvent={bumpCounter}
                      />
                    )}
                  </p>
                  <AssistantText content={m.content} />
                  <SavedActions actions={m.actions} />
                  {m.partial && (
                    <p className="mt-1 text-caption text-light-text-muted dark:text-dark-text-muted">
                      This answer was cut short.
                    </p>
                  )}
                </li>
              ),
            )}

            {step && (
              // The tokens list the THINKING PANEL as a featured card (glass
              // tier 2), alongside approval cards and the pinned direct line.
              // It was rendering as a bare text row, so the one moment the app
              // is visibly working had less presence than a feed row. Glass +
              // the haloed pulsing mark is the "brand moment" treatment.
              <li className="glass-featured flex items-center gap-2.5 rounded-card px-3.5 py-2.5">
                <span aria-hidden="true" className="relative grid h-6 w-6 shrink-0 place-items-center">
                  <span
                    className="absolute inset-[-4px] rounded-full
                               bg-[radial-gradient(circle,rgba(143,180,232,0.5)_0%,rgba(143,180,232,0)_70%)]
                               dark:bg-[radial-gradient(circle,rgba(143,180,232,0.18)_0%,rgba(143,180,232,0)_70%)]"
                  />
                  <AsteriskMark
                    size={16}
                    strokeWidth={2.2}
                    className="relative animate-pulse text-light-accent dark:text-dark-accent"
                  />
                </span>
                <span className="min-w-0">
                  <span className="block font-mono text-[10.5px] font-medium uppercase tracking-[0.07em] text-light-text-secondary dark:text-dark-text-secondary">
                    {step.name}
                  </span>
                  {step.label && (
                    <span className="mt-0.5 block truncate font-mono text-[11px] text-light-text-muted dark:text-dark-text-muted">
                      {truncateLabel(step.label)}
                    </span>
                  )}
                </span>
              </li>
            )}
          </ol>
        )}

        {error && (
          <p
            role="alert"
            className="mt-3 flex items-start gap-2 rounded-list border border-light-alert/30
                       bg-light-surface p-3 text-body-s
                       dark:border-dark-alert/40 dark:bg-dark-surface"
          >
            <span
              aria-hidden="true"
              className="mt-1.5 block h-[7px] w-[7px] shrink-0 rounded-full bg-light-alert dark:bg-dark-alert"
            />
            {error}
          </p>
        )}

        {notice && (
          <p className="mt-3 flex items-start gap-2 text-caption text-light-text-muted dark:text-dark-text-muted">
            <span
              aria-hidden="true"
              className="mt-1.5 block h-[7px] w-[7px] shrink-0 rounded-full bg-light-neutral dark:bg-dark-neutral"
            />
            {notice}
          </p>
        )}

        {/* THE TURN'S RECEIPT, and it is not the model talking. A confirmation
            comes from the row that was saved; a correction appears when the
            reply above claimed a task that does not exist. Rendered apart from
            the transcript on purpose — merged into the bubble it would read as
            more prose from the same source that just got it wrong. */}
        {verified && (
          <p
            data-testid="verified"
            data-ok={verified.ok ? 'true' : 'false'}
            className={[
              'mt-3 flex items-start gap-2 whitespace-pre-line text-caption',
              verified.ok
                ? 'text-light-text-secondary dark:text-dark-text-secondary'
                : 'text-light-alert dark:text-dark-alert',
            ].join(' ')}
          >
            <span
              aria-hidden="true"
              className={[
                'mt-1.5 block h-[7px] w-[7px] shrink-0 rounded-full',
                verified.ok
                  ? 'bg-light-success dark:bg-dark-success'
                  : 'bg-light-alert dark:bg-dark-alert',
              ].join(' ')}
            />
            {verified.message}
          </p>
        )}

        {chatActions.map((a) => (
          <ChatActionButton key={a} action={a} />
        ))}
      </div>
      )}

      {/* No composer for an operator: break-glass looks, it does not speak.
          The API refuses the POST regardless (require_bodies on /api/chat). */}
      {!structureOnly && (
      <form
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          void send(draft)
        }}
        className="fixed inset-x-0 bottom-[calc(72px+env(safe-area-inset-bottom))] z-20
                   px-screen-x"
      >
        <VoiceStatus state={voice} onDismiss={() => setVoice({ phase: 'idle' })} />

        {(pending.length > 0 || uploading) && (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            {pending.map((a) => (
              <AttachmentChip
                key={a.id}
                attachment={a}
                onRemove={() => setPending((cur) => cur.filter((x) => x.id !== a.id))}
              />
            ))}
            {uploading && (
              <span className="font-mono text-label-mono uppercase text-light-text-muted dark:text-dark-text-muted">
                Uploading…
              </span>
            )}
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept={ACCEPT_ATTR}
          multiple
          className="hidden"
          onChange={(e) => void onPick(e.target.files)}
        />

        <div className="glass-featured flex items-center gap-2 rounded-pill p-1.5">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={streaming || uploading}
            aria-label="Add a photo or file"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full
                       text-light-text-secondary transition-opacity disabled:opacity-40
                       dark:text-dark-text-secondary"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="2"
                    strokeLinecap="round" />
            </svg>
          </button>

          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Ask Jarvis…"
            disabled={streaming}
            aria-label="Message Jarvis"
            className="min-w-0 flex-1 rounded-pill bg-transparent px-4 py-2 text-body
                       text-light-text-primary placeholder:text-light-text-muted
                       focus:outline-none
                       dark:text-dark-text-primary dark:placeholder:text-dark-text-muted"
          />

          {/* The reserved slot, now filled (v1.5 Phase C1). The transcript
              lands in the field EDITABLE — it never auto-sends. */}
          <MicButton
            disabled={streaming || uploading}
            onStateChange={setVoice}
            onTranscript={(text) =>
              setDraft((cur) => (cur.trim() ? `${cur.trim()} ${text}` : text))
            }
          />

          <button
            type="submit"
            disabled={streaming || uploading || (draft.trim().length === 0 && pending.length === 0)}
            aria-label="Send"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-full
                       bg-light-accent text-white shadow-accent-btn
                       transition-opacity disabled:opacity-40
                       dark:bg-dark-accent-fill"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 19V5M6 11l6-6 6 6"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </form>
      )}

      {sheetNode}
      {membersNode}
    </div>
  )
}
