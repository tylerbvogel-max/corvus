/**
 * Verification #6 for durability-tenant-composition:
 * frontend navigation must not advertise a capability the backend profile
 * did not compose.
 *
 * The check stubs /tenant in the browser rather than booting a second backend.
 * That is deliberate: the only reduced profile that exists on disk is
 * corvus-locomo, and starting a backend against corvus_locomo would run seed
 * steps into the frozen LoCoMo benchmark corpus. Stubbing the response
 * exercises the exact code path the real reduced profile would drive —
 * buildNavGroups reads tenantConfig.capabilities and nothing else — with no
 * database risk.
 *
 * Run against the backend-served SPA (the backend mounts frontend/dist):
 *   node frontend/scripts/verify_capability_nav.mjs [baseUrl]
 */

import { chromium } from '@playwright/test';

const BASE = process.argv[2] ?? 'http://localhost:8005';

const FULL = [
  'memory', 'knowledge_graph', 'ingestion', 'governance',
  'evaluation', 'compliance', 'operator', 'external_api',
];
const REDUCED = ['memory', 'knowledge_graph'];

// Nav labels and the capability each one's pages depend on.
const OPERATOR_ONLY = ['Roadmap Ledgers', 'Architecture'];
const GOVERNANCE_ONLY = ['Proposal Queue', 'Inbox', 'Refinements'];
// Only 'Performance', not 'Eval Runs': a second, older filter (the
// memory_surface hidden-set in buildNavGroups) already removes Eval Runs,
// Evaluation, Quality, and Fairness on memory tenants regardless of
// capability. That legacy mechanism has NOT been folded into the capability
// model yet — see the composition record's remaining work.
const EVALUATION_ONLY = ['Performance'];
const MEMORY_ITEMS = ['Pallium', 'Skills'];

async function navLabels(page, capabilities) {
  await page.route('**/tenant', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        tenant_id: 'corvus-mind',
        display_name: 'Corvus Mind',
        description: 'capability nav verification',
        memory_surface: true,
        capabilities,
        disabled_capabilities: FULL.filter(c => !capabilities.includes(c)),
      }),
    });
  });
  // Start with the nav expanded; collapsed state is persisted per browser.
  await page.addInitScript(() => localStorage.setItem('corvus-nav-collapsed', '0'));
  // Not networkidle: the ASCII wake canvas and stat polling never go idle.
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.perch-mode-btn', { timeout: 20000 });
  await page.waitForTimeout(2000);  // let the tenant fetch resolve into nav state

  // Open every group's flyout and collect the item labels it renders.
  //
  // Getting here took three tries, so the reasoning is worth keeping: a real
  // click is intercepted by the full-page ASCII wake canvas, and a forced
  // click lands on that canvas — which the outside-click handler reads as
  // "clicked outside the nav" and immediately closes the flyout. Dispatching
  // `mouseenter` does nothing either, because React synthesizes onMouseEnter
  // from delegated mouseover/mouseout rather than listening for it directly.
  // `mouseover` is the event the component actually receives.
  const labels = new Set();
  const groupCount = (await page.$$('.perch-mode')).length;
  if (groupCount === 0) throw new Error('no nav groups rendered; the page did not load');
  for (let i = 0; i < groupCount; i++) {
    const groups = await page.$$('.perch-mode');
    await groups[i].dispatchEvent('mouseover');
    await page.waitForTimeout(250);
    for (const item of await page.$$('.perch-flyout-item')) {
      const text = (await item.innerText()).trim().split('\n')[0].trim();
      if (text) labels.add(text);
    }
  }
  return labels;
}

const problems = [];

function expectPresent(labels, names, context) {
  for (const name of names) {
    if (!labels.has(name)) problems.push(`${context}: expected "${name}" to be present`);
  }
}

function expectAbsent(labels, names, context) {
  for (const name of names) {
    if (labels.has(name)) {
      problems.push(`${context}: "${name}" is advertised but its capability is not composed`);
    }
  }
}

const browser = await chromium.launch();
try {
  const full = await navLabels(await browser.newPage(), FULL);
  console.log(`full profile      → ${full.size} nav items`);

  // Baseline sanity: absence only means something if the item exists when the
  // capability IS granted. Without this, every assertion below could pass by
  // the label simply never rendering.
  expectPresent(full, [...OPERATOR_ONLY, ...GOVERNANCE_ONLY, ...EVALUATION_ONLY, ...MEMORY_ITEMS],
    'full profile');

  const reduced = await navLabels(await browser.newPage(), REDUCED);
  console.log(`reduced profile   → ${reduced.size} nav items`);

  expectAbsent(reduced, OPERATOR_ONLY, 'reduced profile');
  expectAbsent(reduced, GOVERNANCE_ONLY, 'reduced profile');
  expectAbsent(reduced, EVALUATION_ONLY, 'reduced profile');
  expectPresent(reduced, MEMORY_ITEMS, 'reduced profile');

  if (reduced.size >= full.size) {
    problems.push(`reduced profile did not shrink the nav (${reduced.size} vs ${full.size})`);
  }

  const removed = [...full].filter(l => !reduced.has(l));
  console.log(`removed by profile: ${removed.join(', ') || '(none)'}`);
} finally {
  await browser.close();
}

if (problems.length) {
  console.error('\nFAIL');
  for (const p of problems) console.error(`  - ${p}`);
  process.exit(1);
}
console.log('\nPASS: navigation advertises only composed capabilities');
