import fixturesData from './fixtures.json';

/* ═══════════════════════════════════════════════════════════════════
   Seeded demo mode — a fully static, backend-less build for public
   demonstrations (Render static site).

   How it works: main.tsx installs this shim (only when built with
   VITE_DEMO=1) before the app mounts. It monkey-patches window.fetch:

   - GET requests are answered from fixtures.json — a snapshot of real
     backend responses captured by `npm run demo:capture` against the
     local backend. Paths without a fixture return 404, which pages
     surface through their normal error/empty states.
   - The chat works live-ish: /query/stream returns a synthetic SSE
     stream (staged pipeline progress, then a canned grounded answer
     with real captured neuron scores, cycling through demoAnswers).
   - Chat session writes succeed benignly so the send flow completes;
     every other mutation returns 403 "read-only demo".

   Publishing workflow (see render.yaml):
     1. npm run demo:capture   (local backend running; review the JSON!)
     2. npm run build:demo     (sanity-check locally: npx vite preview)
     3. commit, then push main to the `demo` branch — Render redeploys.
   ═══════════════════════════════════════════════════════════════════ */

interface DemoAnswer {
  text: string;
  neuron_scores: unknown[];
  neurons_activated: number;
  citation_map?: Record<string, unknown>;
}

const fixtures: Record<string, unknown> =
  (fixturesData as { fixtures?: Record<string, unknown> }).fixtures ?? {};
const demoAnswers: DemoAnswer[] =
  (fixturesData as { demoAnswers?: DemoAnswer[] }).demoAnswers ?? [];

let answerIdx = 0;
let demoSessionSeq = 90000;

// Must mirror PIPELINE_STAGE_LABELS keys in HomePage.tsx so every row of
// the pipeline indicator animates during the fake stream.
const STAGES = [
  'input_guard', 'structural_resolve', 'embed_query', 'classify',
  'semantic_prefilter', 'score_neurons', 'spread_activation',
  'assemble_prompt', 'execute_llm', 'output_checks',
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function nextAnswer(): DemoAnswer {
  if (demoAnswers.length === 0) {
    return {
      text: 'This is a static demo build with no canned answers captured. Run `npm run demo:capture` against a local backend with chat history, then rebuild.',
      neuron_scores: [],
      neurons_activated: 0,
    };
  }
  const a = demoAnswers[answerIdx % demoAnswers.length];
  answerIdx++;
  return a;
}

function queryStreamResponse(): Response {
  const answer = nextAnswer();
  const result = {
    query_id: 900000 + answerIdx,
    llm_session_id: null,
    context_reused: false,
    classify_input_tokens: 0,
    classify_output_tokens: 0,
    total_cost: 0,
    neurons_activated: answer.neurons_activated,
    neuron_scores: answer.neuron_scores,
    citation_map: answer.citation_map,
    slots: [{
      response: answer.text,
      input_tokens: 2400,
      output_tokens: 380,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    }],
  };
  const enc = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: string, data: unknown) =>
        controller.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      for (const stage of STAGES) {
        send('stage', { stage, status: 'active' });
        await new Promise(r => setTimeout(r, 110));
        send('stage', { stage, status: 'done', duration_ms: Math.round(40 + Math.random() * 320) });
      }
      send('result', result);
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

export function installDemoShim(): void {
  const realFetch = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const u = new URL(raw, window.location.origin);
    // Anything cross-origin passes through untouched.
    if (u.origin !== window.location.origin) return realFetch(input as RequestInfo, init);

    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const path = u.pathname;

    if (method === 'GET') {
      const hit = fixtures[path + u.search] ?? fixtures[path];
      if (hit !== undefined) return jsonResponse(hit);
      return jsonResponse({ detail: 'Not included in this demo build.' }, 404);
    }

    // Chat flow — must succeed end-to-end.
    if (path === '/query/stream') return queryStreamResponse();
    if (path === '/chat') {
      const a = nextAnswer();
      return jsonResponse({ response: a.text, model: 'demo', input_tokens: 1200, output_tokens: 300, cost_usd: 0 });
    }
    if (path === '/chat/sessions' && method === 'POST') {
      demoSessionSeq++;
      const now = new Date().toISOString();
      return jsonResponse({ id: demoSessionSeq, title: 'Demo conversation', created_at: now, updated_at: now });
    }
    if (/^\/chat\/sessions\/\d+\/messages$/.test(path)) return jsonResponse({ ok: true });
    if (/^\/chat\/sessions\/\d+\/generate-title$/.test(path)) return jsonResponse({ title: 'Demo conversation' });
    if (/^\/chat\/sessions\/\d+$/.test(path)) return jsonResponse({ ok: true }); // rename / delete
    if (/^\/query\/\d+\/followups$/.test(path)) return jsonResponse({ query_id: 0, suggestions: [], cost_usd: 0 });
    if (/^\/query\/\d+\/rate$/.test(path)) return jsonResponse({ ok: true });

    // Everything else is a mutation of graph state — refuse politely.
    return jsonResponse({ detail: 'Read-only demo — this action is disabled here.' }, 403);
  };

  console.info('[corvus-demo] Demo mode: canned fixtures, no backend attached.');
}
