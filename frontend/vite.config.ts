import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiPort = process.env.VITE_API_PORT || '8002'
const apiTarget = `http://127.0.0.1:${apiPort}`

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8004,
    proxy: {
      '/neurons': apiTarget,
      '/engrams': apiTarget,
      '/queries': apiTarget,
      '/query': apiTarget,
      '/context': apiTarget,
      '/admin': apiTarget,
      '/health': apiTarget,
      '/tenant': apiTarget,
      '/eval-scores': apiTarget,
      '/ingest': apiTarget,
      '/corvus': apiTarget,
      '/chat': apiTarget,
      '/models': apiTarget,
      '/learning-analytics': apiTarget,
      '/v1': apiTarget,
    },
  },
})
