import { useState, useEffect, useCallback } from 'react';
import { listAgents, getAgent, triggerAgentRun } from '../api/governance';
import type { AgentSummary, AgentDetail } from '../api/governance';

interface RunResult {
  action_id: number;
  summary: string;
}

const ROLE_LABELS: Record<string, string> = {
  proposal_curator: 'Proposal curator',
};

function friendlyRole(role: string): string {
  return ROLE_LABELS[role] ?? role.replace(/_/g, ' ');
}

export function friendlyName(name: string): string {
  return name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

// Agents whose manual run needs a target id (mirrors the backend's
// _RUN_PROFILES — the API 400s without it).
const REQUIRED_CONTEXT: Record<string, { key: string; hint: string }> = {
  neuron_placer: { key: 'artifact_id', hint: 'ID of the artifact proposal to place' },
};

type RunContext =
  | { ok: true; context: Record<string, unknown> }
  | { ok: false; error?: string };

function buildRunContext(name: string): RunContext {
  const needed = REQUIRED_CONTEXT[name];
  if (!needed) return { ok: true, context: {} };
  const raw = window.prompt(`Agent "${name}" needs ${needed.key} (${needed.hint}):`);
  if (!raw || !raw.trim()) return { ok: false };
  const parsed = Number(raw.trim());
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return { ok: false, error: `${needed.key} must be a positive integer` };
  }
  return { ok: true, context: { [needed.key]: parsed } };
}

function triggerSummary(agent: AgentSummary): string {
  const parts: string[] = [];
  if (agent.manual_trigger) parts.push('Admin-triggered');
  if (agent.schedule_enabled) parts.push('Scheduled');
  if (parts.length === 0) parts.push('Not currently triggerable');
  return parts.join(' · ');
}

export default function AgentsPage({ onOpenAgent }: {
  /** When set, each card shows a pop-out button opening that agent in its own window. */
  onOpenAgent?: (name: string) => void;
} = {}) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<Record<string, RunResult>>({});
  const [runErrors, setRunErrors] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await listAgents();
      setAgents(rows);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onRun = async (name: string) => {
    const rc = buildRunContext(name);
    if (!rc.ok) {
      if (rc.error) setRunErrors(prev => ({ ...prev, [name]: rc.error! }));
      return;
    }
    const context = rc.context;
    setTriggering(name);
    setRunErrors(prev => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    try {
      const r = await triggerAgentRun(name, context);
      setRunResults(prev => ({
        ...prev,
        [name]: { action_id: r.action_id, summary: r.summary || '(no summary)' },
      }));
    } catch (e) {
      setRunErrors(prev => ({ ...prev, [name]: String(e) }));
    } finally {
      setTriggering(null);
    }
  };

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1000, margin: '0 auto' }}>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: '1.4rem', margin: 0, fontWeight: 700 }}>Agents</h1>
        <p style={{ color: 'var(--text-dim)', marginTop: 6, fontSize: '0.85rem', lineHeight: 1.5 }}>
          Autonomous maintenance agents that curate the knowledge graph. Each agent reviews a specific
          kind of finding and files proposals into the review queue — no agent can mutate neurons
          directly. Use this page to understand what each agent does before enabling it or running it
          manually.
        </p>
      </header>

      {loading && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>Loading agents...</div>
      )}
      {err && (
        <div style={{
          color: '#e74c3c', fontSize: '0.82rem', padding: '10px 14px',
          background: '#e74c3c11', borderRadius: 8, border: '1px solid #e74c3c33',
        }}>{err}</div>
      )}
      {!loading && !err && agents.length === 0 && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
          No agents are currently registered for this tenant.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {agents.map(a => (
          <AgentCard
            key={a.name}
            agent={a}
            triggering={triggering === a.name}
            runResult={runResults[a.name]}
            runError={runErrors[a.name]}
            onRun={() => onRun(a.name)}
            onPopOut={onOpenAgent ? () => onOpenAgent(a.name) : undefined}
          />
        ))}
      </div>
    </div>
  );
}

// Renders one agent in its own window on the desktop. Fetches the agent
// list itself and manages its own run state, so the window is fully
// self-contained (survives the Agents list window being closed).
export function SingleAgentPane({ name }: { name: string }) {
  const [agent, setAgent] = useState<AgentSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [runResult, setRunResult] = useState<RunResult | undefined>(undefined);
  const [runError, setRunError] = useState<string | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    listAgents()
      .then(rows => {
        if (cancelled) return;
        const a = rows.find(r => r.name === name) ?? null;
        setAgent(a);
        if (!a) setErr(`Agent "${name}" is not registered for this tenant.`);
      })
      .catch(e => { if (!cancelled) setErr(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [name]);

  const onRun = async () => {
    const rc = buildRunContext(name);
    if (!rc.ok) {
      if (rc.error) setRunError(rc.error);
      return;
    }
    setTriggering(true);
    setRunError(undefined);
    try {
      const r = await triggerAgentRun(name, rc.context);
      setRunResult({ action_id: r.action_id, summary: r.summary || '(no summary)' });
    } catch (e) {
      setRunError(String(e));
    } finally {
      setTriggering(false);
    }
  };

  return (
    <div style={{ padding: '20px 22px' }}>
      {loading && <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>Loading agent...</div>}
      {err && (
        <div style={{
          color: '#e74c3c', fontSize: '0.82rem', padding: '10px 14px',
          background: '#e74c3c11', borderRadius: 8, border: '1px solid #e74c3c33',
        }}>{err}</div>
      )}
      {agent && (
        <AgentCard
          agent={agent}
          triggering={triggering}
          runResult={runResult}
          runError={runError}
          onRun={onRun}
        />
      )}
    </div>
  );
}

function AgentCard({ agent, triggering, runResult, runError, onRun, onPopOut }: {
  agent: AgentSummary;
  triggering: boolean;
  runResult?: RunResult;
  runError?: string;
  onRun: () => void;
  onPopOut?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<AgentDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);

  const toggle = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !detail && !detailErr) {
      try {
        const d = await getAgent(agent.name);
        setDetail(d);
      } catch (e) {
        setDetailErr(String(e));
      }
    }
  };

  const summary = agent.admin_description?.trim() || agent.description;

  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10,
      padding: '16px 20px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
        gap: 16, flexWrap: 'wrap',
      }}>
        <div style={{ flex: '1 1 auto', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: '1.05rem', fontWeight: 700, margin: 0 }}>
              {friendlyName(agent.name)}
            </h2>
            <code style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>{agent.name}</code>
          </div>
          <div style={{
            fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: 3,
            display: 'flex', gap: 14, flexWrap: 'wrap',
          }}>
            <span>{friendlyRole(agent.role)}</span>
            <span>· {agent.model}</span>
            <span>· {agent.tool_count} tool{agent.tool_count === 1 ? '' : 's'}</span>
            <span>· {triggerSummary(agent)}</span>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
          <button
            onClick={onRun}
            disabled={!agent.manual_trigger || triggering}
            style={{
              padding: '7px 18px', borderRadius: 6, border: 'none',
              cursor: agent.manual_trigger && !triggering ? 'pointer' : 'not-allowed',
              fontSize: '0.8rem', fontWeight: 600,
              background: agent.manual_trigger ? 'var(--accent)' : 'var(--border)',
              color: '#fff', opacity: triggering ? 0.7 : 1,
            }}
          >
            {triggering ? 'Running...' : 'Run now'}
          </button>
          {onPopOut && (
            <button
              onClick={onPopOut}
              title="Open this agent in its own window"
              style={{
                padding: '7px 10px', borderRadius: 6,
                border: '1px solid var(--border)', background: 'none',
                color: 'var(--text-dim)', cursor: 'pointer', fontSize: '0.8rem',
              }}
            >
              ⧉
            </button>
          )}
        </div>
      </div>

      <p style={{
        fontSize: '0.86rem', lineHeight: 1.55, color: 'var(--text)',
        marginTop: 12, marginBottom: 0, whiteSpace: 'pre-wrap',
      }}>
        {summary}
      </p>

      {runResult && (
        <div style={{
          marginTop: 12, color: '#4caf50', fontSize: '0.8rem', padding: '8px 12px',
          background: '#4caf5011', borderRadius: 6, border: '1px solid #4caf5033',
        }}>
          Run #{runResult.action_id} completed — {runResult.summary}
        </div>
      )}
      {runError && (
        <div style={{
          marginTop: 12, color: '#e74c3c', fontSize: '0.8rem', padding: '8px 12px',
          background: '#e74c3c11', borderRadius: 6, border: '1px solid #e74c3c33',
        }}>{runError}</div>
      )}

      <button
        onClick={toggle}
        style={{
          marginTop: 14, background: 'transparent', border: 'none',
          color: 'var(--accent)', cursor: 'pointer', fontSize: '0.78rem',
          padding: 0, fontWeight: 600,
        }}
      >
        {expanded ? '▾ Hide technical details' : '▸ Show technical details'}
      </button>

      {expanded && (
        <div style={{
          marginTop: 12, padding: '12px 14px',
          background: 'var(--bg-elevated, rgba(0,0,0,0.03))',
          borderRadius: 6, fontSize: '0.78rem',
        }}>
          {detailErr && (
            <div style={{ color: '#e74c3c' }}>{detailErr}</div>
          )}
          {!detail && !detailErr && (
            <div style={{ color: 'var(--text-dim)' }}>Loading details...</div>
          )}
          {detail && (
            <>
              <Detail label="Role" value={detail.role} />
              <Detail label="Model" value={detail.model} />
              <Detail label="Max turns per run" value={String(detail.max_turns)} />
              <div style={{ marginTop: 8 }}>
                <div style={{ color: 'var(--text-dim)', marginBottom: 4 }}>Tools the agent can call:</div>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {detail.tool_allow_list.map(t => (
                    <li key={t}><code style={{ fontSize: '0.76rem' }}>{t}</code></li>
                  ))}
                </ul>
              </div>
              <div style={{ marginTop: 10 }}>
                <div style={{ color: 'var(--text-dim)', marginBottom: 4 }}>System prompt (first 500 chars):</div>
                <pre style={{
                  margin: 0, whiteSpace: 'pre-wrap', fontSize: '0.72rem',
                  background: 'transparent', padding: 0, color: 'var(--text)',
                }}>{detail.system_prompt_preview}</pre>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 4 }}>
      <span style={{ color: 'var(--text-dim)', minWidth: 140 }}>{label}:</span>
      <span>{value}</span>
    </div>
  );
}
