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
      '/metrics': apiTarget,
      '/roadmap-ledgers': apiTarget,
      '/capabilities': apiTarget,
      '/recall': apiTarget,
      '/janitor': apiTarget,
      '/distill': apiTarget,
      '/compile': apiTarget,
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
      // Present in the committed contract but previously unproxied. /tenants
      // is the one that mattered: config.ts fetchAllTenants() calls it, and an
      // unproxied path is answered by Vite's SPA fallback with 200 + index.html,
      // so `resp.ok` is true and `.json()` throws into a catch that returns [].
      // The tenant switcher rendered empty in dev with no error anywhere — the
      // same fail-soft class as SystemUseBanner's .catch(() => {}).
      '/tenants': apiTarget,
      '/lineage': apiTarget,
      '/auditor': apiTarget,
      '/remember': apiTarget,
    },
  },
  build: {
    // Emitted so scripts/check-bundle-budget.mjs can identify the initial route
    // from the entry's own import graph rather than by pattern-matching chunk
    // filenames, which change on every content hash.
    manifest: true,
  },
})
