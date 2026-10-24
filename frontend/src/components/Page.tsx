import { createContext, useContext, type ReactNode } from 'react'
import { Button } from '../jm'

/** Set by the shell on phones, where a screen replaces the sidebar and needs a way back. */
export const BackContext = createContext<(() => void) | null>(null)

export function BackButton({ label = 'Back' }: { label?: string }) {
  const back = useContext(BackContext)
  if (!back) return null
  return <Button variant="ghost" icon="back" aria-label={label} className="md:hidden" onClick={back} />
}

/** One screen: a header row (title, subtitle, actions) above a centred reading column. */
export default function Page({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto grid w-full max-w-[760px] gap-jm-5 px-jm-4 pb-jm-12 pt-jm-4 md:px-jm-8 md:pt-jm-8">
        <header className="grid gap-jm-1">
          <div className="flex min-w-0 items-center gap-jm-2">
            <BackButton />
            <h1 className="m-0 min-w-0 flex-1 truncate text-jm-title text-jm-fg">{title}</h1>
            {actions}
          </div>
          {subtitle && <p className="m-0 text-jm-body-sm text-jm-fg-secondary">{subtitle}</p>}
        </header>
        {children}
      </div>
    </div>
  )
}
