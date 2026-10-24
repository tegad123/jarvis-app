/**
 * The theme is a 'dark' or 'light' class on <html>; jm/tokens/tokens.css
 * resolves every colour from it. Dark unless the person chose light.
 *
 * The initial value is also applied by an inline script in index.html so the
 * first painted frame is already correct — if you change the storage key or
 * the default here, change it there too.
 */

const STORAGE_KEY = 'jarvis-mono-theme'

export type Theme = 'light' | 'dark'

/** Token values for light.bg / dark.bg, mirrored into <meta name="theme-color">. */
const THEME_COLOR: Record<Theme, string> = {
  light: '#FFFFFF',   // must track --jm-bg in jm/tokens/tokens.css
  dark: '#0A0A0A',
}

export function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    /* storage disabled — fall through to the default */
  }
  return 'dark'
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  document.documentElement.classList.toggle('light', theme === 'light')

  // Keeps the phone's status bar / PWA chrome in step with the app.
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', THEME_COLOR[theme])

  try {
    localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* not persisting is survivable; the default applies next load */
  }
}
