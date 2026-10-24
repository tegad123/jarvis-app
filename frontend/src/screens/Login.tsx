import { useEffect, useState } from 'react'
import { ApiError, Unauthorized, login, getPeople, type Person } from '../lib/api'
import { SignIn } from '../jm'

/**
 * Sign in: Google first, a PIN for a named person, and the operator
 * passphrase as a quiet link — it is for support and recovery, and a login
 * screen that offers it first teaches everyone to share one secret.
 */
interface Props {
  onAuthenticated: () => void
}

type Mode = 'start' | 'pin' | 'operator'

export default function Login({ onAuthenticated }: Props) {
  // The roster is fetched unauthenticated so the picker can render before
  // anyone has proved anything; it carries names and titles only.
  const [people, setPeople] = useState<Person[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // A failed attempt remounts the form so the secret clears, keeping the mode and person.
  const [attempt, setAttempt] = useState(0)
  const [mode, setMode] = useState<Mode | undefined>(undefined)
  const [who, setWho] = useState<string | null | undefined>(undefined)

  useEffect(() => {
    void getPeople().then(setPeople)
  }, [])

  async function submit(input: { person: string | null; secret: string; operator: boolean }) {
    if (busy) return
    setError(null)
    setBusy(true)
    try {
      await login(input.secret, input.operator ? undefined : (input.person ?? undefined))
      onAuthenticated()
    } catch (err) {
      // A 401 here means a wrong passphrase, not an expired session.
      if (err instanceof Unauthorized) setError(input.operator ? "That passphrase didn't work." : 'That PIN is not right.')
      else if (err instanceof ApiError) setError(err.message)
      else setError('Something went wrong. Try again.')
      setMode(input.operator ? 'operator' : 'pin')
      if (!input.operator) setWho(input.person)
      setAttempt((n) => n + 1)
    } finally {
      setBusy(false)
    }
  }

  if (people === null) {
    return (
      <main className="grid min-h-[100dvh] place-items-center bg-jm-bg">
        <span className="sr-only">Loading</span>
      </main>
    )
  }

  return (
    <SignIn
      key={attempt}
      className="min-h-[100dvh]"
      people={people.map((p) => ({ id: p.person_key, name: p.name, title: p.title }))}
      onGoogle={() => {
        window.location.href = '/api/auth/google/start'
      }}
      onSubmit={(input) => void submit(input)}
      busy={busy}
      error={error}
      onModeChange={(m) => {
        setMode(m)
        setError(null)
      }}
      defaultMode={mode}
      defaultPerson={who}
    />
  )
}
