#!/usr/bin/env node

import { chromium } from '@playwright/test';

const base = process.env.CORVUS_UI ?? 'http://127.0.0.1:8005';
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
page.setDefaultTimeout(10_000);
const browserErrors = [];

page.on('pageerror', error => browserErrors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') browserErrors.push(message.text());
});

async function openLab(name) {
  const collapsed = page.getByTitle('Open navigation (drag to move)');
  if (await collapsed.isVisible().catch(() => false)) {
    await collapsed.click();
  }
  const labGroup = page.locator('.perch-mode-btn[title="Carlos Lab"]');
  await labGroup.waitFor({ state: 'visible' });
  await labGroup.click();
  await page.locator('.perch-flyout-item', { hasText: name }).click();
}

try {
  const bannerReady = page.waitForResponse(response =>
    response.url().endsWith('/admin/system-banner'),
  );
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await page.getByTitle('Open navigation (drag to move)').waitFor({ state: 'visible' });
  await bannerReady;
  const acknowledge = page.getByRole('button', { name: 'I Acknowledge' });
  await acknowledge.waitFor({ state: 'visible', timeout: 5_000 }).catch(() => {});
  if (await acknowledge.isVisible().catch(() => false)) {
    await acknowledge.click();
    await acknowledge.waitFor({ state: 'hidden' });
  }

  await openLab('Nexus Graph');
  const nexus = page.getByTestId('nexus-lab-canvas');
  await nexus.waitFor({ state: 'visible' });
  await page.getByText(/\d+ projected nodes/).waitFor({ state: 'visible' });
  await page.getByText(/\d+ deterministic Leiden communities/).waitFor({ state: 'visible' });
  await page.getByRole('button', { name: /Bridges/ }).click();
  await page.getByRole('button', { name: /Local/ }).click();
  await page.getByRole('button', { name: /Leiden/ }).click();
  await page.screenshot({ path: '/tmp/carlos-lab-nexus.png', fullPage: true });

  await openLab('Oracle Funnel');
  await page.getByRole('heading', { name: 'Oracle Funnel' }).waitFor({ state: 'visible' });
  const empty = page.getByTestId('oracle-funnel-empty');
  const populated = page.getByTestId('oracle-funnel-headline');
  await Promise.race([
    empty.waitFor({ state: 'visible' }),
    populated.waitFor({ state: 'visible' }),
  ]);
  await page.getByText('OBSERVER ONLY · ZERO EXTRA LLM CALLS').waitFor({ state: 'visible' });
  await page.screenshot({ path: '/tmp/carlos-lab-oracle.png', fullPage: true });

  const nexusStats = (await page.locator('.nexus-lab__stats').textContent())?.trim();
  const oracleState = await populated.isVisible().catch(() => false) ? 'populated' : 'honest-empty';
  const unexpected = browserErrors.filter(message =>
    !message.includes('favicon') &&
    !message.includes('ResizeObserver loop') &&
    !(message.includes('fonts.googleapis.com') && message.includes('Content Security Policy')),
  );
  if (unexpected.length) {
    throw new Error(`browser errors: ${unexpected.join(' | ')}`);
  }

  console.log(JSON.stringify({
    base,
    nexus: nexusStats,
    oracle: oracleState,
    screenshots: [
      '/tmp/carlos-lab-nexus.png',
      '/tmp/carlos-lab-oracle.png',
    ],
  }, null, 2));
} finally {
  await browser.close();
}
