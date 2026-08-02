/* Shared plumbing for the operator-journey specs (roadmap record
   durability-frontend-contracts, verification #6).

   Topology comes from playwright.journeys.config.ts: the corvus-mind
   fixture stack is 4174 → 8006, the corvus-locomo negative stack is
   4175 → 8007. Both backends are throwaway-Postgres databases rebuilt
   and reseeded on every run — journeys may click real write actions. */

import type { Page } from '@playwright/test';

export const MIND_URL = 'http://localhost:4174';
export const LOCOMO_URL = 'http://localhost:4175';
export const MIND_API = 'http://localhost:8006';
export const LOCOMO_API = 'http://localhost:8007';

/** Land on the app and clear the AC-8 system-use banner when the profile
    serves one (fresh fixture databases usually don't enable it; the live
    corvus-mind deployment does — handle both). */
export async function bootOperator(page: Page, url: string = MIND_URL): Promise<void> {
  // Boot with the WebGL neuron universe hidden (a real operator setting,
  // persisted the same way the toolbar toggle persists it). The journeys
  // assert governance surfaces, not the universe — and the three.js layer
  // plus two fixture stacks OOM-kills Chromium on this 6.5 GB machine.
  await page.addInitScript(() => {
    localStorage.setItem('corvus-desktop-graph-visible', '0');
  });
  // The AC-8 banner renders whenever GET /admin/system-banner says so, and
  // its full-screen modal intercepts every click until acknowledged. Anchor
  // on the response itself — a fixed delay races the fetch (measured flake).
  const bannerProbe = page
    .waitForResponse((r) => r.url().includes('/admin/system-banner'), { timeout: 60_000 })
    .catch(() => null);
  await page.goto(url);
  await bannerProbe;
  const ack = page.getByRole('button', { name: 'I Acknowledge' });
  const bannerShown = await ack
    .waitFor({ state: 'visible', timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (bannerShown) {
    await ack.click();
    await ack.waitFor({ state: 'hidden' });
  }
}

/** Navigate the way an operator does: open the floating launcher, open the
    group flyout, click the entry. The nav has no URL routing. */
export async function openNavItem(page: Page, group: string, item: string): Promise<void> {
  await page.locator('button[title^="Open navigation"]').click();
  await page.locator(`button.perch-mode-btn[title="${group}"]`).click();
  await page.locator('button.perch-flyout-item', { hasText: item }).click();
}

/** Open a window by key through the production walkthrough event
    (DemoHelper's corvus-open-window, always registered in App.tsx). Used
    for surfaces editorially curated out of the memory-surface nav rail
    (integrity) and for the composed-away deep-link negative. */
export async function openWindowByKey(page: Page, key: string): Promise<void> {
  await page.evaluate((k) => {
    window.dispatchEvent(new CustomEvent('corvus-open-window', { detail: k }));
  }, key);
}
