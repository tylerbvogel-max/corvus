import { useEffect, useState } from 'react';
import { MindStyle, StatTile, UtilityBadge, Spark, Bars } from './mindUi';

/** Pallium dashboard — performance, trust, and growth for the
 *  corvus-mind tenant. Data: GET /metrics/mind + /metrics/mind/trust. */

export default function MindMetricsPage() {
  const [m, setM] = useState<any>(null);
  const [trust, setTrust] = useState<any[]>([]);
  const [error, setError] = useState('');

  const load = () => {
    fetch('/metrics/mind').then(r => r.json()).then(setM).catch(e => setError(String(e)));
    fetch('/metrics/mind/trust').then(r => r.json()).then(d => setTrust(d.lessons ?? [])).catch(() => {});
  };
  useEffect(load, []);

  if (error) return <div className="error-msg">Pallium metrics unavailable: {error}</div>;
  if (!m) return <div className="detail-empty">Loading Pallium metrics…</div>;

  const { recall, lessons, growth, injections, distiller, janitors, compiler } = m;
  const spend = (distiller.cost_usd ?? 0).toFixed(2);
  const stageRows = Object.entries(recall.stage_mean_ms as Record<string, number>)
    .filter(([, v]) => v > 0.05).sort((a, b) => b[1] - a[1])
    .map(([label, value]) => ({ label, value: Math.round(value * 10) / 10 }));

  return (
    <div className="mm-root">
      <MindStyle />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Pallium</h2>
        <span className="mm-sub">{m.generated_at} · <a onClick={load} style={{ cursor: 'pointer' }}>refresh</a></span>
      </div>

      <div className="mm-tiles">
        <StatTile value={lessons.active} label="active lessons" sub={`${lessons.reinforced} reinforced`} />
        <StatTile value={recall.total} label="recalls served" sub={`p50 ${recall.latency_ms.p50 ?? '—'} ms`} />
        <StatTile value={injections.events} label="injections" sub={`${injections.distinct_lessons_injected} distinct lessons`} />
        <StatTile value={compiler.skills.length} label="skills compiled" />
        <StatTile value={distiller.logs_awaiting} label="logs awaiting distill" sub={`${distiller.sessions_distilled} done`} />
        <StatTile value={`$${spend}`} label="llm spend" sub="recall & injection: $0.00" />
      </div>

      <div className="mm-grid">
        <div className="mm-card mm-wide">
          <h3>Trust — utility trajectories</h3>
          <div className="mm-tablewrap">
          <table className="mm-table"><thead>
            <tr><th>lesson</th><th>scope</th><th>trajectory</th><th style={{ textAlign: 'right' }}>recalls</th><th>trust</th></tr>
          </thead><tbody>
            {trust.slice(0, 14).map(t => (
              <tr key={t.id}>
                <td className="mm-key" style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={t.label}>{t.label}</td>
                <td><span className="mm-chip">{t.scope ?? '—'}</span></td>
                <td><Spark points={t.points.map((p: any) => p.u)} /></td>
                <td className="num">{t.invocations}</td>
                <td><UtilityBadge u={t.utility} /></td>
              </tr>
            ))}
          </tbody></table>
          </div>
          {trust.length > 14 && <div className="mm-sub" style={{ marginTop: 6 }}>+{trust.length - 14} more lessons</div>}
        </div>

        <div className="mm-card">
          <h3>Recall pipeline — mean stage timings</h3>
          <Bars rows={stageRows} unit=" ms" />
          <h4>By source</h4>
          <Bars color="var(--mm-violet)" rows={Object.entries(recall.by_source as Record<string, number>)
            .sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} />
        </div>

        <div className="mm-card">
          <h3>Growth</h3>
          <h4>Lessons created / day</h4>
          <Bars color="var(--mm-aqua)" rows={[...growth.lessons_created_daily].reverse()
            .map((d: any) => ({ label: d.date, value: d.count }))} />
          <h4>Recalls served / day</h4>
          <Bars rows={[...growth.recalls_daily].reverse()
            .map((d: any) => ({ label: d.date, value: d.count }))} />
          <h4>Corpus by scope</h4>
          <Bars color="var(--mm-violet)" rows={Object.entries(lessons.by_scope as Record<string, number>)
            .sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} />
        </div>

        <div className="mm-card">
          <h3>Distiller funnel</h3>
          <Bars rows={[
            { label: 'sessions distilled', value: distiller.sessions_distilled },
            { label: 'candidates', value: distiller.candidates },
            { label: 'saved', value: distiller.saved },
            { label: 'duplicates skipped', value: distiller.duplicate },
            { label: 'usage skipped', value: distiller.usage_skipped },
            { label: 'flagged dropped', value: distiller.flagged },
          ]} />
          <h4>Janitor & compiler actions</h4>
          {Object.keys(janitors).length === 0
            ? <div className="mm-empty">no actions yet</div>
            : <Bars color="var(--mm-aqua)" rows={Object.entries(janitors as Record<string, number>)
                .map(([label, value]) => ({ label, value }))} />}
        </div>

        <div className="mm-card">
          <h3>Most injected</h3>
          {injections.top_injected.length === 0
            ? <div className="mm-empty">nothing injected yet</div>
            : <Bars color="var(--mm-violet)" rows={injections.top_injected
                .map((t: any) => ({ label: t.label, value: t.count }))} />}
          {lessons.zombie_candidates.length > 0 && <>
            <h4>Zombies — recalled, never reinforced</h4>
            <table className="mm-table"><tbody>
              {lessons.zombie_candidates.map((z: any) => (
                <tr key={z.id}><td className="mm-key">#{z.id} {z.label}</td><td className="num">{z.invocations}</td></tr>
              ))}
            </tbody></table>
          </>}
        </div>
      </div>
    </div>
  );
}
