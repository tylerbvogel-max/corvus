import { defineConfig } from '@playwright/test';

/* Disabled-capability browser negative (roadmap record
   durability-frontend-contracts, verification #6): the app booted against
   the corvus-locomo profile, which composes governance away.

   Separate from playwright.journeys.config.ts ON PURPOSE — one fixture
   backend at a time fits this machine's memory; two OOM-kill Chromium
   (see the note in that config). npm run test:journeys chains both.

   Topology:
     :8007 backend, corvus-locomo profile, db corvus_test_journeys_locomo
     :4175 vite dev server proxying to :8007 */

export default defineConfig({
  testDir: './tests/journeys',
  testMatch: 'composed-away-negative.spec.ts',
  // Same cold-compile budget as playwright.journeys.config.ts.
  timeout: 180_000,
  workers: 1,
  retries: 0,
  use: {
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'bash ../backend/tests/run_journey_backend.sh corvus-locomo corvus_test_journeys_locomo 8007',
      url: 'http://localhost:8007/health',
      reuseExistingServer: false,
      timeout: 240_000,
      stdout: 'pipe',
    },
    {
      command: 'VITE_API_PORT=8007 npx vite --port 4175 --strictPort',
      url: 'http://localhost:4175',
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
