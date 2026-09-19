/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The FastAPI backend runs on 127.0.0.1:8000 in development. The browser only
// ever talks to the Vite origin (http://localhost:5173 = LS_PUBLIC_ORIGIN), so
// the session cookie stays same-origin and the backend's Origin check passes.
const BACKEND_URL = 'http://127.0.0.1:8000'

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
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
  },
})
