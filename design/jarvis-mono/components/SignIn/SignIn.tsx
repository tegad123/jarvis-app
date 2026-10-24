import { useId, useState, type FormEvent } from 'react'
import { Button } from '../Button/Button'
import { Icon } from '../shared/Icon'
import { Avatar, ErrorLine, Field, cx, focus, press, truncate } from '../shared/primitives'

export interface Person {
  id: string
  name: string
  /** Shown under the name; a bracketed note is cut from the row and kept as the tooltip. */
  title?: string
}

export interface SignInProps {
  /** The roster from /api/people. Names and titles only. */
  people?: Person[]
  onGoogle?: () => void
  /** person is null on operator access. */
  onSubmit?: (input: { person: string | null; secret: string; operator: boolean }) => void
  busy?: boolean
  /** The sentence to show, e.g. "That PIN is not right." */
  error?: string | null
  onModeChange?: (mode: 'start' | 'pin' | 'operator') => void
  defaultMode?: 'start' | 'pin' | 'operator'
  defaultPerson?: string | null
  defaultSecret?: string
  brand?: string
  className?: string
}

/** Minted PINs are three groups of four: 12 characters and 2 dashes. */
const PIN_GROUPS = 3
const PIN_GROUP_LEN = 4
const PIN_LEN = PIN_GROUPS * PIN_GROUP_LEN + (PIN_GROUPS - 1)

/** "abcd1234wxyz", "ABCD 1234" or "abcd-1234-wxyz" -> "abcd-1234-wxyz". */
export function formatPin(raw: string): string {
  const bare = raw.replace(/[^a-z0-9]/gi, '').toLowerCase().slice(0, PIN_GROUPS * PIN_GROUP_LEN)
  return (bare.match(new RegExp(`.{1,${PIN_GROUP_LEN}}`, 'g')) ?? []).join('-')
}

const link = cx('cursor-pointer appearance-none justify-self-center rounded-jm-sm border-0 bg-transparent px-jm-1 py-0.5 font-jm underline-offset-[3px] hover:underline', focus)

export function SignIn(p: SignInProps) {
  const people = p.people ?? []
  const id = useId()
  const [who, setWho] = useState<string | null>(p.defaultPerson !== undefined ? p.defaultPerson : people.length === 1 ? people[0].id : null)
  const [mode, setMode] = useState(p.defaultMode ?? (people.length === 0 ? 'operator' : 'start'))
  const [secret, setSecret] = useState(p.defaultSecret ?? '')
  const [reveal, setReveal] = useState(false)
  const operator = mode === 'operator'
  const complete = operator ? secret.length > 0 : secret.length === PIN_LEN

  const go = (next: 'start' | 'pin' | 'operator') => {
    setMode(next)
    setSecret('')
    p.onModeChange?.(next)
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (p.busy || !complete || (!operator && !who)) return
    p.onSubmit?.({ person: operator ? null : who, secret, operator })
  }

  const subtitle = operator
    ? 'Operator access — for support and recovery.'
    : mode === 'pin'
      ? who
        ? 'Enter your PIN to continue.'
        : 'Choose who you are.'
      : 'Continue with Google, or sign in with a PIN.'

  return (
    <main className={cx('box-border grid min-h-full place-items-center bg-jm-bg px-jm-4 py-jm-12 text-jm-fg', p.className)}>
      <div className="grid w-full max-w-jm-signin gap-jm-3">
        <header className="mb-jm-5 text-center">
          <h1 className="m-0 text-jm-display text-jm-fg">{p.brand ?? 'Jarvis'}</h1>
          <p className="m-0 mt-jm-2 text-jm-body-sm text-jm-fg-secondary">{subtitle}</p>
        </header>

        {!operator && (
          // One primary at a time: once the PIN form is open, its Continue is.
          <Button variant={mode === 'pin' ? 'secondary' : 'primary'} className="min-h-jm-touch w-full" onClick={p.onGoogle}>
            Continue with Google
          </Button>
        )}
        {!operator && (
          <button type="button" aria-expanded={mode === 'pin'} onClick={() => go(mode === 'pin' ? 'start' : 'pin')} className={cx(link, 'text-jm-label text-jm-accent-text')}>
            {mode === 'pin' ? 'Hide PIN sign-in' : 'Sign in with PIN'}
          </button>
        )}

        {mode === 'pin' && people.length > 0 && (
          <ul aria-label="Who is signing in" className="m-0 mt-jm-2 grid list-none gap-jm-2 p-0">
            {people.map((person) => {
              const on = person.id === who
              const role = person.title?.split(' (')[0]
              return (
                <li key={person.id}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => setWho(person.id)}
                    className={cx(
                      'box-border flex min-h-jm-touch w-full cursor-pointer appearance-none items-center gap-jm-3 rounded-jm-md border px-jm-3 py-jm-2 text-left font-jm text-jm-fg',
                      on ? 'border-jm-accent bg-jm-accent-soft' : 'border-jm-border bg-transparent hover:bg-jm-panel',
                      press,
                      focus,
                    )}
                  >
                    <Avatar name={person.name} />
                    <span className="grid min-w-0 flex-1">
                      <span className={cx('text-jm-label text-jm-fg', truncate)}>{person.name}</span>
                      {role && (
                        <span title={person.title} className={cx('text-jm-caption text-jm-fg-secondary', truncate)}>
                          {role}
                        </span>
                      )}
                    </span>
                    {on && (
                      <span className="text-jm-accent-text">
                        <Icon name="check" size={18} />
                      </span>
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}

        {(mode === 'pin' || operator) && (
          <form onSubmit={submit} className="mt-jm-2 grid gap-jm-3">
            <label htmlFor={id} className="sr-only">
              {operator ? 'Operator passphrase' : 'PIN'}
            </label>
            <div className="relative">
              <Field
                id={id}
                large
                type={reveal ? 'text' : 'password'}
                autoComplete="current-password"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                value={secret}
                placeholder={operator ? 'Operator passphrase' : 'PIN'}
                aria-invalid={p.error ? true : undefined}
                aria-describedby={p.error ? `${id}-error` : undefined}
                onChange={(e) => setSecret(operator ? e.target.value : formatPin(e.target.value))}
                className={cx(!operator && 'px-jm-12 text-center tabular-nums tracking-[0.12em] placeholder:tracking-normal')}
              />
              <Button
                variant="ghost"
                size="sm"
                icon={reveal ? 'eye-off' : 'eye'}
                tabIndex={-1}
                aria-label={`${reveal ? 'Hide' : 'Show'} ${operator ? 'passphrase' : 'PIN'}`}
                aria-pressed={reveal}
                onClick={() => setReveal(!reveal)}
                className="absolute right-jm-2 top-1/2 -translate-y-1/2"
              />
            </div>
            <Button type="submit" variant="primary" className="min-h-jm-touch w-full" disabled={p.busy || !complete || (!operator && !who)}>
              {p.busy ? 'Checking…' : 'Continue'}
            </Button>
            {p.error && <ErrorLine id={`${id}-error`}>{p.error}</ErrorLine>}
          </form>
        )}

        <button type="button" onClick={() => go(operator ? 'start' : 'operator')} className={cx(link, 'mt-jm-4 text-jm-caption text-jm-fg-secondary')}>
          {operator ? 'Back to sign in' : 'Operator access'}
        </button>
      </div>
    </main>
  )
}
