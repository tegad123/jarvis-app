import { useCallback, useEffect, useState } from 'react'
import AmbientField from './components/AmbientField'
import Chrome from './components/Chrome'
import TabBar, { type TabId } from './components/TabBar'
import Approvals from './screens/Approvals'
import Ask from './screens/Ask'
import Feed from './screens/Feed'
import Leads from './screens/Leads'
import Login from './screens/Login'
import Tasks from './screens/Tasks'
import { Unauthorized, getSession, logout } from './lib/api'
import { applyTheme, initialTheme, type Theme } from './lib/theme'
import type { Role } from './lib/types'

type AuthState =
  | { status: 'checking' }
  | { status: 'out' }
  | { status: 'in'; displayName?: string; role: Role; personKey: string | null }

export default function App() {
  const [auth, setAuth] = useState<AuthState>({ status: 'checking' })
  const [tab, setTab] = useState<TabId>('feed')
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
      })
    } catch (err) {
      if (err instanceof Unauthorized) {
        setAuth({ status: 'out' })
      } else {
        // Server unreachable at boot. Show Login rather than a dead screen —
        // the login attempt will surface the real error.
        setAuth({ status: 'out' })
      }
    }
  }, [])

  useEffect(() => {
    void check()
  }, [check])

  const signOut = useCallback(async () => {
    await logout()
    setAuth({ status: 'out' })
    setTab('feed')
  }, [])

  if (auth.status === 'checking') {
    return (
      <main className="grid min-h-[100dvh] place-items-center">
        <span className="sr-only">Loading</span>
      </main>
    )
  }

  if (auth.status === 'out') {
    return <Login onAuthenticated={() => void check()} />
  }

  return (
    <div className="relative min-h-[100dvh]">
      <AmbientField />

      <Chrome
        displayName={auth.displayName}
        theme={theme}
        onToggleTheme={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
        onSignOut={() => void signOut()}
      />

      {/* Padding clears the fixed chrome above and the tab bar below.
          DESKTOP: the content column is capped at 700px and centred. The design
          was drawn at 390px, and letting it fill a 1280px viewport turns the
          hero into a void and stretches every card until one line of text sits
          alone on 1200px. A reading column keeps the composition the design
          actually has; the extra width becomes quiet ground around it, which is
          what makes the elevation read at all.
          md:pl-[88px] clears the left rail the tab bar becomes at that width. */}
      {/* lg (1024px+): the rail is 220px wide and the reading column becomes a
          1080px container centred in the space to its right, with 48px of
          horizontal padding. The header shares the same container so its left
          edge is the content's left edge. Nothing below lg changes. */}
      <main
        className="relative z-10 mx-auto w-full max-w-[700px]
                   px-screen-x pb-[calc(96px+env(safe-area-inset-bottom))] pt-[92px]
                   md:max-w-[788px] md:pb-16 md:pl-[88px]
                   lg:max-w-none lg:px-0 lg:pb-20 lg:pl-[220px] lg:pt-[104px]"
      >
       <div className="lg:mx-auto lg:w-full lg:max-w-[1080px] lg:px-12">
        {tab === 'feed' && <Feed role={auth.role} onUnauthorized={() => setAuth({ status: 'out' })} />}

        {tab === 'ask' && (
          <Ask
            role={auth.role}
            personKey={auth.personKey}
            onUnauthorized={() => setAuth({ status: 'out' })}
          />
        )}

        {/* Owner-only server-side (auth.require_owner). Hiding the screen is
            a courtesy on top of that enforcement, never instead of it. */}
        {tab === 'approvals' && auth.role === 'owner' && (
          <Approvals onUnauthorized={() => setAuth({ status: 'out' })} />
        )}
        {tab === 'approvals' && auth.role !== 'owner' && (
          <p className="py-10 text-center text-body-s text-light-text-muted dark:text-dark-text-muted">
            Approvals are the account owner’s to make.
          </p>
        )}

        {tab === 'tasks' && (
          <Tasks
            role={auth.role}
            personKey={auth.personKey}
            onUnauthorized={() => setAuth({ status: 'out' })}
          />
        )}

        {tab === 'leads' && (
          <Leads personKey={auth.personKey} onUnauthorized={() => setAuth({ status: 'out' })} />
        )}
       </div>
      </main>

      <TabBar active={tab} onChange={setTab} />
    </div>
  )
}
