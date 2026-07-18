import { useEffect, useState } from 'react';
import { MindStyle, StatTile, UtilityBadge, Spark, Bars, UsageMeter } from './mindUi';

/** Pallium dashboard — performance, trust, and growth for the
 *  corvus-mind tenant. Data: GET /metrics/mind + /metrics/mind/trust. */

function resetsIn(iso: string | null): string {
  if (!iso) return '—';
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return 'now';
  const h = Math.floor(ms / 3_600_000), min = Math.round((ms % 3_600_000) / 60_000);
  return h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : h > 0 ? `${h}h ${min}m` : `${min}m`;
}

function compactNumber(value: number | null | undefined): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
}

async function fetchJson<T>(url: string, attempts = 3): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      const response = await fetch(url);
      const body = await response.text();
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      if (!body.trim()) throw new Error('empty response');
      return JSON.parse(body) as T;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await new Promise(resolve => setTimeout(resolve, attempt * 500));
    }
  }
  throw lastError;
}

export default function MindMetricsPage() {
  const [m, setM] = useState<any>(null);
  const [trust, setTrust] = useState<any[]>([]);
  const [usage, setUsage] = useState<any>(null);
  const [codexUsage, setCodexUsage] = useState<any>(null);
  const [error, setError] = useState('');

  const load = () => {
    setError('');
    fetchJson<any>('/metrics/mind').then(setM).catch(e => setError(String(e)));
    fetchJson<any>('/metrics/mind/trust').then(d => setTrust(d.lessons ?? [])).catch(() => {});
  };
  useEffect(load, []);

  // Subscription gauges are live: poll every 60s (server caches upstream calls).
  useEffect(() => {
    const poll = () => fetchJson<any>('/metrics/mind/subscription')
      .then(setUsage).catch(() => setUsage(null));
    poll();
    const id = setInterval(poll, 60_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const poll = () => fetchJson<any>('/metrics/mind/subscription/codex')
      .then(setCodexUsage).catch(() => setCodexUsage(null));
    poll();
    const id = setInterval(poll, 60_000);
    return () => clearInterval(id);
  }, []);

  if (error) return <div className="error-msg">Pallium metrics unavailable: {error}</div>;
  if (!m) return <div className="detail-empty">Loading Pallium metrics…</div>;

  const { recall, lessons, growth, injections, distiller, janitors, compiler } = m;
  const modelUsage = m.model_usage ?? {};
  const equivalent = (modelUsage.equivalent_cost_usd ?? distiller.cost_usd ?? 0).toFixed(2);
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
        <StatTile value={`$${equivalent}`} label="compute equivalent"
          sub={`${modelUsage.tracked_calls ?? 0} model calls ledgered`} />
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
          <h3>Claude subscription — live</h3>
          {!usage ? <div className="mm-empty">loading…</div>
            : !usage.available ? <div className="mm-empty">unavailable: {usage.reason}</div>
            : <>
                {usage.gauges.map((g: any) => (
                  <UsageMeter key={g.kind} label={g.label} percent={g.percent}
                    severity={g.severity} sub={`resets in ${resetsIn(g.resets_at)}`} />
                ))}
                <div className="mm-sub" style={{ marginTop: 6 }}>
                  as of {usage.fetched_at}{usage.stale ? ' (stale)' : ''} · refreshes every 60s
                </div>
              </>}
        </div>

        <div className="mm-card">
          <h3>Codex subscription — live</h3>
          {!codexUsage ? <div className="mm-empty">loading…</div>
            : !codexUsage.available ? <div className="mm-empty">unavailable: {codexUsage.reason}</div>
            : <>
                {codexUsage.gauges.map((g: any) => (
                  <UsageMeter key={g.kind} label={g.label} percent={g.percent}
                    severity={g.severity} sub={`resets in ${resetsIn(g.resets_at)}`} />
                ))}
                <table className="mm-table"><tbody>
                  <tr><td>lifetime tokens</td><td className="num">{compactNumber(codexUsage.summary.lifetimeTokens)}</td></tr>
                  <tr><td>peak daily tokens</td><td className="num">{compactNumber(codexUsage.summary.peakDailyTokens)}</td></tr>
                  <tr><td>current streak</td><td className="num">{codexUsage.summary.currentStreakDays ?? '—'}d</td></tr>
                </tbody></table>
                <div className="mm-sub" style={{ marginTop: 6 }}>
                  {codexUsage.plan_type ?? 'unknown plan'} · {codexUsage.source} · as of {codexUsage.fetched_at}
                  {codexUsage.stale ? ' (stale)' : ''}
                </div>
              </>}
        </div>

        <div className="mm-card">
          <h3>Recall pipeline — latest {recall.performance_window ?? recall.total} mean stage timings</h3>
          <Bars rows={stageRows} unit=" ms" />
          <h4>By source</h4>
          <Bars color="var(--mm-violet)" rows={Object.entries(recall.by_source as Record<string, number>)
            .sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value }))} />
        </div>

        <div className="mm-card">
          <h3>Model economics</h3>
          <div className="mm-sub">API-list-price equivalent, not subscription cash charged</div>
          <table className="mm-table"><tbody>
            <tr><td>compute equivalent</td><td className="num">${equivalent}</td></tr>
            <tr><td>known actual cash</td><td className="num">
              {modelUsage.actual_cash_complete ? `$${(modelUsage.known_actual_cash_usd ?? 0).toFixed(2)}` : 'incomplete'}
            </td></tr>
            <tr><td>historical distillation</td><td className="num">
              ${(modelUsage.historical_distillation_equivalent_usd ?? distiller.cost_usd ?? 0).toFixed(2)}
            </td></tr>
          </tbody></table>
          <h4>Ledgered calls by model</h4>
          {(modelUsage.by_model ?? []).length === 0
            ? <div className="mm-empty">ledger starts with the next model call</div>
            : <Bars color="var(--mm-aqua)" rows={modelUsage.by_model
                .map((x: any) => ({ label: `${x.label} · ${x.tokens} tok`, value: x.calls }))} unit=" calls" />}
          <h4>By harness</h4>
          {(modelUsage.by_harness ?? []).length === 0
            ? <div className="mm-empty">no model calls ledgered yet</div>
            : <Bars color="var(--mm-violet)" rows={modelUsage.by_harness
                .map((x: any) => ({ label: x.label, value: x.calls }))} unit=" calls" />}
          <div className="mm-sub" style={{ marginTop: 8 }}>{modelUsage.coverage_note}</div>
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
