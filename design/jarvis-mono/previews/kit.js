/* Preview helper: picks the theme (?theme=light|dark, else the last choice,
   else dark) and adds a theme switch when a preview is opened on its own. */
(function () {
  var KEY = 'jm-preview-theme'
  var html = document.documentElement
  var asked = new URLSearchParams(location.search).get('theme')
  var saved = null
  try { saved = localStorage.getItem(KEY) } catch (e) { /* storage blocked: dark */ }
  html.setAttribute('data-theme', asked === 'light' || asked === 'dark' ? asked : saved === 'light' ? 'light' : 'dark')

  function mount() {
    if (window.self !== window.top) return // inside the gallery, which has its own switch
    var b = document.createElement('button')
    b.type = 'button'
    b.className =
      'fixed bottom-jm-4 right-jm-4 z-[60] inline-flex min-h-jm-control-sm cursor-pointer items-center rounded-jm-pill ' +
      'border border-jm-border bg-jm-panel-raised px-jm-3 font-jm text-jm-label text-jm-fg shadow-jm-float ' +
      'focus-visible:outline-none focus-visible:shadow-jm-focus'
    function label() { b.textContent = html.getAttribute('data-theme') === 'light' ? 'Dark theme' : 'Light theme' }
    b.onclick = function () {
      var next = html.getAttribute('data-theme') === 'light' ? 'dark' : 'light'
      html.setAttribute('data-theme', next)
      try { localStorage.setItem(KEY, next) } catch (e) { /* not remembered */ }
      label()
    }
    label()
    document.body.appendChild(b)
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount)
  else mount()
})()
