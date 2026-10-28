import { useEffect, useRef, useState } from 'react'
import { Button, SettingsSection } from '../jm'
import { ErrorLine, Select } from '../jm/shared/primitives'
import { Unauthorized, getTeam, previewLeadImport, runLeadImport } from '../lib/api'
import type { LeadImportPreview, TeamPerson } from '../lib/types'

/**
 * Settings → Leads, owner only (the API refuses anyone else). Bring the team's
 * open-house spreadsheet in once; after that the app is where agents work.
 *
 * Preview first, always: how many leads, which agent each tab goes to, and any
 * agent name the team list can't place — the owner picks a person or "Nobody"
 * for each before Import is offered. Importing the same file again adds
 * nothing new.
 */
export default function LeadImport({ onUnauthorized }: { onUnauthorized: () => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<LeadImportPreview | null>(null)
  const [team, setTeam] = useState<TeamPerson[]>([])
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    getTeam()
      .then((t) => setTeam(t.people))
      .catch((err) => {
        if (err instanceof Unauthorized) onUnauthorized()
      })
  }, [onUnauthorized])

  const unplaced = preview?.agents.filter((a) => !a.person_key) ?? []
  const ready = !!preview && unplaced.every((a) => a.name in answers)

  const pick = async (file: File) => {
    setBusy(true)
    setError(null)
    setDone(null)
    setPreview(null)
    setAnswers({})
    try {
      const r = await previewLeadImport(file)
      if (r.ok) setPreview(r.data)
      else setError(r.detail)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError('That file could not be uploaded.')
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  const run = async () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    try {
      const agents = Object.fromEntries(unplaced.map((a) => [a.name, answers[a.name] || null]))
      const r = await runLeadImport(preview.token, agents)
      if (r.ok) {
        setDone(`Imported: ${r.data.created} new, ${r.data.updated} already here.`)
        setPreview(null)
      } else setError(r.detail)
    } catch (err) {
      if (err instanceof Unauthorized) return onUnauthorized()
      setError('The import did not run.')
    } finally {
      setBusy(false)
    }
  }

  const people = [{ value: '', label: 'Choose…' }, { value: '__nobody', label: 'Nobody yet' },
    ...team.map((p) => ({ value: p.person_key, label: p.name }))]

  return (
    <SettingsSection
      title="Leads"
      description="Bring in the open-house spreadsheet once (.xlsx, or .csv per tab). After that, agents work their leads here and in chat."
      status={done ? { tone: 'done', label: 'Imported' } : undefined}
      actions={
        <>
          <input
            ref={input}
            type="file"
            accept=".xlsx,.xlsm,.csv"
            className="hidden"
            data-testid="lead-import-file"
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f) void pick(f)
            }}
          />
          <Button disabled={busy} onClick={() => input.current?.click()}>
            {preview ? 'Choose another file' : 'Import a spreadsheet'}
          </Button>
          {preview && (
            <Button variant="primary" disabled={busy || !ready || preview.leads === 0} onClick={() => void run()}>
              Import {preview.new} lead{preview.new === 1 ? '' : 's'}
            </Button>
          )}
        </>
      }
    >
      {error && <ErrorLine>{error}</ErrorLine>}
      {done && <p className="m-0 text-jm-body-sm text-jm-fg">{done}</p>}
      {preview && (
        <div className="grid gap-jm-3 text-jm-body-sm" data-testid="lead-import-preview">
          <p className="m-0 text-jm-fg">
            {preview.leads} leads found · {preview.new} new · {preview.already_imported} already imported ·{' '}
            {preview.due_now} due for a follow-up now
          </p>
          <ul className="m-0 grid list-none gap-jm-1 p-0 text-jm-fg-secondary">
            {preview.tabs.map((t) => (
              <li key={t.tab}>
                {t.tab}: {t.skipped ? `skipped (${t.skipped})` : `${t.leads} leads`}
                {t.unmatched_headers && t.unmatched_headers.length > 0 && ` · columns left out: ${t.unmatched_headers.join(', ')}`}
              </li>
            ))}
          </ul>
          {preview.agents.filter((a) => a.person_key).length > 0 && (
            <p className="m-0 text-jm-fg-secondary">
              Goes to:{' '}
              {preview.agents
                .filter((a) => a.person_key)
                .map((a) => `${team.find((p) => p.person_key === a.person_key)?.name ?? a.name} (${a.leads})`)
                .join(' · ')}
            </p>
          )}
          {unplaced.map((a) => (
            <label key={a.name} className="flex flex-wrap items-center justify-between gap-jm-3 text-jm-fg">
              <span>
                “{a.name}” isn’t on the team list ({a.leads} leads). Whose are they?
              </span>
              <Select
                label={`Agent for ${a.name}`}
                value={a.name in answers ? answers[a.name] || '__nobody' : ''}
                options={people}
                onChange={(v) =>
                  setAnswers((s) => {
                    const next = { ...s }
                    if (v === '') delete next[a.name]
                    else next[a.name] = v === '__nobody' ? '' : v
                    return next
                  })
                }
              />
            </label>
          ))}
          {preview.co_listed > 0 && (
            <p className="m-0 text-jm-fg-secondary">{preview.co_listed} leads appear on two tabs under the same Lead ID; each comes in once.</p>
          )}
          {preview.collisions.length > 0 && (
            <ErrorLine>
              {preview.collisions.length} Lead ID{preview.collisions.length === 1 ? ' is' : 's are'} used for two different people; the
              second of each is left out: {preview.collisions.slice(0, 5).map((c) => `${c.ref} (${c.second})`).join(', ')}
            </ErrorLine>
          )}
          {preview.bad_dates > 0 && (
            <p className="m-0 text-jm-fg-secondary">{preview.bad_dates} follow-up dates couldn’t be read; those leads come in without one.</p>
          )}
        </div>
      )}
    </SettingsSection>
  )
}
