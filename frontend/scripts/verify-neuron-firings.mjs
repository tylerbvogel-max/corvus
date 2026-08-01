#!/usr/bin/env node

import { chromium } from '@playwright/test';

const base = process.env.CORVUS_UI ?? 'http://127.0.0.1:8004';
const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.setDefaultTimeout(15_000);
const browserErrors = [];

page.on('pageerror', error => browserErrors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') browserErrors.push(message.text());
});

try {
  await page.addInitScript(() => {
    sessionStorage.setItem('corvus-banner-ack', '1');
  });
  await page.goto(base, { waitUntil: 'domcontentloaded' });

  const controls = page.getByTestId('desktop-neuron-controls');
  await controls.waitFor({ state: 'visible' });
  const openWindow = page.locator('.app-window-close').first();
  if (await openWindow.isVisible().catch(() => false)) await openWindow.click();

  const canvas = page.locator('.desktop-neuron-layer canvas').first();
  const firingToggle = page.getByTestId('toggle-firings');
  const paceHandle = page.getByTestId('radar-handle-firing-pace');
  await canvas.waitFor({ state: 'visible' });
  await firingToggle.waitFor({ state: 'visible' });
  await paceHandle.waitFor({ state: 'visible' });

  await page.waitForFunction(() => {
    const graph = document.querySelector('.desktop-neuron-layer canvas');
    return (
      Number(graph?.getAttribute('data-coverage-total-edges')) > 0 &&
      Number(graph?.getAttribute('data-last-coverage-chain-length')) >= 4 &&
      Number(graph?.getAttribute('data-coverage-edges-seen')) >= 4
    );
  });

  if (await firingToggle.getAttribute('aria-pressed') !== 'true') {
    throw new Error('Firings did not initialize enabled');
  }
  if (await paceHandle.getAttribute('aria-valuetext') !== 'Alive') {
    throw new Error(`Firing pace did not initialize at Alive: ${await paceHandle.getAttribute('aria-valuetext')}`);
  }

  const readCoverage = () => canvas.evaluate(element => ({
    enabled: element.getAttribute('data-firings-enabled'),
    pace: Number(element.getAttribute('data-firing-pace')),
    targetSeconds: element.getAttribute('data-coverage-target-seconds'),
    edgesSeen: Number(element.getAttribute('data-coverage-edges-seen')),
    totalEdges: Number(element.getAttribute('data-coverage-total-edges')),
    nodesSeen: Number(element.getAttribute('data-coverage-nodes-seen')),
    zeroActivityEdgesSeen: Number(element.getAttribute('data-coverage-zero-activity-edges-seen')),
    unreplayedEdgesSeen: Number(element.getAttribute('data-coverage-unreplayed-edges-seen')),
    lastChainLength: Number(element.getAttribute('data-last-coverage-chain-length')),
    nextInterval: Number(element.getAttribute('data-coverage-next-interval')),
    activePulses: Number(element.getAttribute('data-firing-active-pulses')),
    synapsesEnabled: element.getAttribute('data-synapses-enabled'),
    visibleSynapses: Number(element.getAttribute('data-visible-synapses')),
  }));

  const alive = await readCoverage();
  if (alive.unreplayedEdgesSeen < 1 || alive.nodesSeen < 4) {
    throw new Error(`Fair sweep did not reach rare graph territory: ${JSON.stringify(alive)}`);
  }

  // The inner end of the spider axis is truly static.
  await paceHandle.focus();
  await page.keyboard.press('Home');
  await page.waitForFunction(() => {
    const graph = document.querySelector('.desktop-neuron-layer canvas');
    return (
      graph?.getAttribute('data-firings-enabled') === 'false' &&
      graph?.getAttribute('data-coverage-target-seconds') === 'static' &&
      graph?.getAttribute('data-firing-active-pulses') === '0'
    );
  });
  const staticBefore = await readCoverage();
  await page.waitForTimeout(1_000);
  const staticAfter = await readCoverage();
  if (staticAfter.edgesSeen !== staticBefore.edgesSeen) {
    throw new Error(`Static pace continued advancing coverage: ${staticBefore.edgesSeen} -> ${staticAfter.edgesSeen}`);
  }

  // Slow is still coverage-bounded below one hour.
  await page.keyboard.press('ArrowUp');
  await page.waitForFunction(() => {
    const graph = document.querySelector('.desktop-neuron-layer canvas');
    return (
      graph?.getAttribute('data-firings-enabled') === 'true' &&
      graph?.getAttribute('data-coverage-target-seconds') === '3300' &&
      Number(graph?.getAttribute('data-last-coverage-chain-length')) === 4
    );
  });
  const slowAfter = await readCoverage();
  const slowDelta = slowAfter.edgesSeen - staticAfter.edgesSeen;
  if (slowDelta < 1 || slowAfter.nextInterval <= 0) {
    throw new Error(`Slow firing pace did not advance coverage: ${JSON.stringify({ staticAfter, slowAfter })}`);
  }

  // The outer end is visibly more active and grows longer connected chains.
  await paceHandle.focus();
  await page.keyboard.press('End');
  await page.waitForFunction(() => {
    const graph = document.querySelector('.desktop-neuron-layer canvas');
    return (
      graph?.getAttribute('data-firing-pace') === '5' &&
      graph?.getAttribute('data-coverage-target-seconds') === '180' &&
      Number(graph?.getAttribute('data-last-coverage-chain-length')) >= 8
    );
  });
  const franticAfter = await readCoverage();
  const franticDelta = franticAfter.edgesSeen - slowAfter.edgesSeen;
  if (
    franticDelta < 1 ||
    franticAfter.lastChainLength <= slowAfter.lastChainLength ||
    franticAfter.nextInterval >= slowAfter.nextInterval
  ) {
    throw new Error(
      `Frantic pace was not materially faster/longer: slow=${JSON.stringify(slowAfter)}, frantic=${JSON.stringify(franticAfter)}`,
    );
  }

  // Firings can stop independently while structural synapses remain visible.
  await firingToggle.click();
  await page.waitForFunction(() => {
    const graph = document.querySelector('.desktop-neuron-layer canvas');
    return (
      graph?.getAttribute('data-firings-enabled') === 'false' &&
      graph?.getAttribute('data-firing-active-pulses') === '0' &&
      graph?.getAttribute('data-synapses-enabled') === 'true' &&
      Number(graph?.getAttribute('data-visible-synapses')) > 0
    );
  });
  const disabled = await readCoverage();
  await page.screenshot({ path: '/tmp/neuron-firing-coverage.png' });

  if (browserErrors.length) {
    throw new Error(`Browser errors: ${browserErrors.join(' | ')}`);
  }

  console.log(JSON.stringify({
    alive,
    static: staticAfter,
    slow: { ...slowAfter, delta: slowDelta },
    frantic: { ...franticAfter, delta: franticDelta },
    disabled,
    screenshot: '/tmp/neuron-firing-coverage.png',
  }, null, 2));
} finally {
  await browser.close();
}
