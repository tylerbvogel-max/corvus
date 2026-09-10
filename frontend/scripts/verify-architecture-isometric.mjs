import { readFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { preview } from 'vite';
import { chromium } from 'playwright';
import { expect } from '@playwright/test';

// Exercise the packaged normal build. Only reviewed source-derived architecture
// and the existing public demo fixture supply API responses; no personal backend.
const model = JSON.parse(await readFile('../architecture/conformance.json', 'utf8'));
const { fixtures } = JSON.parse(await readFile('src/demo/fixtures.json', 'utf8'));
const artifacts = resolve('../.artifacts/architecture-browser');
await mkdir(artifacts, { recursive: true });
const server = await preview({ configFile: false, preview: { host: '127.0.0.1', port: 0, strictPort: true } });
let browser;
try {
  browser = await chromium.launch({ args: ['--no-sandbox'] });
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const viewport of [{ width: 1440, height: 1080 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let fresh = true;
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (['fetch', 'xhr'].includes(request.resourceType())) {
        const body = url.pathname === '/admin/architecture'
          ? { ...model, freshness: { ...model.freshness, fresh } }
          : url.pathname === '/tenants' ? [fixtures['/tenant']]
          : fixtures[url.pathname] ?? {};
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      }
      if (url.origin !== base) return route.abort();
      return route.continue();
    });
    await page.goto(base);
    async function openArchitecture() {
      if (await page.locator('.iso-map').isVisible()) return;
      if (viewport.width < 600) {
        await page.locator('.mobile-bottombar-btn', { hasText: 'Menu' }).click();
        await page.locator('.mobile-sheet-group', { hasText: 'Observe' }).click();
        await page.locator('.mobile-sheet-subitem', { hasText: 'Architecture' }).click();
      } else {
        if (await page.locator('.sidebar-pill-btn').isVisible()) await page.locator('.sidebar-pill-btn').click();
        await page.locator('.perch-mode-btn', { hasText: 'Observe' }).click();
        await page.locator('.perch-flyout').getByRole('button', { name: /^Architecture / }).click();
      }
      await expect(page.locator('.iso-map')).toBeVisible();
    }
    await openArchitecture();
    await expect(page.locator('.iso-map__stamp')).toHaveText('SOURCE MODEL CURRENT');
    await expect(page.locator('.iso-map__tile')).toHaveCount(model.memory_engine.zones.length);
    for (const zone of model.memory_engine.zones) {
      const button = page.locator('.iso-map__zone-buttons button', { hasText: zone.name });
      await button.click();
      await expect(button).toHaveAttribute('aria-pressed', 'true');
      await expect(page.locator('.iso-map__inspector h4')).toHaveText(zone.name);
      await expect(page.locator('.iso-map__inspector')).toContainText(zone.purpose);
    }
    const tile = page.locator('.iso-map__tile').first();
    await tile.focus();
    await page.keyboard.press('Enter');
    await expect(tile).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.iso-map__disclosure')).toContainText('not runtime health');
    const overflow = await page.locator('.iso-map').evaluate(element => element.scrollWidth > element.clientWidth + 1);
    expect(overflow).toBe(false);
    await page.locator('.iso-map').screenshot({ path: `${artifacts}/isometric-${viewport.width}.png` });
    fresh = false;
    await page.reload();
    await openArchitecture();
    await expect(page.locator('.iso-map__stamp')).toHaveText('SOURCE MODEL STALE');
    expect(errors).toEqual([]);
    await context.close();
    console.log(`PASS packaged architecture ${viewport.width}px: all zones, keyboard, containment, stale label, no page errors`);
  }
} finally {
  if (browser) await browser.close();
  await new Promise(resolveClose => server.httpServer.close(resolveClose));
}
