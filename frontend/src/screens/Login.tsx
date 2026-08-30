import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import AsteriskMark from '../components/AsteriskMark'
import AmbientField from '../components/AmbientField'
import { ApiError, Unauthorized, login, getPeople, type Person } from '../lib/api'

/**
 * The brand moment: glass tier 3 panel floating on the radial ink halo,
 * centred asterisk mark, one pill field, one accent button. Nothing else.
 *
 * DESKTOP (lg, 1024px+): the same card at 640px with 48px inset, the picker
 * as a 2×2 grid of tiles, and a faint asterisk mark in the corner so the
 * ground is not flat beige. Below lg nothing here changes shape.
 */
interface Props {
  onAuthenticated: () => void
}

/** Minted PINs are three 4-char groups (accounts.make_pin). 12 chars + 2 dashes. */
const PIN_GROUPS = 3
const PIN_GROUP_LEN = 4
const PIN_LEN = PIN_GROUPS * PIN_GROUP_LEN + (PIN_GROUPS - 1)

/** "abcd1234wxyz" or "abcd-1234-wxyz" or "ABCD 1234" → "abcd-1234-wxyz". */
export function formatPin(raw: string): string {
  const bare = raw.replace(/[^a-z0-9]/gi, '').toLowerCase().slice(0, PIN_GROUPS * PIN_GROUP_LEN)
  const groups = bare.match(new RegExp(`.{1,${PIN_GROUP_LEN}}`, 'g')) ?? []
  return groups.join('-')
}

/**
 * The picker shows a short role, and keeps the whole title as a tooltip. A
 * registry title may carry a parenthetical note meant for the operator
 * ("qa contractor (external tester account — …)"); on a tile that note is
 * noise, so the tile shows the part before the bracket and the tooltip shows
 * everything.
 */
function roleOf(title?: string): { short: string; full?: string } | null {
  if (!title) return null
  const short = title.split(' (')[0].trim()
  return { short: short || title, full: short !== title.trim() ? title : undefined }
}

export default function Login({ onAuthenticated }: Props) {
  const [passphrase, setPassphrase] = useState('')
  const [reveal, setReveal] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const pinRef = useRef<HTMLInputElement>(null)

  // WHO is signing in. The roster is fetched unauthenticated so the picker can
  // render before anyone has proved anything; it carries names and titles only.
  const [people, setPeople] = useState<Person[] | null>(null)
  const [who, setWho] = useState<string | null>(null)
  // Break-glass: the operator passphrase, deliberately behind a link rather
  // than presented as a peer option. It is for support and recovery, and a
  // login screen that offers it first teaches everyone to share one secret —
  // which is the thing this release exists to end.
  const [breakGlass, setBreakGlass] = useState(false)

  useEffect(() => {
    void getPeople().then((p) => {
      setPeople(p)
      if (p.length === 1) setWho(p[0].person_key)
      if (p.length === 0) setBreakGlass(true)   // nobody has a PIN yet
    })
  }, [])

  // Choosing a profile is the moment the PIN becomes the next thing to do, so
  // on desktop the field takes focus without a second click (or a second
  // Tab). Desktop only: on a phone, focusing the field raises the keyboard
  // over the picker, and the mobile screen is meant to stay exactly as it is.
  useEffect(() => {
    if (who && !breakGlass && window.matchMedia('(min-width: 1024px)').matches) {
      pinRef.current?.focus()
    }
  }, [who, breakGlass])

  // A PIN is complete at exactly xxxx-xxxx-xxxx; the operator passphrase is
  // free-form and is complete when it is non-empty.
  const complete = breakGlass ? passphrase.length > 0 : passphrase.length === PIN_LEN

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (busy || !complete) return
    setError(null)
    setBusy(true)
    try {
      await login(passphrase, breakGlass ? undefined : who ?? undefined)
      onAuthenticated()
    } catch (err) {
      // A 401 here means a wrong passphrase, not an expired session.
      if (err instanceof Unauthorized) {
        setError(breakGlass ? "That passphrase didn't work." : "That PIN is not right.")
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Something went wrong. Try again.')
      }
      setPassphrase('')
    } finally {
      setBusy(false)
    }
  }

  // Arrow keys walk the tiles (Tab still reaches them; Enter/Space selects,
  // which is the button's own behaviour). On the 2-column desktop grid Up and
  // Down move by a row; on the single-column list every arrow is next/prev.
  function onGridKey(e: KeyboardEvent<HTMLUListElement>) {
    const keys = ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp']
    if (!keys.includes(e.key)) return
    const tiles = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>('button'))
    const i = tiles.indexOf(document.activeElement as HTMLButtonElement)
    if (i < 0) return
    const cols = window.matchMedia('(min-width: 1024px)').matches ? 2 : 1
    const step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowDown' ? cols : -cols
    const next = tiles[i + step]
    if (next) {
      e.preventDefault()
      next.focus()
    }
  }

  return (
    <main className="relative min-h-[100dvh] overflow-hidden">
      <AmbientField />

      {/* DESKTOP GROUND. A faint, very large mark in one corner — the same
          asterisk as the card, at a whisper — so a 1440px viewport around a
          640px card is quiet paper rather than empty beige. Hidden below lg. */}
      <AsteriskMark
        size={620}
        strokeWidth={0.28}
        className="pointer-events-none absolute -bottom-[160px] -right-[140px] hidden
                   text-light-accent opacity-[0.07] lg:block
                   dark:text-dark-accent dark:opacity-[0.07]"
      />

      <div className="relative z-10 grid min-h-[100dvh] place-items-center px-screen-x">
        <div className="relative w-full max-w-[340px] lg:max-w-[640px]">
          {/* The halo the moment sits on. */}
          <div
            aria-hidden="true"
            className="ink-halo pointer-events-none absolute left-1/2 top-1/2
                       h-[560px] w-[560px] -translate-x-1/2 -translate-y-1/2 rounded-full
                       lg:h-[760px] lg:w-[760px]"
          />

          <div className="glass-moment relative rounded-screen px-6 py-8 lg:p-12">
            <div className="grid place-items-center gap-4">
              <AsteriskMark
                size={44}
                strokeWidth={2.2}
                className="text-light-accent dark:text-dark-accent"
              />
              <div className="text-center">
                <h1 className="text-screen-title">Jarvis</h1>
                <p className="mt-1 text-body-s text-light-text-secondary dark:text-dark-text-secondary">
                  {breakGlass
                    ? 'Operator access — for support and recovery.'
                    : who
                      ? 'Enter your PIN to continue.'
                      : 'Choose who you are.'}
                </p>
              </div>
            </div>

            {/* THE PICKER. A small team on a private deployment, so a list of
                names is friendlier and less error-prone than typing a key. It
                shows names, never addresses.

                CONTAINMENT: the column is minmax(0,1fr) and every layer down to
                the text is min-w-0. A grid column's default min-width is
                `auto`, which is the widest unbreakable content inside it — so
                one long title made the column, and every row in it, wider
                than the card. Rows are 100% of the card's content box, and the
                text truncates instead of pushing. */}
            {!breakGlass && people && people.length > 0 && (
              <ul
                className="mt-5 grid grid-cols-[minmax(0,1fr)] gap-2
                           lg:mt-8 lg:grid-cols-[repeat(2,minmax(0,1fr))] lg:gap-3"
                onKeyDown={onGridKey}
              >
                {people.map((person) => {
                  const on = person.person_key === who
                  const role = roleOf(person.title)
                  return (
                    <li key={person.person_key} className="min-w-0">
                      <button
                        type="button"
                        onClick={() => { setWho(person.person_key); setError(null) }}
                        aria-pressed={on}
                        className={[
                          'box-border flex w-full min-w-0 items-center gap-3 rounded-list border px-4 py-3 text-left',
                          'lg:gap-3.5 lg:rounded-card lg:px-4 lg:py-3.5',
                          'transition-[background-color,border-color,box-shadow,transform] duration-[120ms]',
                          on
                            ? 'border-light-accent bg-light-accent/[0.06] dark:border-dark-accent dark:bg-white/[0.06]'
                            : 'border-light-border bg-light-surface dark:border-dark-border dark:bg-dark-surface ' +
                              'lg:hover:-translate-y-px lg:hover:shadow-lift dark:lg:hover:shadow-lift-dark',
                        ].join(' ')}
                      >
                        <span
                          aria-hidden="true"
                          className="grid h-8 w-8 shrink-0 place-items-center rounded-full
                                     bg-light-accent text-[12px] font-semibold text-white
                                     dark:bg-dark-accent-fill lg:h-10 lg:w-10 lg:text-[13px]"
                        >
                          {person.name.slice(0, 1).toUpperCase()}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-row-title">{person.name}</span>
                          {role && (
                            <span
                              title={role.full}
                              className="block truncate text-caption text-light-text-muted dark:text-dark-text-muted"
                            >
                              {/* Mobile keeps the full title (it truncates);
                                  the desktop tile shows the short role. */}
                              <span className="lg:hidden">{person.title}</span>
                              <span className="hidden lg:inline">{role.short}</span>
                            </span>
                          )}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}

            <form onSubmit={submit} className="mt-4 grid gap-3 lg:mt-6">
              <label htmlFor="passphrase" className="sr-only">
                {breakGlass ? 'Operator passphrase' : 'PIN'}
              </label>
              <div className="relative">
                <input
                  ref={pinRef}
                  id="passphrase"
                  name="passphrase"
                  type={reveal ? 'text' : 'password'}
                  autoComplete="current-password"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  autoFocus={breakGlass || !!who}
                  value={passphrase}
                  // The PIN is shaped as the user types: bare characters get
                  // their dashes, pasted dashes are kept, anything else is
                  // dropped. The passphrase is free-form and untouched.
                  onChange={(e) => setPassphrase(breakGlass ? e.target.value : formatPin(e.target.value))}
                  placeholder={breakGlass ? 'Operator passphrase' : 'PIN'}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? 'login-error' : undefined}
                  className={[
                    'w-full rounded-pill border border-light-border-strong',
                    'bg-light-surface px-5 py-3 text-body',
                    'text-light-text-primary placeholder:text-light-text-muted',
                    'dark:border-dark-border-strong dark:bg-dark-surface',
                    'dark:text-dark-text-primary dark:placeholder:text-dark-text-muted',
                    // Desktop: mono, centred, room on the right for the reveal
                    // toggle. Placeholder stays in the UI face and centred too.
                    'lg:px-12 lg:text-center lg:placeholder:font-sans lg:placeholder:tracking-normal',
                    breakGlass ? '' : 'lg:font-mono lg:tracking-[0.12em]',
                  ].join(' ')}
                />
                {/* Reveal toggle — desktop only, so the mobile field is
                    unchanged. Stroke-only eye on the 24px grid. */}
                <button
                  type="button"
                  onClick={() => setReveal((v) => !v)}
                  aria-label={reveal ? 'Hide PIN' : 'Show PIN'}
                  aria-pressed={reveal}
                  tabIndex={-1}
                  className="absolute right-2 top-1/2 hidden h-8 w-8 -translate-y-1/2
                             place-items-center rounded-full text-light-text-muted
                             transition-colors duration-[120ms] hover:bg-light-accent/[0.06]
                             hover:text-light-text-primary lg:grid
                             dark:text-dark-text-muted dark:hover:bg-white/[0.06]
                             dark:hover:text-dark-text-primary"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path
                      d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinejoin="round"
                    />
                    <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
                    {reveal && (
                      <line x1="4" y1="20" x2="20" y2="4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    )}
                  </svg>
                </button>
              </div>

              <button
                type="submit"
                disabled={busy || !complete || (!breakGlass && !who)}
                className="w-full rounded-pill bg-light-accent px-5 py-3 text-body
                           font-semibold text-white shadow-accent-btn
                           transition-opacity disabled:opacity-50
                           dark:bg-dark-accent-fill
                           lg:transition-[opacity,box-shadow,transform] lg:duration-[120ms]
                           lg:enabled:hover:-translate-y-px lg:enabled:hover:shadow-glass"
              >
                {busy ? 'Checking…' : 'Continue'}
              </button>

              {error && (
                <p
                  id="login-error"
                  role="alert"
                  className="flex items-center justify-center gap-2 text-caption
                             text-light-alert dark:text-dark-alert"
                >
                  <span
                    aria-hidden="true"
                    className="block h-[7px] w-[7px] rounded-full bg-light-alert dark:bg-dark-alert"
                  />
                  {error}
                </p>
              )}
            </form>

            {/* Break-glass, deliberately understated. Bottom-centre on desktop. */}
            <button
              type="button"
              onClick={() => { setBreakGlass((v) => !v); setPassphrase(''); setError(null) }}
              className="mt-4 text-caption text-light-text-muted underline-offset-2
                         hover:underline dark:text-dark-text-muted lg:mx-auto lg:mt-6 lg:block"
            >
              {breakGlass ? 'Back to sign in' : 'Operator access'}
            </button>
          </div>
        </div>
      </div>
    </main>
  )
}
