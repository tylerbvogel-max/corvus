import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

/* Smoke test for the seeded demo build — the end-to-end proof the
   contract checks (scripts/check-demo.mjs) can't give: the desktop
   boots, a chat message round-trips through the shim's fake pipeline
   to a canned answer, and the companion windows open. Runs against the
   built demo bundle via `vite preview` (see playwright.config.ts). */

const fixtures = JSON.parse(readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'demo', 'fixtures.json'), 'utf8',
));

test('demo boots, replays a grounded chat answer, opens companion windows', async ({ page }) => {
  await page.goto('/');

  // Desktop shell: floating nav, ASCII substrate, Home window with hero
  await expect(page.locator('.sidebar')).toBeVisible();
  await expect(page.locator('canvas.ascii-wake')).toBeVisible();
  await expect(page.locator('.chat-hero-title').first()).toBeVisible();

  // Demo affordances: replay pill, honest single-entry model roster
  await expect(page.locator('.demo-llm-pill')).toContainText('Replay mode');
  await expect(page.locator('.chat-model-select').first()).toContainText('demo replay');

  // Chat History window is seeded with captured sessions
  const sessionCount = (fixtures.fixtures['/chat/sessions'] ?? []).length;
  expect(sessionCount).toBeGreaterThan(0);

  // Send a message through the hero input
  const input = page.locator('.chat-input').first();
  await input.fill('What should we do to prepare for an AS9100 audit?');
  await input.press('Enter');

  // The canned answer renders as an assistant bubble (fake pipeline takes ~1.5s)
  const assistant = page.locator('.chat-msg--assistant').first();
  await expect(assistant).toBeVisible({ timeout: 20_000 });
  const answerText = await assistant.innerText();
  expect(answerText.length).toBeGreaterThan(80); // a real canned answer, not an error blurb
  expect(answerText).not.toContain('failed');

  // Chat start opened the two companion windows
  await expect(page.locator('.app-window-title', { hasText: 'Chat History' })).toBeVisible();
  await expect(page.locator('.app-window-title', { hasText: 'Neuron Graph' })).toBeVisible();

  // The graph window received the canned answer's neuron scores: it should
  // no longer show its empty state.
  await expect(page.getByText('Ask something in the chat')).toHaveCount(0);
});

test('history "New Chat" reopens the chat window after it is closed', async ({ page }) => {
  await page.goto('/');

  // Enter chat state so the companion windows open
  const input = page.locator('.chat-input').first();
  await input.fill('hello');
  await input.press('Enter');
  await expect(page.locator('.chat-msg--assistant').first()).toBeVisible({ timeout: 20_000 });

  // Close the Home (chat) window
  const homeWindow = page.locator('.app-window', { has: page.locator('.chat-msg--assistant') });
  await homeWindow.locator('.app-window-close').click();
  await expect(page.locator('.chat-msg--assistant')).toHaveCount(0);

  // "New Chat" in the history window must bring back a fresh hero
  await page.locator('.chat-new-btn').click();
  await expect(page.locator('.chat-hero-title').first()).toBeVisible({ timeout: 10_000 });
});
