/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI backend runs on 127.0.0.1:8000 in development. The browser only
// ever talks to the Vite origin (http://localhost:5173 = LS_PUBLIC_ORIGIN), so
// the session cookie stays same-origin and the backend's Origin check passes.
const BACKEND_URL = 'http://127.0.0.1:8000'

// task-25-brief.md req. 1: the web app's own CSP (distinct from the API's
// `default-src 'none'` JSON policy in `app/api/middleware.py`). `'unsafe-inline'`
// is scoped to `style-src` only, and only because Recharts (used by the
// capacity/quality trend charts) sets inline `style="..."` attributes on the
// SVG elements it renders (e.g. tooltip positioning) — there is no supported
// way to have it emit a nonce/hash instead. `script-src` stays `'self'` with
// no exception. Mirrored in `index.html`'s `<meta http-equiv>` tag for any
// static host that will not attach custom response headers; `frame-ancestors`
// is dropped there since the `<meta>` form of CSP cannot enforce it (the HTTP
// header below is what actually blocks framing).
const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; " +
  "base-uri 'self'; form-action 'self'"
const SECURITY_HEADERS: Record<string, string> = {
  'content-security-policy': CSP,
  'permissions-policy': 'camera=(), microphone=(), geolocation=()',
  'x-content-type-options': 'nosniff',
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: BACKEND_URL },
      '/auth': { target: BACKEND_URL },
    },
    headers: SECURITY_HEADERS,
  },
  preview: {
    headers: SECURITY_HEADERS,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
})
