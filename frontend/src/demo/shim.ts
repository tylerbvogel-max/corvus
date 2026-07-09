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
   - The chat has two modes:
       REPLAY (default): /query/stream returns a synthetic SSE stream —
       staged pipeline progress, then a canned grounded answer with
       real captured neuron scores, cycling through demoAnswers.
       BYOK (visitor-configured via the key wizard): the question is
       matched client-side against the captured neuron pool, the top
       excerpts are sent with the question DIRECTLY from the visitor's
       browser to their chosen LLM provider using THEIR key (stored in
       their localStorage only — it never touches our host), and the
       matched neurons feed the Neuron Graph. See DemoKeyWizard.tsx.
   - Chat session writes succeed benignly so the send flow completes;
     every other mutation returns 403 "read-only demo".

   Publishing workflow (see render.yaml):
     1. npm run demo:capture   (local backend running; review the JSON!)
     2. npm run build:demo     (sanity-check locally: npx vite preview)
     3. commit, then push main to the `demo` branch — Render redeploys.
   ═══════════════════════════════════════════════════════════════════ */

interface DemoAnswer {
  text: string;
  neuron_scores: NeuronScoreLike[];
  neurons_activated: number;
  citation_map?: Record<string, unknown>;
}

interface NeuronScoreLike {
  neuron_id: number;
  combined: number;
  label: string | null;
  department: string | null;
  summary: string | null;
  [k: string]: unknown;
}

const fixtures: Record<string, unknown> =
  (fixturesData as { fixtures?: Record<string, unknown> }).fixtures ?? {};
const demoAnswers: DemoAnswer[] =
  (fixturesData as { demoAnswers?: DemoAnswer[] }).demoAnswers ?? [];
const neuronPool: NeuronScoreLike[] =
  (fixturesData as { neuronPool?: NeuronScoreLike[] }).neuronPool ?? [];

let answerIdx = 0;
let querySeq = 0;
let demoSessionSeq = 90000;

// ── BYOK: visitor-supplied provider key (their browser, their quota) ──

export interface DemoLLMConfig {
  provider: 'openrouter' | 'anthropic' | 'gemini' | 'groq';
  key: string;
  model: string;
}

const LLM_STORE_KEY = 'corvus-demo-llm';
export const DEMO_LLM_CHANGED_EVENT = 'corvus-demo-llm-changed';

export const PROVIDER_INFO: Record<DemoLLMConfig['provider'], {
  label: string;
  defaultModel: string;
  keyHint: string;
}> = {
  openrouter: {
    label: 'OpenRouter',
    defaultModel: 'nvidia/llama-3.1-nemotron-70b-instruct:free',
    keyHint: 'sk-or-… (openrouter.ai/keys — ":free" models cost nothing)',
  },
  gemini: {
    label: 'Google Gemini',
    defaultModel: 'gemini-2.5-flash',
    keyHint: 'AIza… (aistudio.google.com — free tier)',
  },
  groq: {
    label: 'Groq',
    defaultModel: 'llama-3.3-70b-versatile',
    keyHint: 'gsk_… (console.groq.com — free tier)',
  },
  anthropic: {
    label: 'Anthropic',
    defaultModel: 'claude-haiku-4-5-20251001',
    keyHint: 'sk-ant-… (console.anthropic.com — paid key)',
  },
};

export function getDemoLLM(): DemoLLMConfig | null {
  try {
    const raw = localStorage.getItem(LLM_STORE_KEY);
    if (!raw) return null;
    const cfg = JSON.parse(raw) as DemoLLMConfig;
    if (cfg?.provider && cfg?.key && cfg?.model && PROVIDER_INFO[cfg.provider]) return cfg;
  } catch { /* corrupt entry — treat as unset */ }
  return null;
}

export function setDemoLLM(cfg: DemoLLMConfig): void {
  localStorage.setItem(LLM_STORE_KEY, JSON.stringify(cfg));
  window.dispatchEvent(new Event(DEMO_LLM_CHANGED_EVENT));
}

export function clearDemoLLM(): void {
  localStorage.removeItem(LLM_STORE_KEY);
  window.dispatchEvent(new Event(DEMO_LLM_CHANGED_EVENT));
}

/** Browser-direct provider call. All four providers support CORS for
    browser use; the visitor's key goes straight to the provider. */
export async function callProvider(cfg: DemoLLMConfig, prompt: string): Promise<string> {
  if (cfg.provider === 'anthropic') {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': cfg.key,
        'anthropic-version': '2023-06-01',
        // Anthropic's explicit opt-in for BYOK browser apps
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: JSON.stringify({ model: cfg.model, max_tokens: 1024, messages: [{ role: 'user', content: prompt }] }),
    });
    if (!res.ok) throw new Error(`Anthropic ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const body = await res.json();
    return body.content?.[0]?.text ?? '(empty response)';
  }
  if (cfg.provider === 'gemini') {
    const url = `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(cfg.model)}:generateContent?key=${encodeURIComponent(cfg.key)}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
    });
    if (!res.ok) throw new Error(`Gemini ${res.status}: ${(await res.text()).slice(0, 200)}`);
    const body = await res.json();
    return body.candidates?.[0]?.content?.parts?.[0]?.text ?? '(empty response)';
  }
  // OpenRouter and Groq are OpenAI-shaped
  const url = cfg.provider === 'openrouter'
    ? 'https://openrouter.ai/api/v1/chat/completions'
    : 'https://api.groq.com/openai/v1/chat/completions';
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${cfg.key}` },
    body: JSON.stringify({ model: cfg.model, messages: [{ role: 'user', content: prompt }] }),
  });
  if (!res.ok) throw new Error(`${PROVIDER_INFO[cfg.provider].label} ${res.status}: ${(await res.text()).slice(0, 200)}`);
  const body = await res.json();
  return body.choices?.[0]?.message?.content ?? '(empty response)';
}

// ── Client-side retrieval over the captured neuron pool ──

function extractQuestion(message: string): string {
  // The hero packs history as "[Conversation so far]\n…\n\nUser: <question>"
  const idx = message.lastIndexOf('\nUser: ');
  return idx >= 0 ? message.slice(idx + 7) : message;
}

function retrieveNeurons(question: string, limit = 10): { scored: NeuronScoreLike[]; strength: number } {
  const tokens = (question.toLowerCase().match(/[a-z0-9-]{3,}/g) ?? []);
  const qSet = new Set(tokens);
  const scored = neuronPool
    .map(n => {
      const label = (n.label ?? '').toLowerCase();
      const body = `${n.summary ?? ''} ${n.department ?? ''}`.toLowerCase();
      let hits = 0;
      for (const t of qSet) {
        if (label.includes(t)) hits += 2; // title hits matter more
        else if (body.includes(t)) hits += 1;
      }
      return { n, hits };
    })
    .filter(x => x.hits > 0)
    .sort((a, b) => b.hits - a.hits)
    .slice(0, limit);
  const maxHits = scored[0]?.hits ?? 0;
  return {
    scored: scored.map(x => ({ ...x.n, combined: maxHits ? x.hits / maxHits : 0 })),
    strength: maxHits,
  };
}

function buildGroundedPrompt(question: string, excerpts: NeuronScoreLike[]): string {
  const blocks = excerpts.map((n, i) =>
    `${i + 1}. ${n.label ?? `Neuron ${n.neuron_id}`}${n.department ? ` (${n.department})` : ''}: ${n.summary ?? '(no summary)'}`
  ).join('\n');
  return [
    'You are Corvus, an internal-documentation assistant for an aerospace',
    'manufacturer (synthetic demo corpus). Answer ONLY from the excerpts',
    'below. Cite the excerpt titles you rely on inline in brackets, like',
    '[Title]. If the excerpts do not cover the question, say the demo',
    'corpus does not cover it and suggest a covered topic instead.',
    'Be concise and factual.',
    '',
    'EXCERPTS:',
    blocks || '(none matched)',
    '',
    `QUESTION: ${question}`,
  ].join('\n');
}

// ── Chat responses ──

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

function makeResult(text: string, scores: NeuronScoreLike[], citationMap?: Record<string, unknown>) {
  return {
    query_id: 900000 + ++querySeq,
    llm_session_id: null,
    context_reused: false,
    classify_input_tokens: 0,
    classify_output_tokens: 0,
    total_cost: 0,
    neurons_activated: scores.length,
    neuron_scores: scores,
    citation_map: citationMap,
    slots: [{
      response: text,
      input_tokens: 2400,
      output_tokens: 380,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    }],
  };
}

function queryStreamResponse(requestBody: string): Response {
  const cfg = getDemoLLM();
  let message = '';
  try { message = JSON.parse(requestBody)?.message ?? ''; } catch { /* replay mode needs no message */ }

  // BYOK: kick the provider call off immediately; the stage animation
  // plays while it runs.
  let livePromise: Promise<{ text: string; scores: NeuronScoreLike[] }> | null = null;
  if (cfg && message) {
    const question = extractQuestion(message);
    const { scored } = retrieveNeurons(question);
    livePromise = callProvider(cfg, buildGroundedPrompt(question, scored))
      .then(text => ({ text, scores: scored }));
  }

  const enc = new TextEncoder();
  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: string, data: unknown) =>
        controller.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
      for (const stage of STAGES) {
        send('stage', { stage, status: 'active' });
        await new Promise(r => setTimeout(r, 110));
        // In BYOK mode, hold on the LLM stage until the provider returns
        if (stage === 'execute_llm' && livePromise) await livePromise.catch(() => {});
        send('stage', { stage, status: 'done', duration_ms: Math.round(40 + Math.random() * 320) });
      }
      if (livePromise) {
        try {
          const { text, scores } = await livePromise;
          send('result', makeResult(text, scores));
        } catch (e) {
          send('error', { message: `Your provider call failed — ${(e as Error).message}. Check your key/model in the demo setup (bottom right).` });
        }
      } else {
        const answer = nextAnswer();
        send('result', makeResult(answer.text, answer.neuron_scores, answer.citation_map as Record<string, unknown> | undefined));
      }
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
    // Anything cross-origin passes through untouched (including the
    // BYOK provider calls above, which use this same patched fetch).
    if (u.origin !== window.location.origin) return realFetch(input as RequestInfo, init);

    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const path = u.pathname;

    if (method === 'GET') {
      // The dev environment's model roster doesn't apply here — the demo
      // offers exactly one "model": the canned replay, or the visitor's
      // own BYOK model. (The wizard reloads the page on save/disconnect
      // so this list is refetched.)
      if (path === '/models') {
        const cfg = getDemoLLM();
        return jsonResponse([cfg
          ? { display_name: cfg.model, provider: cfg.provider, api_id: cfg.model, tier: 'Your key (BYOK)', input_price: 0, output_price: 0, context_window_tokens: 200_000 }
          : { display_name: 'demo replay', provider: 'demo', api_id: 'demo', tier: 'Demo', input_price: 0, output_price: 0, context_window_tokens: 200_000 },
        ]);
      }
      const hit = fixtures[path + u.search] ?? fixtures[path];
      if (hit !== undefined) return jsonResponse(hit);
      return jsonResponse({ detail: 'Not included in this demo build.' }, 404);
    }

    // Chat flow — must succeed end-to-end.
    if (path === '/query/stream') return queryStreamResponse(typeof init?.body === 'string' ? init.body : '');
    if (path === '/chat') {
      const cfg = getDemoLLM();
      let message = '';
      try { message = JSON.parse(typeof init?.body === 'string' ? init.body : '')?.message ?? ''; } catch { /* fall back to replay */ }
      if (cfg && message) {
        try {
          const question = extractQuestion(message);
          const { scored } = retrieveNeurons(question);
          const text = await callProvider(cfg, buildGroundedPrompt(question, scored));
          return jsonResponse({ response: text, model: cfg.model, input_tokens: 1200, output_tokens: 300, cost_usd: 0 });
        } catch (e) {
          return jsonResponse({ detail: `Provider call failed: ${(e as Error).message}` }, 502);
        }
      }
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

  console.info('[corvus-demo] Demo mode: canned fixtures; BYOK live chat available via the setup pill.');
}
