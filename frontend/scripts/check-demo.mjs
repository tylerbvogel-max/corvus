#!/usr/bin/env node
/* Contract checks for the seeded demo — run before publishing (part of
   `npm run demo:publish`). The demo shim mirrors a handful of contracts
   in the live app; development can drift them without breaking the
   normal build, so this catches the drift mechanically:

     1. shim STAGES must equal HomePage's PIPELINE_STAGE_LABELS keys
     2. endpoints the shim handles must still exist in the app source
     3. fixtures.json must be fresh-ish and materially complete

   Exits non-zero with a list of failures. Add checks here whenever the
   shim starts mirroring a new contract. */

import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFile(join(root, p), 'utf8');

const failures = [];
const ok = (msg) => console.log(`  ok   ${msg}`);
const fail = (msg) => { failures.push(msg); console.error(`  FAIL ${msg}`); };

// ── 1. Pipeline stage keys: shim vs HomePage ──
const shimSrc = await read('src/demo/shim.ts');
const homeSrc = await read('src/components/HomePage.tsx');

const shimStages = [...(shimSrc.match(/const STAGES = \[([\s\S]*?)\]/)?.[1] ?? '')
  .matchAll(/'([a-z_]+)'/g)].map(m => m[1]);
const labelBlock = homeSrc.match(/const PIPELINE_STAGE_LABELS[^{]*\{([\s\S]*?)\n\};/)?.[1] ?? '';
const homeStages = [...labelBlock.matchAll(/^  ([a-z_]+): \{/gm)].map(m => m[1]);

if (shimStages.length === 0 || homeStages.length === 0) {
  fail('could not parse stage lists (shim STAGES or PIPELINE_STAGE_LABELS moved?)');
} else if (JSON.stringify(shimStages) !== JSON.stringify(homeStages)) {
  fail(`stage mismatch — shim: [${shimStages}] vs HomePage: [${homeStages}]`);
} else {
  ok(`pipeline stages match (${shimStages.length})`);
}

// ── 2. Endpoints the shim handles still exist in app source ──
// src/api.ts was decomposed into capability-owned adapters by
// durability-frontend-contracts; this check read the old monolith and had been
// dying on ENOENT ever since — a contract check that cannot run is worse than
// none, because it reads as a passing gate. Concatenate the adapters instead,
// and glob rather than list them so the next adapter is covered automatically.
const apiDir = join(root, 'src/api');
const adapters = (await readdir(apiDir)).filter((f) => f.endsWith('.ts'));
const apiSrc = (await Promise.all([
  ...adapters.map((f) => read(join('src/api', f))),
  read('src/auth.ts'),
  read('src/config.ts'),
])).join('\n');
const SHIM_ENDPOINTS = [
  '/query/stream', '/chat/sessions', "'/chat'", '/neurons/stats',
  '/tenant', '/models', 'followups', 'rate', 'generate-title',
];
for (const ep of SHIM_ENDPOINTS) {
  if (apiSrc.includes(ep)) ok(`endpoint referenced in app: ${ep}`);
  else fail(`shim handles ${ep} but the app source no longer references it — update shim.ts`);
}

// ── 3. Fixtures: complete and not stale ──
let fx;
try {
  fx = JSON.parse(await read('src/demo/fixtures.json'));
} catch {
  fail('src/demo/fixtures.json missing or unparseable — run npm run demo:capture');
}
if (fx) {
  for (const p of ['/neurons/stats', '/tenant', '/chat/sessions']) {
    if (fx.fixtures?.[p] !== undefined) ok(`fixture present: ${p}`);
    else fail(`fixture missing: ${p} (capture failed or endpoint renamed)`);
  }
  const sessions = Object.keys(fx.fixtures ?? {}).filter(k => /^\/chat\/sessions\/\d+$/.test(k));
  sessions.length > 0 ? ok(`${sessions.length} session details captured`) : fail('no session details captured');
  (fx.demoAnswers?.length ?? 0) > 0 ? ok(`${fx.demoAnswers.length} demo answers`) : fail('no demo answers — replay chat will be empty');
  (fx.neuronPool?.length ?? 0) >= 20 ? ok(`${fx.neuronPool.length} neurons in retrieval pool`) : fail(`neuron pool too small (${fx.neuronPool?.length ?? 0}) — BYOK grounding will be weak`);
  const ageDays = (Date.now() - Date.parse(fx.capturedAt ?? 0)) / 86_400_000;
  if (Number.isNaN(ageDays)) fail('fixtures have no capturedAt stamp');
  else if (ageDays > 60) fail(`fixtures are ${Math.round(ageDays)} days old — recapture before publishing`);
  else ok(`fixtures captured ${ageDays < 1 ? 'today' : Math.round(ageDays) + ' days ago'}`);
}

if (failures.length) {
  console.error(`\n${failures.length} demo contract check(s) failed — fix before publishing.`);
  process.exit(1);
}
console.log('\nAll demo contract checks passed.');
