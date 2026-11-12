import { useEffect, useRef } from 'react'

/**
 * The person's own browser, live, while they have taken it over (headed
 * tenants only). The server picks whose browser from the session; this sends
 * no name. Keys and clicks go straight to the page. When the socket ends (hand
 * back, the server refusing, the network) onEnded is called and the card goes
 * back to the still picture.
 */
export default function LiveView({ title, onEnded }: { title?: string; onEnded: (why: string) => void }) {
  const box = useRef<HTMLDivElement>(null)
  const ended = useRef(onEnded)
  ended.current = onEnded

  useEffect(() => {
    let rfb: import('@novnc/novnc').default | null = null
    let gone = false
    const url = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/browser/view`
    void import('@novnc/novnc').then(({ default: RFB }) => {
      if (gone || !box.current) return
      rfb = new RFB(box.current, url, { wsProtocols: ['binary'] })
      rfb.scaleViewport = true
      rfb.resizeSession = false
      rfb.focusOnClick = true
      rfb.addEventListener('disconnect', (e) => {
        if (!gone) ended.current(e.detail?.clean ? 'closed' : 'lost')
      })
    }, () => ended.current('unavailable'))
    return () => {
      gone = true
      rfb?.disconnect()
    }
  }, [])

  return (
    <div
      ref={box}
      data-testid="browser-live"
      role="application"
      aria-label={title ? `The page, live: ${title}` : 'The browser, live'}
      className="h-full w-full bg-jm-bg"
    />
  )
}
