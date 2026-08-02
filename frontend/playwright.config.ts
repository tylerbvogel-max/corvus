import { defineConfig } from '@playwright/test';

/* Playwright config for the demo smoke test (tests/demo-smoke.spec.ts).
   Serves the ALREADY-BUILT demo bundle — run `npm run build:demo` first
   (CI does; see .github/workflows/demo-smoke.yml). */

export default defineConfig({
  testDir: './tests',
  // Operator journeys run against throwaway fixture backends under their own
  // config (playwright.journeys.config.ts) — never against the demo bundle.
  testIgnore: 'journeys/**',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  webServer: {
    // Build the demo bundle as part of serving: the tests assert demo
    // behaviors, and dist/ may hold a normal build from an interleaved
    // `npm run build` — testing that is a guaranteed confusing failure.
    command: 'npm run build:demo && npx vite preview --port 4173 --strictPort',
    url: 'http://localhost:4173',
    // Never reuse: a leftover preview serves a stale dist/.
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
