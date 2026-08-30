import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Built bundle is served by the FastAPI app at /root/jarvis-app/static.
// Same-origin is mandatory: the app has no CORS middleware and the session
// cookie is httponly + samesite=lax, so a cross-origin dev server cannot
// authenticate. Always verify against a built bundle through the tunnel.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static',
    emptyOutDir: true,
    // Fonts must be emitted as files, never inlined as data: URLs, and no
    // asset may reference an external host at runtime.
    assetsInlineLimit: 0,
  },
})
