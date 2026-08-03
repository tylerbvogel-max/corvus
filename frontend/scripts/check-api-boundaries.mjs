#!/usr/bin/env node
/* Frontend feature boundaries must agree with backend capability vocabulary.
 *
 * Record durability-frontend-contracts (05), criterion 7. The backend composes
 * per-tenant by capability; the client only tells an UNGRANTED route apart from
 * a FAILED one because it routes through http.ts, which consults the generated
 * ownership table. A component calling `fetch()` directly loses that
 * distinction and reports an absent feature as an error — the same class as the
 * AC-8 banner vanishing silently.
 *
 * Three rules, all mechanical:
 *   1. An adapter calls only routes its own capability owns.
 *   2. A capability with browser-reachable routes has an adapter.
 *   3. Components do not call contract routes directly.
 *
 * Exits non-zero with the offending file and route. */

import { readdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFile(join(root, p), 'utf8');

const failures = [];
const ok = (m) => console.log(`  ok   ${m}`);
const fail = (m) => { failures.push(m); console.error(`  FAIL ${m}`); };

// ── the generated ownership table is the single source of truth ──
const contract = await read('src/contracts/capabilities.ts');
const owners = new Map();
for (const [, path, capability] of contract.matchAll(
  /path:\s*'([^']+)',\s*capability:\s*'([a-z_]+)'/g)) {
  owners.set(path, capability);
}
if (owners.size === 0) {
  fail('could not parse the generated ownership table (did its shape change?)');
}

/* Match a route only where it is actually addressed, never as a prefix.
 * A naive substring test reports `/chat/sessions?limit=` as a use of `/chat`,
 * which is how an earlier hand-check invented a boundary violation that did
 * not exist. The next character must end the path: a quote, a backtick, a
 * query, or a template hole. */
const addresses = (body, path) => {
  const escaped = path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`['\`"]${escaped}(?:['\`"?]|\\$\\{)`).test(body);
};

// ── rule 1: an adapter calls only its own capability's routes ──
const adapterFiles = (await readdir(join(root, 'src/api'))).filter(f => f.endsWith('.ts'));
const INFRASTRUCTURE = new Set(['http']);          // transport, owns no capability
const adapters = new Set();

for (const file of adapterFiles) {
  const name = file.replace(/\.ts$/, '');
  if (INFRASTRUCTURE.has(name)) continue;
  adapters.add(name);
  const body = await read(join('src/api', file));
  const foreign = [];
  for (const [path, capability] of owners) {
    if (capability !== name && addresses(body, path)) foreign.push(`${path} (${capability})`);
  }
  if (foreign.length) fail(`api/${file} calls routes it does not own: ${foreign.join(', ')}`);
  else ok(`api/${file} stays inside '${name}'`);
}

// ── rule 2: every capability with browser-reachable routes has an adapter ──
const reachable = new Set();
for (const [, capability] of owners) reachable.add(capability);

const componentFiles = [];
for (const dir of ['src/components', 'src']) {
  for (const entry of await readdir(join(root, dir), { withFileTypes: true })) {
    if (entry.isFile() && /\.tsx?$/.test(entry.name)) componentFiles.push(join(dir, entry.name));
  }
}
const componentBodies = new Map();
for (const file of componentFiles) {
  if (file.includes('/api/') || file.includes('/contracts/')) continue;
  componentBodies.set(file, await read(file));
}

for (const capability of [...reachable].sort()) {
  if (adapters.has(capability)) { ok(`capability '${capability}' has an adapter`); continue; }
  // No adapter is fine ONLY if nothing in the browser reaches its routes.
  const used = [];
  for (const [path, owner] of owners) {
    if (owner !== capability) continue;
    for (const [file, body] of componentBodies) {
      if (addresses(body, path)) { used.push(`${path} <- ${file}`); break; }
    }
  }
  if (used.length) {
    fail(`capability '${capability}' has no adapter but its routes are called: ${used.slice(0, 3).join('; ')}`);
  } else {
    ok(`capability '${capability}' has no adapter and no browser caller`);
  }
}

// ── rule 3: components do not call contract routes directly ──
/* auth.ts is the one legitimate exception and is named rather than ignored:
 * checkAccess() must read the raw HTTP status to tell 401 (invalid key) from
 * ok (open or valid), and json<T>() deliberately throws on non-ok, abstracting
 * away the very distinction the probe exists to make. */
const RAW_STATUS_EXEMPT = new Set(['src/auth.ts']);

for (const [file, body] of componentBodies) {
  if (RAW_STATUS_EXEMPT.has(file)) { ok(`${file} exempt: needs raw status for the 401 probe`); continue; }
  const direct = [];
  for (const [path] of owners) {
    if (!addresses(body, path)) continue;
    // Referencing the path in prose or a link is fine; calling fetch() is not.
    if (/\bfetch\s*\(/.test(body)) direct.push(path);
  }
  if (direct.length) {
    fail(`${file} calls ${direct.slice(0, 3).join(', ')} with bare fetch() — route it through an api/ adapter so composed-away is distinguishable from failed`);
  }
}
if (![...componentBodies].some(([, b]) => /\bfetch\s*\(/.test(b))) {
  ok('no component calls a contract route with bare fetch()');
}

if (failures.length) {
  console.error(`\n${failures.length} boundary violation(s) — frontend and backend vocabulary disagree.`);
  process.exit(1);
}
console.log(`\n  boundaries agree — ${adapters.size} adapters, ${owners.size} owned routes, ${reachable.size} capabilities`);
