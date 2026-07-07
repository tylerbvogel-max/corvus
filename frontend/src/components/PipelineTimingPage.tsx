import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { fetchStageTelemetry, type StageStat, type StageTelemetryReport } from '../api';

/**
 * Pipeline Timing — statistical view of per-stage recall latency from
 * Query.stage_telemetry_json. Surfaces where the latency budget goes, how far
 * measured p50 drifts from the documented estimate, per-stage stability (CoV +
 * percentiles), and drift over time. Read-only; all stats computed server-side.
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

export default function PipelineTimingPage() {
  const [data, setData] = useState<StageTelemetryReport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [logScale, setLogScale] = useState(true);
  const [trendStage, setTrendStage] = useState('classify');
  const [tip, setTip] = useState<Tip>(null);

  useEffect(() => {
    setLoading(true);
    fetchStageTelemetry()
      .then(d => { if (d.error) setErr(d.error); else setData(d); })
      .catch(e => setErr(e instanceof Error ? e.message : 'Failed to load telemetry'))
      .finally(() => setLoading(false));
  }, []);

  const stages = data?.stages ?? [];
  const dominant = useMemo(() => [...stages].sort((a, b) => b.share_pct - a.share_pct)[0], [stages]);
  const byBudget = useMemo(() => [...stages].sort((a, b) => b.p50 - a.p50), [stages]);
  const withEst = useMemo(() => stages.filter(s => s.estimate_ms != null && s.ratio_p50_vs_estimate != null), [stages]);
  const trendPts = useMemo(
    () => (data?.trend ?? []).filter(t => t.stage === trendStage).sort((a, b) => a.bucket.localeCompare(b.bucket)),
    [data, trendStage],
  );

  if (loading) return <div style={{ padding: 32, color: C.dim }}>Loading pipeline telemetry…</div>;
  if (err) return <div style={{ padding: 32, color: C.red }}>{err}</div>;
  if (!data || !dominant) return <div style={{ padding: 32, color: C.dim }}>No telemetry yet.</div>;

  const m = data.meta;
  const smallN = m.queries_with_telemetry < 30;
  const maxBudget = Math.max(...byBudget.map(s => s.p50), 1);

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1080, position: 'relative' }} onMouseLeave={() => setTip(null)}>
      <h2 style={{ color: C.text, fontSize: 20, fontWeight: 700, margin: '0 0 4px' }}>Pipeline Timing</h2>
      <p style={{ color: C.dim, fontSize: 13, margin: '0 0 6px', maxWidth: 720 }}>
        Per-stage recall latency measured from <code>stage_telemetry_json</code>. Where the budget goes,
        how far reality drifts from the documented estimates, per-stage stability, and drift over time.
      </p>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', color: C.dim, fontSize: 12, marginBottom: 6 }}>
        <span><strong style={{ color: C.text }}>{m.queries_with_telemetry}</strong> queries · {m.total_samples} stage samples</span>
        <span>pipeline median <strong style={{ color: C.text }}>{fmtMs(m.pipeline_total_p50_ms)}</strong></span>
        {m.date_range[0] && <span>{m.date_range[0].slice(0, 10)} → {(m.date_range[1] || '').slice(0, 10)}</span>}
      </div>
      {smallN && (
        <div style={{ ...caution, marginBottom: 18 }}>
          Small sample (n={m.queries_with_telemetry}) — treat these as directional, not precise. Percentile tails especially are noisy.
        </div>
      )}

      {/* Hero insight (computed) */}
      <div style={{ ...card, borderColor: C.accent, marginBottom: 20 }}>
        <div style={{ color: C.accent, fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8, marginBottom: 6 }}>Where the time goes</div>
        <div style={{ color: C.text, fontSize: 15, lineHeight: 1.5 }}>
          <strong>{dominant.label}</strong> is <strong>{dominant.share_pct}%</strong> of the median pipeline latency
          {dominant.ratio_p50_vs_estimate != null && <> — <strong>{dominant.ratio_p50_vs_estimate}×</strong> its documented estimate</>}.
          {' '}Everything else combines to {Math.round(100 - dominant.share_pct)}%.
        </div>
      </div>

      {/* 1 — Latency budget */}
      <Section
        title="Latency budget (median per stage)"
        subtitle="One hue = magnitude. Log scale by default because the top stage dwarfs the rest."
        right={<Toggle on={logScale} setOn={setLogScale} label="log scale" />}
      >
        <div style={{ display: 'grid', gap: 9 }}>
          {byBudget.map(s => {
            const frac = logScale ? log1p(s.p50) / log1p(maxBudget) : s.p50 / maxBudget;
            return (
              <div key={s.stage} style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 12, alignItems: 'center' }}>
                <span style={{ color: C.dim, fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.label}</span>
                <div style={{ position: 'relative', height: 20 }}
                  onMouseMove={e => setTip({ x: e.clientX, y: e.clientY, lines: [s.label, `p50 ${fmtMs(s.p50)} · p95 ${fmtMs(s.p95)}`, `max ${fmtMs(s.max)} · n=${s.n}`] })}>
                  <div style={{ position: 'absolute', inset: 0, background: C.input, borderRadius: 4 }} />
                  <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.max(frac * 100, 0.6)}%`, background: C.accent, borderRadius: 4, minWidth: 3 }} />
                  <span style={{ position: 'absolute', left: 8, top: 2, fontSize: 11, color: '#0d0d0d', fontWeight: 700, textShadow: '0 0 2px rgba(255,255,255,.4)' }}>
                    {fmtMs(s.p50)} · {s.share_pct}%
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </Section>

      {/* 2 — Estimate vs actual */}
      <Section
        title="Estimate vs. actual (measured p50 ÷ documented estimate)"
        subtitle="1× = matches the design assumption. Right/red = slower than documented; left/green = faster. Bars are log-scaled around 1×."
      >
        <RatioBars items={withEst} onTip={setTip} />
      </Section>

      {/* 3 — Distribution & stability */}
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

      {/* 4 — Drift over time */}
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

      {tip && (
        <div style={{ position: 'fixed', left: tip.x + 14, top: tip.y + 14, zIndex: 50, pointerEvents: 'none', background: 'rgba(12,14,20,0.95)', border: `1px solid ${C.border}`, borderRadius: 7, padding: '7px 10px', fontSize: 12 }}>
          {tip.lines.map((l, i) => <div key={i} style={{ color: i === 0 ? C.text : C.dim, fontWeight: i === 0 ? 700 : 400 }}>{l}</div>)}
        </div>
      )}
    </div>
  );
}

function RatioBars({ items, onTip }: { items: StageStat[]; onTip: (t: Tip) => void }) {
  const ratios = items.map(s => s.ratio_p50_vs_estimate!);
  const maxLog = Math.max(...ratios.map(r => Math.abs(Math.log10(r))), 0.3);
  return (
    <div style={{ display: 'grid', gap: 9 }}>
      {items.map(s => {
        const r = s.ratio_p50_vs_estimate!;
        const l = Math.log10(r);
        const half = (Math.abs(l) / maxLog) * 50; // % of half-width
        const slower = r >= 1;
        return (
          <div key={s.stage} style={{ display: 'grid', gridTemplateColumns: '150px 1fr', gap: 12, alignItems: 'center' }}>
            <span style={{ color: C.dim, fontSize: 12, textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.label}</span>
            <div style={{ position: 'relative', height: 20 }}
              onMouseMove={e => onTip({ x: e.clientX, y: e.clientY, lines: [s.label, `measured p50 ${fmtMs(s.p50)}`, `documented ${fmtMs(s.estimate_ms!)} → ${r}×`] })}>
              <div style={{ position: 'absolute', inset: 0, background: C.input, borderRadius: 4 }} />
              <div style={{ position: 'absolute', left: '50%', top: -2, bottom: -2, width: 1, background: C.border }} />
              <div style={{ position: 'absolute', top: 0, bottom: 0, borderRadius: 4, background: slower ? C.red : C.green,
                left: slower ? '50%' : `${50 - half}%`, width: `${Math.max(half, 0.6)}%` }} />
              <span style={{ position: 'absolute', top: 2, fontSize: 11, fontWeight: 700, color: C.text,
                left: slower ? `calc(50% + ${half}% + 6px)` : undefined, right: slower ? undefined : `calc(50% + ${half}% + 6px)` }}>
                {slower ? '▲' : '▼'} ×{r}
              </span>
            </div>
          </div>
        );
      })}
      <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: 11, color: C.dim }}>
        <span><span style={{ color: C.green }}>▼</span> faster than documented</span>
        <span><span style={{ color: C.red }}>▲</span> slower than documented</span>
      </div>
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
const th: CSSProperties = { padding: '6px 10px', fontWeight: 600, fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.4 };
const tdc: CSSProperties = { padding: '6px 10px', textAlign: 'right', color: C.dim, fontFamily: 'var(--font-mono, monospace)', whiteSpace: 'nowrap' };
const select: CSSProperties = { background: C.input, color: C.text, border: `1px solid ${C.border}`, borderRadius: 6, padding: '4px 8px', fontSize: 12 };
