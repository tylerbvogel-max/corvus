import { defineConfig } from '@playwright/test';

/* Playwright config for the demo smoke test (tests/demo-smoke.spec.ts).
   Serves the ALREADY-BUILT demo bundle — run `npm run build:demo` first
   (CI does; see .github/workflows/demo-smoke.yml). */

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  webServer: {
    command: 'npx vite preview --port 4173 --strictPort',
    url: 'http://localhost:4173',
    // Never reuse: a leftover preview serves a stale dist/ (e.g. the
    // normal build overwriting the demo build) and fails confusingly.
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
