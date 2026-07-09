#!/usr/bin/env node
/* Captures a snapshot of the local backend's GET responses into
   src/demo/fixtures.json — the seed data for the static demo build.

   Usage (backend running locally):
     CORVUS_API=http://localhost:8002 npm run demo:capture

   Then REVIEW the generated JSON before publishing: everything in it
   ships verbatim inside the public demo bundle. Keep the source tenant
   synthetic (corvus-aero demo corpus), never real company data.

   Add paths to GET_PATHS as more windows should work in the demo. */

import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.env.CORVUS_API ?? 'http://localhost:8002';
const KEY = process.env.CORVUS_ACCESS_KEY; // optional, if the gate is on
const OUT = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'demo', 'fixtures.json');

const MAX_SESSIONS = 8;   // most-recent chat sessions to include
const MAX_ANSWERS = 4;    // canned grounded answers for the demo chat

const GET_PATHS = [
  '/neurons/stats',        // also serves as the auth-gate probe → demo is "open"
  '/tenant',
  '/tenants',
  // '/models' is intentionally NOT captured: the shim serves a demo-aware
  // roster (replay entry, or the visitor's BYOK model) instead of the dev
  // environment's model list.
  '/chat/sessions',
  '/admin/proposals/stats',
];

const headers = KEY ? { Authorization: `Bearer ${KEY}` } : {};

async function grab(path) {
  try {
    const res = await fetch(BASE + path, { headers });
    if (!res.ok) {
      console.warn(`  skip ${path} (${res.status})`);
      return undefined;
    }
    return await res.json();
  } catch (e) {
    console.warn(`  skip ${path} (${e.message})`);
    return undefined;
  }
}

const fixtures = {};
console.log(`Capturing from ${BASE} ...`);
for (const path of GET_PATHS) {
  const body = await grab(path);
  if (body !== undefined) {
    fixtures[path] = body;
    console.log(`  ok   ${path}`);
  }
}

// Session details (feed both the Chat History window and canned answers)
const demoAnswers = [];
const sessions = Array.isArray(fixtures['/chat/sessions']) ? fixtures['/chat/sessions'] : [];
for (const s of sessions.slice(0, MAX_SESSIONS)) {
  const detail = await grab(`/chat/sessions/${s.id}`);
  if (detail === undefined) continue;
  fixtures[`/chat/sessions/${s.id}`] = detail;
  console.log(`  ok   /chat/sessions/${s.id}`);
  if (demoAnswers.length < MAX_ANSWERS) {
    for (const m of detail.messages ?? []) {
      if (m.role === 'assistant' && Array.isArray(m.neuron_scores) && m.neuron_scores.length > 0) {
        demoAnswers.push({
          text: m.text,
          neuron_scores: m.neuron_scores,
          neurons_activated: m.neurons_activated ?? m.neuron_scores.length,
        });
        break; // one answer per session keeps the rotation varied
      }
    }
  }
}
// Trim the visible session list to what we actually captured
if (sessions.length > MAX_SESSIONS) fixtures['/chat/sessions'] = sessions.slice(0, MAX_SESSIONS);

// Neuron pool for BYOK retrieval: every neuron the captured sessions
// activated, deduped. label+summary become the grounding text; the full
// score objects double as Neuron Graph payloads for live answers.
const neuronPool = [];
const seen = new Set();
for (const key of Object.keys(fixtures)) {
  if (!/^\/chat\/sessions\/\d+$/.test(key)) continue;
  for (const m of fixtures[key].messages ?? []) {
    for (const ns of m.neuron_scores ?? []) {
      if (ns.neuron_id == null || seen.has(ns.neuron_id)) continue;
      seen.add(ns.neuron_id);
      neuronPool.push(ns);
    }
  }
}

const out = { capturedAt: new Date().toISOString(), source: BASE, fixtures, demoAnswers, neuronPool };
await writeFile(OUT, JSON.stringify(out, null, 2));
console.log(`\nWrote ${OUT}`);
console.log(`  fixtures: ${Object.keys(fixtures).length} paths, demo answers: ${demoAnswers.length}, neuron pool: ${neuronPool.length}`);
console.log('Review the file before publishing — its contents ship in the public bundle.');
