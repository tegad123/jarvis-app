import { useState } from 'react'
import { Button } from '../Button/Button'
import { Dots, Tick, cx, srOnly } from '../shared/primitives'

export interface PairingCodeProps {
  code: string
  /** Formatted time: "Works once, until {expires}." */
  expires?: string
  paired?: boolean
  deviceName?: string
  /** null hides the instruction line. */
  instruction?: string | null
  waitingLabel?: string
  onCopy?: (code: string) => void
  className?: string
}

export function PairingCode(p: PairingCodeProps) {
  const [copy, setCopy] = useState<'idle' | 'copied' | 'failed'>('idle')
  const code = String(p.code ?? '')

  const onCopy = () => {
    p.onCopy?.(code)
    const fail = () => setCopy('failed')
    try {
      if (!navigator.clipboard?.writeText) return fail()
      navigator.clipboard.writeText(code).then(() => {
        setCopy('copied')
        setTimeout(() => setCopy('idle'), 1600)
      }, fail)
    } catch {
      fail()
    }
  }

  return (
    <div className="grid gap-jm-3" data-paired={p.paired || undefined}>
      {!p.paired && p.instruction !== null && (
        <p className="m-0 text-jm-body-sm text-jm-fg-secondary">{p.instruction ?? 'On your Mac, run the Jarvis helper’s pair command with this code:'}</p>
      )}
      {!p.paired && (
        <div className="flex flex-wrap items-center gap-jm-4">
          <span className={srOnly}>Pairing code: {code.split('').join(' ')}</span>
          <div className="flex flex-wrap items-center gap-jm-2" aria-hidden="true">
            {code.split('').map((ch, i) =>
              ch === '-' || ch === ' ' ? (
                <span key={i} className="block h-0.5 w-2.5 rounded-sm bg-jm-border-strong" />
              ) : (
                <span
                  key={i}
                  className="box-border grid h-[52px] min-w-[40px] place-items-center rounded-jm-md border border-jm-border-strong bg-jm-bg text-jm-pairing tabular-nums text-jm-fg"
                >
                  {ch}
                </span>
              ),
            )}
          </div>
          <Button size="sm" icon="copy" onClick={onCopy}>
            {copy === 'copied' ? 'Copied' : copy === 'failed' ? 'Select and copy' : 'Copy'}
          </Button>
        </div>
      )}
      {!p.paired && p.expires && <p className="m-0 text-jm-caption text-jm-fg-secondary">Works once, until {p.expires}.</p>}
      <p role="status" className={cx('m-0 flex items-center gap-jm-2 text-jm-body-sm', p.paired ? 'text-jm-fg' : 'text-jm-fg-secondary')}>
        {p.paired ? <Tick /> : <Dots />}
        {p.paired ? `Connected${p.deviceName ? ` · ${p.deviceName}` : ''}` : (p.waitingLabel ?? 'Waiting for your Mac…')}
      </p>
    </div>
  )
}
