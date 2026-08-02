import { defineConfig } from '@playwright/test';

/* Operator-journey specs (tests/journeys/) for roadmap record
   durability-frontend-contracts verification #6.

   Mutation safety: journeys click REAL write actions (proposal approval,
   integrity resolution, roadmap reconciliation), so they run against
   throwaway-Postgres fixture backends rebuilt from `alembic upgrade head`
   on every run — never against corvus_mind. See
   backend/tests/run_journey_backend.sh for the one-time database creation.

   Topology (positive journeys only):
     :8006 backend, corvus-mind profile, db corvus_test_journeys (seeded)
     :4174 vite dev server proxying to :8006

   The corvus-locomo negative lives in playwright.journeys-negative.config.ts.
   The stacks are separate configs ON PURPOSE: booting both fixture backends
   (each holding a torch/sentence-transformers runtime) plus two Vite servers
   and Chromium OOM-kills the browser on a 6.5 GB machine — measured, not
   hypothetical (dmesg: oom-kill chrome-headless, 2026-08-02).

   Run both: npm run test:journeys */

export default defineConfig({
  testDir: './tests/journeys',
  testIgnore: 'composed-away-negative.spec.ts',
  // Generous per-test budget: the first page load pays the Vite dev-server
  // cold compile on a memory-tight machine (measured flake at 90s).
  timeout: 180_000,
  // Journeys mutate shared fixture state (approve/resolve/reconcile), so
  // they run serially against the one seeded database.
  workers: 1,
  retries: 0,
  use: {
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'bash ../backend/tests/run_journey_backend.sh corvus-mind corvus_test_journeys 8006',
      url: 'http://localhost:8006/health',
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: 'pipe',
    },
    {
      command: 'VITE_API_PORT=8006 npx vite --port 4174 --strictPort',
      url: 'http://localhost:4174',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
