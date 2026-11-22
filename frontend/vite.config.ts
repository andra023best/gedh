import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Локально /api/* проксіюється на backend (uvicorn :8010) — так само, як rewrites на Vercel.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8010',
    },
  },
})
