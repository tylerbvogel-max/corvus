import { test, expect, type Page } from '@playwright/test';

const FAILURE_VIEWPORT = { width: 953, height: 927 };

async function canvasEnergy(page: Page) {
  return page.locator('canvas.ascii-wake').evaluate(canvas => {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('ASCII wake canvas has no 2D context');
    const { width, height } = canvas;
    const pixels = ctx.getImageData(0, 0, width, height).data;
    let total = 0;
    let bottom = 0;
    let right = 0;
    for (let y = 0; y < height; y += 3) {
      for (let x = 0; x < width; x += 3) {
        const i = (y * width + x) * 4;
        const value = pixels[i] + pixels[i + 1] + pixels[i + 2];
        total += value;
        if (y >= height * 0.8) bottom += value;
        if (x >= width * 0.8) right += value;
      }
    }
    return { total, bottom, right };
  });
}

test.describe('ASCII wake fractional-DPR clearing', () => {
  test.use({ viewport: FAILURE_VIEWPORT, deviceScaleFactor: 1.25 });

  test('clears bottom and right backing-store strips after wake activity', async ({ page }) => {
    await page.addInitScript(() => {
      sessionStorage.setItem('corvus-banner-ack', '1');
      localStorage.setItem('corvus-nav-collapsed', '0');
      localStorage.setItem('corvus-desktop-graph-visible', '0');
      localStorage.setItem('corvus-wake-settings', JSON.stringify({
        rain: false,
        damping: 0.90,
      }));
      localStorage.setItem('corvus-windows-v1', JSON.stringify({
        'mind-metrics': { x: 226, y: 27, w: 685, h: 529, z: 11, min: false, max: false },
      }));
    });

    await page.goto('/');
    const canvas = page.locator('canvas.ascii-wake');
    await expect(canvas).toBeVisible();
    const scale = await canvas.evaluate(el => el.width / el.clientWidth);
    expect(scale).toBeCloseTo(1.25, 1);

    await page.waitForTimeout(1_000);
    const baseline = await canvasEnergy(page);

    for (let pass = 0; pass < 8; pass++) {
      await page.mouse.move(pass % 2 ? 40 : 920, 840, { steps: 3 });
      await page.mouse.move(925, pass % 2 ? 80 : 820, { steps: 3 });
    }
    const active = await canvasEnergy(page);
    expect(active.total).toBeGreaterThan(baseline.total * 2);

    await page.waitForTimeout(6_000);
    const settled = await canvasEnergy(page);
    expect(settled.bottom).toBeLessThanOrEqual(baseline.bottom * 1.08 + 1_000);
    expect(settled.right).toBeLessThanOrEqual(baseline.right * 1.08 + 1_000);
  });
});
