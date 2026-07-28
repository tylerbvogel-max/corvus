#!/usr/bin/env node

import { chromium } from '@playwright/test';

const base = process.env.CORVUS_UI ?? 'http://127.0.0.1:8004';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.setDefaultTimeout(15_000);
const browserErrors = [];
let graphPayload = null;
let clusterPayload = null;
async function dismissSystemNotice() {
  const acknowledge = page.getByRole('button', { name: 'I Acknowledge' });
  if (await acknowledge.isVisible().catch(() => false)) {
    await acknowledge.click();
    await acknowledge.waitFor({ state: 'hidden' });
  }
}

page.on('pageerror', error => browserErrors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') browserErrors.push(message.text());
});
page.on('response', async response => {
  if (response.url().includes('/neurons/graph-3d?') && response.ok()) {
    graphPayload = await response.json();
  }
  if (response.url().includes('/neurons/clusters?') && response.ok()) {
    clusterPayload = await response.json();
  }
});

try {
  const bannerReady = page.waitForResponse(response =>
    response.url().endsWith('/admin/system-banner'),
  );
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  await bannerReady;
  const acknowledge = page.getByRole('button', { name: 'I Acknowledge' });
  await acknowledge.waitFor({ state: 'visible', timeout: 10_000 }).catch(() => {});
  await dismissSystemNotice();

  const controls = page.getByTestId('desktop-neuron-controls');
  await controls.waitFor({ state: 'visible' });
  const openWindow = page.locator('.app-window-close').first();
  if (await openWindow.isVisible().catch(() => false)) await openWindow.click();
  const canvas = page.locator('.desktop-neuron-layer canvas').first();
  await canvas.waitFor({ state: 'visible' });
  await page.waitForFunction(() => (
    document.querySelector('.desktop-neuron-layer canvas')
      ?.getAttribute('data-replay-trace-count') !== '0'
  ));
  if (!graphPayload || !clusterPayload) throw new Error('Graph payloads were not captured');
  await dismissSystemNotice();

  const contested = graphPayload.neurons.filter(neuron => neuron.health_state === 'contested');
  const active30d = graphPayload.edges.filter(edge => edge.activity_30d > 0);
  const enrichedTraces = graphPayload.replays.traces.filter(trace => (
    typeof trace.query_preview === 'string' && trace.firings?.length > 0
  ));
  if (!contested.length || !active30d.length || !enrichedTraces.length) {
    throw new Error('Graph payload is missing health, recent activity, or enriched trace evidence');
  }

  // Fit View: frame the full organic universe, then prove the same action
  // scopes itself to a later inspection rather than resetting that context.
  const fitButton = page.getByTestId('fit-neuron-view');
  await fitButton.click();
  await page.waitForFunction(expected => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      neuronCanvas?.getAttribute('data-fit-flight-complete') === 'true' &&
      neuronCanvas?.getAttribute('data-fit-scope') === 'universe' &&
      Number(neuronCanvas?.getAttribute('data-fit-visible-nodes')) === expected &&
      Number(neuronCanvas?.getAttribute('data-fit-projected-inside')) === expected
    );
  }, graphPayload.neurons.length);

  // Search & Fly: select a real memory and prove its ego universe opens.
  const searchTarget = graphPayload.neurons.find(neuron => (
    neuron.node_type === 'lesson' && neuron.label.length >= 24
  ));
  if (!searchTarget) throw new Error('No suitable Search & Fly target');
  const searchInput = page.getByTestId('neuron-search-input');
  await searchInput.fill(searchTarget.label);
  const searchResults = page.getByTestId('neuron-search-results');
  await searchResults.waitFor({ state: 'visible' });
  const firstSearchResult = searchResults.getByRole('button').first();
  const firstSearchBox = await firstSearchResult.boundingBox();
  if (firstSearchBox) {
    const intercept = await page.evaluate(({ x, y }) => {
      const element = document.elementFromPoint(x, y);
      return element ? {
        tag: element.tagName,
        className: element.className,
        text: element.textContent?.slice(0, 120),
        html: element.outerHTML.slice(0, 400),
      } : null;
    }, {
      x: firstSearchBox.x + firstSearchBox.width / 2,
      y: firstSearchBox.y + firstSearchBox.height / 2,
    });
    if (!intercept?.html.includes('<button')) {
      throw new Error(`Search result is pointer-obscured: ${JSON.stringify(intercept)}`);
    }
  }
  await firstSearchResult.click();
  const focusDetail = page.getByTestId('neuron-focus-detail');
  await focusDetail.getByText(searchTarget.label, { exact: true }).waitFor({ state: 'visible' });
  await page.waitForTimeout(1_000);
  const fullFitSequence = Number(await canvas.getAttribute('data-fit-sequence'));
  await fitButton.click();
  await page.waitForFunction(previousSequence => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      neuronCanvas?.getAttribute('data-fit-flight-complete') === 'true' &&
      Number(neuronCanvas?.getAttribute('data-fit-sequence')) > previousSequence
    );
  }, fullFitSequence);
  const focusedFitNodes = Number(await canvas.getAttribute('data-fit-visible-nodes'));
  const focusedFitInside = Number(await canvas.getAttribute('data-fit-projected-inside'));
  const focusedFitScope = await canvas.getAttribute('data-fit-scope');
  if (focusedFitScope !== 'focus' || focusedFitInside !== focusedFitNodes) {
    throw new Error(
      `Focused Fit View missed its scope: ${JSON.stringify({
        scope: focusedFitScope,
        visible: focusedFitNodes,
        projectedInside: focusedFitInside,
      })}`,
    );
  }
  if (focusedFitNodes >= graphPayload.neurons.length) {
    throw new Error('Fit View reset the focused universe instead of framing its visible subset');
  }
  await page.screenshot({ path: '/tmp/neuron-search-fly.png' });
  await page.getByTestId('neuron-focus-back').click();

  // Recall Lens: freeze a real query and prove its ordered firings reach the renderer.
  await page.getByTestId('lens-recall').click();
  const traceList = page.getByTestId('recall-trace-list');
  await traceList.waitFor({ state: 'visible' });
  await traceList.getByRole('button').first().click();
  const traceDetail = page.getByTestId('recall-trace-detail');
  await traceDetail.waitFor({ state: 'visible' });
  const inspectedQuery = await canvas.getAttribute('data-inspected-replay-query');
  if (!inspectedQuery) throw new Error('Recall Lens did not publish an inspected query');
  await page.getByTestId('recall-replay-status').getByText(/paused/i).waitFor({ state: 'visible' });
  await page.screenshot({ path: '/tmp/neuron-recall-lens.png' });

  // Pathway Heat: select a real time window and prove used retained edges are coloured.
  await page.getByTestId('lens-heat').click();
  await page.getByTestId('time-30d').click();
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      neuronCanvas?.getAttribute('data-heat-mode') === 'true' &&
      neuronCanvas?.getAttribute('data-heat-window') === '30d' &&
      Number(neuronCanvas?.getAttribute('data-heat-used-edges')) > 0
    );
  });
  const heatSummary = page.getByTestId('pathway-heat-summary');
  await heatSummary.getByText(/used synapses/i).waitFor({ state: 'visible' });
  const heatUsedEdges = Number(await canvas.getAttribute('data-heat-used-edges'));
  await page.screenshot({ path: '/tmp/neuron-pathway-heat.png' });

  // Memory Health: evidence categories are visible and the graph swaps colour language.
  await page.getByTestId('lens-health').click();
  await page.waitForFunction(() => (
    document.querySelector('.desktop-neuron-layer canvas')
      ?.getAttribute('data-health-mode') === 'true'
  ));
  const healthSummary = page.getByTestId('memory-health-summary');
  await healthSummary.getByText('Contested', { exact: true }).waitFor({ state: 'visible' });
  await page.screenshot({ path: '/tmp/neuron-memory-health.png' });
  await page.getByTestId('lens-health').click();

  // Assembly Bridges: Search & Fly into a named engram, then traverse a real bridge.
  const zoneLabel = clusterPayload.clusters.find(cluster => cluster.suggested_label)?.suggested_label;
  if (!zoneLabel) throw new Error('No named assembly available for bridge navigation');
  await searchInput.fill(zoneLabel);
  await searchResults.waitFor({ state: 'visible' });
  await searchResults.getByRole('button').first().click();
  const assemblyInspection = page.getByTestId('assembly-inspection');
  await assemblyInspection.getByText(zoneLabel, { exact: true }).waitFor({ state: 'visible' });
  const bridgeList = page.getByTestId('assembly-bridges');
  await bridgeList.waitFor({ state: 'visible' });
  const beforeBridge = (await assemblyInspection.textContent())?.replace(/\s+/g, ' ').trim();
  await bridgeList.getByRole('button').first().click();
  await page.waitForFunction(previous => {
    const card = document.querySelector('[data-testid="assembly-inspection"]');
    return Boolean(card?.textContent && card.textContent.replace(/\s+/g, ' ').trim() !== previous);
  }, beforeBridge);
  const afterBridge = (await assemblyInspection.textContent())?.replace(/\s+/g, ' ').trim();
  await page.screenshot({ path: '/tmp/neuron-assembly-bridges.png' });

  const unexpected = browserErrors.filter(message =>
    !message.includes('favicon') &&
    !message.includes('ResizeObserver loop') &&
    !(message.includes('fonts.googleapis.com') && message.includes('Content Security Policy')),
  );
  if (unexpected.length) throw new Error(`Browser errors: ${unexpected.join(' | ')}`);

  console.log(JSON.stringify({
    base,
    payload: {
      neurons: graphPayload.neurons.length,
      contested: contested.length,
      active30d: active30d.length,
      enrichedTraces: enrichedTraces.length,
    },
    searchAndFly: searchTarget.label,
    fitView: {
      universeNodes: graphPayload.neurons.length,
      focusedNodes: focusedFitNodes,
    },
    recallQuery: Number(inspectedQuery),
    heatUsedEdges,
    bridgeNavigation: { from: beforeBridge, to: afterBridge },
    screenshots: [
      '/tmp/neuron-search-fly.png',
      '/tmp/neuron-recall-lens.png',
      '/tmp/neuron-pathway-heat.png',
      '/tmp/neuron-memory-health.png',
      '/tmp/neuron-assembly-bridges.png',
    ],
  }, null, 2));
} finally {
  await browser.close();
}
