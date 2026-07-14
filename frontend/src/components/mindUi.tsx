/** Shared visual kit for the Pallium pages.
 *  Palette: validated dark-mode steps (dataviz reference instance) —
 *  series blue #3987e5, aqua #199e70, violet #9085e9; status good
 *  #0ca30c / warning #fab219 / serious #ec835a; ink #fff / #c3c2b7. */

export const MIND_CSS = `
.mm-root { padding: 1.1rem 1.4rem; overflow-y: auto; height: 100%;
  --mm-card: rgba(255,255,255,0.03); --mm-border: #383835;
  --mm-ink: #ffffff; --mm-ink2: #c3c2b7; --mm-ink3: #8a897f;
  --mm-blue: #3987e5; --mm-aqua: #199e70; --mm-violet: #9085e9;
  --mm-good: #0ca30c; --mm-warn: #fab219; --mm-serious: #ec835a;
  color: var(--mm-ink); }
.mm-root h2 { font-weight: 600; letter-spacing: -0.01em; margin: 0; }
.mm-sub { color: var(--mm-ink3); font-size: 0.78rem; }
.mm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 0.9rem; margin-top: 1rem; }
.mm-card { background: var(--mm-card); border: 1px solid var(--mm-border); border-radius: 10px; padding: 0.9rem 1rem; min-width: 0; overflow: hidden; }
.mm-wide { grid-column: 1 / -1; }
.mm-tablewrap { overflow-x: auto; }
.mm-card h3 { margin: 0 0 0.6rem; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--mm-ink2); }
.mm-card h4 { margin: 0.8rem 0 0.3rem; font-size: 0.72rem; font-weight: 600; color: var(--mm-ink3); text-transform: uppercase; letter-spacing: 0.06em; }
.mm-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.9rem; margin-top: 1rem; }
.mm-tile { background: var(--mm-card); border: 1px solid var(--mm-border); border-radius: 10px; padding: 0.8rem 1rem; }
.mm-tile .v { font-family: 'JetBrains Mono', monospace; font-size: 1.7rem; font-weight: 500; line-height: 1.15; }
.mm-tile .k { font-size: 0.72rem; color: var(--mm-ink3); text-transform: uppercase; letter-spacing: 0.07em; margin-top: 2px; }
.mm-tile .s { font-size: 0.72rem; color: var(--mm-ink2); font-family: 'JetBrains Mono', monospace; }
.mm-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.mm-table td, .mm-table th { padding: 5px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); text-align: left; vertical-align: middle; }
.mm-table th { color: var(--mm-ink3); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.mm-table td.num { font-family: 'JetBrains Mono', monospace; text-align: right; }
.mm-key { color: var(--mm-ink2); }
.mm-bar-row { display: flex; align-items: center; gap: 8px; margin: 3px 0; font-size: 0.78rem; }
.mm-bar-label { flex: 0 0 130px; color: var(--mm-ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mm-bar-track { flex: 1; height: 8px; }
.mm-bar-fill { height: 8px; border-radius: 0 4px 4px 0; background: var(--mm-blue); min-width: 2px; }
.mm-bar-val { flex: 0 0 62px; text-align: right; font-family: 'JetBrains Mono', monospace; color: var(--mm-ink2); }
.mm-badge { display: inline-block; font-family: 'JetBrains Mono', monospace; font-size: 0.75rem; padding: 1px 7px; border-radius: 9px; border: 1px solid var(--mm-border); }
.mm-chip { display: inline-block; font-size: 0.68rem; padding: 1px 7px; border-radius: 8px; background: rgba(144,133,233,0.14); color: var(--mm-violet); }
.mm-empty { color: var(--mm-ink3); font-size: 0.8rem; padding: 0.4rem 0; }
.mm-pre { font-family: 'JetBrains Mono', monospace; font-size: 0.74rem; white-space: pre-wrap; background: rgba(0,0,0,0.25); border: 1px solid var(--mm-border); border-radius: 8px; padding: 0.7rem; max-height: 420px; overflow: auto; color: var(--mm-ink2); }
`;

export function MindStyle() {
  return <style>{MIND_CSS}</style>;
}

export function StatTile({ value, label, sub }: { value: string | number; label: string; sub?: string }) {
  return (
    <div className="mm-tile">
      <div className="v">{value}</div>
      <div className="k">{label}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}

/** Trust badge: status colors carry state, never identity — always paired
 *  with the numeric value so state is never color-alone. */
export function UtilityBadge({ u }: { u: number }) {
  const color = u >= 0.6 ? 'var(--mm-good)' : u < 0.45 ? 'var(--mm-serious)' : 'var(--mm-ink2)';
  const word = u >= 0.6 ? 'trusted' : u < 0.45 ? 'doubted' : 'neutral';
  return <span className="mm-badge" style={{ color }} title={`utility ${u} (${word})`}>{u.toFixed(2)} {word}</span>;
}

/** Single-series sparkline on a fixed utility domain [0.3, 0.95] so
 *  trajectories are comparable across lessons. 2px line, recessive
 *  midline, endpoint dot. */
export function Spark({ points, w = 120, h = 26 }: { points: number[]; w?: number; h?: number }) {
  if (points.length === 0) return null;
  const lo = 0.3, hi = 0.95;
  const y = (u: number) => h - 3 - ((Math.min(hi, Math.max(lo, u)) - lo) / (hi - lo)) * (h - 6);
  const x = (i: number) => points.length === 1 ? w / 2 : 3 + (i / (points.length - 1)) * (w - 6);
  const path = points.map((u, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(u).toFixed(1)}`).join(' ');
  const baseline = y(0.5);
  return (
    <svg width={w} height={h} role="img" aria-label={`utility trajectory, now ${points[points.length - 1]}`}>
      <line x1={0} x2={w} y1={baseline} y2={baseline} stroke="#383835" strokeWidth={1} />
      <path d={path} fill="none" stroke="var(--mm-blue)" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={x(points.length - 1)} cy={y(points[points.length - 1])} r={3} fill="var(--mm-blue)" stroke="#1a1a19" strokeWidth={2} />
    </svg>
  );
}

/** Horizontal magnitude bars, one hue (sequential job), value labels in ink. */
export function Bars({ rows, color = 'var(--mm-blue)', unit = '' }:
  { rows: { label: string; value: number }[]; color?: string; unit?: string }) {
  const max = Math.max(...rows.map(r => r.value), 1);
  return (
    <div>
      {rows.map(r => (
        <div className="mm-bar-row" key={r.label} title={`${r.label}: ${r.value}${unit}`}>
          <span className="mm-bar-label">{r.label}</span>
          <span className="mm-bar-track"><span className="mm-bar-fill" style={{ width: `${(r.value / max) * 100}%`, background: color }} /></span>
          <span className="mm-bar-val">{r.value}{unit}</span>
        </div>
      ))}
    </div>
  );
}
