/* Browser-level disabled-capability negative (roadmap record
   durability-frontend-contracts, verification #6): booted against the
   corvus-locomo profile, the governance surface must simply not exist —
   absence of the nav entry is the assertion. The deep-link fallback proves
   the fail-soft seam end to end in a real browser: an ungranted surface
   reached anyway reports "composed away", never a fake network failure.
   Complements the browser-less classification tests in
   tests/composed-away.spec.ts. */

import { test, expect } from '@playwright/test';
import { LOCOMO_URL, bootOperator, openWindowByKey } from './helpers';

test('locomo profile composes governance away, visibly and honestly', async ({ page }) => {
  await bootOperator(page, LOCOMO_URL);

  // Open the floating launcher, then walk every nav group flyout.
  await page.locator('button[title^="Open navigation"]').click();
  const groups = page.locator('button.perch-mode-btn');
  const groupCount = await groups.count();
  expect(groupCount).toBeGreaterThan(0);

  let totalItems = 0;
  for (let i = 0; i < groupCount; i++) {
    await groups.nth(i).click();
    totalItems += await page.locator('button.perch-flyout-item').count();
    // Absence is the assertion: no group may offer the Governance entry.
    await expect(page.locator('button.perch-flyout-item', { hasText: 'Governance' })).toHaveCount(0);
  }
  // Positive control: the nav is real and populated — absence of Governance
  // is composition, not a broken rail.
  expect(totalItems).toBeGreaterThan(0);

  // Deep-link fallback: force the composed-away surface open anyway. The
  // page's fetches must surface ComposedAwayError's own sentence — naming
  // the profile — not a masqueraded network failure.
  await openWindowByKey(page, 'knowledge-governance');
  await expect(
    page.getByText(/composed away on the corvus-locomo profile/).first(),
  ).toBeVisible();
});
