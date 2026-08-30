/**
 * Dark mode via Tailwind's class strategy: a 'dark' class on <html>.
 *
 * The initial value is also applied by an inline script in index.html so the
 * first painted frame is already correct — if you change the storage key or
 * the default here, change it there too.
 */

const STORAGE_KEY = 'jarvis-theme'

export type Theme = 'light' | 'dark'

/** Token values for light.bg / dark.bg, mirrored into <meta name="theme-color">. */
const THEME_COLOR: Record<Theme, string> = {
  light: '#F2EEE6',   // must track dark:bg in tailwind.config.cjs
  dark: '#0C0E12',
}

export function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* storage disabled — fall through to the OS preference */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')

  // Keeps the phone's status bar / PWA chrome in step with the app.
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[theme])

  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* not persisting is survivable; the OS preference still applies next load */
  }
}
