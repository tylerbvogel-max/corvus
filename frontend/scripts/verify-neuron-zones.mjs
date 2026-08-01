#!/usr/bin/env node

import { chromium } from '@playwright/test';

const base = process.env.CORVUS_UI ?? 'http://127.0.0.1:8004';
const browser = await chromium.launch({ args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1008, height: 896 } });
page.setDefaultTimeout(15_000);
const browserErrors = [];
let clusterPayload = null;
let graphPayload = null;

page.on('pageerror', error => browserErrors.push(error.message));
page.on('console', message => {
  if (message.type() === 'error') browserErrors.push(message.text());
});
page.on('response', async response => {
  try {
    if (response.url().includes('/neurons/clusters?') && response.ok()) {
      const payload = await response.json();
      if (payload?.cluster_count > 0) clusterPayload = payload;
    }
    if (response.url().includes('/neurons/graph-3d?') && response.ok()) {
      const payload = await response.json();
      if (payload?.neurons?.length > 0) graphPayload = payload;
    }
  } catch (error) {
    // A final response can finish while the browser is tearing down. All
    // assertions have already consumed the captured payload by that point.
    if (!page.isClosed()) throw error;
  }
});

try {
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  const acknowledge = page.getByRole('button', { name: 'I Acknowledge' });
  await acknowledge.waitFor({ state: 'visible', timeout: 5_000 }).catch(() => {});
  if (await acknowledge.isVisible().catch(() => false)) {
    await acknowledge.click();
  }

  const controls = page.getByTestId('desktop-neuron-controls');
  await controls.waitFor({ state: 'visible' });
  await page.getByTestId('neuron-layout-toggle').waitFor({ state: 'visible' });
  const radar = page.getByTestId('neuron-radar-control');
  await radar.waitFor({ state: 'visible' });
  await page.getByText(/neurons · .* synapses/).waitFor({ state: 'visible' });
  const openWindow = page.locator('.app-window-close').first();
  if (await openWindow.isVisible().catch(() => false)) {
    await openWindow.click();
  }
  await page.waitForTimeout(1_500);

  const canvas = page.locator('.desktop-neuron-layer canvas').first();
  await canvas.waitFor({ state: 'visible' });
  const canvasSize = await canvas.evaluate(element => ({
    width: element.width,
    height: element.height,
  }));
  if (canvasSize.width < 100 || canvasSize.height < 100) {
    throw new Error(`Neuron canvas is undersized: ${JSON.stringify(canvasSize)}`);
  }

  // Ambient motion combines truthful historical replay with the independent
  // fair structural sweep. Prove the shared renderer produces both a moving
  // pulse and a glowing retained synapse before pausing Motion later.
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return Number(neuronCanvas?.getAttribute('data-replay-trace-count')) > 0;
  });
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      Number(neuronCanvas?.getAttribute('data-replay-active-pulses')) > 0 &&
      Number(neuronCanvas?.getAttribute('data-replay-glowing-edges')) > 0
    );
  }, { timeout: 15_000 });
  const replayRuntime = await canvas.evaluate(element => ({
    traces: Number(element.getAttribute('data-replay-trace-count')),
    segments: Number(element.getAttribute('data-replay-historical-segments')),
    activePulses: Number(element.getAttribute('data-replay-active-pulses')),
    glowingEdges: Number(element.getAttribute('data-replay-glowing-edges')),
    queryId: Number(element.getAttribute('data-last-replay-query')),
  }));
  await page.screenshot({ path: '/tmp/neuron-recall-replay.png' });

  // Organic and Assembly inspection share the same content-bearing hover.
  const bloomButton = page.getByTestId('toggle-bloom');
  const motionButton = page.getByTestId('toggle-motion');
  const synapsesButton = page.getByTestId('toggle-synapses');
  if (
    await bloomButton.getAttribute('aria-pressed') !== 'true' ||
    await motionButton.getAttribute('aria-pressed') !== 'true' ||
    await synapsesButton.getAttribute('aria-pressed') !== 'true'
  ) {
    throw new Error('Bloom, Motion, and Synapses did not initialize as selected');
  }
  await motionButton.click();
  await page.getByTestId('recall-replay-status').getByText(/paused/i).waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return Boolean(
      neuronCanvas?.getAttribute('data-neuron-probe-x') &&
      neuronCanvas?.getAttribute('data-neuron-probe-y'),
    );
  });
  const organicCanvasBox = await canvas.boundingBox();
  const organicNeuronProbe = await canvas.evaluate(element => ({
    x: Number(element.getAttribute('data-neuron-probe-x')),
    y: Number(element.getAttribute('data-neuron-probe-y')),
    id: Number(element.getAttribute('data-neuron-probe-id')),
  }));
  if (!organicCanvasBox) throw new Error('Canvas position unavailable for Organic hover');
  await page.mouse.move(
    organicCanvasBox.x + organicNeuronProbe.x,
    organicCanvasBox.y + organicNeuronProbe.y,
  );
  const organicHoverContent = page.getByTestId('neuron-hover-content');
  await organicHoverContent.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const content = document.querySelector('[data-testid="neuron-hover-content"]');
    return Boolean(content?.textContent && content.textContent !== 'Loading memory…');
  });
  const organicHoverPreview = (await organicHoverContent.textContent())?.trim() || '';
  if (organicHoverPreview.length < 8) {
    throw new Error(`Organic neuron ${organicNeuronProbe.id} did not expose stored content`);
  }
  await page.screenshot({ path: '/tmp/neuron-organic-hover.png' });
  await motionButton.click();
  await page.getByTestId('recall-replay-status').getByText(/live/i).waitFor({ state: 'visible' });

  const controlBox = await controls.boundingBox();
  const radarBox = await radar.boundingBox();
  if (!controlBox || controlBox.width < 260) {
    throw new Error(`Neuron controls are still crushed: ${JSON.stringify(controlBox)}`);
  }
  if (!radarBox || radarBox.width < 220 || radarBox.height < 210) {
    throw new Error(`Radar control is undersized: ${JSON.stringify(radarBox)}`);
  }

  const navLogo = page.locator('.sidebar-pill');
  const [expandedRadius, navLogoRadius] = await Promise.all([
    controls.evaluate(element => getComputedStyle(element).borderRadius),
    navLogo.evaluate(element => getComputedStyle(element).borderRadius),
  ]);
  if (expandedRadius !== navLogoRadius) {
    throw new Error(`Expanded control radius ${expandedRadius} does not match nav logo ${navLogoRadius}`);
  }

  // Collapsed universe controls share the nav logo's footprint and frame.
  const controlsToggle = page.getByTestId('neuron-controls-toggle');
  await controlsToggle.click();
  const compactControlBox = await controls.boundingBox();
  const navLogoBox = await navLogo.boundingBox();
  const compactRadius = await controls.evaluate(element => getComputedStyle(element).borderRadius);
  if (
    !compactControlBox ||
    !navLogoBox ||
    compactControlBox.width !== navLogoBox.width ||
    compactControlBox.height !== navLogoBox.height ||
    compactRadius !== navLogoRadius
  ) {
    throw new Error(
      `Collapsed controls do not match nav logo: controls=${JSON.stringify(compactControlBox)} radius=${compactRadius} nav=${JSON.stringify(navLogoBox)} radius=${navLogoRadius}`,
    );
  }
  await controlsToggle.click();
  await page.getByTestId('neuron-layout-toggle').waitFor({ state: 'visible' });

  // Keyboard: each radar handle remains an accessible range input.
  const neuronLightHandle = page.getByTestId('radar-handle-neuron-light');
  const neuronLightBefore = Number(await neuronLightHandle.getAttribute('aria-valuenow'));
  await neuronLightHandle.focus();
  await page.keyboard.press('ArrowUp');
  const neuronLightAfter = Number(await neuronLightHandle.getAttribute('aria-valuenow'));
  if (!(neuronLightAfter > neuronLightBefore)) {
    throw new Error(`Keyboard radar adjustment failed: ${neuronLightBefore} -> ${neuronLightAfter}`);
  }

  // Pointer: drag repulsion outward along its spoke and prove the live value moves.
  const repulsionHandle = page.getByTestId('radar-handle-repulsion');
  const repulsionBefore = Number(await repulsionHandle.getAttribute('aria-valuenow'));
  const handleBox = await repulsionHandle.boundingBox();
  const svgBox = await page.locator('.neuron-radar').boundingBox();
  if (!handleBox || !svgBox) throw new Error('Radar handle geometry unavailable');
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  const radarCenter = {
    x: svgBox.x + svgBox.width / 2,
    y: svgBox.y + svgBox.height / 2,
  };
  const handleCenter = {
    x: handleBox.x + handleBox.width / 2,
    y: handleBox.y + handleBox.height / 2,
  };
  const handleDistance = Math.hypot(
    handleCenter.x - radarCenter.x,
    handleCenter.y - radarCenter.y,
  ) || 1;
  await page.mouse.move(
    radarCenter.x + ((handleCenter.x - radarCenter.x) / handleDistance) * svgBox.width * 0.29,
    radarCenter.y + ((handleCenter.y - radarCenter.y) / handleDistance) * svgBox.width * 0.29,
  );
  await page.mouse.up();
  const repulsionAfter = Number(await repulsionHandle.getAttribute('aria-valuenow'));
  if (!(repulsionAfter > repulsionBefore)) {
    throw new Error(`Pointer radar adjustment failed: ${repulsionBefore} -> ${repulsionAfter}`);
  }

  // The spider now contains only continuous controls. Its protected inner ring
  // still keeps a minimum-valued handle away from the exact origin.
  await neuronLightHandle.focus();
  await page.keyboard.press('Home');
  const minimumHandleBox = await neuronLightHandle.boundingBox();
  if (!minimumHandleBox) throw new Error('Minimum radar handle is not measurable');
  const minimumHandleCenter = {
    x: minimumHandleBox.x + minimumHandleBox.width / 2,
    y: minimumHandleBox.y + minimumHandleBox.height / 2,
  };
  const zeroRingRadius = Math.hypot(
    minimumHandleCenter.x - radarCenter.x,
    minimumHandleCenter.y - radarCenter.y,
  );
  if (zeroRingRadius < 10) {
    throw new Error(`Minimum handle collapsed at origin: radius=${zeroRingRadius}`);
  }

  await bloomButton.click();
  if (await bloomButton.getAttribute('aria-pressed') !== 'false') {
    throw new Error('Bloom selection button did not switch off');
  }
  await bloomButton.click();
  await synapsesButton.click();
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      neuronCanvas?.getAttribute('data-synapses-enabled') === 'false' &&
      neuronCanvas?.getAttribute('data-visible-synapses') === '0' &&
      neuronCanvas?.getAttribute('data-replay-active-pulses') === '0' &&
      neuronCanvas?.getAttribute('data-replay-glowing-edges') === '0'
    );
  });
  await synapsesButton.click();
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return (
      neuronCanvas?.getAttribute('data-synapses-enabled') === 'true' &&
      Number(neuronCanvas?.getAttribute('data-visible-synapses')) > 0
    );
  });
  await motionButton.click();
  if (
    await bloomButton.getAttribute('aria-pressed') !== 'true' ||
    await motionButton.getAttribute('aria-pressed') !== 'false'
  ) {
    throw new Error('Effect selection buttons did not preserve independent state');
  }
  await page.getByTestId('recall-replay-status').getByText(/paused/i).waitFor({ state: 'visible' });
  await page.screenshot({ path: '/tmp/neuron-organic.png' });

  const zonesButton = page.getByTestId('layout-zones');
  await zonesButton.click();
  const assemblyOverview = page.getByText(/named assemblies · \d+\/\d+ mapped/);
  await assemblyOverview.waitFor({ state: 'visible' });
  const assemblyOverviewText = await assemblyOverview.textContent();
  const namedAssemblyCount = Number(assemblyOverviewText?.match(/^(\d+) named assemblies/)?.[1]);
  if (!namedAssemblyCount) {
    throw new Error(`Could not read the promoted assembly count: ${assemblyOverviewText}`);
  }
  if (await zonesButton.getAttribute('aria-pressed') !== 'true') {
    throw new Error('Assemblies mode did not become active');
  }
  await page.waitForTimeout(4_000);
  if (await page.locator('[data-assembly-chip]').count()) {
    throw new Error('The redundant bottom assembly labels are still present');
  }
  await page.screenshot({ path: '/tmp/neuron-zones.png' });

  // Use the live camera projection published by the renderer to click directly
  // on an assembly volume. This exercises the canvas raycaster, not a DOM proxy.
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return Boolean(
      neuronCanvas?.getAttribute('data-assembly-probe-x') &&
      neuronCanvas?.getAttribute('data-assembly-probe-y'),
    );
  });
  const assemblyProbe = await canvas.evaluate(element => ({
    x: Number(element.getAttribute('data-assembly-probe-x')),
    y: Number(element.getAttribute('data-assembly-probe-y')),
    targets: Number(element.getAttribute('data-assembly-hit-targets')),
  }));
  if (assemblyProbe.targets !== namedAssemblyCount) {
    throw new Error(
      `Assembly hit volumes do not match promoted assemblies: ${assemblyProbe.targets} vs ${namedAssemblyCount}`,
    );
  }
  const liveCanvasBox = await canvas.boundingBox();
  if (!liveCanvasBox) throw new Error('Canvas position unavailable for assembly click');
  await page.mouse.click(
    liveCanvasBox.x + assemblyProbe.x,
    liveCanvasBox.y + assemblyProbe.y,
  );
  const assemblyInspection = page.getByTestId('assembly-inspection');
  await assemblyInspection.waitFor({ state: 'visible' });
  await assemblyInspection.getByText(/neurons · \d+ internal synapses/).waitFor({ state: 'visible' });
  const inspectedAssembly = (await assemblyInspection.textContent())?.replace(/\s+/g, ' ').trim() || 'unknown';

  // Once isolated, hover a live neuron and prove its stored body is fetched.
  await page.waitForFunction(() => {
    const neuronCanvas = document.querySelector('.desktop-neuron-layer canvas');
    return Boolean(
      neuronCanvas?.getAttribute('data-neuron-probe-x') &&
      neuronCanvas?.getAttribute('data-neuron-probe-y'),
    );
  });
  const neuronProbe = await canvas.evaluate(element => ({
    x: Number(element.getAttribute('data-neuron-probe-x')),
    y: Number(element.getAttribute('data-neuron-probe-y')),
    id: Number(element.getAttribute('data-neuron-probe-id')),
  }));
  await page.mouse.move(
    liveCanvasBox.x + neuronProbe.x,
    liveCanvasBox.y + neuronProbe.y,
  );
  const hoverContent = page.getByTestId('neuron-hover-content');
  await hoverContent.waitFor({ state: 'visible' });
  await page.waitForFunction(() => {
    const content = document.querySelector('[data-testid="neuron-hover-content"]');
    return Boolean(content?.textContent && content.textContent !== 'Loading memory…');
  });
  const hoverPreview = (await hoverContent.textContent())?.trim() || '';
  if (hoverPreview.length < 8) {
    throw new Error(`Neuron ${neuronProbe.id} did not expose a meaningful content preview`);
  }
  await page.waitForTimeout(1_500);
  await page.screenshot({ path: '/tmp/neuron-assembly-inspection.png' });
  await page.getByTestId('assembly-back').click();
  await page.getByText(/named assemblies · \d+\/\d+ mapped/).waitFor({ state: 'visible' });

  const organicButton = page.getByTestId('layout-organic');
  await organicButton.click();
  await page.getByText(/neurons · .* synapses/).waitFor({ state: 'visible' });
  if (await organicButton.getAttribute('aria-pressed') !== 'true') {
    throw new Error('Organic mode did not reactivate');
  }

  // The optional cartographic overlay must never become a dependency of the
  // original universe. Re-run startup with an empty community response and
  // prove Organic remains live while Assemblies is explicitly unavailable.
  await page.route('**/neurons/clusters?**', route => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ cluster_count: 0, clusters: [] }),
  }));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByTestId('desktop-neuron-controls').waitFor({ state: 'visible' });
  await page.getByText(/neurons · .* synapses/).waitFor({ state: 'visible' });
  if (!await page.getByTestId('layout-zones').isDisabled()) {
    throw new Error('Assemblies should be disabled when no community map is available');
  }

  const unexpected = browserErrors.filter(message =>
    !message.includes('favicon') &&
    !message.includes('ResizeObserver loop') &&
    !message.includes('THREE.WebGLRenderer') &&
    !(message.includes('fonts.googleapis.com') && message.includes('Content Security Policy')),
  );
  if (unexpected.length) {
    throw new Error(`Browser errors: ${unexpected.join(' | ')}`);
  }
  if (!clusterPayload?.cluster_count) {
    throw new Error('No associative communities were returned');
  }
  if (!graphPayload?.replays?.traces?.length) {
    throw new Error('Graph payload contained no historical replay traces');
  }
  if (!graphPayload.replays.basis.includes('historical co-activation')) {
    throw new Error(`Replay provenance is ambiguous: ${graphPayload.replays.basis}`);
  }
  const graphEdgeKeys = new Set(graphPayload.edges.map(edge =>
    `${Math.min(edge.source, edge.target)}:${Math.max(edge.source, edge.target)}`));
  const replaySegments = graphPayload.replays.traces.flatMap(trace => trace.segments);
  const orphanedReplay = replaySegments.find(segment =>
    !graphEdgeKeys.has(`${Math.min(segment.source, segment.target)}:${Math.max(segment.source, segment.target)}`));
  if (orphanedReplay) {
    throw new Error(`Replay segment is not a retained visible synapse: ${JSON.stringify(orphanedReplay)}`);
  }
  const spreadTraceCount = graphPayload.replays.traces.filter(trace => trace.spread_derived).length;
  if (!spreadTraceCount) {
    throw new Error('Historical replay sample contained no spread-assisted trace');
  }

  console.log(JSON.stringify({
    base,
    communities: clusterPayload.cluster_count,
    largestZone: {
      label: clusterPayload.clusters[0]?.suggested_label,
      neurons: clusterPayload.clusters[0]?.member_count,
      examples: clusterPayload.clusters[0]?.representative_labels,
    },
    canvas: canvasSize,
    controls: {
      width: Math.round(controlBox.width),
      radar: `${Math.round(radarBox.width)}x${Math.round(radarBox.height)}`,
      keyboardNeuronLight: `${neuronLightBefore} -> ${neuronLightAfter}`,
      pointerRepulsion: `${repulsionBefore} -> ${repulsionAfter}`,
      zeroRingRadius: `${zeroRingRadius.toFixed(1)}px`,
      bloomButton: 'on -> off -> on',
      motionButton: 'on -> off',
      synapsesButton: 'on -> off -> on',
    },
    recallReplay: {
      basis: graphPayload.replays.basis,
      traces: graphPayload.replays.traces.length,
      segments: replaySegments.length,
      spreadTraces: spreadTraceCount,
      runtime: replayRuntime,
    },
    organicHover: {
      neuron: organicNeuronProbe.id,
      preview: organicHoverPreview.slice(0, 120),
    },
    assemblyInspection: {
      targetVolumes: assemblyProbe.targets,
      selection: inspectedAssembly,
      hoveredNeuron: neuronProbe.id,
      hoverPreview: hoverPreview.slice(0, 120),
    },
    toggled: ['organic', 'assemblies', 'assembly-volume inspection', 'organic'],
    fallback: 'organic remained live with zero communities',
    screenshots: [
      '/tmp/neuron-organic.png',
      '/tmp/neuron-organic-hover.png',
      '/tmp/neuron-recall-replay.png',
      '/tmp/neuron-zones.png',
      '/tmp/neuron-assembly-inspection.png',
    ],
  }, null, 2));
} finally {
  await browser.close();
}
