import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getTenantConfig, type SeedPrompt } from '../config';
import {
  sendChat, submitQueryStream, createSession, listSessions, getSession,
  appendMessage, generateSessionTitle, deleteSession, fetchNeuron, updateSessionTitle,
  submitRating, fetchFollowUps,
  type ChatMessage, type ChatResponse, type StageEvent, type SlotSpec, type SessionSummary,
} from '../api';
import type { NeuronScoreResponse, CitationSource } from '../types';
import { useModels } from '../hooks/useModels';
import { marked } from 'marked';
import NeuronTreeViz from './NeuronTreeViz';

marked.setOptions({ breaks: true, gfm: true });

interface Message {
  role: 'user' | 'assistant';
  text: string;
  model?: string;
  // `input` is the FRESH (non-cached) input token count from the LLM
  // provider. `cache_creation` and `cache_read` are the Anthropic prompt-
  // cache buckets; on cached calls, most of the effective context lives
  // there. "Effective context size" = input + cache_creation + cache_read.
  tokens?: { input: number; output: number; cache_creation?: number; cache_read?: number };
  cost?: number;
  neurons_activated?: number;
  neuron_scores?: NeuronScoreResponse[];
  // Frequency-hop citations: token -> source, for numbered superscripts.
  citation_map?: Record<string, CitationSource>;
  isCondensed?: boolean;
  condensedOriginals?: { role: string; text: string }[];
  // Per-message timestamp — from backend for loaded history; set locally
  // (new Date().toISOString()) when added in-flight during the current session.
  created_at?: string;
  // Populated on new current-session assistant messages from QueryResponse
  // (neuron path only). Loaded-history messages don't carry it because
  // ChatSessionMessage has no query_id column yet. Rating UI keys off this.
  query_id?: number;
  // Persisted rating chosen by the user (1 = thumbs up, 0 = thumbs down).
  user_rating?: number;
}

function describeQueryError(e: unknown): string {
  // B3: translate raw errors into something a non-technical user can
  // act on. Rendered as the assistant message when a query throws.
  if (e instanceof DOMException && e.name === 'AbortError') {
    return '_Query cancelled._';
  }
  const msg = e instanceof Error ? e.message : String(e ?? '');
  // json() helper throws `<status>: <body>` when an HTTP error comes back;
  // bucket by status code if we can parse it out.
  const statusMatch = msg.match(/^(\d{3})\b/);
  const status = statusMatch ? Number(statusMatch[1]) : 0;
  if (status === 429) {
    return (
      'Too many requests right now. Please wait a moment and try again — ' +
      'the system is rate-limited to keep costs in check.'
    );
  }
  if (status >= 500 && status < 600) {
    return (
      'Something went wrong on our end (server error). Please try again in a ' +
      'moment. If this keeps happening, check with your Corvus administrator.'
    );
  }
  if (status === 400 || status === 422) {
    // Length-limit 422: the packed conversation history + user message
    // exceeded the backend's hard cap. Point the user at the condense
    // escape valve instead of surfacing the raw Pydantic error.
    if (/string.*most.*character|message.*too long|max_length/i.test(msg)) {
      return (
        'This conversation is too long to send in one request. ' +
        'Use **Summarize older messages** in the token bar below to condense the history, ' +
        'then ask your question again.'
      );
    }
    return `Your question couldn't be processed. ${msg.replace(/^\d{3}:\s*/, '')}`;
  }
  if (status === 401 || status === 403) {
    return 'You don\'t have permission to run that query. Please sign in again or contact your administrator.';
  }
  // Network-layer failures — fetch rejects with TypeError before any HTTP
  // status lands.
  if (e instanceof TypeError && /fetch/i.test(msg)) {
    return 'Can\'t reach the server. Please check your connection and try again.';
  }
  // Fallback — raw message but framed so the user knows it's an error, not
  // the actual answer.
  return `Something went wrong: ${msg || 'unknown error'}. Please try again.`;
}


function formatMessageTime(iso: string): string {
  // Per-message timestamp shown in the chat-meta row. Same UTC-suffix
  // defensive handling as relativeTime — the backend returns naive UTC.
  const normalized = iso.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function SessionTitle({
  title, onRename,
}: { title: string; onRename: (newTitle: string) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const cancel = () => { setDraft(title); setEditing(false); };
  const save = async () => {
    const next = draft.trim();
    if (!next || next === title) { cancel(); return; }
    await onRename(next);
    setEditing(false);
  };

  if (editing) {
    return (
      <input
        ref={inputRef}
        className="chat-session-title chat-session-title-editing"
        value={draft}
        onChange={e => setDraft(e.target.value)}
        onClick={e => e.stopPropagation()}
        onBlur={save}
        onKeyDown={e => {
          e.stopPropagation();
          if (e.key === 'Enter') { e.preventDefault(); save(); }
          if (e.key === 'Escape') { e.preventDefault(); cancel(); }
        }}
      />
    );
  }

  return (
    <span
      className="chat-session-title"
      onDoubleClick={e => { e.stopPropagation(); setEditing(true); }}
      title="Double-click to rename"
    >
      {title || 'Untitled'}
    </span>
  );
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function AssistantText({ text, scores, citationMap }: {
  text: string;
  scores?: NeuronScoreResponse[];
  citationMap?: Record<string, CitationSource>;
}) {
  // Inline citations become clickable superscripts. Two schemes:
  //  - Frequency-hop tokens [FQ-XXXXXX]: the LLM emits per-query unforgeable
  //    keys; each is resolved via citationMap and RE-NUMBERED 1..k for display
  //    (humans see [1],[2]). With a map, unknown/fabricated keys are dropped;
  //    without one (reloaded history) we still re-number so raw keys never show.
  //  - Legacy numeric [N]: maps to the Nth neuron by score order.
  const html = useMemo(() => {
    const raw = marked.parse(text, { async: false }) as string;
    if (/\[FQ-[0-9A-Fa-f]{6}\]/i.test(raw)) {
      const map = citationMap ?? {};
      const displayNum = new Map<string, number>();
      return raw.replace(/\[(FQ-[0-9A-Fa-f]{6})\]/gi, (_m, tokRaw: string) => {
        const token = tokRaw.toUpperCase();
        const src = map[token];
        if (citationMap && !src) return '';  // resolvable map, fabricated key → drop
        let n = displayNum.get(token);
        if (n === undefined) { n = displayNum.size + 1; displayNum.set(token, n); }
        const idAttr = src
          ? (src.kind === 'engram' ? `data-engram-id="${src.id}"` : `data-neuron-id="${src.id}"`)
          : '';
        const title = escapeAttr(src?.label ?? `Source ${n}`);
        return `<sup class="chat-citation" data-source="${n}" ${idAttr} tabindex="0" title="${title}">[${n}]</sup>`;
      });
    }
    if (!scores || scores.length === 0) return raw;
    // Legacy numeric [N] citations (hopping disabled).
    return raw.replace(/\[(\d+)\]/g, (match, n: string) => {
      const idx = Number(n);
      if (idx < 1 || idx > scores.length) return match;
      const neuronId = scores[idx - 1].neuron_id;
      return `<sup class="chat-citation" data-source="${idx}" data-neuron-id="${neuronId}" tabindex="0" title="Source ${idx}">[${idx}]</sup>`;
    });
  }, [text, scores, citationMap]);

  // Attach a click handler to citation sups. Since we're using
  // dangerouslySetInnerHTML, event delegation is the cleanest path.
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onClick = (e: Event) => {
      const target = e.target as HTMLElement;
      if (target?.classList?.contains('chat-citation')) {
        const neuronId = target.getAttribute('data-neuron-id');
        if (neuronId) {
          // Broadcast a custom event so the sources chip (if present) can
          // open itself and highlight the matching row.
          window.dispatchEvent(new CustomEvent('chat-citation-click', {
            detail: { neuronId: Number(neuronId) },
          }));
        }
      }
    };
    el.addEventListener('click', onClick);
    return () => el.removeEventListener('click', onClick);
  }, [html]);

  return (
    <div
      ref={ref}
      className="chat-text markdown-body"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function FollowUpChips({ queryId, onPick }: {
  queryId: number;
  onPick: (text: string) => void;
}) {
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchFollowUps(queryId)
      .then(r => { if (!cancelled) setSuggestions(r.suggestions.map(s => s.text)); })
      .catch(() => { if (!cancelled) setSuggestions([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [queryId]);
  if (loading) return null;
  if (!suggestions || suggestions.length === 0) return null;
  return (
    <div className="chat-followups" role="group" aria-label="Suggested follow-up questions">
      <span className="chat-followups-label">Suggested follow-ups:</span>
      {suggestions.map((s, i) => (
        <button
          key={i}
          type="button"
          className="chat-followup-chip"
          onClick={() => onPick(s)}
          title={s}
        >{s}</button>
      ))}
    </div>
  );
}

function RateButtons({ queryId, initialRating, onRated }: {
  queryId: number;
  initialRating?: number;
  onRated: (rating: number) => void;
}) {
  const [rating, setRating] = useState<number | undefined>(initialRating);
  const [submitting, setSubmitting] = useState(false);
  const vote = async (value: number) => {
    if (submitting || rating === value) return;
    setSubmitting(true);
    try {
      await submitRating(queryId, value);
      setRating(value);
      onRated(value);
    } catch {
      // Silent — user can retry; better than a scary error toast.
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <span className="chat-rate-buttons">
      <button
        type="button"
        className={`chat-rate-btn${rating === 1 ? ' selected' : ''}`}
        onClick={() => vote(1)}
        disabled={submitting}
        title="This answer was helpful"
        aria-label="Thumbs up"
      >👍</button>
      <button
        type="button"
        className={`chat-rate-btn${rating === 0 ? ' selected-down' : ''}`}
        onClick={() => vote(0)}
        disabled={submitting}
        title="This answer missed the mark"
        aria-label="Thumbs down"
      >👎</button>
    </span>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="chat-copy-btn"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard API unavailable (older browsers / insecure context).
          // Silent failure is fine — nothing to act on.
        }
      }}
      title={copied ? 'Copied!' : 'Copy answer to clipboard'}
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  );
}


function relativeTime(iso: string): string {
  // Backend returns naive UTC timestamps without a trailing `Z`. JS's
  // Date parser treats tz-less ISO as LOCAL time, which makes every
  // session look "just now" on machines not running UTC. Append Z so
  // the timestamp is unambiguously UTC.
  const normalized = iso.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return '';
  const now = Date.now();
  const diffMs = Math.max(0, now - d.getTime());
  // Within 24 hours → show the time (e.g. "3:42 PM"). Past that → show
  // the date (e.g. "Apr 21"). Same calendar-year date stays short;
  // years back get a year appended.
  if (diffMs < 24 * 60 * 60 * 1000) {
    return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
  const sameYear = d.getFullYear() === new Date(now).getFullYear();
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  });
}

// Pipeline stage labels — Option 3 (hybrid). The user sees friendly names
// that convey each step's purpose in the regulated-answer workflow; a hover
// tooltip surfaces the internal technical name for power users / debugging.
// The subtitle lines reinforce "this is a regulated process" without
// requiring the user to know what a "neuron" or "embedding" is.
interface StageLabel { label: string; subtitle: string; technical: string; }
const PIPELINE_STAGE_LABELS: Record<string, StageLabel> = {
  input_guard: {
    label: 'Safety check',
    subtitle: 'Checking your input against company policy',
    technical: 'input_guard',
  },
  structural_resolve: {
    label: 'Looking up direct match',
    subtitle: 'Checking for an exact policy or procedure reference',
    technical: 'structural_resolve',
  },
  embed_query: {
    label: 'Parsing your question',
    subtitle: 'Encoding your question into a searchable form',
    technical: 'embed_query',
  },
  classify: {
    label: 'Understanding your intent',
    subtitle: 'Identifying the domain, role, and specific ask',
    technical: 'classify',
  },
  semantic_prefilter: {
    label: 'Finding relevant documentation',
    subtitle: 'Searching your company’s internal knowledge',
    technical: 'semantic_prefilter',
  },
  score_neurons: {
    label: 'Ranking sources by relevance',
    subtitle: 'Weighing authority, recency, and match quality',
    technical: 'score_neurons',
  },
  spread_activation: {
    label: 'Gathering related policies',
    subtitle: 'Pulling in nearby context the answer may need',
    technical: 'spread_activation',
  },
  assemble_prompt: {
    label: 'Building grounded context',
    subtitle: 'Composing a prompt anchored to internal documentation',
    technical: 'assemble_prompt',
  },
  execute_llm: {
    label: 'Generating answer',
    subtitle: 'Producing a response from the grounded context',
    technical: 'execute_llm',
  },
  output_checks: {
    label: 'Compliance verification',
    subtitle: 'Screening the answer for policy violations',
    technical: 'output_checks',
  },
};

// Stages that only run when neurons are enabled
const NEURON_ONLY_STAGES = new Set([
  'structural_resolve', 'embed_query', 'classify',
  'semantic_prefilter', 'score_neurons', 'spread_activation', 'assemble_prompt',
]);

function CondensedMessage({ msg }: { msg: Message }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="chat-msg chat-msg--assistant">
      <div className="chat-bubble chat-bubble--condensed">
        <div className="chat-text markdown-body" dangerouslySetInnerHTML={{ __html: marked.parse(msg.text, { async: false }) as string }} />
        {msg.condensedOriginals && msg.condensedOriginals.length > 0 && (
          <>
            <button className="chat-condensed-toggle" onClick={() => setExpanded(v => !v)}>
              {expanded ? 'Hide' : 'Show'} {msg.condensedOriginals.length} original messages
            </button>
            {expanded && (
              <div className="chat-condensed-originals">
                {msg.condensedOriginals.map((m, j) => (
                  <div key={j} className={`chat-condensed-msg chat-condensed-msg--${m.role}`}>
                    <span className="chat-condensed-role">{m.role === 'user' ? 'You' : 'Assistant'}</span>
                    <span className="chat-condensed-text">{m.text}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
        {msg.tokens && (
          <div className="chat-meta">
            <span>condensed by {msg.model}</span>
            <span>{(msg.tokens.input + msg.tokens.output).toLocaleString()} tokens</span>
            {msg.cost != null && <span>${msg.cost.toFixed(4)}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export default function HomePage({ onNavigate: _onNavigate }: { onNavigate: (tab: string) => void }) {
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState('haiku');
  // AbortController handle + boolean paired state for the stop button.
  // Ref holds the callable (no re-render needed); state drives button visibility.
  const abortRef = useRef<(() => void) | null>(null);
  const [canAbort, setCanAbort] = useState(false);
  const [useNeurons, setUseNeurons] = useState(true);
  const [effort, setEffort] = useState('low');
  // Spread-activation reach for KG recall (hero exposes the same knobs as
  // Query Lab cards): hop cap 1-6 and min-activation floor 0-0.5.
  const [spreadHops, setSpreadHops] = useState<number | 'auto'>('auto');
  const [spreadFloor, setSpreadFloor] = useState(0.15);
  // Persisted CLI session for this conversation (Claude models only). While
  // set, the server carries conversation memory (prompt-cached) and we stop
  // packing history into the message. Reset on model switch / new chat so
  // non-Claude providers and fresh conversations fall back to history packing.
  const [llmSessionId, setLlmSessionId] = useState<string | null>(null);
  const { models: availableModels, grouped: groupedModels } = useModels();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  // B2: client-side substring filter over session titles. Cheap at typical
  // scale (<500 sessions); no backend change needed.
  const [sessionQuery, setSessionQuery] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const sessionCreatingRef = useRef(false);

  const [pipelineStages, setPipelineStages] = useState<Record<string, StageEvent>>({});
  const [neuronSidebarOpen, setNeuronSidebarOpen] = useState(false);
  const neuronSidebarOpenedRef = useRef(false); // tracks if we already opened it this session

  useEffect(() => {
    listSessions().then(setSessions).catch(() => {}).finally(() => setSessionsLoading(false));
  }, []);

  const refreshSessions = useCallback(() => {
    listSessions().then(setSessions).catch(() => {});
  }, []);

  useEffect(() => {
    if (loading) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [pipelineStages, loading]);

  async function handleSend() {
    const text = input.trim();
    if (!text || loading) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    const userMsg: Message = { role: 'user', text, created_at: new Date().toISOString() };
    const isFirstMessage = messages.length === 0;
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    setPipelineStages({});

    try {
      let sessionId = currentSessionId;
      if (sessionId === null && !sessionCreatingRef.current) {
        sessionCreatingRef.current = true;
        try {
          const s = await createSession();
          sessionId = s.id;
          setCurrentSessionId(s.id);
        } finally {
          sessionCreatingRef.current = false;
        }
      }

      if (sessionId) {
        await appendMessage(sessionId, { role: 'user', text });
      }

      let assistantMsg: Message;

      if (useNeurons) {
        // Neuron-enriched path — include recent conversation in full. Prior
        // per-message truncation at 500 chars was clipping assistant responses
        // (which can easily be 2k+ tokens / 8k+ chars) to ~100 tokens, so the
        // LLM would not see what it had answered moments earlier. The turn
        // count (slice(-10)) is the intended bound on history size. When the
        // accumulated tokens approach the model's context window, the
        // "Summarize older messages" button condenses older turns in place.
        const recentHistory = messages.slice(-10);
        let userMessage = text;
        // With a persisted CLI session, the server already holds the
        // conversation (prompt-cached) — packing history would double it.
        if (recentHistory.length > 0 && !llmSessionId) {
          const historyLines = recentHistory.map(m =>
            `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text}`
          ).join('\n');
          userMessage = `[Conversation so far]\n${historyLines}\n\nUser: ${text}`;
        }

        const priorNeuronIds: number[] = [];
        for (const m of messages) {
          if (m.neuron_scores) {
            for (const ns of m.neuron_scores) priorNeuronIds.push(ns.neuron_id);
          }
        }

        // Workspace priming always on for the hero chat (Query Lab keeps a toggle)
        const slot: SlotSpec = {
          mode: `${model}_neuron`, token_budget: 8000, top_k: 60, priming: true,
          spread_hops: spreadHops === 'auto' ? undefined : spreadHops, spread_floor: spreadFloor,
        };
        const { promise, abort } = submitQueryStream(
          userMessage,
          (event: StageEvent) => setPipelineStages(prev => ({ ...prev, [event.stage]: event })),
          priorNeuronIds.length > 0 ? priorNeuronIds : undefined,
          [slot],
          effort,
          { persist: true, llmSessionId },
        );
        abortRef.current = abort;
        setCanAbort(true);
        const res = await promise;
        setLlmSessionId(res.llm_session_id ?? null);
        const slotResult = res.slots[0];
        assistantMsg = {
          role: 'assistant',
          text: slotResult?.response ?? '',
          model,
          tokens: {
            input: (res.classify_input_tokens || 0) + (slotResult?.input_tokens || 0),
            output: (res.classify_output_tokens || 0) + (slotResult?.output_tokens || 0),
            cache_creation: slotResult?.cache_creation_tokens || 0,
            cache_read: slotResult?.cache_read_tokens || 0,
          },
          cost: res.total_cost || 0,
          neurons_activated: res.neurons_activated,
          neuron_scores: res.neuron_scores,
          citation_map: res.citation_map,
          created_at: new Date().toISOString(),
          query_id: res.query_id,
        };
      } else {
        // Raw LLM path (no neurons) — manually set stage indicators
        setPipelineStages({ input_guard: { stage: 'input_guard', status: 'done' } });
        setPipelineStages(prev => ({ ...prev, execute_llm: { stage: 'execute_llm', status: 'active' } }));
        const history: ChatMessage[] = messages.map(m => ({ role: m.role, text: m.text }));
        const res: ChatResponse = await sendChat(text, model, history);
        setPipelineStages(prev => ({ ...prev, execute_llm: { stage: 'execute_llm', status: 'done' }, output_checks: { stage: 'output_checks', status: 'done' } }));
        assistantMsg = {
          role: 'assistant',
          text: res.response,
          model: res.model,
          tokens: { input: res.input_tokens, output: res.output_tokens },
          cost: res.cost_usd,
          created_at: new Date().toISOString(),
        };
      }

      setMessages(prev => [...prev, assistantMsg]);

      // Neuron sidebar stays COLLAPSED by default — it's the technical
      // graph-explorer surface, not the end-user focus. The toggle button in
      // the top-right lets curious users open it. Expert/debug flows (query
      // lab, dossier) are the right home for graph interrogation.

      if (sessionId) {
        await appendMessage(sessionId, {
          role: 'assistant', text: assistantMsg.text, model: assistantMsg.model,
          input_tokens: assistantMsg.tokens?.input, output_tokens: assistantMsg.tokens?.output,
          cost: assistantMsg.cost, neurons_activated: assistantMsg.neurons_activated,
          neuron_scores: assistantMsg.neuron_scores,
        });
      }

      if (isFirstMessage && sessionId) {
        generateSessionTitle(sessionId).then(() => refreshSessions()).catch(() => {});
      }
    } catch (e) {
      // B3: turn raw backend/network errors into actionable copy.
      const errText = describeQueryError(e);
      setMessages(prev => [...prev, {
        role: 'assistant', text: errText, created_at: new Date().toISOString(),
      }]);
    } finally {
      abortRef.current = null;
      setCanAbort(false);
      setLoading(false);
      setPipelineStages({});
      setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    }
  }

  // A2: user-initiated cancel. Aborts the in-flight fetch; the catch
  // branch above converts the AbortError into a "cancelled" message.
  function handleStop() {
    if (abortRef.current) {
      abortRef.current();
      abortRef.current = null;
      setCanAbort(false);
    }
  }

  function startNewChat() {
    setCurrentSessionId(null);
    setMessages([]);
    setLlmSessionId(null);
    setNeuronSidebarOpen(false);
    neuronSidebarOpenedRef.current = false;
    refreshSessions();
  }

  async function loadSession(id: number) {
    try {
      const detail = await getSession(id);
      setCurrentSessionId(id);
      setMessages(detail.messages.map((m) => ({
        role: m.role as 'user' | 'assistant',
        text: m.text,
        model: m.model ?? undefined,
        tokens: (m.input_tokens || m.output_tokens) ? { input: m.input_tokens, output: m.output_tokens } : undefined,
        cost: m.cost || undefined,
        neurons_activated: m.neurons_activated || undefined,
        neuron_scores: m.neuron_scores ?? undefined,
        created_at: m.created_at ?? undefined,
      })));
    } catch { /* session may be deleted */ }
  }

  const [condensing, setCondensing] = useState(false);

  async function condenseContext() {
    if (messages.length <= 4 || condensing) return;
    setCondensing(true);
    try {
      const kept = messages.slice(-4);
      const toCondense = messages.slice(0, -4);

      // Build the conversation text to summarize
      const conversationText = toCondense.map(m =>
        `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text}`
      ).join('\n\n');

      // Call Haiku to generate a real summary
      const res = await sendChat(
        `Summarize the following conversation concisely. Preserve all key decisions, facts, technical details, action items, and important context. Write in third person past tense. Be thorough but compact.\n\n---\n\n${conversationText}`,
        'haiku',
        [],
      );

      const summaryMsg: Message = {
        role: 'assistant',
        text: `**[Context condensed — ${toCondense.length} messages summarized by Haiku]**\n\n${res.response}`,
        model: 'haiku',
        tokens: { input: res.input_tokens, output: res.output_tokens },
        cost: res.cost_usd,
        isCondensed: true,
        condensedOriginals: toCondense.map(m => ({ role: m.role, text: m.text })),
      };
      setMessages([summaryMsg, ...kept]);
    } catch (e) {
      // If summarization fails, fall back to truncation
      const kept = messages.slice(-4);
      const toCondense = messages.slice(0, -4);
      const fallbackText = toCondense.map(m =>
        `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text.slice(0, 150)}...`
      ).join('\n');
      const summaryMsg: Message = {
        role: 'assistant',
        text: `*[Context condensed — ${toCondense.length} messages truncated (summarization failed)]*\n\n${fallbackText}`,
        model: 'system',
        isCondensed: true,
        condensedOriginals: toCondense.map(m => ({ role: m.role, text: m.text })),
      };
      setMessages([summaryMsg, ...kept]);
    } finally {
      setCondensing(false);
    }
  }

  async function archiveSession(id: number) {
    await deleteSession(id).catch(() => {});
    setSessions(prev => prev.filter(s => s.id !== id));
    if (currentSessionId === id) startNewChat();
  }

  const hasMessages = messages.length > 0 || currentSessionId !== null;

  const stageKeys = Object.keys(PIPELINE_STAGE_LABELS);

  // Context window tracking — per-model context size from the /models
  // response. Denominator updates when the user swaps models.
  const selectedModelInfo = availableModels.find(m => m.display_name === model);
  const contextMax = selectedModelInfo?.context_window_tokens ?? 200_000;

  // The "tokens in context" reading is the LATEST assistant turn's
  // EFFECTIVE context size — input + cache_creation + cache_read. The raw
  // `input_tokens` count alone excludes content served from Anthropic's
  // prompt cache, which undercounts by thousands when the system prompt
  // gets cached (the usual case). Summing across turns would double-count
  // history (each turn's prompt already re-includes prior exchanges).
  const lastAssistantTokens = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && typeof m.tokens?.input === 'number') {
        return (m.tokens.input || 0)
          + (m.tokens.cache_creation || 0)
          + (m.tokens.cache_read || 0);
      }
    }
    return 0;
  })();
  const contextPct = Math.min(100, (lastAssistantTokens / contextMax) * 100);
  const contextWarning = contextPct >= 80;
  const fmtTokens = (n: number): string => {
    if (n < 1000) return String(n);
    if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}K`;
    return `${(n / 1_000_000).toFixed(2)}M`;
  };

  // Find latest assistant message with neuron scores for the sidebar
  let latestNeuronScores: NeuronScoreResponse[] | null = null;
  let latestQueryId: number | undefined;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'assistant' && messages[i].neuron_scores && messages[i].neuron_scores!.length > 0) {
      latestNeuronScores = messages[i].neuron_scores!;
      break;
    }
  }
  // We don't have queryId per message in chat, but NeuronTreeViz can work without it (just won't fetch spread trail)
  latestQueryId = undefined;

  const inputBar = (
    <div className="chat-input-bar">
      <div className="chat-input-controls">
        <select className="chat-model-select" value={model} onChange={e => { setModel(e.target.value); setLlmSessionId(null); }}>
          {Object.entries(groupedModels).map(([group, models]) => ([
            <option key={`hdr-${group}`} disabled>── {group} ──</option>,
            ...models.map(m => <option key={m.display_name} value={m.display_name}>{m.display_name}</option>),
          ]))}
        </select>
        <select className="chat-model-select" value={effort} onChange={e => setEffort(e.target.value)} title="Reasoning effort — higher deliberates more but is slower">
          <option value="low">Effort: Low</option>
          <option value="medium">Effort: Medium</option>
          <option value="high">Effort: High</option>
        </select>
        {useNeurons && (
          <>
            <select className="chat-model-select" value={spreadHops} onChange={e => setSpreadHops(e.target.value === 'auto' ? 'auto' : parseInt(e.target.value, 10))} title="Spread-activation hop cap. Auto derives it from graph structure (log N / log avg-degree) and self-terminates when deeper hops can't change the promoted set; 1-6 pins it manually.">
              <option value="auto">Hops: Auto</option>
              {[1, 2, 3, 4, 5, 6].map(h => (
                <option key={h} value={h}>Hops: {h}</option>
              ))}
            </select>
            <select className="chat-model-select" value={spreadFloor} onChange={e => setSpreadFloor(parseFloat(e.target.value))} title="Minimum activation for a spread neighbor to survive. Lower = deeper associative reach; higher = only the strongest associations.">
              {[0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5].map(f => (
                <option key={f} value={f}>Floor: {f.toFixed(2)}{f === 0.15 ? ' (default)' : ''}</option>
              ))}
            </select>
          </>
        )}
        <button
          className={`chat-neuron-toggle${useNeurons ? ' active' : ''}`}
          onClick={() => setUseNeurons(v => !v)}
          title={useNeurons ? 'Neuron enrichment ON — click to disable' : 'Neuron enrichment OFF — click to enable'}
        >
          <span className="chat-neuron-toggle-dot" />
          Neurons {useNeurons ? 'ON' : 'OFF'}
        </button>
      </div>
      {messages.length > 0 && lastAssistantTokens > 0 && (
        <div className="chat-context-bar" title="Effective context size the model processed on the last turn (fresh input + cached input). The higher this goes, the more the model has to hold in working memory. Near 80% the bar turns amber so you can summarize older exchanges before the session risks collapse.">
          <div className="chat-context-track">
            <div className={`chat-context-fill${contextWarning ? ' warning' : ''}`} style={{ width: `${contextPct}%` }} />
          </div>
          <span className="chat-context-label">{fmtTokens(lastAssistantTokens)} / {fmtTokens(contextMax)} ({contextPct.toFixed(0)}%)</span>
          {contextWarning && (
            <button className="chat-context-condense" onClick={condenseContext} disabled={condensing} title="Summarize older messages via Haiku to free context space">
              {condensing ? 'Summarizing older messages…' : 'Summarize older messages'}
            </button>
          )}
        </div>
      )}
      <div className="chat-input-row">
        <textarea
          ref={textareaRef}
          className="chat-input"
          placeholder="Ask anything..."
          value={input}
          onChange={e => { setInput(e.target.value); e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px'; }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
          rows={1}
        />
        <button className="chat-send-btn" onClick={handleSend} disabled={loading || !input.trim()}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" /></svg>
        </button>
      </div>
    </div>
  );

  // ── Hero state ──
  if (!hasMessages) {
    return (
      <div className="chat-hero">
        <div className="chat-hero-center">
          <img src="/corvus-logo.png" alt="Corvus" className="chat-hero-logo" />
          <h1 className="chat-hero-title">{getTenantConfig()?.display_name ?? 'Corvus'}</h1>
          <p className="chat-hero-subtitle">I know our company's internal documentation — ask me anything.</p>
          {inputBar}
          {(() => {
            const prompts: SeedPrompt[] = getTenantConfig()?.seed_prompts ?? [];
            if (prompts.length === 0) return null;
            return (
              <div className="chat-seed-prompts">
                {prompts.map((p, i) => (
                  <button
                    key={i}
                    className="chat-seed-chip"
                    onClick={() => { setInput(p.text); textareaRef.current?.focus(); }}
                    title={p.text}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            );
          })()}
          {/* Admin CTAs (Query Lab / Explorer / Dashboard) hidden on the
              hero page — the hero is a grounded-answer surface for general
              employees, not a graph-interrogation dashboard. Power users
              reach those pages via the side navigation. When role-based
              access lands (see master-corvus gov-rbac), these can be
              re-enabled conditionally for admin viewers. */}
          {!sessionsLoading && sessions.length > 0 && (
            <div className="chat-hero-sessions">
              <h3>Recent Conversations</h3>
              {sessions.slice(0, 8).map(s => (
                <div key={s.id} className="chat-session-item" onClick={() => loadSession(s.id)}>
                  <SessionTitle
                    title={s.title || 'Untitled'}
                    onRename={async (newTitle) => {
                      await updateSessionTitle(s.id, newTitle);
                      setSessions(prev => prev.map(ss => ss.id === s.id ? { ...ss, title: newTitle } : ss));
                    }}
                  />
                  <span className="chat-session-time">{relativeTime(s.updated_at)}</span>
                  <button className="chat-session-del" onClick={e => { e.stopPropagation(); archiveSession(s.id); }}>×</button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  // ── Chat state ──
  return (
    <div className="chat-layout">
      {/* Session sidebar */}
      <div className={`chat-sidebar${sidebarOpen ? '' : ' chat-sidebar--collapsed'}`}>
        <div className="chat-sidebar-header">
          <button className="chat-sidebar-toggle" onClick={() => setSidebarOpen(v => !v)}>
            {sidebarOpen ? '\u25C0' : '\u25B6'}
          </button>
          {sidebarOpen && <button className="chat-new-btn" onClick={startNewChat}>+ New Chat</button>}
        </div>
        {sidebarOpen && (
          <>
            <div className="chat-sidebar-search">
              <input
                type="text"
                className="chat-sidebar-search-input"
                placeholder="Search conversations…"
                value={sessionQuery}
                onChange={e => setSessionQuery(e.target.value)}
                aria-label="Search conversations"
              />
            </div>
            <div className="chat-sidebar-list">
            {sessions
              .filter(s => {
                const q = sessionQuery.trim().toLowerCase();
                if (!q) return true;
                return (s.title || '').toLowerCase().includes(q);
              })
              .map(s => (
              <div
                key={s.id}
                className={`chat-session-item${currentSessionId === s.id ? ' active' : ''}`}
                onClick={() => loadSession(s.id)}
              >
                <SessionTitle
                  title={s.title || 'Untitled'}
                  onRename={async (newTitle) => {
                    await updateSessionTitle(s.id, newTitle);
                    setSessions(prev => prev.map(ss => ss.id === s.id ? { ...ss, title: newTitle } : ss));
                  }}
                />
                <span className="chat-session-time">{relativeTime(s.updated_at)}</span>
                <button className="chat-session-del" onClick={e => { e.stopPropagation(); archiveSession(s.id); }}>×</button>
              </div>
            ))}
            </div>
          </>
        )}
      </div>

      {/* Messages */}
      <div className="chat-main">
        {/* A5: grounded banner — reinforces "this is a regulated, sourced
            process" signal once the user has moved past the empty state. */}
        <div className="chat-grounded-banner" title="Every answer is traced back to internal sources you can inspect.">
          <span className="chat-grounded-shield" aria-hidden="true">◆</span>
          <span>Grounded in your company's internal documentation. Every answer is traced to source material.</span>
        </div>
        <div className="chat-messages">
          {messages.map((msg, i) => {
            if (msg.isCondensed) return <CondensedMessage key={i} msg={msg} />;
            return (
              <div key={i} className={`chat-msg chat-msg--${msg.role}`}>
                <div className="chat-bubble">
                  {msg.role === 'assistant' ? (
                    <AssistantText
                      text={msg.text}
                      scores={msg.neuron_scores}
                      citationMap={msg.citation_map}
                    />
                  ) : (
                    <div className="chat-text">{msg.text}</div>
                  )}
                  {msg.role === 'assistant' && msg.neuron_scores && msg.neuron_scores.length > 0 && (
                    <ChatSourcesChip scores={msg.neuron_scores} citationMap={msg.citation_map} />
                  )}
                  {msg.role === 'assistant' && msg.query_id != null && (
                    <FollowUpChips
                      queryId={msg.query_id}
                      onPick={(text) => { setInput(text); textareaRef.current?.focus(); }}
                    />
                  )}
                  {msg.role === 'assistant' && (
                    <div className="chat-meta">
                      {msg.model && <span>{msg.model}</span>}
                      {msg.tokens && (() => {
                        const effectiveIn = (msg.tokens.input || 0)
                          + (msg.tokens.cache_creation || 0)
                          + (msg.tokens.cache_read || 0);
                        const total = effectiveIn + (msg.tokens.output || 0);
                        const cached = (msg.tokens.cache_creation || 0) + (msg.tokens.cache_read || 0);
                        const title = cached > 0
                          ? `${msg.tokens.input.toLocaleString()} fresh in + ${cached.toLocaleString()} cached in + ${msg.tokens.output.toLocaleString()} out`
                          : `${(msg.tokens.input || 0).toLocaleString()} in + ${(msg.tokens.output || 0).toLocaleString()} out`;
                        return <span title={title}>{total.toLocaleString()} tokens</span>;
                      })()}
                      {msg.created_at && <span>{formatMessageTime(msg.created_at)}</span>}
                      <CopyButton text={msg.text} />
                      {msg.query_id != null && (
                        <RateButtons
                          queryId={msg.query_id}
                          initialRating={msg.user_rating}
                          onRated={(r) => setMessages(prev => prev.map((m, mi) => mi === i ? { ...m, user_rating: r } : m))}
                        />
                      )}
                      {/* Per-message cost hidden on the hero page — aggregated
                          visibility for admins lives on the Performance page. */}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {loading && (
            <div className="chat-msg chat-msg--assistant">
              <div className="chat-bubble">
                <div className="chat-pipeline-stages">
                  {(() => {
                    // Compute the single active stage up-front.
                    //
                    // Not every frontend stage has a matching backend SSE
                    // event — some are narrative sub-steps of a larger
                    // backend stage. Specifically:
                    //   - embed_query happens INSIDE the backend `classify` stage
                    //   - semantic_prefilter + score_neurons are both inside the
                    //     backend `prefilter_score` stage
                    //   - execute_llm runs outside the pipeline runner; only
                    //     `output_checks` (fired after) signals it completed
                    //
                    // So "active" is: the first stage that (a) has no event
                    // of its own yet AND (b) has no LATER stage with an event
                    // either — i.e. it's genuinely next, not a phantom that
                    // got implicitly completed when its parent backend stage
                    // fired.
                    const firstUnreportedIdx = (() => {
                      if (!loading) return -1;
                      for (let i = 0; i < stageKeys.length; i++) {
                        const k = stageKeys[i];
                        if (!useNeurons && NEURON_ONLY_STAGES.has(k)) continue;
                        if (pipelineStages[k]) continue;
                        const laterReported = stageKeys.slice(i + 1).some(lk => pipelineStages[lk]);
                        if (laterReported) continue; // phantom — parent stage already moved on
                        return i;
                      }
                      return -1;
                    })();
                    return stageKeys.map((key, idx) => {
                    const isNeuronOnly = NEURON_ONLY_STAGES.has(key);
                    const skipped = !useNeurons && isNeuronOnly;
                    const ev = pipelineStages[key];
                    // A phantom stage (no own event, but a later stage has
                    // fired) is considered done — its parent backend stage
                    // has already completed, which implies the phantom's
                    // work is done too.
                    const laterReported = stageKeys.slice(idx + 1).some(k => pipelineStages[k]);
                    const isActive = !skipped && idx === firstUnreportedIdx;
                    const isDone = !isActive && (skipped || !!ev || laterReported);
                    // duration_ms is emitted at the TOP LEVEL of the SSE stage
                    // payload (pipeline/runner.py::_payload_for_emit), not inside
                    // detail. Previously we read detail?.duration_ms which always
                    // returned undefined → "0ms" fallback on every stage.
                    const durMs = ev?.duration_ms;
                    let timing = '';
                    if (skipped) {
                      timing = 'skipped';
                    } else if (typeof durMs === 'number') {
                      timing = durMs < 1 ? '<1ms' : durMs < 1000 ? `${durMs.toFixed(0)}ms` : `${(durMs / 1000).toFixed(2)}s`;
                    } else if (isDone) {
                      // Stage completed but no duration reported — treat as sub-millisecond.
                      timing = '<1ms';
                    }
                    const spec = PIPELINE_STAGE_LABELS[key];
                    // Tooltip surfaces the technical stage name + purpose so
                    // power users / debuggers can trace what's happening, while
                    // the visible label stays friendly.
                    const tooltip = `${spec.subtitle}\n(internal: ${spec.technical})`;
                    return (
                      <div key={key} className={`chat-stage-row${isDone ? ' done' : ''}${isActive ? ' active' : ''}${skipped ? ' skipped' : ''}`} title={tooltip}>
                        <span className="chat-stage-dot" />
                        <span className="chat-stage-name">{spec.label}</span>
                        {/* Time column is ALWAYS rendered — empty placeholder
                            preserves column alignment for pending/active rows. */}
                        <span className="chat-stage-time">{timing}</span>
                      </div>
                    );
                    });
                  })()}
                </div>
                {/* A2: user-cancel affordance. Only the neuron path exposes
                    an abort handle today; raw path finishes too fast to need one. */}
                {canAbort && (
                  <button
                    type="button"
                    className="chat-stop-btn"
                    onClick={handleStop}
                    title="Cancel this query"
                  >
                    ■ Stop
                  </button>
                )}
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
        {inputBar}
      </div>

      {/* Neuron graph sidebar */}
      {neuronSidebarOpen && latestNeuronScores && (
        <div className="chat-neuron-panel">
          <div className="chat-neuron-panel-header">
            <span style={{ fontSize: '0.72rem', fontWeight: 600, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>Neuron Graph</span>
            <button className="chat-neuron-panel-close" onClick={() => setNeuronSidebarOpen(false)}>&times;</button>
          </div>
          <div className="chat-neuron-panel-body">
            <NeuronTreeViz
              queryId={latestQueryId}
              neuronScores={latestNeuronScores}
            />
          </div>
        </div>
      )}
      {!neuronSidebarOpen && latestNeuronScores && (
        <button
          className="chat-neuron-panel-toggle"
          onClick={() => setNeuronSidebarOpen(true)}
          title="Show neuron graph"
        >
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5">
            <circle cx="8" cy="8" r="3" /><line x1="8" y1="1" x2="8" y2="4" /><line x1="8" y1="12" x2="8" y2="15" />
            <line x1="1" y1="8" x2="4" y2="8" /><line x1="12" y1="8" x2="15" y2="8" />
          </svg>
        </button>
      )}
    </div>
  );
}


// ── Sources chip ────────────────────────────────────────────────────────
// Surfaces the internal documentation the answer was grounded in. Shown
// per assistant message as a compact pill that expands to a list. This is
// the mark of an enterprise-grounded LLM vs. a consumer chat — non-admin
// users don't need to understand "neurons" but they DO benefit from seeing
// "Source: HR Policy 4.2 · Travel Procedure 2.1" to trust the answer.

function ChatSourcesChip({ scores, citationMap }: {
  scores: NeuronScoreResponse[];
  citationMap?: Record<string, CitationSource>;
}) {
  const [open, setOpen] = React.useState(false);
  const [popupId, setPopupId] = React.useState<number | null>(null);
  const [highlightedId, setHighlightedId] = React.useState<number | null>(null);

  // Tier C1: listen for citation clicks in the message text. When the user
  // clicks `[2]` in the response, this source chip — if it owns that
  // neuron_id — auto-opens and highlights the row.
  const neuronIds = React.useMemo(() => new Set(scores.map(s => s.neuron_id)), [scores]);
  React.useEffect(() => {
    const onCite = (e: Event) => {
      const ev = e as CustomEvent<{ neuronId: number }>;
      const nid = ev.detail?.neuronId;
      if (nid == null || !neuronIds.has(nid)) return;
      setOpen(true);
      setHighlightedId(nid);
      // Clear the highlight after a couple seconds so it's a pulse, not a lock.
      setTimeout(() => setHighlightedId(prev => (prev === nid ? null : prev)), 2400);
    };
    window.addEventListener('chat-citation-click', onCite);
    return () => window.removeEventListener('chat-citation-click', onCite);
  }, [neuronIds]);

  // Regulatory (engram) sources come from the citation map, not neuron_scores.
  const engramSources = React.useMemo(() => {
    const seen = new Map<number, string>();
    for (const src of Object.values(citationMap ?? {})) {
      if (src.kind === 'engram' && !seen.has(src.id)) {
        seen.set(src.id, src.label ?? `Regulation ${src.id}`);
      }
    }
    return Array.from(seen, ([id, label]) => ({ id, label }));
  }, [citationMap]);
  const totalCount = scores.length + engramSources.length;
  if (totalCount === 0) return null;
  // Top-ranked first — every source is shown (no cap), each opens a popup
  // showing its full neuron content when clicked.
  const ranked = [...scores].sort((a, b) => (b.combined ?? 0) - (a.combined ?? 0));
  return (
    <div className="chat-sources">
      <button
        className={`chat-sources-trigger${open ? ' open' : ''}`}
        onClick={() => setOpen(o => !o)}
        title="Internal documentation consulted for this answer"
      >
        <span className="chat-sources-icon" aria-hidden="true">◆</span>
        <span>{totalCount} {totalCount === 1 ? 'source' : 'sources'}</span>
        <span className="chat-sources-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <ul className="chat-sources-list">
          {ranked.map(s => (
            <li key={s.neuron_id}>
              <button
                type="button"
                className={`chat-sources-item chat-sources-item-button${highlightedId === s.neuron_id ? ' highlighted' : ''}`}
                onClick={() => setPopupId(s.neuron_id)}
                title="Click to view full source content"
              >
                <span className="chat-sources-label">{s.label || `Source #${s.neuron_id}`}</span>
                {s.department && <span className="chat-sources-dept">{s.department}</span>}
              </button>
            </li>
          ))}
          {engramSources.map(e => (
            <li key={`engram-${e.id}`}>
              <div className="chat-sources-item chat-sources-item-regulatory">
                <span className="chat-sources-label">{e.label}</span>
                <span className="chat-sources-dept">REGULATORY · eCFR</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {popupId != null && <SourcePopup neuronId={popupId} onClose={() => setPopupId(null)} />}
    </div>
  );
}

// Modal that fetches + displays full neuron content when a source chip is
// clicked. Dismissed via the × button, clicking the backdrop, or Esc.
function SourcePopup({ neuronId, onClose }: { neuronId: number; onClose: () => void }) {
  const [detail, setDetail] = React.useState<import('../types').NeuronDetail | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchNeuron(neuronId)
      .then(d => { if (!cancelled) setDetail(d); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [neuronId]);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="chat-source-popup-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="chat-source-popup" onClick={e => e.stopPropagation()}>
        <button className="chat-source-popup-close" onClick={onClose} aria-label="Close">×</button>
        {loading && <div className="chat-source-popup-loading">Loading…</div>}
        {error && <div className="chat-source-popup-error">Failed to load: {error}</div>}
        {detail && (
          <>
            <h3 className="chat-source-popup-title">{detail.label || `Source #${detail.id}`}</h3>
            <div className="chat-source-popup-meta">
              <span className="chat-source-popup-chip">L{detail.layer} · {detail.node_type}</span>
              {detail.department && <span className="chat-source-popup-chip">{detail.department}</span>}
              {detail.role_key && <span className="chat-source-popup-chip">{detail.role_key}</span>}
              {!detail.is_active && <span className="chat-source-popup-chip chat-source-popup-inactive">inactive</span>}
            </div>
            {detail.summary && (
              <div className="chat-source-popup-summary">{detail.summary}</div>
            )}
            {detail.content
              ? <pre className="chat-source-popup-content">{detail.content}</pre>
              : <div className="chat-source-popup-empty">(no content)</div>}
          </>
        )}
      </div>
    </div>
  );
}
