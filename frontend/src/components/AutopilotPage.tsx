import { useState, useEffect, useCallback } from 'react';
import type { AutopilotConfig, AutopilotRun, AutopilotChange, TreeNode, StageTelemetry } from '../types';
import { useModels } from '../hooks/useModels';
import { useListKeyboardNav } from '../hooks/useListKeyboardNav';
import {
  fetchAutopilotConfig,
  updateAutopilotConfig,
  triggerAutopilotRunNow,
  fetchAutopilotRuns,
  fetchAutopilotRunChanges,
  cancelAutopilotTick,
  fetchAutopilotStatus,
  fetchTree,
} from '../api';

const GAP_SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  emergent_queue: { label: 'Emergent Queue', color: '#e8b84d' },
  low_eval: { label: 'Low Eval', color: '#e85d5d' },
  thin_neuron: { label: 'Thin Neuron', color: '#8b8bea' },
  sparse_subtree: { label: 'Sparse Subtree', color: '#5dbb5d' },
  directive: { label: 'Directive', color: '#888' },
};

export default function AutopilotPage() {
  const [config, setConfig] = useState<AutopilotConfig | null>(null);
  const [runs, setRuns] = useState<AutopilotRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [expandedRun, setExpandedRun] = useState<number | null>(null);
  const [runChanges, setRunChanges] = useState<Record<number, AutopilotChange[]>>({});
  const [runResult, setRunResult] = useState<string | null>(null);
  const [stepDetail, setStepDetail] = useState('');

  // Draft config for editing
  const [directive, setDirective] = useState('');
  const [interval, setInterval] = useState(30);
  const [maxLayer, setMaxLayer] = useState(5);
  const [evalModel, setEvalModel] = useState('haiku');
  const { grouped: groupedModels } = useModels();
  const [focusNeuronId, setFocusNeuronId] = useState<number | null>(null);
  const [focusNeuronLabel, setFocusNeuronLabel] = useState<string | null>(null);

  // Step progress
  const [currentStep, setCurrentStep] = useState('');

  // Countdown timer
  const [countdown, setCountdown] = useState<string | null>(null);

  // Tree for focus picker
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [showTreePicker, setShowTreePicker] = useState(false);

  const loadData = useCallback(async () => {
    try {
      const [c, r, t] = await Promise.all([
        fetchAutopilotConfig(),
        fetchAutopilotRuns(),
        fetchTree(),
      ]);
      setConfig(c);
      setRuns(r);
      setTree(t);
      setDirective(c.directive);
      setInterval(c.interval_minutes);
      setMaxLayer(c.max_layer);
      setEvalModel(c.eval_model);
      setFocusNeuronId(c.focus_neuron_id);
      setFocusNeuronLabel(c.focus_neuron_label);
    } catch (e) {
      console.error('Failed to load autopilot data:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  // Poll step progress
  useEffect(() => {
    const poll = async () => {
      try {
        const status = await fetchAutopilotStatus();
        if (status.running) {
          setRunning(true);
          setCurrentStep(status.step);
          setStepDetail(status.detail);
        } else if (running && !status.running) {
          setRunning(false);
          setCurrentStep('');
          setStepDetail('');
          loadData();
        }
      } catch { /* ignore */ }
    };
    poll();
    const id = window.setInterval(poll, running ? 1500 : 5000);
    return () => clearInterval(id);
  }, [running, loadData]);

  // Live countdown timer
  useEffect(() => {
    if (!config?.enabled || !config?.last_tick_at) {
      setCountdown(null);
      return;
    }
    const tick = () => {
      const raw = config.last_tick_at!;
      const lastTick = new Date(raw.endsWith('Z') ? raw : raw + 'Z').getTime();
      const nextTick = lastTick + config.interval_minutes * 60 * 1000;
      const remaining = nextTick - Date.now();
      if (remaining <= 0) {
        setCountdown('any moment...');
      } else {
        const mins = Math.floor(remaining / 60000);
        const secs = Math.floor((remaining % 60000) / 1000);
        setCountdown(`${mins}m ${secs.toString().padStart(2, '0')}s`);
      }
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [config?.enabled, config?.last_tick_at, config?.interval_minutes]);

  const handleToggle = async () => {
    if (!config) return;
    try {
      const updated = await updateAutopilotConfig({
        enabled: !config.enabled,
        directive,
        interval_minutes: interval,
        max_layer: maxLayer,
        eval_model: evalModel,
        focus_neuron_id: focusNeuronId ?? 0,
      });
      setConfig(updated);
    } catch (e) {
      console.error('Failed to toggle:', e);
    }
  };

  const handleRunNow = async () => {
    setRunning(true);
    setRunResult(null);
    try {
      const saved = await updateAutopilotConfig({
        directive,
        interval_minutes: interval,
        max_layer: maxLayer,
        eval_model: evalModel,
        focus_neuron_id: focusNeuronId ?? 0,
      });
      setConfig(saved);
      const result = await triggerAutopilotRunNow();
      setRunResult(`${result.status}${result.message ? ': ' + result.message : ''}${result.run_id ? ' (run #' + result.run_id + ')' : ''}`);
      const r = await fetchAutopilotRuns();
      setRuns(r);
      const c = await fetchAutopilotConfig();
      setConfig(c);
    } catch (e) {
      setRunResult(`Error: ${e}`);
    } finally {
      setRunning(false);
      setCancelling(false);
    }
  };

  const handleCancel = async () => {
    setCancelling(true);
    try {
      await cancelAutopilotTick();
    } catch (e) {
      console.error('Failed to cancel:', e);
    }
  };

  const handleExpandRun = async (runId: number) => {
    if (expandedRun === runId) {
      setExpandedRun(null);
      return;
    }
    setExpandedRun(runId);
    if (!runChanges[runId]) {
      try {
        const changes = await fetchAutopilotRunChanges(runId);
        setRunChanges(prev => ({ ...prev, [runId]: changes }));
      } catch (e) {
        console.error('Failed to load changes:', e);
      }
    }
  };

  // j/k navigation: always expand the target row (never collapse) so the
  // hook's advance-to-next behavior stays intuitive. The click handler
  // keeps its toggle semantics for mouse users.
  const navigateToRun = useCallback(async (runId: number) => {
    setExpandedRun(runId);
    if (!runChanges[runId]) {
      try {
        const changes = await fetchAutopilotRunChanges(runId);
        setRunChanges(prev => ({ ...prev, [runId]: changes }));
      } catch (e) {
        console.error('Failed to load changes:', e);
      }
    }
  }, [runChanges]);

  useListKeyboardNav({
    items: runs,
    selectedId: expandedRun,
    onSelect: (id) => { void navigateToRun(id as number); },
  });

  const selectFocusNeuron = (id: number, label: string) => {
    setFocusNeuronId(id);
    setFocusNeuronLabel(label);
    setShowTreePicker(false);
  };

  const clearFocus = () => {
    setFocusNeuronId(null);
    setFocusNeuronLabel(null);
  };

  if (loading) return <div className="loading">Loading autopilot...</div>;

  // Gap source stats from run history
  const gapStats = runs.reduce<Record<string, number>>((acc, r) => {
    if (r.gap_source) acc[r.gap_source] = (acc[r.gap_source] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="autopilot-page">
      {/* Config Panel */}
      <div className="result-card">
        <div className="autopilot-config-header">
          <h3>Autopilot Configuration</h3>
          <div className="autopilot-toggle" onClick={handleToggle}>
            <div className={`toggle-switch ${config?.enabled ? 'on' : ''}`}>
              <div className="toggle-knob" />
            </div>
            <span className="toggle-text">{config?.enabled ? 'Enabled' : 'Disabled'}</span>
          </div>
        </div>

        <div style={{ padding: '8px 0 4px', color: '#aaa', fontSize: '0.85rem' }}>
          Gap-driven mode: autopilot scans for emergent queue entries, low-eval queries,
          thin neurons, and sparse subtrees. Falls back to directive when no gaps are found.
        </div>

        <div className="autopilot-config-body">
          <div className="autopilot-field">
            <label>Directive (optional — guides fallback queries when no gaps found)</label>
            <textarea
              value={directive}
              onChange={e => setDirective(e.target.value)}
              placeholder="e.g., Industrial Engineering and Lean / Six Sigma Methodology"
              rows={3}
            />
          </div>

          <div className="autopilot-row">
            <div className="autopilot-field" style={{ flex: 1 }}>
              <label>Focus Area</label>
              <div className="focus-picker">
                {focusNeuronId ? (
                  <div className="focus-selected">
                    <span className="focus-label">
                      {focusNeuronLabel || `Neuron #${focusNeuronId}`}
                    </span>
                    <button className="focus-clear" onClick={clearFocus} title="Clear focus">x</button>
                    <button className="btn btn-sm" onClick={() => setShowTreePicker(!showTreePicker)}>
                      Change
                    </button>
                  </div>
                ) : (
                  <button className="btn btn-sm" onClick={() => setShowTreePicker(!showTreePicker)}>
                    Select Focus Neuron
                  </button>
                )}
                {!focusNeuronId && (
                  <span className="focus-hint">No focus = entire graph</span>
                )}
              </div>
            </div>

            <div className="autopilot-field">
              <label>Eval Model</label>
              <select value={evalModel} onChange={e => setEvalModel(e.target.value)}>
                {Object.entries(groupedModels).map(([group, models]) => (
                  [
                    <option key={`hdr-${group}`} disabled style={{ fontWeight: 'bold' }}>── {group} ──</option>,
                    ...models.map(m => (
                      <option key={m.display_name} value={m.display_name}>
                        {m.display_name}
                      </option>
                    )),
                  ]
                ))}
              </select>
            </div>

            <div className="autopilot-field">
              <label>Interval</label>
              <select value={interval} onChange={e => setInterval(Number(e.target.value))}>
                <option value={5}>5 min</option>
                <option value={10}>10 min</option>
                <option value={15}>15 min</option>
                <option value={30}>30 min</option>
                <option value={60}>60 min</option>
              </select>
            </div>

            <div className="autopilot-field">
              <label>Max Layer</label>
              <select value={maxLayer} onChange={e => setMaxLayer(Number(e.target.value))}>
                {[0,1,2,3,4,5].map(l => (
                  <option key={l} value={l}>L{l}{l === 5 ? ' (all)' : ''}</option>
                ))}
              </select>
            </div>
          </div>

          {showTreePicker && (
            <div className="focus-tree-picker">
              <div className="focus-tree-header">Select a neuron to focus autopilot on:</div>
              <div className="focus-tree-scroll">
                {tree.map(node => (
                  <FocusTreeNode key={node.id} node={node} onSelect={selectFocusNeuron} depth={0} />
                ))}
              </div>
            </div>
          )}

          <div className="autopilot-actions">
            {running ? (
              <button
                className="btn autopilot-cancel-btn"
                onClick={handleCancel}
                disabled={cancelling}
              >
                {cancelling ? 'Cancelling...' : 'Cancel Run'}
              </button>
            ) : (
              <button
                className="btn autopilot-run-btn"
                onClick={handleRunNow}
              >
                Run Now
              </button>
            )}
            <div className="autopilot-timer-info">
              {config?.last_tick_at && (
                <span className="autopilot-last-tick">
                  Last: {new Date(config.last_tick_at.endsWith('Z') ? config.last_tick_at : config.last_tick_at + 'Z').toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </span>
              )}
              {countdown && (
                <span className="autopilot-countdown">
                  Next tick in {countdown}
                </span>
              )}
            </div>
          </div>

          {running && (
            <StepProgress currentStep={currentStep} detail={stepDetail} />
          )}

          {runResult && (
            <div className={`autopilot-run-result ${runResult.startsWith('error') || runResult.startsWith('Error') ? 'error' : ''}`}>
              {runResult}
            </div>
          )}
        </div>
      </div>

      {/* Gap Source Stats */}
      {Object.keys(gapStats).length > 0 && (
        <div className="result-card">
          <h3>Gap Source Distribution</h3>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', padding: '8px 0' }}>
            {Object.entries(gapStats).map(([source, count]) => {
              const info = GAP_SOURCE_LABELS[source] || { label: source, color: '#888' };
              return (
                <div key={source} style={{
                  padding: '6px 12px',
                  borderRadius: 6,
                  background: 'rgba(255,255,255,0.05)',
                  border: `1px solid ${info.color}40`,
                  fontSize: '0.85rem',
                }}>
                  <span style={{ color: info.color, fontWeight: 600 }}>{info.label}</span>
                  <span style={{ color: '#aaa', marginLeft: 8 }}>{count} run{count !== 1 ? 's' : ''}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Run History */}
      <div className="result-card">
        <h3>Run History ({runs.length})</h3>
        {runs.length === 0 ? (
          <div className="history-empty">No autopilot runs yet.</div>
        ) : (
          <table className="autopilot-runs-table score-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Gap Source</th>
                <th>Generated Query</th>
                <th>Focus</th>
                <th>Eval</th>
                <th>Updates</th>
                <th>New</th>
                <th>Cost</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => (
                <>
                  <tr
                    key={run.id}
                    className={`autopilot-run-row ${expandedRun === run.id ? 'expanded' : ''}`}
                    onClick={() => handleExpandRun(run.id)}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="run-time">
                      {run.created_at ? new Date(run.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'}
                    </td>
                    <td>
                      {run.gap_source ? (
                        <span style={{
                          color: (GAP_SOURCE_LABELS[run.gap_source] || { color: '#888' }).color,
                          fontSize: '0.8rem',
                          fontWeight: 600,
                        }}>
                          {(GAP_SOURCE_LABELS[run.gap_source] || { label: run.gap_source }).label}
                        </span>
                      ) : (
                        <span style={{ color: '#666' }}>-</span>
                      )}
                    </td>
                    <td className="run-query" title={run.generated_query}>
                      {run.generated_query.slice(0, 70)}{run.generated_query.length > 70 ? '...' : ''}
                    </td>
                    <td className="run-focus">
                      {run.focus_neuron_label ? (
                        <span className="tag dept">{run.focus_neuron_label}</span>
                      ) : '-'}
                    </td>
                    <td>
                      <span className={`eval-score-badge score-${run.eval_overall}`}>
                        {run.eval_overall}/5
                      </span>
                    </td>
                    <td>{run.updates_applied}</td>
                    <td>{run.neurons_created}</td>
                    <td>${run.cost_usd.toFixed(4)}</td>
                    <td>
                      <span className={`status-badge ${run.status}`}>{run.status}</span>
                    </td>
                  </tr>
                  {expandedRun === run.id && (
                    <tr key={`${run.id}-detail`} className="autopilot-run-detail">
                      <td colSpan={9}>
                        <div className="run-detail-content">
                          {run.gap_target && (
                            <div className="run-detail-section">
                              <strong>Gap Target:</strong>
                              <div className="detail-content">{run.gap_target}</div>
                            </div>
                          )}
                          <div className="run-detail-section">
                            <strong>Full Query:</strong>
                            <div className="detail-content">{run.generated_query}</div>
                          </div>
                          {run.eval_text && (
                            <div className="run-detail-section">
                              <strong>Evaluation:</strong>
                              <div className="detail-content">{run.eval_text}</div>
                            </div>
                          )}
                          {run.refine_reasoning && (
                            <div className="run-detail-section">
                              <strong>Refine Reasoning:</strong>
                              <div className="detail-content">{run.refine_reasoning}</div>
                            </div>
                          )}

                          {run.stage_telemetry && run.stage_telemetry.length > 0 && (
                            <div className="run-detail-section">
                              <strong>Pipeline Timing <span style={{ color: 'var(--text-dim)', fontWeight: 400, fontSize: '0.75rem' }}>({run.stage_telemetry.length} stages)</span></strong>
                              <StageTimingTable telemetry={run.stage_telemetry} />
                            </div>
                          )}

                          {/* Neuron Changes */}
                          {(runChanges[run.id]?.length ?? 0) > 0 && (
                            <div className="run-detail-section">
                              <strong>Neuron Changes:</strong>
                              <div className="run-changes">
                                {runChanges[run.id].map(change => (
                                  <div key={change.id} className={`run-change-row ${change.action}`}>
                                    <div className="run-change-header">
                                      <span className={`action-badge action-${change.action}`}>{change.action}</span>
                                      <span className="run-change-neuron">#{change.neuron_id} {change.neuron_label}</span>
                                      {change.field && <span className="refine-field">{change.field}</span>}
                                    </div>
                                    {change.action === 'update' && change.old_value && change.new_value && (
                                      <div className="refine-diff">
                                        <div className="diff-old">{change.old_value}</div>
                                        <span className="refine-arrow">-&gt;</span>
                                        <div className="diff-new">{change.new_value}</div>
                                      </div>
                                    )}
                                    {change.action === 'create' && change.neuron_detail && (
                                      <div className="run-change-create-detail">
                                        <div className="create-meta">
                                          <span className="meta-chip layer-chip">L{change.neuron_detail.layer}</span>
                                          <span className="meta-chip">{change.neuron_detail.node_type}</span>
                                          {change.neuron_detail.department && <span className="tag dept">{change.neuron_detail.department}</span>}
                                        </div>
                                        {change.neuron_detail.summary && (
                                          <div className="create-summary">{change.neuron_detail.summary}</div>
                                        )}
                                        {change.neuron_detail.content && (
                                          <div className="create-content">{change.neuron_detail.content}</div>
                                        )}
                                      </div>
                                    )}
                                    {change.action === 'create' && !change.neuron_detail && (
                                      <div className="diff-new">{change.new_value}</div>
                                    )}
                                    {change.reason && (
                                      <div className="refine-reason">{change.reason}</div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {run.error_message && (
                            <div className="run-detail-section">
                              <strong>Error:</strong>
                              <div className="detail-content error-msg">{run.error_message}</div>
                            </div>
                          )}
                          <div className="run-detail-meta">
                            <span>Neurons activated: {run.neurons_activated}</span>
                            {run.directive && <span>Directive: {run.directive.slice(0, 100)}</span>}
                            {run.query_id && <span>Query ID: #{run.query_id}</span>}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

const STEPS = [
  { key: 'detect', label: 'Detect' },
  { key: 'generate', label: 'Generate' },
  { key: 'execute', label: 'Execute' },
  { key: 'evaluate', label: 'Evaluate' },
  { key: 'refine', label: 'Refine' },
  { key: 'apply', label: 'Apply' },
  { key: 'record', label: 'Record' },
];

function StepProgress({ currentStep, detail }: { currentStep: string; detail?: string }) {
  const currentIdx = STEPS.findIndex(s => s.key === currentStep);
  return (
    <div className="step-progress">
      <div className="step-progress-bar">
        {STEPS.map((step, i) => {
          let cls = 'step-item';
          if (i < currentIdx) cls += ' completed';
          else if (i === currentIdx) cls += ' active';
          else cls += ' pending';
          return (
            <div key={step.key} className={cls}>
              <div className="step-dot" />
              <span className="step-label">{step.label}</span>
              {i < STEPS.length - 1 && <div className="step-line" />}
            </div>
          );
        })}
      </div>
      {detail && (
        <div className="step-detail">{detail}</div>
      )}
    </div>
  );
}

function FocusTreeNode({ node, onSelect, depth }: {
  node: TreeNode;
  onSelect: (id: number, label: string) => void;
  depth: number;
}) {
  const [expanded, setExpanded] = useState(depth < 1);

  return (
    <div className="focus-tree-node">
      <div
        className={`focus-tree-row layer-${node.layer}`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {(node.children ?? []).length > 0 ? (
          <span
            className={`tree-toggle ${expanded ? 'open' : ''}`}
            onClick={e => { e.stopPropagation(); setExpanded(!expanded); }}
          >
            {expanded ? '\u25BC' : '\u25B6'}
          </span>
        ) : (
          <span className="tree-toggle" />
        )}
        <span className="tree-label" onClick={() => onSelect(node.id, node.label)}>
          {node.label}
        </span>
        <span className="meta-chip layer-chip">L{node.layer}</span>
        <button
          className="focus-select-btn"
          onClick={() => onSelect(node.id, node.label)}
          title="Select this neuron as focus"
        >
          Select
        </button>
      </div>
      {expanded && (node.children ?? []).length > 0 && (
        <div className="focus-tree-children">
          {(node.children ?? []).map(child => (
            <FocusTreeNode key={child.id} node={child} onSelect={onSelect} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function StageTimingTable({ telemetry }: { telemetry: StageTelemetry[] }) {
  const totalMs = telemetry.reduce((acc, t) => acc + (t.duration_ms || 0), 0);
  const fmt = (ms: number): string => {
    if (ms < 1) return '<1ms';
    if (ms < 1000) return `${ms.toFixed(1)}ms`;
    return `${(ms / 1000).toFixed(2)}s`;
  };
  const statusColor = (status: string): string => {
    if (status === 'done') return '#22c55e';
    if (status === 'error') return '#ef4444';
    return '#94a3b8';
  };
  const fmtCost = (v: number): string => {
    if (v === 0) return '$0';
    if (v < 0.01) return `$${v.toFixed(4)}`;
    if (v < 1) return `$${v.toFixed(3)}`;
    return `$${v.toFixed(2)}`;
  };
  const fmtDetail = (detail: Record<string, unknown> | undefined): string => {
    if (!detail) return '—';
    const entries = Object.entries(detail);
    if (entries.length === 0) return '—';
    return entries
      .map(([k, v]) => {
        if (k === 'cost_usd' && typeof v === 'number') return `cost=${fmtCost(v)}`;
        if (Array.isArray(v)) return `${k}=[${v.length}]`;
        return `${k}=${String(v)}`;
      })
      .join(' · ');
  };
  return (
    <table className="score-table" style={{ width: '100%', fontSize: '0.8rem', marginTop: 4 }}>
      <thead>
        <tr>
          <th style={{ textAlign: 'left' }}>Stage</th>
          <th style={{ textAlign: 'left' }}>Status</th>
          <th style={{ textAlign: 'right' }}>Duration</th>
          <th style={{ textAlign: 'right' }}>% of tick</th>
          <th style={{ textAlign: 'left', paddingLeft: 24 }}>Detail</th>
        </tr>
      </thead>
      <tbody>
        {telemetry.map((st, i) => {
          const pct = totalMs > 0 ? ((st.duration_ms / totalMs) * 100) : 0;
          return (
            <tr key={`${st.stage}-${i}`}>
              <td style={{ fontFamily: 'monospace' }}>{st.stage}</td>
              <td>
                <span
                  style={{
                    fontSize: '0.65rem', padding: '1px 6px', borderRadius: 3,
                    background: `${statusColor(st.status)}22`,
                    color: statusColor(st.status),
                    textTransform: 'uppercase', fontWeight: 600,
                  }}
                >
                  {st.status}
                </span>
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-dim)' }}>
                {fmt(st.duration_ms)}
              </td>
              <td style={{ textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-dim)' }}>
                {pct.toFixed(1)}%
              </td>
              <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem', paddingLeft: 24 }}>
                {st.error_message ? (
                  <span style={{ color: '#ef4444' }}>{st.error_message}</span>
                ) : fmtDetail(st.detail)}
              </td>
            </tr>
          );
        })}
        <tr>
          <td colSpan={2} style={{ fontFamily: 'monospace', color: 'var(--text-dim)', paddingTop: 6 }}>total</td>
          <td style={{ textAlign: 'right', fontFamily: 'monospace', color: 'var(--text-dim)', paddingTop: 6 }}>
            {fmt(totalMs)}
          </td>
          <td colSpan={2}></td>
        </tr>
      </tbody>
    </table>
  );
}
