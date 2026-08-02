import { useEffect, useRef, useState } from 'react'
import { fetchStats, fetchSpreadLog } from '../api/knowledge_graph';
import { fetchCostReport, fetchScoringHealth, fetchHealthCheck, acknowledgeAlert, acknowledgeAllAlerts } from '../api/operator';
import type { SpreadLogResponse } from '../api/knowledge_graph';
import type { ScoringHealthResponse, HealthCheckResponse } from '../api/operator';
import type { NeuronStats, CostReport } from '../types'
import { Chart, BarController, BarElement, BubbleController, PointElement, CategoryScale, LinearScale, LogarithmicScale, Tooltip, Legend } from 'chart.js'
import DeptChordDiagram from './DeptChordDiagram'

Chart.register(BarController, BarElement, BubbleController, PointElement, CategoryScale, LinearScale, LogarithmicScale, Tooltip, Legend);

const layerColors = [
  '#2dd4bf', '#60a5fa', '#a78bfa', '#f472b6', '#fb923c', '#facc15',
];

const roleColors = [
  '#2dd4bf', '#60a5fa', '#a78bfa', '#f472b6',
  '#fb923c', '#facc15', '#22c55e', '#ef4444',
  '#38bdf8', '#c084fc', '#f87171', '#34d399',
  '#fbbf24', '#818cf8', '#fb7185', '#a3e635',
  '#e879f9', '#67e8f9', '#fdba74', '#86efac',
  '#c4b5fd', '#fca5a1', '#6ee7b7', '#93c5fd',
  '#d8b4fe', '#fda4af', '#5eead4', '#bef264',
  '#f0abfc', '#7dd3fc', '#fcd34d', '#a5b4fc',
];

export default function Dashboard() {
  const [stats, setStats] = useState<NeuronStats | null>(null);
  const [cost, setCost] = useState<CostReport | null>(null);
  const [spreadLog, setSpreadLog] = useState<SpreadLogResponse | null>(null);
  const [health, setHealth] = useState<ScoringHealthResponse | null>(null);
  const segmentation = health?.segmentation;
  const [healthCheck, setHealthCheck] = useState<HealthCheckResponse | null>(null);
  const [error, setError] = useState('');
  const [bubbleLogX, setBubbleLogX] = useState(true);
  const [bubbleLogY, setBubbleLogY] = useState(true);
  const [spreadExpanded, setSpreadExpanded] = useState<number | null>(null);
  const layerRef = useRef<HTMLCanvasElement>(null);
  const deptRef = useRef<HTMLCanvasElement>(null);
  const bubbleRef = useRef<HTMLCanvasElement>(null);
  const layerChart = useRef<Chart | null>(null);
  const deptChart = useRef<Chart | null>(null);
  const bubbleChart = useRef<Chart | null>(null);

  useEffect(() => {
    Promise.all([fetchStats(), fetchCostReport(), fetchSpreadLog(), fetchScoringHealth()])
      .then(([s, c, sl, h]) => { setStats(s); setCost(c); setSpreadLog(sl); setHealth(h); })
      .catch(e => setError(e.message));
    // Health check fetched independently — don't let it break the dashboard if endpoint is unavailable
    fetchHealthCheck().then(setHealthCheck).catch(() => {});
  }, []);

  useEffect(() => {
    if (!stats) return;

    // Layer chart (includes concept layer if present)
    if (layerRef.current) {
      layerChart.current?.destroy();
      const conceptCount = stats.by_layer['layer_-1'] || 0;
      const layerLabels = [...Array(6)].map((_, i) => `L${i}`);
      const layerData = [...Array(6)].map((_, i) => stats.by_layer[`layer_${i}`] || 0);
      const barColors = [...layerColors];
      if (conceptCount > 0) {
        layerLabels.push('C');
        layerData.push(conceptCount);
        barColors.push('#e879f9');
      }
      layerChart.current = new Chart(layerRef.current, {
        type: 'bar',
        data: {
          labels: layerLabels,
          datasets: [{
            data: layerData,
            backgroundColor: barColors,
            borderRadius: 4,
          }],
        },
        options: {
          responsive: true,
          plugins: { tooltip: { enabled: true } },
          scales: {
            y: { beginAtZero: true, ticks: { color: '#c8d0dc' }, grid: { color: '#1e2d4a' } },
            x: { ticks: { color: '#c8d0dc' }, grid: { display: false } },
          },
        },
      });
    }

    // Department chart — stacked by L1 roles
    if (deptRef.current) {
      deptChart.current?.destroy();
      const deptLabels = Object.keys(stats.by_department_roles);
      // Collect all unique role labels across departments
      const allRoles = new Set<string>();
      for (const roles of Object.values(stats.by_department_roles)) {
        for (const role of Object.keys(roles)) allRoles.add(role);
      }
      const roleList = Array.from(allRoles);
      // One dataset per role
      const datasets = roleList.map((role, i) => ({
        label: role,
        data: deptLabels.map(dept => stats.by_department_roles[dept]?.[role] ?? 0),
        backgroundColor: roleColors[i % roleColors.length],
        borderRadius: 2,
      }));
      deptChart.current = new Chart(deptRef.current, {
        type: 'bar',
        data: { labels: deptLabels, datasets },
        options: {
          responsive: true,
          plugins: {
            tooltip: { enabled: true },
            legend: { display: false },
          },
          scales: {
            y: { stacked: true, beginAtZero: true, ticks: { color: '#c8d0dc' }, grid: { color: '#1e2d4a' } },
            x: { stacked: true, ticks: { color: '#c8d0dc', maxRotation: 45 }, grid: { display: false } },
          },
        },
      });
    }

    // Bubble chart — role health: neuron count vs invocations, bubble size = avg utility
    if (bubbleRef.current) {
      bubbleChart.current?.destroy();
      const departments = [...new Set(stats.role_bubbles.map(b => b.department))];
      const deptColorMap: Record<string, string> = {};
      departments.forEach((d, i) => { deptColorMap[d] = roleColors[i % roleColors.length]; });

      const datasets = departments.map(dept => {
        const roles = stats.role_bubbles.filter(b => b.department === dept);
        return {
          label: dept,
          data: roles.map(r => ({
            x: r.neuron_count,
            y: r.total_invocations,
            r: Math.max(4, r.avg_utility * 20),
            _role: r.role,
            _dept: r.department,
            _utility: r.avg_utility,
          })),
          backgroundColor: deptColorMap[dept] + '99',
          borderColor: deptColorMap[dept],
          borderWidth: 1,
        };
      });

      bubbleChart.current = new Chart(bubbleRef.current, {
        type: 'bubble',
        data: { datasets },
        options: {
          responsive: true,
          plugins: {
            legend: { display: true, position: 'bottom', labels: { color: '#c8d0dc', boxWidth: 10, font: { size: 10 } } },
            tooltip: {
              callbacks: {
                label: (ctx) => {
                  const raw = ctx.raw as { _role: string; x: number; y: number; _utility: number };
                  return `${raw._role}: ${raw.x} neurons, ${raw.y} invocations, utility ${raw._utility}`;
                },
              },
            },
          },
          scales: {
            x: { type: bubbleLogX ? 'logarithmic' as const : 'linear' as const, title: { display: true, text: 'Neuron Count', color: '#c8d0dc' }, ticks: { color: '#c8d0dc' }, grid: { color: '#1e2d4a' } },
            y: { type: bubbleLogY ? 'logarithmic' as const : 'linear' as const, title: { display: true, text: 'Total Invocations', color: '#c8d0dc' }, beginAtZero: !bubbleLogY, ticks: { color: '#c8d0dc' }, grid: { color: '#1e2d4a' } },
          },
        },
      });
    }

    return () => {
      layerChart.current?.destroy();
      deptChart.current?.destroy();
      bubbleChart.current?.destroy();
    };
  }, [stats, bubbleLogX, bubbleLogY]);

  if (error) return <div className="error-msg">{error}</div>;
  if (!stats || !cost) return <div className="loading">Loading...</div>;

  return (
    <div className="dashboard">
      <div className="stat-cards">
        <div className="stat-card">
          <div className="card-value">${cost.total_cost_usd.toFixed(4)}</div>
          <div className="card-label">Total Cost</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{cost.total_queries}</div>
          <div className="card-label">Queries</div>
        </div>
        <div className="stat-card">
          <div className="card-value">${cost.avg_cost_per_query.toFixed(6)}</div>
          <div className="card-label">Avg / Query</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{cost.total_input_tokens.toLocaleString()}</div>
          <div className="card-label">Input Tokens</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{cost.total_output_tokens.toLocaleString()}</div>
          <div className="card-label">Output Tokens</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{stats.total_neurons}</div>
          <div className="card-label">Neurons</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{stats.total_firings.toLocaleString()}</div>
          <div className="card-label">Firings</div>
        </div>
      </div>

      {health && health.status === 'ok' && (
        <div className="chart-card" style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Scoring Health Monitor</h3>
            <span style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>
              {health.queries_analyzed} queries analyzed (baseline: {health.baseline_window}, recent: {health.recent_window})
            </span>
          </div>

          {health.drift_alerts.length > 0 && (
            <div style={{
              background: '#ef444420', border: '1px solid #ef4444', borderRadius: 8,
              padding: '10px 14px', marginBottom: 16,
            }}>
              <strong style={{ color: '#ef4444', fontSize: '0.85rem' }}>Drift Detected</strong>
              {health.drift_alerts.map(a => (
                <div key={a.signal} style={{ color: '#fca5a5', fontSize: '0.8rem', marginTop: 4 }}>
                  {a.message}
                </div>
              ))}
            </div>
          )}

          {health.drift_alerts.length === 0 && health.can_detect_drift && (
            <div style={{
              background: '#22c55e15', border: '1px solid #22c55e40', borderRadius: 8,
              padding: '8px 14px', marginBottom: 16, color: '#22c55e', fontSize: '0.8rem',
            }}>
              All signals within normal range — no drift detected.
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
            {Object.entries(health.signals).map(([sig, data]) => {
              const barWidth = Math.max(5, Math.min(100, data.recent_query_means.mean * 100));
              const baselineWidth = Math.max(5, Math.min(100, data.baseline_query_means.mean * 100));
              return (
                <div key={sig} style={{
                  background: 'var(--bg-input)', borderRadius: 8, padding: '10px 12px',
                  border: data.drifted ? '1px solid #ef4444' : '1px solid transparent',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                    <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', textTransform: 'capitalize' }}>
                      {sig}
                    </span>
                    {data.drifted && <span style={{ fontSize: '0.65rem', color: '#ef4444', fontWeight: 600 }}>DRIFT</span>}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: '#c8d0dc', marginBottom: 4 }}>
                    Baseline: <strong style={{ color: '#60a5fa' }}>{data.baseline_query_means.mean.toFixed(3)}</strong>
                    <span style={{ margin: '0 4px' }}>/</span>
                    Recent: <strong style={{ color: data.drifted ? '#ef4444' : '#22c55e' }}>{data.recent_query_means.mean.toFixed(3)}</strong>
                  </div>
                  <div style={{ position: 'relative', height: 12, background: '#0f172a', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{
                      position: 'absolute', height: '100%', width: `${baselineWidth}%`,
                      background: '#60a5fa30', borderRadius: 4,
                    }} />
                    <div style={{
                      position: 'absolute', height: '100%', width: `${barWidth}%`,
                      background: data.drifted ? '#ef444480' : '#22c55e60', borderRadius: 4,
                    }} />
                  </div>
                  <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: 3 }}>
                    z={data.z_score.toFixed(1)} | σ={data.baseline_query_means.stddev.toFixed(3)}
                  </div>
                </div>
              );
            })}
          </div>

          {segmentation && segmentation.reference_neurons > 0 && (
            <div style={{ fontSize: '0.7rem', color: '#94a3b8', marginTop: 10 }}>
              Library segmented out: {segmentation.reference_neurons} reference-class
              neurons (document-ingested) are excluded from the headline signals and drift
              alerts above — their growth is curated, not organic.
              {segmentation.library && (
                <> Reference hits appeared in {segmentation.library.queries_with_reference_hits} of
                the analyzed queries (recent novelty
                μ {segmentation.library.signals.novelty.recent_query_means.mean.toFixed(3)}).</>
              )}
            </div>
          )}
        </div>
      )}

      {health && health.status === 'insufficient_data' && (
        <div className="chart-card" style={{ marginBottom: 24, color: '#c8d0dc', fontSize: '0.85rem' }}>
          <h3>Scoring Health Monitor</h3>
          <p>Insufficient data for drift detection ({health.queries_available} queries, need 5+).</p>
        </div>
      )}

      {healthCheck && (
        <div className="chart-card" style={{ marginBottom: 24 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <h3 style={{ margin: 0 }}>Production Health Check</h3>
            <span style={{
              fontSize: '0.75rem', fontWeight: 600, padding: '2px 10px', borderRadius: 4,
              background: healthCheck.circuit_breaker_tripped ? '#ef444433' : '#22c55e22',
              color: healthCheck.circuit_breaker_tripped ? '#ef4444' : '#22c55e',
              border: `1px solid ${healthCheck.circuit_breaker_tripped ? '#ef444466' : '#22c55e44'}`,
            }}>
              {healthCheck.circuit_breaker_tripped ? 'CIRCUIT BREAKER TRIPPED' : 'SYSTEM OK'}
            </span>
          </div>

          {healthCheck.circuit_breaker_tripped && (
            <div style={{ background: '#ef444422', border: '1px solid #ef444444', borderRadius: 6, padding: '8px 12px', marginBottom: 12, fontSize: '0.8rem', color: '#fca5a5' }}>
              {healthCheck.reasons.map((r, i) => <div key={i}>{r}</div>)}
            </div>
          )}

          <div className="stat-cards" style={{ marginBottom: 12 }}>
            <div className="stat-card">
              <div className="card-value" style={{ color: healthCheck.avg_eval_overall !== null && healthCheck.avg_eval_overall < 2.5 ? '#ef4444' : '#22c55e' }}>
                {healthCheck.avg_eval_overall !== null ? healthCheck.avg_eval_overall.toFixed(2) : '—'}
              </div>
              <div className="card-label">Avg Eval ({healthCheck.eval_count})</div>
            </div>
            <div className="stat-card">
              <div className="card-value" style={{ color: healthCheck.avg_user_rating !== null && healthCheck.avg_user_rating < 0.3 ? '#ef4444' : '#22c55e' }}>
                {healthCheck.avg_user_rating !== null ? healthCheck.avg_user_rating.toFixed(2) : '—'}
              </div>
              <div className="card-label">Avg Rating ({healthCheck.rating_count})</div>
            </div>
            <div className="stat-card">
              <div className="card-value">{healthCheck.drift_alerts_count}</div>
              <div className="card-label">Drift Alerts</div>
            </div>
            <div className="stat-card">
              <div className="card-value" style={{ color: healthCheck.model_version_changed ? '#fb923c' : undefined }}>
                {healthCheck.model_versions.length > 0 ? healthCheck.model_versions[0] : '—'}
              </div>
              <div className="card-label">Model Version{healthCheck.model_version_changed ? ' (changed!)' : ''}</div>
            </div>
          </div>

          {healthCheck.active_alerts.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)' }}>Active Alerts ({healthCheck.active_alerts.length})</span>
                <button className="btn btn-sm" style={{ fontSize: '0.7rem' }} onClick={async () => {
                  await acknowledgeAllAlerts();
                  const hc = await fetchHealthCheck();
                  setHealthCheck(hc);
                }}>Acknowledge All</button>
              </div>
              {healthCheck.active_alerts.map(a => (
                <div key={a.id} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '4px 8px', marginBottom: 4, borderRadius: 4, fontSize: '0.75rem',
                  background: a.severity === 'critical' ? '#ef444418' : a.severity === 'warning' ? '#fb923c18' : '#3b82f618',
                  border: `1px solid ${a.severity === 'critical' ? '#ef444433' : a.severity === 'warning' ? '#fb923c33' : '#3b82f633'}`,
                }}>
                  <span style={{ fontWeight: 600, color: a.severity === 'critical' ? '#ef4444' : a.severity === 'warning' ? '#fb923c' : '#3b82f6', textTransform: 'uppercase', fontSize: '0.65rem' }}>
                    {a.severity}
                  </span>
                  <span style={{ color: 'var(--text-dim)', flex: 1 }}>{a.message}</span>
                  <button style={{ background: 'none', border: 'none', color: '#c8d0dc', cursor: 'pointer', fontSize: '0.7rem' }} onClick={async () => {
                    await acknowledgeAlert(a.id);
                    const hc = await fetchHealthCheck();
                    setHealthCheck(hc);
                  }}>dismiss</button>
                </div>
              ))}
            </div>
          )}

          {healthCheck.active_alerts.length === 0 && !healthCheck.circuit_breaker_tripped && (
            <div style={{ fontSize: '0.8rem', color: '#22c55e', marginTop: 4 }}>
              No active alerts. All systems nominal.
            </div>
          )}
        </div>
      )}

      <div className="charts-grid">
        <div className="chart-card">
          <h3>Neurons by Layer</h3>
          <canvas ref={layerRef} />
        </div>
        <div className="chart-card">
          <h3>Neurons by Department</h3>
          <canvas ref={deptRef} />
        </div>
      </div>

      <div className="chart-card" style={{ marginBottom: '24px' }}>
        <h3>Department Co-Firing (Chord Diagram)</h3>
        <DeptChordDiagram />
      </div>

      {spreadLog && (
        <div className="chart-card" style={{ marginBottom: '24px' }}>
          <h3>Spread Activation Log</h3>
          <div className="stat-cards" style={{ marginBottom: 16 }}>
            <div className="stat-card">
              <div className="card-value">{spreadLog.queries_with_spread}</div>
              <div className="card-label">Queries with Spread</div>
            </div>
            <div className="stat-card">
              <div className="card-value">{Math.round(spreadLog.spread_rate * 100)}%</div>
              <div className="card-label">Spread Rate</div>
            </div>
            <div className="stat-card">
              <div className="card-value">{spreadLog.top_corridors.length}</div>
              <div className="card-label">Cross-Dept Corridors</div>
            </div>
          </div>

          {spreadLog.top_corridors.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h4 style={{ color: '#e8a735', margin: '0 0 8px', fontSize: '0.85rem' }}>Top Cross-Department Corridors</h4>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {spreadLog.top_corridors.map(c => (
                  <span key={c.pair} style={{
                    background: '#e8a73520', color: '#e8a735', padding: '4px 10px',
                    borderRadius: 6, fontSize: '0.75rem', border: '1px solid #e8a73540',
                  }}>
                    {c.pair} <strong>({c.count})</strong>
                  </span>
                ))}
              </div>
            </div>
          )}

          {spreadLog.top_neurons.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <h4 style={{ color: '#60a5fa', margin: '0 0 8px', fontSize: '0.85rem' }}>Most Frequently Spread-Promoted Neurons</h4>
              <table className="about-table" style={{ fontSize: '0.8rem' }}>
                <thead>
                  <tr><th>Neuron</th><th>Department</th><th>Spread Count</th></tr>
                </thead>
                <tbody>
                  {spreadLog.top_neurons.map(n => (
                    <tr key={n.neuron_id}>
                      <td>{n.label}</td>
                      <td style={{ color: '#c8d0dc' }}>{n.department}</td>
                      <td><strong>{n.spread_count}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <h4 style={{ color: 'var(--text-dim)', margin: '0 0 8px', fontSize: '0.85rem' }}>Recent Queries</h4>
          <table className="about-table" style={{ fontSize: '0.8rem' }}>
            <thead>
              <tr>
                <th>Query</th>
                <th style={{ textAlign: 'center' }}>Promoted</th>
                <th style={{ textAlign: 'center' }}>Avg Boost</th>
                <th style={{ textAlign: 'center' }}>Max Boost</th>
                <th style={{ textAlign: 'center' }}>Cross-Dept</th>
              </tr>
            </thead>
            <tbody>
              {spreadLog.entries.map(e => (
                <>
                  <tr
                    key={e.query_id}
                    style={{ cursor: e.promoted_count > 0 ? 'pointer' : 'default' }}
                    onClick={() => e.promoted_count > 0 && setSpreadExpanded(
                      spreadExpanded === e.query_id ? null : e.query_id
                    )}
                  >
                    <td style={{ maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.promoted_count > 0 && (
                        <span style={{ color: 'var(--text-dim)', marginRight: 4, fontSize: '0.7rem' }}>
                          {spreadExpanded === e.query_id ? '▼' : '▶'}
                        </span>
                      )}
                      {e.user_message}
                    </td>
                    <td style={{ textAlign: 'center', color: e.promoted_count > 0 ? '#e8a735' : 'var(--text-dim)' }}>
                      {e.promoted_count || '—'}
                    </td>
                    <td style={{ textAlign: 'center' }}>{e.promoted_count > 0 ? e.avg_boost.toFixed(3) : '—'}</td>
                    <td style={{ textAlign: 'center' }}>{e.promoted_count > 0 ? e.max_boost.toFixed(3) : '—'}</td>
                    <td style={{ textAlign: 'center' }}>
                      {e.promoted_count > 0 ? (
                        <span style={{ color: e.cross_dept ? '#22c55e' : 'var(--text-dim)' }}>
                          {e.cross_dept ? 'Yes' : 'No'}
                        </span>
                      ) : '—'}
                    </td>
                  </tr>
                  {spreadExpanded === e.query_id && e.promoted_neurons.map(pn => (
                    <tr key={`${e.query_id}-${pn.neuron_id}`} style={{ background: 'var(--bg-input)' }}>
                      <td style={{ paddingLeft: 28, color: '#e8a735' }}>{pn.label}</td>
                      <td style={{ textAlign: 'center', color: '#c8d0dc' }} colSpan={2}>{pn.department}</td>
                      <td style={{ textAlign: 'center' }}>+{pn.boost.toFixed(3)}</td>
                      <td />
                    </tr>
                  ))}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="chart-card" style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ margin: 0 }}>Role Health — Neurons vs Invocations (bubble = utility)</h3>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={() => setBubbleLogX(v => !v)}
              style={{
                padding: '4px 10px', fontSize: '0.75rem', borderRadius: '6px', cursor: 'pointer',
                border: '1px solid ' + (bubbleLogX ? '#60a5fa' : '#334155'),
                background: bubbleLogX ? '#1e3a5f' : 'transparent',
                color: bubbleLogX ? '#60a5fa' : '#c8d0dc',
              }}
            >
              X: {bubbleLogX ? 'Log' : 'Linear'}
            </button>
            <button
              onClick={() => setBubbleLogY(v => !v)}
              style={{
                padding: '4px 10px', fontSize: '0.75rem', borderRadius: '6px', cursor: 'pointer',
                border: '1px solid ' + (bubbleLogY ? '#60a5fa' : '#334155'),
                background: bubbleLogY ? '#1e3a5f' : 'transparent',
                color: bubbleLogY ? '#60a5fa' : '#c8d0dc',
              }}
            >
              Y: {bubbleLogY ? 'Log' : 'Linear'}
            </button>
          </div>
        </div>
        <canvas ref={bubbleRef} />
      </div>

    </div>
  );
}
