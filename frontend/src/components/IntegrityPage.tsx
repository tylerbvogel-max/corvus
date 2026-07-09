import { useState, useEffect, useCallback } from 'react';
import {
  fetchIntegrityDashboard,
  fetchIntegrityFindings,
  fetchIntegrityFindingDetail,
  resolveIntegrityFinding,
  dismissIntegrityFinding,
  bulkResolveIntegrityFindings,
  proposeIntegrityFinding,
  runHomeostasisScan,
  runDuplicatesScan,
  runConnectionsScan,
  runConflictsScan,
  runAgingScan,
  applyHomeostasisScan,
  fetchStats,
  listIntegrityRuns,
  listAgents,
  getAgentRun,
  triggerAgentRun,
} from '../api';
import type {
  IntegrityDashboard,
  IntegrityFinding,
  IntegrityFindingDetail,
  IntegrityScanResponse,
  IntegrityWeightDistribution,
} from '../types';
import type { IntegrityRun, AgentSummary, AgentRunDetail } from '../api';
import { useListKeyboardNav } from '../hooks/useListKeyboardNav';

type Panel = 'dashboard' | 'scan' | 'findings';

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#e74c3c',
  warning: '#f59e0b',
  info: '#3b82f6',
};

const STATUS_COLORS: Record<string, string> = {
  open: '#f59e0b',
  proposed: '#3b82f6',
  resolved: '#4caf50',
  dismissed: '#888',
};

const FINDING_TYPE_LABELS: Record<string, string> = {
  weight_inflation: 'Weight Imbalance',
  near_duplicate: 'Near Duplicate',
  missing_connection: 'Missing Connection',
  contradiction: 'Contradiction',
  stale_content: 'Stale Content',
};

const SCAN_TYPE_LABELS: Record<string, string> = {
  homeostasis: 'Homeostasis',
  duplicate_detection: 'Duplicate Detection',
  missing_connections: 'Missing Connections',
  conflict_detection: 'Conflict Detection',
  aging_review: 'Aging Review',
};

// Graph-modifying resolutions → create a proposal for approval
const PROPOSAL_RESOLUTIONS: Record<string, Array<{ label: string; value: string }>> = {
  near_duplicate: [
    { label: 'Merge', value: 'merged' },
    { label: 'Differentiate', value: 'differentiated' },
  ],
  missing_connection: [
    { label: 'Link', value: 'linked' },
  ],
  contradiction: [
    { label: 'A Correct', value: 'a_correct' },
    { label: 'B Correct', value: 'b_correct' },
    { label: 'Add Context', value: 'context_added' },
  ],
};

// Non-graph resolutions → resolve directly
const DIRECT_RESOLUTIONS: Record<string, Array<{ label: string; value: string }>> = {
  stale_content: [
    { label: 'Mark Reviewed', value: 'reviewed' },
    { label: 'Flag for Update', value: 'flagged' },
  ],
};

const selectStyle: React.CSSProperties = {
  background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
  borderRadius: 4, padding: '4px 8px', fontSize: '0.8rem',
};

const btnStyle: React.CSSProperties = {
  padding: '4px 12px', borderRadius: 4, border: '1px solid var(--border)',
  background: 'var(--bg-card)', color: 'var(--text)', cursor: 'pointer',
  fontSize: '0.8rem', fontWeight: 600,
};

function SeverityBadge({ severity }: { severity: string }) {
  const color = SEVERITY_COLORS[severity] || '#888';
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 4,
      fontSize: '0.7rem', fontWeight: 600,
      background: color + '22', color, border: `1px solid ${color}44`,
    }}>
      {severity}
    </span>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || '#888';
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 4,
      fontSize: '0.7rem', fontWeight: 600,
      background: color + '22', color, border: `1px solid ${color}44`,
    }}>
      {status}
    </span>
  );
}

// ── Dashboard Panel ──────────────────────────────────────────────

function KindBadge({ kind }: { kind: string }) {
  // "scan:<type>" in warm, "agent:<name>" in accent.
  const isAgent = kind.startsWith('agent:');
  const color = isAgent ? 'var(--accent)' : '#f59e0b';
  const label = isAgent
    ? `agent · ${kind.slice(6)}`
    : `scan · ${SCAN_TYPE_LABELS[kind.slice(5)] || kind.slice(5)}`;
  return (
    <span style={{
      display: 'inline-block', padding: '1px 8px', borderRadius: 4,
      fontSize: '0.7rem', fontWeight: 600,
      background: `${color}22`, color, border: `1px solid ${color}44`,
    }}>
      {label}
    </span>
  );
}

function outcomeLabel(row: IntegrityRun): string {
  if (row.kind.startsWith('scan:')) {
    const n = row.findings_count;
    return n === null || n === undefined ? '—' : `${n} finding${n === 1 ? '' : 's'}`;
  }
  if (row.kind.startsWith('agent:')) {
    const parts: string[] = [];
    if (row.tool_calls !== null && row.tool_calls !== undefined) parts.push(`${row.tool_calls} calls`);
    if (row.mutations !== null && row.mutations !== undefined) parts.push(`${row.mutations} mutations`);
    if (row.errors !== null && row.errors !== undefined && row.errors > 0) parts.push(`${row.errors} errors`);
    return parts.length > 0 ? parts.join(', ') : '—';
  }
  return '—';
}

function RunDetailModal({ row, onClose }: { row: IntegrityRun; onClose: () => void }) {
  const [detail, setDetail] = useState<AgentRunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isAgent = row.kind.startsWith('agent:');

  useEffect(() => {
    if (!isAgent) return;
    setLoading(true);
    getAgentRun(row.id)
      .then(d => { setDetail(d); setErr(null); })
      .catch(e => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [isAgent, row.id]);

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--bg-card)', color: 'var(--text)',
          border: '1px solid var(--border)', borderRadius: 10,
          width: '86%', maxWidth: 960, maxHeight: '86vh',
          overflow: 'auto', padding: 20,
          boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>Run #{row.id}</span>
            <KindBadge kind={row.kind} />
            <StatusBadge status={row.state} />
          </div>
          <button onClick={onClose} style={btnStyle}>Close</button>
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginBottom: 12 }}>
          Initiated by <strong style={{ color: 'var(--text)' }}>{row.initiated_by}</strong>
          {row.completed_at && <> · completed {new Date(row.completed_at).toLocaleString()}</>}
          {isAgent && (
            <> · {row.turns ?? '—'} turns · {row.tool_calls ?? '—'} calls · {row.mutations ?? '—'} mutations · {row.errors ?? '—'} errors</>
          )}
          {!isAgent && row.scan_scope && <> · scope <code>{row.scan_scope}</code> · {row.findings_count ?? 0} findings</>}
        </div>
        {row.summary && (
          <div style={{ fontStyle: 'italic', color: 'var(--text-dim)', fontSize: '0.8rem', marginBottom: 12 }}>
            {row.summary}
          </div>
        )}

        {isAgent && (
          <div>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: 6 }}>Tool-call trace</div>
            {loading && <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>Loading trace...</div>}
            {err && <div style={{ color: '#e74c3c', fontSize: '0.78rem' }}>{err}</div>}
            {detail && (
              <table className="score-table" style={{ width: '100%', fontSize: '0.72rem' }}>
                <thead>
                  <tr>
                    <th>#</th><th>Tool</th><th>State</th><th>Reason</th><th>Input</th><th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.child_actions.map((c, i) => (
                    <tr key={c.action_id}>
                      <td>{i + 1}</td>
                      <td><code>{c.tool}</code></td>
                      <td><StatusBadge status={c.state} /></td>
                      <td style={{ maxWidth: 200, color: 'var(--text-dim)' }}>{c.reason || '—'}</td>
                      <td style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        <code style={{ fontSize: '0.68rem' }}>{JSON.stringify(c.input)}</code>
                      </td>
                      <td style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {c.error
                          ? <span style={{ color: '#e74c3c' }}>{c.error}</span>
                          : <code style={{ fontSize: '0.68rem' }}>{JSON.stringify(c.result)}</code>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {!isAgent && (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-dim)' }}>
            Scan row — no tool-call trace. Open the Findings Queue tab to inspect findings from this scan.
          </div>
        )}
      </div>
    </div>
  );
}

function DashboardPanel() {
  const [data, setData] = useState<IntegrityDashboard | null>(null);
  const [runs, setRuns] = useState<IntegrityRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<IntegrityRun | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [dash, runList] = await Promise.all([
        fetchIntegrityDashboard(),
        listIntegrityRuns(50),
      ]);
      setData(dash);
      setRuns(runList);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div style={{ color: 'var(--text-dim)', padding: 16, fontSize: '0.85rem' }}>Loading dashboard...</div>;
  if (error) return <div style={{ color: '#e74c3c', padding: 16, fontSize: '0.85rem' }}>{error}</div>;
  if (!data) return null;

  const findingTypes = ['weight_inflation', 'near_duplicate', 'missing_connection', 'contradiction', 'stale_content'];
  const severities = ['critical', 'warning', 'info'];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Open findings total */}
      <div style={{ fontSize: '0.85rem', color: 'var(--text-dim)' }}>
        <strong style={{ color: 'var(--text)', fontSize: '1.1rem' }}>{data.open_findings_total}</strong> open findings
      </div>

      {/* Finding type cards */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {findingTypes.map(t => (
          <div key={t} style={{
            background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6,
            padding: '10px 14px', minWidth: 120, flex: '1 1 120px',
          }}>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginBottom: 4 }}>
              {FINDING_TYPE_LABELS[t] || t}
            </div>
            <div style={{ fontSize: '1.2rem', fontWeight: 700, color: 'var(--text)' }}>
              {data.open_by_type[t] || 0}
            </div>
          </div>
        ))}
      </div>

      {/* Severity breakdown */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginRight: 4 }}>By severity:</span>
        {severities.map(s => {
          const count = data.open_by_severity[s] || 0;
          const color = SEVERITY_COLORS[s];
          return (
            <span key={s} style={{
              padding: '2px 8px', borderRadius: 10,
              background: color + '22', color, fontWeight: 600, fontSize: '0.75rem',
            }}>
              {s}: {count}
            </span>
          );
        })}
      </div>

      {/* Unified recent runs */}
      <div>
        <div style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8,
        }}>
          <div style={{ fontSize: '0.85rem', fontWeight: 600 }}>Recent runs</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
            Scans and agent runs, newest first
          </div>
        </div>
        {runs.length === 0 ? (
          <div style={{ color: 'var(--text-dim)', fontSize: '0.8rem' }}>No runs yet.</div>
        ) : (
          <table className="score-table" style={{ width: '100%', fontSize: '0.75rem' }}>
            <thead>
              <tr>
                <th>#</th>
                <th>When</th>
                <th>Kind</th>
                <th>Initiated by</th>
                <th>State</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(r => (
                <tr
                  key={`${r.kind}-${r.id}`}
                  onClick={() => setSelectedRun(r)}
                  style={{
                    cursor: 'pointer',
                    background: r.errors && r.errors > 0 ? 'rgba(245, 158, 11, 0.08)' : undefined,
                  }}
                >
                  <td>{r.id}</td>
                  <td>{r.completed_at ? new Date(r.completed_at).toLocaleString()
                       : r.started_at ? new Date(r.started_at).toLocaleString() : '—'}</td>
                  <td><KindBadge kind={r.kind} /></td>
                  <td>{r.initiated_by}</td>
                  <td><StatusBadge status={r.state} /></td>
                  <td>{outcomeLabel(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {selectedRun && <RunDetailModal row={selectedRun} onClose={() => setSelectedRun(null)} />}
    </div>
  );
}

// ── Scan Panel ───────────────────────────────────────────────────

function SegmentedControl({ options, value, onChange }: {
  options: Array<{ value: string; label: string }>; value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{
      display: 'inline-flex', background: 'var(--bg-input)', borderRadius: 6,
      padding: 2, gap: 1,
    }}>
      {options.map(opt => {
        const active = value === opt.value;
        return (
          <button key={opt.value} onClick={() => onChange(opt.value)} style={{
            padding: '5px 14px', borderRadius: 5, border: 'none', cursor: 'pointer',
            fontSize: '0.75rem', fontWeight: 600, transition: 'all 0.15s ease',
            background: active ? 'var(--accent)' : 'transparent',
            color: active ? '#fff' : 'var(--text-dim)',
            boxShadow: active ? '0 1px 3px rgba(0,0,0,0.2)' : 'none',
          }}>
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function ChipSelect({ options, value, onChange }: {
  options: Array<{ value: string; label: string }>; value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {options.map(opt => {
        const active = value === opt.value;
        return (
          <button key={opt.value} onClick={() => onChange(opt.value)} style={{
            padding: '4px 12px', borderRadius: 14, cursor: 'pointer',
            fontSize: '0.72rem', fontWeight: 500, transition: 'all 0.15s ease',
            border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}`,
            background: active ? 'var(--accent)' + '18' : 'transparent',
            color: active ? 'var(--accent)' : 'var(--text-dim)',
          }}>
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function Slider({ label, value, onChange, min, max, step, hint }: {
  label: string; value: number; onChange: (v: number) => void;
  min: number; max: number; step: number; hint?: string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>{label}</span>
        <span style={{
          fontSize: '0.75rem', fontWeight: 700, color: 'var(--text)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          {step < 1 ? value.toFixed(2) : value}
        </span>
      </div>
      <div style={{ position: 'relative', height: 20, display: 'flex', alignItems: 'center' }}>
        {/* Filled track */}
        <div style={{
          position: 'absolute', left: 0, top: '50%', transform: 'translateY(-50%)',
          width: '100%', height: 4, borderRadius: 2, background: 'var(--border)',
        }}>
          <div style={{
            width: `${pct}%`, height: '100%', borderRadius: 2,
            background: 'var(--accent)', transition: 'width 0.1s ease',
          }} />
        </div>
        <input type="range" min={min} max={max} step={step} value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          style={{
            position: 'relative', width: '100%', appearance: 'none',
            WebkitAppearance: 'none', background: 'transparent', cursor: 'pointer',
            height: 20, margin: 0,
          }} />
      </div>
      {hint && <div style={{ fontSize: '0.65rem', color: 'var(--text-dim)', opacity: 0.7, marginTop: -1 }}>{hint}</div>}
    </div>
  );
}

function ToggleSwitch({ label, checked, onChange, hint }: {
  label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string;
}) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      <div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>{label}</div>
        {hint && <div style={{ fontSize: '0.65rem', color: 'var(--text-dim)', opacity: 0.7 }}>{hint}</div>}
      </div>
      <button
        onClick={() => onChange(!checked)}
        style={{
          width: 36, height: 20, borderRadius: 10, border: 'none', cursor: 'pointer',
          background: checked ? 'var(--accent)' : 'var(--border)',
          position: 'relative', transition: 'background 0.2s ease', flexShrink: 0,
        }}
      >
        <div style={{
          width: 16, height: 16, borderRadius: '50%', background: '#fff',
          position: 'absolute', top: 2,
          left: checked ? 18 : 2,
          transition: 'left 0.2s ease',
          boxShadow: '0 1px 3px rgba(0,0,0,0.25)',
        }} />
      </button>
    </div>
  );
}

function AgentsCard({ onRan }: { onRan: () => void }) {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    listAgents()
      .then(setAgents)
      .catch(e => setErr(String(e)));
  }, []);

  const onRun = async (name: string) => {
    setTriggering(name);
    setErr(null);
    setMsg(null);
    try {
      const r = await triggerAgentRun(name, {});
      setMsg(`Run #${r.action_id} — ${r.summary || '(no summary)'}.`);
      onRan();
    } catch (e) {
      setErr(String(e));
    } finally {
      setTriggering(null);
    }
  };

  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10,
      padding: '14px 18px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
      display: 'flex', flexDirection: 'column', gap: 10,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <div>
          <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>Available agents</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 1 }}>
            Autonomous maintenance agents. Each run lands as an Action with a full tool-call trace.
          </div>
        </div>
      </div>
      {err && (
        <div style={{
          color: '#e74c3c', fontSize: '0.78rem', padding: '6px 10px',
          background: '#e74c3c11', borderRadius: 6, border: '1px solid #e74c3c33',
        }}>{err}</div>
      )}
      {msg && (
        <div style={{
          color: '#4caf50', fontSize: '0.78rem', padding: '6px 10px',
          background: '#4caf5011', borderRadius: 6, border: '1px solid #4caf5033',
        }}>{msg}</div>
      )}
      {agents.length === 0 && !err && (
        <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>No agents registered.</div>
      )}
      {agents.length > 0 && (
        <table className="score-table" style={{ width: '100%', fontSize: '0.75rem' }}>
          <thead>
            <tr>
              <th>Agent</th>
              <th>Role</th>
              <th>Description</th>
              <th>Model</th>
              <th>Tools</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {agents.map(a => (
              <tr key={a.name}>
                <td><code>{a.name}</code></td>
                <td>{a.role}</td>
                <td style={{ color: 'var(--text-dim)', maxWidth: 320 }}>{a.description}</td>
                <td style={{ color: 'var(--text-dim)' }}>{a.model}</td>
                <td>{a.tool_count}</td>
                <td>
                  <button
                    onClick={() => onRun(a.name)}
                    disabled={!a.manual_trigger || triggering === a.name}
                    style={{
                      padding: '5px 14px', borderRadius: 6, border: 'none', cursor: 'pointer',
                      fontSize: '0.75rem', fontWeight: 600,
                      background: 'var(--accent)', color: '#fff',
                    }}
                  >
                    {triggering === a.name ? 'Running...' : 'Run now'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ScanPanel({ onAgentRan }: { onAgentRan: () => void }) {
  const [running, setRunning] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, IntegrityScanResponse | null>>({});
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [departments, setDepartments] = useState<string[]>([]);

  // Scope: two-level selection
  const [scopeCategory, setScopeCategory] = useState<'global' | 'department' | 'layer'>('global');
  const [scopeDept, setScopeDept] = useState('');
  const [scopeLayer, setScopeLayer] = useState(0);

  const scope = scopeCategory === 'global' ? 'global'
    : scopeCategory === 'department' ? `department:${scopeDept}`
    : `layer:${scopeLayer}`;

  const scopeDisplay = scopeCategory === 'global' ? 'All neurons'
    : scopeCategory === 'department' ? (scopeDept || 'Select department')
    : `Layer ${scopeLayer}`;

  // Load departments once
  useEffect(() => {
    fetchStats().then(s => {
      const depts = Object.keys(s.by_department).sort();
      setDepartments(depts);
      if (depts.length > 0 && !scopeDept) setScopeDept(depts[0]);
    }).catch(() => {});
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Form states
  const [homeo, setHomeo] = useState({ scale_factor: 0.8, floor_threshold: 0.05 });
  const [dupes, setDupes] = useState({ similarity_threshold: 0.92, max_pairs: 100, cross_department_only: false });
  const [conns, setConns] = useState({ similarity_threshold: 0.65, max_suggestions: 50, exclude_same_parent: true });
  const [conflicts, setConflicts] = useState({ sim_min: 0.60, sim_max: 0.85, batch_size: 5, max_pairs: 200 });
  const [aging, setAging] = useState({ include_never_verified: true, min_invocations: 0 });

  const runScan = async (key: string, fn: () => Promise<IntegrityScanResponse>) => {
    setRunning(key);
    setError(null);
    setApplyMsg(null);
    try {
      const result = await fn();
      setResults(prev => ({ ...prev, [key]: result }));
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(null);
    }
  };

  const handleApply = async (scanId: number) => {
    setRunning('apply');
    setError(null);
    try {
      const result = await applyHomeostasisScan(scanId, 'admin');
      setApplyMsg(`Proposal #${result.proposal_id} created (${result.item_count} items). Review it in the Proposal Queue.`);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(null);
    }
  };

  const renderDistribution = (label: string, dist: IntegrityWeightDistribution) => (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: '0.72rem', fontWeight: 600, marginBottom: 4, color: 'var(--text-dim)' }}>{label}</div>
      <table className="score-table" style={{ width: '100%', fontSize: '0.7rem' }}>
        <tbody>
          {(['count', 'mean', 'median', 'std', 'p10', 'p25', 'p75', 'p90', 'max'] as const).map(k => (
            <tr key={k}>
              <td style={{ fontWeight: 500, color: 'var(--text-dim)' }}>{k}</td>
              <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                {typeof dist[k] === 'number' ? (k === 'count' ? dist[k] : dist[k].toFixed(4)) : '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );

  const homeoResult = results['homeostasis'] as (IntegrityScanResponse & { before?: IntegrityWeightDistribution; after?: IntegrityWeightDistribution }) | null;

  const tileStyle: React.CSSProperties = {
    background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10,
    padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 14,
    flex: '1 1 340px', minWidth: 300,
    boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
    transition: 'box-shadow 0.2s ease',
  };

  const scanBtnStyle: React.CSSProperties = {
    padding: '7px 20px', borderRadius: 6, border: 'none', cursor: 'pointer',
    fontSize: '0.78rem', fontWeight: 600, alignSelf: 'flex-start',
    background: 'var(--accent)', color: '#fff',
    boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
    transition: 'opacity 0.15s ease',
  };

  const resultBanner = (result: IntegrityScanResponse) => (
    <div style={{
      background: 'var(--bg-input)', borderRadius: 6, padding: '8px 12px',
      fontSize: '0.78rem', display: 'flex', alignItems: 'center', gap: 8,
    }}>
      <StatusBadge status={result.status} />
      <span style={{ fontWeight: 600 }}>{result.findings_count}</span>
      <span style={{ color: 'var(--text-dim)' }}>finding(s)</span>
      {result.completed_at && (
        <span style={{ color: 'var(--text-dim)', fontSize: '0.7rem', marginLeft: 'auto' }}>
          {new Date(result.completed_at).toLocaleString()}
        </span>
      )}
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {error && (
        <div style={{
          color: '#e74c3c', fontSize: '0.8rem', padding: '8px 12px',
          background: '#e74c3c11', borderRadius: 6, border: '1px solid #e74c3c33',
        }}>{error}</div>
      )}

      {/* Available agents */}
      <AgentsCard onRan={onAgentRan} />

      {/* Shared Scope Selector */}
      <div style={{
        background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10,
        padding: '14px 18px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)',
        display: 'flex', flexDirection: 'column', gap: 10,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>Scan Scope</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 1 }}>
              Target: <span style={{ color: 'var(--text)', fontWeight: 600 }}>{scopeDisplay}</span>
            </div>
          </div>
          <SegmentedControl
            value={scopeCategory}
            onChange={v => setScopeCategory(v as 'global' | 'department' | 'layer')}
            options={[
              { value: 'global', label: 'Global' },
              { value: 'department', label: 'Department' },
              { value: 'layer', label: 'Layer' },
            ]}
          />
        </div>

        {/* Secondary selector */}
        {scopeCategory === 'department' && departments.length > 0 && (
          <ChipSelect
            value={scopeDept}
            onChange={setScopeDept}
            options={departments.map(d => ({ value: d, label: d }))}
          />
        )}
        {scopeCategory === 'layer' && (
          <ChipSelect
            value={String(scopeLayer)}
            onChange={v => setScopeLayer(parseInt(v))}
            options={[0, 1, 2, 3, 4, 5].map(l => ({ value: String(l), label: `L${l}` }))}
          />
        )}
      </div>

      {/* Tile Grid */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {/* Homeostasis Tile */}
        <div style={tileStyle}>
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.88rem' }}>Synaptic Homeostasis</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.4 }}>
              Multiplicative weight renormalization to prevent co-firing inflation
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Slider label="Scale Factor" value={homeo.scale_factor} onChange={v => setHomeo({ ...homeo, scale_factor: v })} min={0.05} max={1} step={0.05} hint="Multiplier applied to all edge weights" />
            <Slider label="Floor Threshold" value={homeo.floor_threshold} onChange={v => setHomeo({ ...homeo, floor_threshold: v })} min={0} max={0.5} step={0.01} hint="Edges below this weight after scaling are flagged" />
          </div>
          <button style={scanBtnStyle} disabled={running !== null} onClick={() => runScan('homeostasis', () => runHomeostasisScan({ scope, ...homeo }))}>
            {running === 'homeostasis' ? 'Scanning...' : 'Run Scan'}
          </button>
          {homeoResult && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {resultBanner(homeoResult)}
              {homeoResult.before && homeoResult.after && (
                <div style={{ display: 'flex', gap: 12 }}>
                  {renderDistribution('Before', homeoResult.before)}
                  {renderDistribution('After', homeoResult.after)}
                </div>
              )}
              {homeoResult.findings_count > 0 && (
                <div>
                  <button
                    style={{ ...scanBtnStyle, background: '#4caf50' }}
                    disabled={running === 'apply'}
                    onClick={() => handleApply(homeoResult.id)}
                  >
                    {running === 'apply' ? 'Creating Proposal...' : 'Apply Rescaling'}
                  </button>
                  {applyMsg && <div style={{ fontSize: '0.75rem', color: '#4caf50', marginTop: 6 }}>{applyMsg}</div>}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Duplicates Tile */}
        <div style={tileStyle}>
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.88rem' }}>Pattern Separation</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.4 }}>
              Near-duplicate neuron detection via cosine similarity on embeddings
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Slider label="Similarity Threshold" value={dupes.similarity_threshold} onChange={v => setDupes({ ...dupes, similarity_threshold: v })} min={0.5} max={1} step={0.01} hint="Pairs above this threshold are flagged as duplicates" />
            <Slider label="Max Pairs" value={dupes.max_pairs} onChange={v => setDupes({ ...dupes, max_pairs: v })} min={1} max={500} step={1} hint="Maximum duplicate pairs to return" />
            <ToggleSwitch label="Cross-Department Only" checked={dupes.cross_department_only} onChange={v => setDupes({ ...dupes, cross_department_only: v })} hint="Only flag duplicates across different departments" />
          </div>
          <button style={scanBtnStyle} disabled={running !== null} onClick={() => runScan('duplicates', () => runDuplicatesScan({ scope, ...dupes }))}>
            {running === 'duplicates' ? 'Scanning...' : 'Run Scan'}
          </button>
          {results['duplicates'] && resultBanner(results['duplicates']!)}
        </div>

        {/* Connections Tile */}
        <div style={tileStyle}>
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.88rem' }}>Pattern Completion</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.4 }}>
              Find missing semantic connections between related neurons
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Slider label="Similarity Threshold" value={conns.similarity_threshold} onChange={v => setConns({ ...conns, similarity_threshold: v })} min={0.3} max={1} step={0.05} hint="Minimum content similarity to suggest a connection" />
            <Slider label="Max Suggestions" value={conns.max_suggestions} onChange={v => setConns({ ...conns, max_suggestions: v })} min={1} max={500} step={1} hint="Maximum missing connections to return" />
            <ToggleSwitch label="Exclude Same Parent" checked={conns.exclude_same_parent} onChange={v => setConns({ ...conns, exclude_same_parent: v })} hint="Skip pairs that share a parent neuron" />
          </div>
          <button style={scanBtnStyle} disabled={running !== null} onClick={() => runScan('connections', () => runConnectionsScan({ scope, ...conns }))}>
            {running === 'connections' ? 'Scanning...' : 'Run Scan'}
          </button>
          {results['connections'] && resultBanner(results['connections']!)}
        </div>

        {/* Conflicts Tile */}
        <div style={tileStyle}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontWeight: 700, fontSize: '0.88rem' }}>Conflict Monitor</span>
              <span style={{
                fontSize: '0.6rem', fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                background: '#f59e0b22', color: '#f59e0b', border: '1px solid #f59e0b44',
              }}>LLM</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.4 }}>
              Contradiction detection via embedding prefilter + LLM classification
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Slider label="Similarity Min" value={conflicts.sim_min} onChange={v => setConflicts({ ...conflicts, sim_min: v })} min={0} max={1} step={0.05} hint="Lower bound of embedding similarity band to check" />
            <Slider label="Similarity Max" value={conflicts.sim_max} onChange={v => setConflicts({ ...conflicts, sim_max: v })} min={0} max={1} step={0.05} hint="Upper bound of embedding similarity band to check" />
            <Slider label="Batch Size" value={conflicts.batch_size} onChange={v => setConflicts({ ...conflicts, batch_size: v })} min={1} max={20} step={1} hint="Pairs sent to LLM per batch" />
            <Slider label="Max Pairs" value={conflicts.max_pairs} onChange={v => setConflicts({ ...conflicts, max_pairs: v })} min={1} max={1000} step={1} hint="Maximum candidate pairs to evaluate" />
          </div>
          <button style={scanBtnStyle} disabled={running !== null} onClick={() => runScan('conflicts', () => runConflictsScan({ scope, ...conflicts }))}>
            {running === 'conflicts' ? 'Scanning...' : 'Run Scan'}
          </button>
          {results['conflicts'] && resultBanner(results['conflicts']!)}
        </div>

        {/* Aging Tile */}
        <div style={tileStyle}>
          <div>
            <div style={{ fontWeight: 700, fontSize: '0.88rem' }}>Age-Based Review</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 3, lineHeight: 1.4 }}>
              Surface stale neurons by source type and usage patterns
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <ToggleSwitch label="Include Never Verified" checked={aging.include_never_verified} onChange={v => setAging({ ...aging, include_never_verified: v })} hint="Include neurons that have never been reviewed" />
            <Slider label="Min Invocations" value={aging.min_invocations} onChange={v => setAging({ ...aging, min_invocations: v })} min={0} max={100} step={1} hint="Only flag neurons used at least this many times" />
          </div>
          <button style={scanBtnStyle} disabled={running !== null} onClick={() => runScan('aging', () => runAgingScan({ scope, ...aging }))}>
            {running === 'aging' ? 'Scanning...' : 'Run Scan'}
          </button>
          {results['aging'] && resultBanner(results['aging']!)}
        </div>
      </div>
    </div>
  );
}

// ── Findings Queue Panel ─────────────────────────────────────────

function FindingsPanel() {
  const [findings, setFindings] = useState<IntegrityFinding[]>([]);
  const [selected, setSelected] = useState<IntegrityFindingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState(false);
  const [queueCollapsed, setQueueCollapsed] = useState(false);
  const [agentRunModal, setAgentRunModal] = useState<IntegrityRun | null>(null);

  // Filters
  const [typeFilter, setTypeFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('open');
  const [severityFilter, setSeverityFilter] = useState<string>('');

  // Reviewer
  const [reviewer, setReviewer] = useState('');
  const [notes, setNotes] = useState('');

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkResolution, setBulkResolution] = useState('dismissed');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const f = await fetchIntegrityFindings({
        finding_type: typeFilter || undefined,
        status: statusFilter || undefined,
        severity: severityFilter || undefined,
      });
      setFindings(f);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [typeFilter, statusFilter, severityFilter]);

  useEffect(() => { load(); }, [load]);

  const selectFinding = async (id: number) => {
    try {
      const detail = await fetchIntegrityFindingDetail(id);
      setSelected(detail);
      setNotes('');
    } catch (e) {
      setError(String(e));
    }
  };

  useListKeyboardNav({
    items: findings,
    selectedId: selected?.id ?? null,
    onSelect: (id) => { void selectFinding(id as number); },
    enabled: !agentRunModal,
  });

  const [proposeMsg, setProposeMsg] = useState<string | null>(null);

  const handlePropose = async (resolution: string) => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    setProposeMsg(null);
    try {
      const result = await proposeIntegrityFinding(selected.id, resolution, reviewer.trim(), notes);
      setProposeMsg(`Proposal #${result.proposal_id} created (${result.item_count} items). Review it in the Proposal Queue.`);
      await load();
      // Reload selected finding to show updated status
      const updated = await fetchIntegrityFindingDetail(selected.id);
      setSelected(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleDirectResolve = async (resolution: string) => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    setProposeMsg(null);
    try {
      await resolveIntegrityFinding(selected.id, resolution, reviewer.trim(), notes);
      setSelected(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleDismiss = async () => {
    if (!selected || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      await dismissIntegrityFinding(selected.id, reviewer.trim(), notes);
      setSelected(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  };

  const handleBulkResolve = async () => {
    if (selectedIds.size === 0 || !reviewer.trim()) return;
    setActionLoading(true);
    try {
      await bulkResolveIntegrityFindings(Array.from(selectedIds), bulkResolution, reviewer.trim(), notes);
      setSelectedIds(new Set());
      setSelected(null);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setActionLoading(false);
    }
  };

  const toggleBulkId = (id: number) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const proposalOptions = selected ? (PROPOSAL_RESOLUTIONS[selected.finding_type] || []) : [];
  const directOptions = selected ? (DIRECT_RESOLUTIONS[selected.finding_type] || []) : [];

  return (
    <div style={{ display: 'flex', gap: 0, height: 'calc(100vh - 180px)' }}>
      {/* Left Panel */}
      <div style={{
        width: queueCollapsed ? 40 : 360, flexShrink: 0,
        display: 'flex', flexDirection: 'column',
        borderRight: '1px solid var(--border)', transition: 'width 0.2s ease', overflow: 'hidden',
      }}>
        <button
          onClick={() => setQueueCollapsed(!queueCollapsed)}
          style={{
            background: 'none', border: 'none', color: 'var(--text)',
            cursor: 'pointer', padding: '8px 10px', fontSize: '0.85rem',
            display: 'flex', alignItems: 'center', gap: 6,
            borderBottom: '1px solid var(--border)',
          }}
        >
          <span style={{ fontSize: '0.7rem' }}>{queueCollapsed ? '\u25B6' : '\u25C0'}</span>
          {!queueCollapsed && <span style={{ fontWeight: 600 }}>Findings ({findings.length})</span>}
        </button>

        {!queueCollapsed && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 8px', flex: 1, overflow: 'hidden' }}>
            {/* Filters */}
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
              <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} style={{ ...selectStyle, flex: 1 }}>
                <option value="">All Types</option>
                <option value="weight_inflation">Weight Imbalance</option>
                <option value="near_duplicate">Near Duplicate</option>
                <option value="missing_connection">Missing Connection</option>
                <option value="contradiction">Contradiction</option>
                <option value="stale_content">Stale Content</option>
              </select>
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ ...selectStyle, flex: 1 }}>
                <option value="">All Status</option>
                <option value="open">Open</option>
                <option value="proposed">Proposed</option>
                <option value="resolved">Resolved</option>
                <option value="dismissed">Dismissed</option>
              </select>
              <select value={severityFilter} onChange={e => setSeverityFilter(e.target.value)} style={{ ...selectStyle, flex: 1 }}>
                <option value="">All Severity</option>
                <option value="critical">Critical</option>
                <option value="warning">Warning</option>
                <option value="info">Info</option>
              </select>
            </div>

            {/* Bulk actions */}
            {selectedIds.size > 0 && (
              <div style={{
                display: 'flex', gap: 4, alignItems: 'center', padding: '4px 0',
                borderBottom: '1px solid var(--border)', fontSize: '0.75rem',
              }}>
                <span style={{ color: 'var(--text-dim)' }}>{selectedIds.size} selected</span>
                <select value={bulkResolution} onChange={e => setBulkResolution(e.target.value)} style={selectStyle}>
                  <option value="dismissed">Dismiss</option>
                  <option value="reviewed">Reviewed</option>
                  <option value="merged">Merged</option>
                  <option value="linked">Linked</option>
                </select>
                <button style={btnStyle} disabled={actionLoading || !reviewer.trim()} onClick={handleBulkResolve}>
                  Bulk Apply
                </button>
              </div>
            )}

            {/* List */}
            <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {loading && <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>Loading...</div>}
              {error && <div style={{ color: '#e74c3c', padding: 8, fontSize: '0.8rem' }}>{error}</div>}
              {!loading && findings.length === 0 && (
                <div style={{ color: 'var(--text-dim)', padding: 8, fontSize: '0.8rem' }}>No findings found.</div>
              )}
              {findings.map(f => (
                <div
                  key={f.id}
                  style={{
                    padding: '6px 8px', borderRadius: 6, cursor: 'pointer',
                    background: selected?.id === f.id ? 'var(--bg-active)' : 'var(--bg-card)',
                    border: `1px solid ${selected?.id === f.id ? 'var(--accent)' : 'var(--border)'}`,
                    display: 'flex', gap: 6, alignItems: 'flex-start',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(f.id)}
                    onChange={() => toggleBulkId(f.id)}
                    style={{ marginTop: 3, flexShrink: 0 }}
                  />
                  <div style={{ flex: 1 }} onClick={() => selectFinding(f.id)}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
                      <span style={{ fontSize: '0.75rem', fontWeight: 600 }}>
                        {FINDING_TYPE_LABELS[f.finding_type] || f.finding_type}
                      </span>
                      <SeverityBadge severity={f.severity} />
                    </div>
                    <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 2, lineHeight: 1.3 }}>
                      {f.description.length > 100 ? f.description.slice(0, 100) + '...' : f.description}
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 3, fontSize: '0.65rem', color: 'var(--text-dim)' }}>
                      <span>Score: {f.priority_score.toFixed(2)}</span>
                      <StatusBadge status={f.status} />
                      {f.created_at && <span>{new Date(f.created_at).toLocaleDateString()}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Right Panel — Detail */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>
        {!selected ? (
          <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem', paddingTop: 32, textAlign: 'center' }}>
            Select a finding to view details
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {/* Header */}
            <div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontWeight: 700, fontSize: '0.95rem' }}>Finding #{selected.id}</span>
                <SeverityBadge severity={selected.severity} />
                <StatusBadge status={selected.status} />
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                Type: {FINDING_TYPE_LABELS[selected.finding_type] || selected.finding_type}
                &nbsp;&middot;&nbsp;Priority: {selected.priority_score.toFixed(2)}
                {selected.created_at && <>&nbsp;&middot;&nbsp;{new Date(selected.created_at).toLocaleString()}</>}
              </div>
            </div>

            {/* Auto-proposed banner */}
            {selected.created_by_agent_run_id && (
              <div style={{
                padding: '8px 12px', borderRadius: 6, fontSize: '0.78rem',
                background: 'var(--accent)' + '15',
                border: '1px solid ' + 'var(--accent)' + '55',
                color: 'var(--text)',
              }}>
                Auto-proposed by{' '}
                <code style={{ fontWeight: 600 }}>{selected.created_by_agent_name || 'agent'}</code>
                {' — '}
                <a
                  onClick={() => setAgentRunModal({
                    id: selected.created_by_agent_run_id!,
                    kind: `agent:${selected.created_by_agent_name || 'agent'}`,
                    started_at: null, completed_at: null,
                    initiated_by: 'agent', state: 'applied',
                    summary: null, scan_scope: null, findings_count: null,
                    turns: null, tool_calls: null, mutations: null, errors: null,
                  })}
                  style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline' }}
                >
                  view run #{selected.created_by_agent_run_id}
                </a>
              </div>
            )}

            {/* Description */}
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: 4 }}>Description</div>
              <div style={{ fontSize: '0.8rem', lineHeight: 1.5 }}>{selected.description}</div>
            </div>

            {/* Detail JSON */}
            {selected.detail && Object.keys(selected.detail).length > 0 && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: 4 }}>Detail</div>
                <pre style={{
                  fontSize: '0.7rem', background: 'var(--bg-input)', padding: 8, borderRadius: 4,
                  overflow: 'auto', maxHeight: 200, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {JSON.stringify(selected.detail, null, 2)}
                </pre>
              </div>
            )}

            {/* Neurons */}
            {Object.keys(selected.neurons).length > 0 && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: 8 }}>Referenced Neurons</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {Object.values(selected.neurons).map(n => (
                    <div key={n.id} style={{
                      background: 'var(--bg-card)', border: '1px solid var(--border)',
                      borderRadius: 6, padding: 10,
                    }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                        <span style={{ fontWeight: 600, fontSize: '0.8rem' }}>#{n.id} {n.label}</span>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
                          L{n.layer} &middot; {n.department || 'no dept'} &middot; {n.invocations} invocations
                        </span>
                      </div>
                      {n.summary && (
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginBottom: 4 }}>{n.summary}</div>
                      )}
                      {n.content && (
                        <pre style={{
                          fontSize: '0.7rem', background: 'var(--bg-input)', padding: 8, borderRadius: 4,
                          overflow: 'auto', maxHeight: 200, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                          margin: 0,
                        }}>
                          {n.content}
                        </pre>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Actions */}
            {selected.status === 'open' && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, marginBottom: 8 }}>Actions</div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)', width: 60 }}>Reviewer</label>
                    <input
                      style={{ ...selectStyle, flex: 1 }}
                      value={reviewer}
                      onChange={e => setReviewer(e.target.value)}
                      placeholder="Your name"
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                    <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)', width: 60, paddingTop: 4 }}>Notes</label>
                    <textarea
                      style={{ ...selectStyle, flex: 1, minHeight: 40, resize: 'vertical', fontFamily: 'inherit' }}
                      value={notes}
                      onChange={e => setNotes(e.target.value)}
                      placeholder="Optional notes..."
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                    {/* Graph-modifying actions → create proposal */}
                    {proposalOptions.map(opt => (
                      <button
                        key={opt.value}
                        style={{
                          padding: '7px 16px', borderRadius: 6, border: 'none', cursor: 'pointer',
                          fontSize: '0.78rem', fontWeight: 600,
                          background: 'var(--accent)', color: '#fff',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.15)',
                        }}
                        disabled={actionLoading || !reviewer.trim()}
                        onClick={() => handlePropose(opt.value)}
                      >
                        {opt.label}
                      </button>
                    ))}
                    {/* Non-graph actions → resolve directly */}
                    {directOptions.map(opt => (
                      <button
                        key={opt.value}
                        style={btnStyle}
                        disabled={actionLoading || !reviewer.trim()}
                        onClick={() => handleDirectResolve(opt.value)}
                      >
                        {opt.label}
                      </button>
                    ))}
                    <button
                      style={{ ...btnStyle, color: 'var(--text-dim)' }}
                      disabled={actionLoading || !reviewer.trim()}
                      onClick={handleDismiss}
                    >
                      Dismiss
                    </button>
                  </div>
                  {proposeMsg && (
                    <div style={{
                      fontSize: '0.78rem', color: '#4caf50', padding: '8px 12px',
                      background: '#4caf5011', borderRadius: 6, border: '1px solid #4caf5033',
                    }}>
                      {proposeMsg}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Proposed — waiting for proposal approval */}
            {selected.status === 'proposed' && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                <div style={{
                  fontSize: '0.8rem', padding: '8px 12px',
                  background: '#3b82f611', borderRadius: 6, border: '1px solid #3b82f633',
                }}>
                  <span style={{ color: '#3b82f6', fontWeight: 600 }}>Awaiting proposal approval</span>
                  {selected.proposal_id && (
                    <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>
                      Proposal #{selected.proposal_id}
                    </span>
                  )}
                  {selected.resolution && (
                    <span style={{ color: 'var(--text-dim)', marginLeft: 8 }}>
                      ({selected.resolution})
                    </span>
                  )}
                </div>
              </div>
            )}

            {/* Resolution info for already-resolved findings */}
            {selected.status === 'resolved' && (
              <div style={{ borderTop: '1px solid var(--border)', paddingTop: 10, fontSize: '0.8rem' }}>
                <span style={{ color: 'var(--text-dim)' }}>Resolved as </span>
                <strong>{selected.resolution}</strong>
                {selected.resolved_by && <span style={{ color: 'var(--text-dim)' }}> by {selected.resolved_by}</span>}
                {selected.resolved_at && <span style={{ color: 'var(--text-dim)' }}> on {new Date(selected.resolved_at).toLocaleString()}</span>}
                {selected.proposal_id && <span style={{ color: 'var(--text-dim)' }}> (Proposal #{selected.proposal_id})</span>}
              </div>
            )}
          </div>
        )}
      </div>

      {agentRunModal && (
        <RunDetailModal row={agentRunModal} onClose={() => setAgentRunModal(null)} />
      )}
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────────

// With `panel` set, renders that single panel with no tab bar — each
// integrity panel is its own window on the desktop, and stays put after
// actions (a scan run no longer hops to the dashboard). Without
// `panel`, the original tabbed page renders unchanged.
export default function IntegrityPage({ panel: fixedPanel }: {
  panel?: Panel;
} = {}) {
  const [panel, setPanel] = useState<Panel>(fixedPanel ?? 'dashboard');

  if (fixedPanel) {
    return (
      <div style={{ height: '100%', overflow: 'auto', padding: '12px 16px' }}>
        {fixedPanel === 'dashboard' && <DashboardPanel />}
        {fixedPanel === 'scan' && <ScanPanel onAgentRan={() => { /* stay put */ }} />}
        {fixedPanel === 'findings' && <FindingsPanel />}
      </div>
    );
  }

  const tabBtn = (key: Panel, label: string) => (
    <button
      key={key}
      onClick={() => setPanel(key)}
      style={{
        background: 'none', border: 'none', color: panel === key ? 'var(--accent)' : 'var(--text-dim)',
        cursor: 'pointer', padding: '8px 16px', fontSize: '0.85rem', fontWeight: 600,
        borderBottom: panel === key ? '2px solid var(--accent)' : '2px solid transparent',
      }}
    >
      {label}
    </button>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Tab bar */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', marginBottom: 12 }}>
        {tabBtn('dashboard', 'Dashboard')}
        {tabBtn('scan', 'Scan')}
        {tabBtn('findings', 'Findings Queue')}
      </div>

      {/* Panel content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '0 4px' }}>
        {panel === 'dashboard' && <DashboardPanel />}
        {panel === 'scan' && <ScanPanel onAgentRan={() => setPanel('dashboard')} />}
        {panel === 'findings' && <FindingsPanel />}
      </div>
    </div>
  );
}
