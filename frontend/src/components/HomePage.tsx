import React, { useCallback, useEffect, useRef, useState } from 'react';
import { getTenantConfig, type SeedPrompt } from '../config';
import {
  sendChat, submitQueryStream, createSession, listSessions, getSession,
  appendMessage, generateSessionTitle, deleteSession, fetchNeuron,
  type ChatMessage, type ChatResponse, type StageEvent, type SlotSpec, type SessionSummary,
} from '../api';
import type { NeuronScoreResponse } from '../types';
import { useModels } from '../hooks/useModels';
import { marked } from 'marked';
import NeuronTreeViz from './NeuronTreeViz';

marked.setOptions({ breaks: true, gfm: true });

interface Message {
  role: 'user' | 'assistant';
  text: string;
  model?: string;
  tokens?: { input: number; output: number };
  cost?: number;
  neurons_activated?: number;
  neuron_scores?: NeuronScoreResponse[];
  isCondensed?: boolean;
  condensedOriginals?: { role: string; text: string }[];
}

function relativeTime(iso: string): string {
  const d = new Date(iso);
  const now = Date.now();
  const diffMs = now - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay === 1) return 'Yesterday';
  if (diffDay < 7) return `${diffDay}d ago`;
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
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
  const [useNeurons, setUseNeurons] = useState(true);
  const { models: availableModels, grouped: groupedModels } = useModels();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [currentSessionId, setCurrentSessionId] = useState<number | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
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
    const userMsg: Message = { role: 'user', text };
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
        if (recentHistory.length > 0) {
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

        const slot: SlotSpec = { mode: `${model}_neuron`, token_budget: 8000, top_k: 60 };
        const { promise } = submitQueryStream(
          userMessage,
          (event: StageEvent) => setPipelineStages(prev => ({ ...prev, [event.stage]: event })),
          priorNeuronIds.length > 0 ? priorNeuronIds : undefined,
          [slot],
        );
        const res = await promise;
        const slotResult = res.slots[0];
        assistantMsg = {
          role: 'assistant',
          text: slotResult?.response ?? '',
          model,
          tokens: {
            input: (res.classify_input_tokens || 0) + (slotResult?.input_tokens || 0),
            output: (res.classify_output_tokens || 0) + (slotResult?.output_tokens || 0),
          },
          cost: res.total_cost || 0,
          neurons_activated: res.neurons_activated,
          neuron_scores: res.neuron_scores,
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
      setMessages(prev => [...prev, { role: 'assistant', text: `Error: ${e instanceof Error ? e.message : 'Failed'}` }]);
    } finally {
      setLoading(false);
      setPipelineStages({});
      setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
    }
  }

  function startNewChat() {
    setCurrentSessionId(null);
    setMessages([]);
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
  // input_tokens — this is what the LLM actually received and accounts for
  // the assembled prompt + conversation history + neuron context. Summing
  // across turns would double-count history (each turn's prompt already
  // re-includes prior exchanges).
  const lastAssistantTokens = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.role === 'assistant' && typeof m.tokens?.input === 'number') {
        return m.tokens.input;
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
        <select className="chat-model-select" value={model} onChange={e => setModel(e.target.value)}>
          {Object.entries(groupedModels).map(([group, models]) => ([
            <option key={`hdr-${group}`} disabled>── {group} ──</option>,
            ...models.map(m => <option key={m.display_name} value={m.display_name}>{m.display_name}</option>),
          ]))}
        </select>
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
        <div className="chat-context-bar" title="Tokens the model received on the last turn — the higher this goes, the more the model has to hold in working memory. Near 80% the bar turns amber so you can summarize older exchanges before the session risks collapse.">
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
                  <span className="chat-session-title">{s.title || 'Untitled'}</span>
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
          <div className="chat-sidebar-list">
            {sessions.map(s => (
              <div
                key={s.id}
                className={`chat-session-item${currentSessionId === s.id ? ' active' : ''}`}
                onClick={() => loadSession(s.id)}
              >
                <span className="chat-session-title">{s.title || 'Untitled'}</span>
                <span className="chat-session-time">{relativeTime(s.updated_at)}</span>
                <button className="chat-session-del" onClick={e => { e.stopPropagation(); archiveSession(s.id); }}>×</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Messages */}
      <div className="chat-main">
        <div className="chat-messages">
          {messages.map((msg, i) => {
            if (msg.isCondensed) return <CondensedMessage key={i} msg={msg} />;
            return (
              <div key={i} className={`chat-msg chat-msg--${msg.role}`}>
                <div className="chat-bubble">
                  {msg.role === 'assistant' ? (
                    <div className="chat-text markdown-body" dangerouslySetInnerHTML={{ __html: marked.parse(msg.text, { async: false }) as string }} />
                  ) : (
                    <div className="chat-text">{msg.text}</div>
                  )}
                  {msg.role === 'assistant' && msg.neuron_scores && msg.neuron_scores.length > 0 && (
                    <ChatSourcesChip scores={msg.neuron_scores} />
                  )}
                  {msg.role === 'assistant' && (msg.model || msg.neurons_activated != null) && (
                    <div className="chat-meta">
                      {msg.model && <span>{msg.model}</span>}
                      {msg.tokens && <span>{(msg.tokens.input + msg.tokens.output).toLocaleString()} tokens</span>}
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
                    // Compute the single active stage up-front. Backend emits
                    // only `done` events, so the running stage is inferred as
                    // the FIRST stage (in chain order) that has no event yet,
                    // skipping stages that are disabled (neuron-only when
                    // useNeurons is off). Returns -1 if not loading or all
                    // stages are already reported.
                    const firstUnreportedIdx = (() => {
                      if (!loading) return -1;
                      for (let i = 0; i < stageKeys.length; i++) {
                        const k = stageKeys[i];
                        if (!useNeurons && NEURON_ONLY_STAGES.has(k)) continue;
                        if (pipelineStages[k]) continue;
                        return i;
                      }
                      return -1;
                    })();
                    return stageKeys.map((key, idx) => {
                    const isNeuronOnly = NEURON_ONLY_STAGES.has(key);
                    const skipped = !useNeurons && isNeuronOnly;
                    const ev = pipelineStages[key];
                    // Only the first unreported stage pulses. Stages before it
                    // are done (either reported explicitly, skipped, or so fast
                    // they completed without emitting). Stages after it are
                    // pending (render as grey).
                    const isActive = !skipped && idx === firstUnreportedIdx;
                    const isDone = !isActive && (skipped || !!ev || (firstUnreportedIdx !== -1 && idx < firstUnreportedIdx) || (firstUnreportedIdx === -1 && !!ev));
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

function ChatSourcesChip({ scores }: { scores: NeuronScoreResponse[] }) {
  const [open, setOpen] = React.useState(false);
  const [popupId, setPopupId] = React.useState<number | null>(null);
  if (!scores.length) return null;
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
        <span>{scores.length} {scores.length === 1 ? 'source' : 'sources'}</span>
        <span className="chat-sources-chevron">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <ul className="chat-sources-list">
          {ranked.map(s => (
            <li key={s.neuron_id}>
              <button
                type="button"
                className="chat-sources-item chat-sources-item-button"
                onClick={() => setPopupId(s.neuron_id)}
                title="Click to view full source content"
              >
                <span className="chat-sources-label">{s.label || `Source #${s.neuron_id}`}</span>
                {s.department && <span className="chat-sources-dept">{s.department}</span>}
              </button>
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
