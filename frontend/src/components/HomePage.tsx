import { useCallback, useEffect, useRef, useState } from 'react';
import { getTenantConfig, type SeedPrompt } from '../config';
import {
  sendChat, submitQueryStream, createSession, listSessions, getSession,
  appendMessage, generateSessionTitle, deleteSession,
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

const PIPELINE_STAGE_LABELS: Record<string, string> = {
  input_guard: 'Guard',
  structural_resolve: 'Resolve',
  embed_query: 'Embed',
  classify: 'Classify',
  semantic_prefilter: 'Prefilter',
  score_neurons: 'Score',
  spread_activation: 'Spread',
  assemble_prompt: 'Assemble',
  execute_llm: 'Execute',
  output_checks: 'Checks',
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

export default function HomePage({ onNavigate }: { onNavigate: (tab: string) => void }) {
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
        // Neuron-enriched path
        const recentHistory = messages.slice(-10);
        let userMessage = text;
        if (recentHistory.length > 0) {
          const historyLines = recentHistory.map(m =>
            `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.text.slice(0, 500)}`
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

      // Open neuron sidebar on first message of session (only if neurons used)
      if (isFirstMessage && useNeurons && !neuronSidebarOpenedRef.current && assistantMsg.neuron_scores && assistantMsg.neuron_scores.length > 0) {
        setNeuronSidebarOpen(true);
        neuronSidebarOpenedRef.current = true;
      }

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
    console.log('[CONDENSE] called, messages:', messages.length, 'condensing:', condensing);
    if (messages.length <= 4 || condensing) { console.log('[CONDENSE] skipped — guard'); return; }
    setCondensing(true);
    console.log('[CONDENSE] starting Haiku call...');
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
          <p className="chat-hero-subtitle">{getTenantConfig()?.description ?? 'Domain-enriched AI assistant'}</p>
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
          <div className="chat-hero-links">
            <button onClick={() => onNavigate('query')}>Query Lab</button>
            <button onClick={() => onNavigate('explorer')}>Explorer</button>
            <button onClick={() => onNavigate('dashboard')}>Dashboard</button>
          </div>
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
                  {msg.role === 'assistant' && (msg.model || msg.neurons_activated != null) && (
                    <div className="chat-meta">
                      {msg.model && <span>{msg.model}</span>}
                      {msg.neurons_activated != null && <span>{msg.neurons_activated} neurons</span>}
                      {msg.tokens && <span>{(msg.tokens.input + msg.tokens.output).toLocaleString()} tokens</span>}
                      {msg.cost != null && <span>${msg.cost.toFixed(4)}</span>}
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
                  {stageKeys.map((key, idx) => {
                    const isNeuronOnly = NEURON_ONLY_STAGES.has(key);
                    const skipped = !useNeurons && isNeuronOnly;
                    const ev = pipelineStages[key];
                    const laterHasEvent = stageKeys.slice(idx + 1).some(k => pipelineStages[k]);
                    const isDone = skipped || ev?.status === 'done' || ev?.status === 'skipped' || laterHasEvent;
                    const isActive = !skipped && ev?.status === 'active' && !laterHasEvent;
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
                    }
                    return (
                      <div key={key} className={`chat-stage-row${isDone ? ' done' : ''}${isActive ? ' active' : ''}${skipped ? ' skipped' : ''}`}>
                        <span className="chat-stage-dot" />
                        <span className="chat-stage-name">{PIPELINE_STAGE_LABELS[key]}</span>
                        {timing && <span className="chat-stage-time">{timing}</span>}
                      </div>
                    );
                  })}
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
