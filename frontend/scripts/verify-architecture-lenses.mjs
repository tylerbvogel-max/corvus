import { readFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { preview } from 'vite';
import { chromium } from 'playwright';
import { expect } from '@playwright/test';

const model = JSON.parse(await readFile('../architecture/conformance.json', 'utf8'));
const { fixtures } = JSON.parse(await readFile('src/demo/fixtures.json', 'utf8'));
const artifacts = resolve('../.artifacts/architecture-lenses');
await mkdir(artifacts, { recursive: true });
const server = await preview({ configFile: false, preview: { host: '127.0.0.1', port: 0, strictPort: true } });
let browser;
try {
  browser = await chromium.launch({ args: ['--no-sandbox'] });
  const base = `http://127.0.0.1:${server.httpServer.address().port}`;
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1080 }, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => { errors.push(error.message); console.error('PAGE ERROR:', error.message); });
    let failJobs = false;
    let fresh = true;
    await page.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (['fetch', 'xhr'].includes(request.resourceType())) {
        if (url.pathname === '/metrics/mind/jobs') {
          if (failJobs) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"synthetic unavailable"}' });
          return route.fulfill({ json: { jobs: [
            { name: 'synthetic-zero', owner: 'test', status: 'ok', receipt: { outcome: 'no-work', duration_ms: 0, finished_at: '2026-09-10T00:00:00Z', exception: 'SYNTHETIC_PRIVATE_CANARY', detail: { secret: 'SYNTHETIC_PRIVATE_CANARY' } } },
            { name: 'synthetic-invalid', receipt: { duration_ms: '30' } },
            { name: 'synthetic-missing' },
          ] } });
        }
        const body = url.pathname === '/admin/architecture' ? { ...model, freshness: { ...model.freshness, fresh } }
          : url.pathname === '/models' ? []
          : url.pathname === '/tenants' ? [fixtures['/tenant']]
          : fixtures[url.pathname];
        return body === undefined
          ? route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
          : route.fulfill({ json: body });
      }
      return url.origin === base ? route.continue() : route.abort();
    });
    await page.goto(base);
    async function openArchitecture() {
      if (await page.locator('.atlas-lenses').isVisible()) return;
      if (width < 600) {
        await page.locator('.mobile-bottombar-btn', { hasText: 'Menu' }).click();
        await page.locator('.mobile-sheet-group', { hasText: 'Observe' }).click();
        await page.locator('.mobile-sheet-subitem', { hasText: 'Architecture' }).click();
      } else {
        if (await page.locator('.sidebar-pill-btn').isVisible()) await page.locator('.sidebar-pill-btn').click();
        await page.locator('.perch-mode-btn', { hasText: 'Observe' }).click();
        await page.locator('.perch-flyout').getByRole('button', { name: /^Architecture / }).click();
      }
      await expect(page.locator('.atlas-lenses')).toBeVisible();
    }
    await openArchitecture();
    const lens = page.locator('.atlas-lenses__tabs');
    await expect(page.locator('.iso-map')).toContainText('BACKEND PROCESS / MODULAR MONOLITH');
    async function checkMapContainment() {
      const failures = await page.locator('.iso-map__field svg').evaluate(svg => {
        const polygon = svg.querySelector('.iso-map__perimeter');
        const points = Array.from(polygon.points).map(p => new DOMPoint(p.x, p.y).matrixTransform(polygon.getScreenCTM()));
        const inside = p => {
          const sides = points.map((a, i) => { const b = points[(i + 1) % points.length]; return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x); });
          return sides.every(n => n >= -.01) || sides.every(n => n <= .01);
        };
        const failures = [];
        for (const tile of svg.querySelectorAll('.iso-map__tile[data-boundary="backend"], .iso-map__tile[data-boundary="cache"]')) {
          for (const face of tile.querySelectorAll('.iso-map__top, .iso-map__wall')) {
            for (let i = 0; i <= 40; i++) {
              const p = face.getPointAtLength(face.getTotalLength() * i / 40);
              if (!inside(new DOMPoint(p.x, p.y).matrixTransform(face.getScreenCTM()))) failures.push(tile.dataset.zone);
            }
          }
        }
        return failures;
      });
      expect(failures).toEqual([]);
    }
    await checkMapContainment();
    expect(new Set(await page.locator('.iso-map__tile').evaluateAll(nodes => nodes.map(n => n.dataset.scale))).size).toBeGreaterThan(1);
    await page.getByLabel('Slab sizing').selectOption('equal');
    await checkMapContainment();
    expect(await page.locator('.iso-map__tile').evaluateAll(nodes => nodes.every(n => n.dataset.scale === '1'))).toBe(true);
    await page.getByLabel('Slab sizing').selectOption('evidence');
    await page.getByLabel('Visible flows').selectOption('recall');
    await expect(page.locator('.iso-map__flow')).toHaveCount(5);
    await page.locator('.iso-map__flow-buttons').getByRole('button', { name: 'Canonical reads', exact: true }).click();
    await expect(page.locator('.iso-map__flow-detail')).toContainText('data movement');
    await page.getByLabel('Visible flows').selectOption('all');
    const allFlows = await page.locator('.iso-map__flow').count();
    expect(allFlows).toBeGreaterThan(5);
    await page.locator('.iso-map__zone-buttons button').nth(1).click();
    expect(await page.locator('.iso-map__flow').count()).toBeLessThan(allFlows);
    await expect(page.locator('.iso-map__inspector')).toContainText('Responsibility inside the shared backend');
    await lens.getByRole('button', { name: 'Process paths' }).click();
    const selected = model.processes.find(item => item.id === 'episode-distillation');
    await expect(page.locator('.atlas-lenses__step-buttons button')).toHaveCount(selected.steps.length);
    await page.locator('.atlas-lenses__step-buttons button').last().focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('.atlas-lenses__inspector')).toContainText(selected.steps.at(-1).state_change);
    await expect(page.locator('.atlas-lenses__panel')).toContainText('Arrows mean reviewed sequence');
    await page.locator('.atlas-lenses__failure summary').first().click();
    await expect(page.locator('.atlas-lenses__failure').first()).toContainText(model.deployments[0].failure_modes[0].recovery);
    await lens.getByRole('button', { name: 'Code footprint' }).click();
    await page.getByLabel('Component group').selectOption('all');
    await expect(page.locator('.atlas-lenses__footprint button')).toHaveCount(model.boxes.length);
    await page.locator('.atlas-lenses__footprint button').first().click();
    await expect(page.locator('.atlas-lenses__inspector')).toContainText(model.boxes[0].purpose);
    await lens.getByRole('button', { name: 'Operational receipts' }).click();
    await expect(page.locator('.atlas-lenses__jobs article')).toHaveCount(3);
    await expect(page.locator('.atlas-lenses__jobs article').first()).toContainText('0 ms');
    await expect(page.locator('.atlas-lenses__jobs article').nth(1)).toContainText('Unavailable');
    await expect(page.locator('.atlas-lenses')).not.toContainText('SYNTHETIC_PRIVATE_CANARY');
    failJobs = true;
    await lens.getByRole('button', { name: 'Boundaries' }).click();
    await lens.getByRole('button', { name: 'Operational receipts' }).click();
    await expect(page.locator('.atlas-lenses [role="alert"]')).toContainText('No healthy state inferred');
    await lens.getByRole('button', { name: 'Boundaries' }).click();
    expect(await page.locator('.atlas-lenses').evaluate(e => e.scrollWidth > e.clientWidth + 1)).toBe(false);
    await page.locator('.atlas-lenses').screenshot({ path: `${artifacts}/boundaries-${width}.png` });
    fresh = false;
    await page.reload(); await openArchitecture();
    await expect(page.locator('.iso-map__stamp')).toHaveText('SOURCE MODEL STALE');
    expect(errors).toEqual([]);
    await context.close();
    console.log(`PASS ${width}px: boundaries, process keyboard/state/recovery, footprint, zero/missing/invalid receipts, error state, private canary, stale model`);
  }
} finally {
  if (browser) await browser.close();
  await new Promise(done => server.httpServer.close(done));
}
