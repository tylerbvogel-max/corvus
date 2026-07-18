import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import {
  fetchStats, fetchCostReport, fetchSpreadLog, fetchScoringHealth,
  fetchStageTelemetry, type StageTelemetryReport,
  type SpreadLogResponse, type ScoringHealthResponse,
} from '../api';
import type { NeuronStats, CostReport } from '../types';

/**
 * Performance — the unified operations dashboard (absorbs the old Dashboard
 * and Pipeline Timing pages). Top to bottom: volume/cost/token/spread tiles,
 * the Corvus-overhead hero line, scoring-health drift monitor, then the
 * per-stage latency suite (budget with inline target comparison, distribution
 * & stability, drift over time). Read-only; no LLM calls.
 */

const C = {
  accent: 'var(--accent, #c87533)', blue: 'var(--blue, #4a9eff)',
  green: 'var(--green, #22c55e)', red: 'var(--red, #e8684a)',
  text: 'var(--text, #e8edf7)', dim: 'var(--text-dim, #8a93a6)',
  border: 'var(--border, #2a3446)', card: 'var(--bg-card, #14161c)',
  input: 'var(--bg-input, #0e0f13)',
};

function fmtMs(v: number): string {
  if (v >= 10000) return (v / 1000).toFixed(1) + 's';
  if (v >= 1000) return (v / 1000).toFixed(2) + 's';
  if (v >= 100) return v.toFixed(0) + 'ms';
  if (v >= 1) return v.toFixed(1) + 'ms';
  return v.toFixed(2) + 'ms';
}
const log1p = (v: number) => Math.log10(Math.max(0, v) + 1);

type Tip = { x: number; y: number; lines: string[] } | null;

export default function PerformancePage() {
  const [stats, setStats] = useState<NeuronStats | null>(null);
  const [cost, setCost] = useState<CostReport | null>(null);
  const [spreadLog, setSpreadLog] = useState<SpreadLogResponse | null>(null);
  const [health, setHealth] = useState<ScoringHealthResponse | null>(null);
  const [data, setData] = useState<StageTelemetryReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [logScale, setLogScale] = useState(true);
  const [trendStage, setTrendStage] = useState('classify');
  const [tip, setTip] = useState<Tip>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchStats(), fetchCostReport(), fetchStageTelemetry()])
      .then(([s, c, t]) => {
        setStats(s); setCost(c);
        if (t.error) setErr(t.error); else setData(t);
      })
      .catch(e => setErr(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
    // Secondary panels load independently — never block the page on them.
    fetchSpreadLog().then(setSpreadLog).catch(() => {});
    fetchScoringHealth().then(setHealth).catch(() => {});
  }, []);

  const stages = data?.stages ?? [];
  const dominant = useMemo(() => [...stages].sort((a, b) => b.share_pct - a.share_pct)[0], [stages]);
  const byBudget = useMemo(() => [...stages].sort((a, b) => b.p50 - a.p50), [stages]);
  const trendPts = useMemo(
    () => (data?.trend ?? []).filter(t => t.stage === trendStage).sort((a, b) => a.bucket.localeCompare(b.bucket)),
    [data, trendStage],
  );

  if (loading) return <div style={{ padding: 32, color: C.dim }}>Loading performance data…</div>;
  if (err && !data) return <div style={{ padding: 32, color: C.red }}>{err}</div>;

  const m = data?.meta;
  const smallN = (m?.queries_with_telemetry ?? 0) < 30;
  const maxBudget = Math.max(...byBudget.map(s => s.p50), 1);

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1080, position: 'relative' }} onMouseLeave={() => setTip(null)}>
      <h2 style={{ color: C.text, fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Performance</h2>
      <p style={{ color: C.dim, fontSize: 13, margin: '0 0 14px', maxWidth: 720 }}>
        Volume, cost, scoring health, spread activation, and per-stage pipeline latency — everything
        measured from live query telemetry.
      </p>

      {/* Volume / cost / token tiles */}
      {stats && cost && (
        <div style={tiles}>
          <Tile value={cost.total_queries.toLocaleString()} label="Queries" />
          <Tile value={`$${cost.maintenance_cost_usd.toFixed(2)}`} label="Total cost (API est.)" />
          <Tile value={`$${cost.maintenance_per_query_usd.toFixed(6)}`} label="Maintenance / query" />
          <Tile value={cost.total_input_tokens.toLocaleString()} label="Input tokens" />
          <Tile value={cost.total_output_tokens.toLocaleString()} label="Output tokens" />
          <Tile value={stats.total_neurons.toLocaleString()} label="Neurons" />
          <Tile value={stats.total_firings.toLocaleString()} label="Firings" />
          {spreadLog && <>
            <Tile value={spreadLog.queries_with_spread.toLocaleString()} label={`Queries with spread (${spreadLog.rate_window_days}d)`} />
            <Tile value={`${(spreadLog.spread_rate * 100).toFixed(1)}%`} label={`Spread rate (${spreadLog.rate_window_days}d)`} />
          </>}
        </div>
      )}

      {/* Hero: what Corvus costs per query, and where that time goes */}
      {m && dominant && cost && (
        <div style={{ ...card, borderColor: C.accent, margin: '14px 0 20px' }}>
          <div style={heroKicker}>Corvus overhead per query</div>
          <div style={{ color: C.text, fontSize: 15, lineHeight: 1.5 }}>
            The pipeline adds a median <strong>{fmtMs(m.pipeline_total_p50_ms)}</strong>, and the
            maintenance jobs that keep the graph viable amortize to{' '}
            <strong>${cost.maintenance_per_query_usd.toFixed(6)}</strong> per query
            ({m.queries_with_telemetry.toLocaleString()} queries measured; the hot path itself makes no LLM calls).{' '}
            <strong>{dominant.label}</strong> is <strong>{dominant.share_pct}%</strong> of that latency
            {dominant.ratio_p50_vs_estimate != null && <> — <strong>{dominant.ratio_p50_vs_estimate}×</strong> its design target</>};
            {' '}everything else combines to {Math.round(100 - dominant.share_pct)}%.
          </div>
        </div>
      )}
      {smallN && m && (
        <div style={{ ...caution, marginBottom: 18 }}>
          Small sample (n={m.queries_with_telemetry}) — treat these as directional, not precise. Percentile tails especially are noisy.
        </div>
      )}

      {/* Maintenance cost by action type */}
      {cost && cost.maintenance_by_workload.length > 0 && (
        <Section
          title="Cost by action type"
          subtitle={`What each maintenance job would bill at API list price (calls run on subscription, so these are price/token equivalents, not cash; benchmark evals excluded). Cost scales with how much CLI-session content gets distilled — not with graph size. Ledger since ${cost.maintenance_since ? cost.maintenance_since.slice(0, 10) : '—'}. Total $${cost.maintenance_cost_usd.toFixed(2)} ÷ ${cost.total_queries.toLocaleString()} queries = $${cost.maintenance_per_query_usd.toFixed(6)}/query.`}
        >
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
              <thead>
                <tr style={{ color: C.dim, textAlign: 'right' }}>
                  <th style={{ ...th, textAlign: 'left' }}>Action type</th>
                  <th style={{ ...th, textAlign: 'left' }}>Models</th>
                  <th style={th}>calls</th><th style={th}>input</th><th style={th}>output</th>
                  <th style={th}>cache write</th><th style={th}>cache read</th><th style={th}>cost</th>
                </tr>
              </thead>
              <tbody>
                {cost.maintenance_by_workload.map(w => (
                  <tr key={w.workload} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td style={{ ...tdc, textAlign: 'left', color: C.text }}>{w.workload}</td>
                    <td style={{ ...tdc, textAlign: 'left' }}>{w.models.join(', ')}</td>
                    <td style={tdc}>{w.calls.toLocaleString()}</td>
                    <td style={tdc}>{w.input_tokens.toLocaleString()}</td>
                    <td style={tdc}>{w.output_tokens.toLocaleString()}</td>
                    <td style={tdc}>{w.cache_creation_tokens.toLocaleString()}</td>
                    <td style={tdc}>{w.cache_read_tokens.toLocaleString()}</td>
                    <td style={{ ...tdc, color: C.text, fontWeight: 600 }}>${w.equivalent_cost_usd.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* Scoring health */}
      {health && health.status === 'ok' && (
        <Section
          title="Scoring health"
          subtitle={`Recent scoring signals vs baseline — drift means the graph is answering differently than it used to. ${health.queries_analyzed} queries analyzed (baseline ${health.baseline_window}, recent ${health.recent_window}).`}
        >
          {health.drift_alerts.length > 0 ? (
            <div style={{ ...alertBox, borderColor: C.red, background: 'rgba(232,104,74,0.08)' }}>
              <strong style={{ color: C.red, fontSize: 13 }}>Drift detected</strong>
              {health.drift_alerts.map(a => (
                <div key={a.signal} style={{ color: C.dim, fontSize: 12, marginTop: 4 }}>{a.message}</div>
              ))}
            </div>
          ) : health.can_detect_drift && (
            <div style={{ ...alertBox, borderColor: 'rgba(34,197,94,0.35)', background: 'rgba(34,197,94,0.07)', color: C.green, fontSize: 12 }}>
              All signals within normal range — no drift detected.
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12, marginTop: 12 }}>
            {Object.entries(health.signals).map(([sig, d]) => {
              const recentW = Math.max(5, Math.min(100, d.recent_query_means.mean * 100));
              const baseW = Math.max(5, Math.min(100, d.baseline_query_means.mean * 100));
              return (
                <div key={sig} style={{ background: C.input, borderRadius: 8, padding: '10px 12px', border: `1px solid ${d.drifted ? C.red : 'transparent'}` }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: C.text, textTransform: 'capitalize' }}>{sig}</span>
                    {d.drifted && <span style={{ fontSize: 10, color: C.red, fontWeight: 700 }}>DRIFT</span>}
                  </div>
                  <div style={{ fontSize: 11, color: C.dim, marginBottom: 4 }}>
                    baseline <strong style={{ color: C.blue }}>{d.baseline_query_means.mean.toFixed(3)}</strong>
                    {' / '}recent <strong style={{ color: d.drifted ? C.red : C.green }}>{d.recent_query_means.mean.toFixed(3)}</strong>
                  </div>
                  <div style={{ position: 'relative', height: 12, background: 'rgba(0,0,0,0.35)', borderRadius: 4, overflow: 'hidden' }}>
                    <div style={{ position: 'absolute', height: '100%', width: `${baseW}%`, background: 'rgba(74,158,255,0.25)', borderRadius: 4 }} />
                    <div style={{ position: 'absolute', height: '100%', width: `${recentW}%`, background: d.drifted ? 'rgba(232,104,74,0.55)' : 'rgba(34,197,94,0.45)', borderRadius: 4 }} />
                  </div>
                  <div style={{ fontSize: 10, color: C.dim, marginTop: 3 }}>
                    z={d.z_score.toFixed(1)} · σ={d.baseline_query_means.stddev.toFixed(3)}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}
      {health && health.status === 'insufficient_data' && (
        <Section title="Scoring health" subtitle={`Insufficient data for drift detection (${health.queries_available} queries, need 5+).`}>{null}</Section>
      )}

      {/* Latency budget */}
      {m && byBudget.length > 0 && <>
        <Section
          title="Latency budget (median per stage)"
          subtitle={`One hue = magnitude. Log scale by default because the top stage dwarfs the rest. ${m.queries_with_telemetry} representative queries · ${m.total_samples} stage samples${m.excluded_incident_queries ? ` · ${m.excluded_incident_queries} incident pipelines excluded (raw telemetry retained)` : ''}${m.date_range[0] ? ` · ${m.date_range[0].slice(0, 10)} → ${(m.date_range[1] || '').slice(0, 10)}` : ''}.`}
          right={<Toggle on={logScale} setOn={setLogScale} label="log scale" />}
        >
          <div style={{ display: 'grid', gap: 9 }}>
            {byBudget.map(s => {
              const frac = logScale ? log1p(s.p50) / log1p(maxBudget) : s.p50 / maxBudget;
              const hasTarget = s.estimate_ms != null && s.ratio_p50_vs_estimate != null;
              const slower = (s.ratio_p50_vs_estimate ?? 0) >= 1;
              return (
                <div key={s.stage} style={latencyRow}>
                  <span style={barLabel}>{s.label}</span>
                  <div style={{ position: 'relative', height: 20 }}
                    onMouseMove={e => setTip({ x: e.clientX, y: e.clientY, lines: [s.label, `measured p50 ${fmtMs(s.p50)} · p95 ${fmtMs(s.p95)}`, hasTarget ? `design target ${fmtMs(s.estimate_ms!)} → ${s.ratio_p50_vs_estimate}×` : `max ${fmtMs(s.max)} · n=${s.n}`] })}>
                    <div style={{ position: 'absolute', inset: 0, background: C.input, borderRadius: 4 }} />
                    <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.max(frac * 100, 0.6)}%`, background: C.accent, borderRadius: 4, minWidth: 3 }} />
                    <span style={{ position: 'absolute', left: 8, top: 2, fontSize: 11, color: '#0d0d0d', fontWeight: 700, textShadow: '0 0 2px rgba(255,255,255,.4)' }}>
                      {fmtMs(s.p50)} · {s.share_pct}%
                    </span>
                  </div>
                  <div style={targetCell}>
                    {hasTarget ? <>
                      <span style={targetKicker}>target {fmtMs(s.estimate_ms!)}</span>
                      <strong style={{ color: slower ? C.red : C.green }}>{slower ? '▲' : '▼'} {s.ratio_p50_vs_estimate}×</strong>
                    </> : <span style={targetKicker}>no target</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>

        {/* Distribution & stability */}
        <Section title="Distribution & stability" subtitle="Percentiles per stage, plus coefficient of variation (stddev ÷ mean) — high CoV = unstable, a tuning target.">
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
              <thead>
                <tr style={{ color: C.dim, textAlign: 'right' }}>
                  <th style={{ ...th, textAlign: 'left' }}>Stage</th>
                  <th style={th}>n</th><th style={th}>mean</th><th style={th}>p50</th><th style={th}>p90</th>
                  <th style={th}>p95</th><th style={th}>p99</th><th style={th}>max</th><th style={th}>CoV</th>
                </tr>
              </thead>
              <tbody>
                {stages.map(s => (
                  <tr key={s.stage} style={{ borderTop: `1px solid ${C.border}` }}>
                    <td style={{ ...tdc, textAlign: 'left', color: C.text }}>{s.label}</td>
                    <td style={tdc}>{s.n}</td>
                    <td style={tdc}>{fmtMs(s.mean)}</td>
                    <td style={{ ...tdc, color: C.text }}>{fmtMs(s.p50)}</td>
                    <td style={tdc}>{fmtMs(s.p90)}</td>
                    <td style={tdc}>{fmtMs(s.p95)}</td>
                    <td style={tdc}>{fmtMs(s.p99)}</td>
                    <td style={tdc}>{fmtMs(s.max)}</td>
                    <td style={{ ...tdc, color: s.cov >= 1 ? C.red : s.cov >= 0.5 ? C.accent : C.dim, fontWeight: s.cov >= 1 ? 700 : 400 }}>{s.cov.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* Drift over time */}
        <Section
          title="Drift over time"
          subtitle="Per-day median (p50) and tail (p95) for one stage. Watch for a stage creeping up over time."
          right={
            <select value={trendStage} onChange={e => setTrendStage(e.target.value)} style={select}>
              {stages.map(s => <option key={s.stage} value={s.stage}>{s.label}</option>)}
            </select>
          }
        >
          <DriftChart points={trendPts} onTip={setTip} />
        </Section>
      </>}

      {tip && (
        <div style={{ position: 'fixed', left: tip.x + 14, top: tip.y + 14, zIndex: 50, pointerEvents: 'none', background: 'rgba(12,14,20,0.95)', border: `1px solid ${C.border}`, borderRadius: 7, padding: '7px 10px', fontSize: 12 }}>
          {tip.lines.map((l, i) => <div key={i} style={{ color: i === 0 ? C.text : C.dim, fontWeight: i === 0 ? 700 : 400 }}>{l}</div>)}
        </div>
      )}
    </div>
  );
}

function Tile({ value, label }: { value: string | number; label: string }) {
  return (
    <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: '12px 14px' }}>
      <div style={{ color: C.text, fontFamily: 'var(--font-mono, monospace)', fontSize: 20, fontWeight: 600, lineHeight: 1.2 }}>{value}</div>
      <div style={{ color: C.dim, fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.6, marginTop: 3 }}>{label}</div>
    </div>
  );
}

function DriftChart({ points, onTip }: { points: { bucket: string; p50: number; p95: number; n: number }[]; onTip: (t: Tip) => void }) {
  const W = 1000, H = 240, padL = 56, padR = 16, padT = 12, padB = 30;
  if (points.length === 0) return <div style={{ color: C.dim, fontSize: 12, padding: 12 }}>No dated samples for this stage.</div>;
  const maxY = Math.max(...points.flatMap(p => [p.p50, p.p95]), 1) * 1.1;
  const n = points.length;
  const xAt = (i: number) => padL + (n === 1 ? (W - padL - padR) / 2 : (i / (n - 1)) * (W - padL - padR));
  const yAt = (v: number) => padT + (1 - v / maxY) * (H - padT - padB);
  const path = (key: 'p50' | 'p95') => points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(1)},${yAt(p[key]).toFixed(1)}`).join(' ');
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => f * maxY);
  return (
    <div>
      <div style={{ display: 'flex', gap: 16, fontSize: 11, color: C.dim, marginBottom: 6 }}>
        <span><span style={{ display: 'inline-block', width: 14, height: 2, background: C.blue, verticalAlign: 'middle' }} /> p50 (median)</span>
        <span><span style={{ display: 'inline-block', width: 14, height: 2, background: C.accent, verticalAlign: 'middle' }} /> p95 (tail)</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: 'auto' }}
        onMouseMove={e => {
          const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const px = ((e.clientX - rect.left) / rect.width) * W;
          const i = Math.max(0, Math.min(n - 1, Math.round(((px - padL) / (W - padL - padR)) * (n - 1))));
          const p = points[i];
          if (p) onTip({ x: e.clientX, y: e.clientY, lines: [p.bucket, `p50 ${fmtMs(p.p50)} · p95 ${fmtMs(p.p95)}`, `n=${p.n}`] });
        }}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={yAt(t)} y2={yAt(t)} stroke={C.border} strokeWidth={0.5} />
            <text x={padL - 6} y={yAt(t) + 3} textAnchor="end" fontSize={10} fill={C.dim}>{fmtMs(t)}</text>
          </g>
        ))}
        <path d={path('p95')} fill="none" stroke={C.accent} strokeWidth={2} strokeLinejoin="round" />
        <path d={path('p50')} fill="none" stroke={C.blue} strokeWidth={2} strokeLinejoin="round" />
        {points.map((p, i) => <circle key={'a' + i} cx={xAt(i)} cy={yAt(p.p95)} r={2.5} fill={C.accent} />)}
        {points.map((p, i) => <circle key={'b' + i} cx={xAt(i)} cy={yAt(p.p50)} r={2.5} fill={C.blue} />)}
        {points.map((p, i) => (n <= 12 || i % Math.ceil(n / 8) === 0) && (
          <text key={'x' + i} x={xAt(i)} y={H - 10} textAnchor="middle" fontSize={9} fill={C.dim}>{p.bucket.slice(5)}</text>
        ))}
      </svg>
    </div>
  );
}

function Section({ title, subtitle, right, children }: { title: string; subtitle?: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ ...card, marginBottom: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
        <div>
          <div style={{ color: C.text, fontSize: 14, fontWeight: 700 }}>{title}</div>
          {subtitle && <div style={{ color: C.dim, fontSize: 12, marginTop: 2, maxWidth: 760 }}>{subtitle}</div>}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Toggle({ on, setOn, label }: { on: boolean; setOn: (v: boolean) => void; label: string }) {
  return (
    <label style={{ display: 'flex', gap: 6, alignItems: 'center', color: C.dim, fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap' }}>
      <input type="checkbox" checked={on} onChange={e => setOn(e.target.checked)} /> {label}
    </label>
  );
}

const card: CSSProperties = { background: C.card, border: `1px solid ${C.border}`, borderRadius: 10, padding: '16px 18px' };
const caution: CSSProperties = { background: 'rgba(200,117,51,0.1)', border: `1px solid ${C.accent}`, borderRadius: 8, padding: '8px 12px', color: C.text, fontSize: 12 };
const alertBox: CSSProperties = { border: '1px solid', borderRadius: 8, padding: '8px 12px' };
const heroKicker: CSSProperties = { color: C.accent, fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 6 };
const tiles: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: 10 };
const latencyRow: CSSProperties = { display: 'grid', gridTemplateColumns: '230px minmax(0, 1fr) 108px', gap: 12, alignItems: 'center' };
const barLabel: CSSProperties = { color: C.dim, fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap' };
const targetCell: CSSProperties = { display: 'grid', gap: 1, justifyItems: 'start', fontSize: 11, fontFamily: 'var(--font-mono, monospace)', whiteSpace: 'nowrap' };
const targetKicker: CSSProperties = { color: C.dim, fontSize: 10 };
const th: CSSProperties = { padding: '6px 10px', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 };
const tdc: CSSProperties = { padding: '6px 10px', textAlign: 'right', color: C.dim, fontFamily: 'var(--font-mono, monospace)', whiteSpace: 'nowrap' };
const select: CSSProperties = { background: C.input, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: '4px 8px', fontSize: 12 };
