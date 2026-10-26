import { useCallback, useEffect, useRef, useState } from 'react'
import { BackContext } from './components/Page'
import Approvals from './screens/Approvals'
import Ask from './screens/Ask'
import Feed from './screens/Feed'
import Leads from './screens/Leads'
import Login from './screens/Login'
import Settings from './screens/Settings'
import Tasks from './screens/Tasks'
import Page from './components/Page'
import CategorySheet, { type CategorySheetMode } from './components/CategorySheet'
import {
  ApiError,
  Unauthorized,
  archiveThread,
  createCategory,
  createThread,
  deleteCategory,
  getApprovals,
  getPeople,
  getSession,
  getThreads,
  logout,
  renameThread,
  setThreadCategory,
  updateCategory,
  type Person,
} from './lib/api'
import { EmptyState, NewChannelSheet, Sidebar, type SidebarNavItem, type SidebarSection } from './jm'
import { canManageTeam, canSeeSettings, ROLE_LABEL } from './lib/roles'
import { applyTheme, initialTheme, type Theme } from './lib/theme'
import { relativeTime } from './lib/time'
import type { Category, Feature, Role, Thread } from './lib/types'

type AuthState =
  | { status: 'checking' }
  | { status: 'out' }
  | {
      status: 'in'
      displayName?: string
      role: Role
      personKey: string | null
      features: Partial<Record<Feature, boolean>>
    }

type NavId = 'feed' | 'approvals' | 'tasks' | 'leads' | 'settings'
type View = { kind: 'nav'; id: NavId } | { kind: 'thread'; id: string } | null

type Sheet = { mode: 'create' } | { mode: 'rename'; thread: Thread } | null

const NAV_IDS: NavId[] = ['feed', 'approvals', 'tasks', 'leads', 'settings']

/** A channel with no message for this long reads "Quiet" in the sidebar. */
const QUIET_AFTER_HOURS = 48

function isQuiet(t: Thread): boolean {
  if (!t.last_message_at) return false
  return Date.now() - new Date(t.last_message_at).getTime() > QUIET_AFTER_HOURS * 3600_000
}

function useDesktop(): boolean {
  const query = '(min-width: 768px)'
  const [desktop, setDesktop] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setDesktop(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [])
  return desktop
}

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'checking' })
  const [theme, setTheme] = useState<Theme>(() => initialTheme())

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // The session cookie is httponly, so the only way to know whether we are
  // signed in is to ask the server and see whether it 401s.
  const check = useCallback(async () => {
    try {
      const s = await getSession()
      setAuth({
        status: 'in',
        displayName: s.display_name,
        // Break-glass sessions arrive as 'operator' with no person. Defaulting
        // to 'member' rather than something privileged means a server that
        // stops sending the field narrows the UI instead of widening it.
        role: s.role ?? 'member',
        personKey: s.person_key ?? null,
        features: s.features ?? {},
      })
    } catch {
      // Unauthorized, or the server is unreachable at boot: show Login rather
      // than a dead screen — the login attempt will surface the real error.
      setAuth({ status: 'out' })
    }
  }, [])

  useEffect(() => {
    void check()
  }, [check])

  if (auth.status === 'checking') {
    return (
      <main className="grid min-h-[100dvh] place-items-center bg-jm-bg">
        <span className="sr-only">Loading</span>
      </main>
    )
  }

  if (auth.status === 'out') {
    return <Login onAuthenticated={() => void check()} />
  }

  return (
    <Shell
      key={auth.personKey ?? 'operator'}
      auth={auth}
      theme={theme}
      onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
      onSignedOut={() => setAuth({ status: 'out' })}
    />
  )
}

function Shell({
  auth,
  theme,
  onToggleTheme,
  onSignedOut,
}: {
  auth: Extract<AuthState, { status: 'in' }>
  theme: Theme
  onToggleTheme: () => void
  onSignedOut: () => void
}) {
  const { role, personKey, features } = auth
  const structureOnly = role === 'operator'
  const manager = canManageTeam(role)
  const on = (f: Feature) => features[f] !== false
  const desktop = useDesktop()
  const [view, setView] = useState<View>(null)
  const [threads, setThreads] = useState<Thread[] | null>(null)
  const [categories, setCategories] = useState<Category[]>([])
  const [catSheet, setCatSheet] = useState<(CategorySheetMode & { threadId?: string }) | null>(null)
  const [threadsError, setThreadsError] = useState<string | null>(null)
  const [people, setPeople] = useState<Person[]>([])
  const [pending, setPending] = useState(0)
  const [sheet, setSheet] = useState<Sheet>(null)
  const [sheetBusy, setSheetBusy] = useState(false)
  const [sheetError, setSheetError] = useState<string | null>(null)
  const onSignedOutRef = useRef(onSignedOut)
  onSignedOutRef.current = onSignedOut
  const unauthorized = useCallback(() => onSignedOutRef.current(), [])

  // The roster is needed by ANYONE who can open the members sheet, not just
  // by someone who can edit it: without it the sheet renders raw person keys
  // instead of names. It is the same unauthenticated list the login picker uses.
  useEffect(() => {
    void getPeople().then(setPeople).catch(() => setPeople([]))
  }, [])

  const loadThreads = useCallback(async () => {
    try {
      const r = await getThreads()
      setThreads(r.threads)
      setCategories(r.categories ?? [])
      setThreadsError(null)
      return r.threads
    } catch (err) {
      if (err instanceof Unauthorized) unauthorized()
      else setThreadsError('Could not load your conversations.')
      return null
    }
  }, [unauthorized])

  useEffect(() => {
    void loadThreads()
  }, [loadThreads])

  // The sidebar's Approvals count. The server decides what each person sees
  // (approvals.may_see); the operator sees none, so it does not ask.
  const loadPending = useCallback(async () => {
    if (structureOnly) return
    try {
      const r = await getApprovals()
      setPending(r.approvals.filter((a) => a.status === 'pending').length)
    } catch (err) {
      if (err instanceof Unauthorized) unauthorized()
    }
  }, [structureOnly, unauthorized])

  useEffect(() => {
    void loadPending()
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') void loadPending()
    }, 20_000)
    return () => window.clearInterval(t)
  }, [loadPending, view])

  const signOut = useCallback(async () => {
    await logout()
    onSignedOutRef.current()
  }, [])

  const direct = threads?.find((t) => t.is_direct) ?? null
  const channels = (threads ?? []).filter((t) => !t.is_direct)

  // Phones show the sidebar until something is opened; desktops open the direct line.
  const shown: View = view ?? (desktop && direct ? { kind: 'thread', id: direct.thread_id } : null)
  const openThread = shown?.kind === 'thread' ? (threads ?? []).find((t) => t.thread_id === shown.id) ?? null : null

  const select = (id: string) => {
    if ((NAV_IDS as string[]).includes(id)) {
      if (id === 'settings' && !canSeeSettings(role)) return
      setView({ kind: 'nav', id: id as NavId })
    } else {
      setView({ kind: 'thread', id })
    }
  }

  const archive = useCallback(
    async (thread: Thread) => {
      try {
        await archiveThread(thread.thread_id, true)
        if (view?.kind === 'thread' && view.id === thread.thread_id) setView(null)
        await loadThreads()
      } catch (err) {
        if (err instanceof Unauthorized) return unauthorized()
        setThreadsError(err instanceof ApiError ? err.message : 'Could not archive that channel.')
      }
    },
    [loadThreads, unauthorized, view],
  )

  const submitCategory = useCallback(
    async (value: string, remove = false) => {
      if (!catSheet) return
      setSheetBusy(true)
      setSheetError(null)
      try {
        if (catSheet.kind === 'new') await createCategory(value)
        else if (catSheet.kind === 'edit') {
          if (remove) await deleteCategory(catSheet.id)
          else await updateCategory(catSheet.id, { name: value })
        } else if (catSheet.threadId) await setThreadCategory(catSheet.threadId, value || null)
        setCatSheet(null)
        await loadThreads()
      } catch (err) {
        if (err instanceof Unauthorized) return unauthorized()
        setSheetError(err instanceof ApiError ? err.message : 'That did not work.')
      } finally {
        setSheetBusy(false)
      }
    },
    [catSheet, loadThreads, unauthorized],
  )

  const submitSheet = useCallback(
    async (name: string, category = '') => {
      if (!sheet) return
      setSheetBusy(true)
      setSheetError(null)
      try {
        if (sheet.mode === 'create') {
          const created = await createThread(name, category || null)
          setSheet(null)
          await loadThreads()
          // Drop straight into the new channel — creating one is an intent to use it.
          setView({ kind: 'thread', id: created.thread_id })
        } else {
          await renameThread(sheet.thread.thread_id, name)
          setSheet(null)
          await loadThreads()
        }
      } catch (err) {
        if (err instanceof Unauthorized) return unauthorized()
        setSheetError(err instanceof ApiError ? err.message : 'That did not work.')
      } finally {
        setSheetBusy(false)
      }
    },
    [sheet, loadThreads, unauthorized],
  )

  const row = (t: Thread) => ({
    id: t.thread_id,
    name: t.name,
    preview: t.preview,
    previewRole: t.preview_role,
    time: t.last_message_at ? relativeTime(t.last_message_at) : undefined,
    unread: t.unread,
    quiet: !t.is_direct && isQuiet(t),
    byJarvis: t.created_by === 'jarvis',
    bodiesHidden: structureOnly,
  })

  const nav: SidebarNavItem[] = [
    { id: 'feed', label: 'Activity', icon: 'activity' },
    { id: 'approvals', label: 'Approvals', icon: 'approvals', count: pending, attention: pending > 0 },
    ...(on('tasks') ? [{ id: 'tasks', label: 'Tasks', icon: 'tasks' as const }] : []),
    ...(on('leads') ? [{ id: 'leads', label: 'Leads', icon: 'leads' as const }] : []),
    ...(canSeeSettings(role) ? [{ id: 'settings', label: 'Settings', icon: 'settings' as const }] : []),
  ]

  const channelRow = (t: Thread) => ({
    ...row(t),
    menu: [
      {
        label: 'Rename',
        onSelect: () => {
          setSheetError(null)
          setSheet({ mode: 'rename', thread: t })
        },
      },
      ...(manager && categories.length
        ? [
            {
              label: 'Move to category',
              onSelect: () => {
                setSheetError(null)
                setCatSheet({ kind: 'move', threadName: t.name, current: t.category_id ?? '', threadId: t.thread_id })
              },
            },
          ]
        : []),
      { label: 'Archive', onSelect: () => void archive(t) },
    ],
  })
  const known = new Set(categories.map((c) => c.category_id))
  // Members see a section only when it holds a channel they were added to.
  const sections: SidebarSection[] = categories
    .map((c) => ({
      id: c.category_id,
      label: c.name,
      channels: channels.filter((t) => t.category_id === c.category_id).map(channelRow),
      onEdit: manager
        ? () => {
            setSheetError(null)
            setCatSheet({ kind: 'edit', id: c.category_id, name: c.name })
          }
        : undefined,
    }))
    .filter((s) => manager || s.channels.length > 0)
  const ungrouped = channels.filter((t) => !t.category_id || !known.has(t.category_id))
  const categoryOptions = categories.map((c) => ({ value: c.category_id, label: c.name }))

  let content
  if (shown?.kind === 'thread' && openThread) {
    content = (
      <Ask
        key={openThread.thread_id}
        thread={openThread}
        role={role}
        personKey={personKey}
        people={people}
        onThreadsChanged={() => void loadThreads()}
        onUnauthorized={unauthorized}
      />
    )
  } else if (shown?.kind === 'nav') {
    switch (shown.id) {
      case 'feed':
        content = <Feed role={role} onUnauthorized={unauthorized} />
        break
      case 'approvals':
        content =
          role === 'operator' ? (
            <Page title="Approvals">
              <EmptyState icon="approvals">Approvals are the account’s people to make.</EmptyState>
            </Page>
          ) : (
            <Approvals onUnauthorized={unauthorized} onDecided={() => void loadPending()} />
          )
        break
      case 'tasks':
        content = <Tasks role={role} personKey={personKey} people={people} onUnauthorized={unauthorized} />
        break
      case 'leads':
        content = <Leads personKey={personKey} onUnauthorized={unauthorized} />
        break
      case 'settings':
        content = canSeeSettings(role) ? (
          <Settings role={role} personKey={personKey} features={features} onUnauthorized={unauthorized} />
        ) : null
        break
    }
  } else if (threads === null && !threadsError) {
    content = <span className="sr-only">Loading</span>
  } else {
    content = (
      <Page title="Jarvis">
        <EmptyState icon="chat">{threadsError ?? 'Pick a conversation.'}</EmptyState>
      </Page>
    )
  }

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-jm-bg text-jm-fg">
      <Sidebar
        className={shown ? 'hidden md:flex' : '!w-full md:!w-jm-sidebar'}
        nav={nav}
        active={shown ? shown.id : undefined}
        onSelect={select}
        onNewChat={
          manager
            ? () => {
                setSheetError(null)
                setSheet({ mode: 'create' })
              }
            : undefined
        }
        direct={direct ? { ...row(direct), direct: true } : undefined}
        sections={sections}
        channels={ungrouped.map(channelRow)}
        onNewSection={
          manager
            ? () => {
                setSheetError(null)
                setCatSheet({ kind: 'new' })
              }
            : undefined
        }
        emptyLabel={threads === null ? 'Loading…' : threadsError ?? undefined}
        person={{ name: auth.displayName || (structureOnly ? 'Operator' : 'Signed in'), role: ROLE_LABEL[role] }}
        theme={theme}
        onToggleTheme={onToggleTheme}
        onSignOut={() => void signOut()}
      />

      <main className={shown ? 'flex min-w-0 flex-1 flex-col' : 'hidden min-w-0 flex-1 flex-col md:flex'}>
        <BackContext.Provider value={desktop ? null : () => setView(null)}>{content}</BackContext.Provider>
      </main>

      {sheet && (
        <NewChannelSheet
          mode={sheet.mode}
          title={sheet.mode === 'create' ? 'New channel' : 'Rename channel'}
          submitLabel={sheet.mode === 'create' ? 'Create' : 'Save'}
          initialValue={sheet.mode === 'rename' ? sheet.thread.name : ''}
          categories={categoryOptions}
          busy={sheetBusy}
          error={sheetError ?? undefined}
          onSubmit={(name, category) => void submitSheet(name, category)}
          onClose={() => {
            setSheet(null)
            setSheetError(null)
          }}
        />
      )}

      {catSheet && (
        <CategorySheet
          key={catSheet.kind + ('id' in catSheet ? catSheet.id : '') + (catSheet.threadId ?? '')}
          mode={catSheet}
          categories={categoryOptions}
          busy={sheetBusy}
          error={sheetError}
          onSave={(v) => void submitCategory(v)}
          onDelete={() => void submitCategory('', true)}
          onClose={() => {
            setCatSheet(null)
            setSheetError(null)
          }}
        />
      )}
    </div>
  )
}
