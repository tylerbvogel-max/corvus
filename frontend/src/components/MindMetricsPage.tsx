import { useEffect, useState } from 'react';

/** Memory-organ observability: the tailored Evaluate surface for a
 *  memory tenant (corvus-mind). Renders GET /metrics/mind — recall
 *  performance, injection coverage, distiller funnel, janitor/compiler
 *  activity, cost ledger, and corpus growth. */

interface MindMetrics {
  generated_at: string;
  recall: {
    total: number;
    by_source: Record<string, number>;
    latency_ms: { p50: number | null; p95: number | null; max: number | null };
    stage_mean_ms: Record<string, number>;
  };
  lessons: {
    active: number;
    absorbed_or_inactive: number;
    superseded: number;
    by_scope: Record<string, number>;
    avg_utility: number | null;
    reinforced: number;
    top_recalled: { id: number; label: string; invocations: number; utility: number }[];
    zombie_candidates: { id: number; label: string; invocations: number }[];
  };
  growth: {
    lessons_created_daily: { date: string; count: number }[];
    recalls_daily: { date: string; count: number }[];
  };
  injections: {
    events: number;
    sessions_with_injections: number;
    distinct_lessons_injected: number;
    top_injected: { label: string; count: number }[];
  };
  distiller: Record<string, number>;
  janitors: Record<string, number>;
  compiler: { skills: { name: string; scope: string; sources: number; compiled_at: string }[] };
  cost_ledger_usd: Record<string, number>;
}

function KV({ data }: { data: Record<string, number | string | null> }) {
  return (
    <table className="mind-kv">
      <tbody>
        {Object.entries(data).map(([k, v]) => (
          <tr key={k}><td className="mind-kv-key">{k}</td><td>{v ?? '—'}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mind-section">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export default function MindMetricsPage() {
  const [metrics, setMetrics] = useState<MindMetrics | null>(null);
  const [error, setError] = useState('');

  const load = () => {
    fetch('/metrics/mind')
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(setMetrics)
      .catch(e => setError(e.message));
  };
  useEffect(load, []);

  if (error) return <div className="error-msg">Memory metrics unavailable: {error}</div>;
  if (!metrics) return <div className="detail-empty">Loading memory metrics…</div>;

  const { recall, lessons, growth, injections, distiller, janitors, compiler } = metrics;

  return (
    <div className="mind-metrics" style={{ padding: '1rem 1.5rem', overflowY: 'auto', height: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2>Memory Organ</h2>
        <span style={{ opacity: 0.6, fontSize: '0.8rem' }}>
          generated {metrics.generated_at} · <a onClick={load} style={{ cursor: 'pointer' }}>refresh</a>
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
        <Section title="Recall performance">
          <KV data={{
            'recalls served': recall.total,
            'latency p50 (ms)': recall.latency_ms.p50,
            'latency p95 (ms)': recall.latency_ms.p95,
            ...Object.fromEntries(Object.entries(recall.by_source).map(([k, v]) => [`source: ${k}`, v])),
          }} />
          <h4>Mean stage timings (ms)</h4>
          <KV data={recall.stage_mean_ms} />
        </Section>

        <Section title="Lesson corpus">
          <KV data={{
            'active lessons': lessons.active,
            'absorbed / inactive': lessons.absorbed_or_inactive,
            'superseded (history kept)': lessons.superseded,
            'reinforced': lessons.reinforced,
            'avg utility': lessons.avg_utility,
            ...Object.fromEntries(Object.entries(lessons.by_scope).map(([k, v]) => [`scope: ${k}`, v])),
          }} />
        </Section>

        <Section title="Injection coverage">
          <KV data={{
            'injection events': injections.events,
            'sessions reached': injections.sessions_with_injections,
            'distinct lessons injected': injections.distinct_lessons_injected,
          }} />
          <h4>Most injected</h4>
          <table className="mind-kv"><tbody>
            {injections.top_injected.map(t => (
              <tr key={t.label}><td className="mind-kv-key">{t.label}</td><td>{t.count}</td></tr>
            ))}
          </tbody></table>
        </Section>

        <Section title="Distiller funnel">
          <KV data={distiller} />
        </Section>

        <Section title="Janitor activity">
          <KV data={Object.keys(janitors).length ? janitors : { '(no actions yet)': '' }} />
        </Section>

        <Section title="Compiled skills">
          <table className="mind-kv"><tbody>
            {compiler.skills.length === 0 && <tr><td>(none yet)</td></tr>}
            {compiler.skills.map(s => (
              <tr key={s.name}>
                <td className="mind-kv-key">{s.name}</td>
                <td>{s.scope} · {s.sources} sources · {s.compiled_at}</td>
              </tr>
            ))}
          </tbody></table>
          <h4>Cost ledger (USD)</h4>
          <KV data={metrics.cost_ledger_usd} />
        </Section>

        <Section title="Top recalled lessons">
          <table className="mind-kv"><tbody>
            {lessons.top_recalled.map(t => (
              <tr key={t.id}>
                <td className="mind-kv-key">#{t.id} {t.label}</td>
                <td>{t.invocations} recalls · utility {t.utility}</td>
              </tr>
            ))}
          </tbody></table>
          {lessons.zombie_candidates.length > 0 && <>
            <h4>Zombie candidates (recalled, never reinforced)</h4>
            <table className="mind-kv"><tbody>
              {lessons.zombie_candidates.map(t => (
                <tr key={t.id}><td className="mind-kv-key">#{t.id} {t.label}</td><td>{t.invocations}</td></tr>
              ))}
            </tbody></table>
          </>}
        </Section>

        <Section title="Growth (daily)">
          <h4>Lessons created</h4>
          <table className="mind-kv"><tbody>
            {growth.lessons_created_daily.map(d => (
              <tr key={d.date}><td className="mind-kv-key">{d.date}</td><td>{d.count}</td></tr>
            ))}
          </tbody></table>
          <h4>Recalls served</h4>
          <table className="mind-kv"><tbody>
            {growth.recalls_daily.map(d => (
              <tr key={d.date}><td className="mind-kv-key">{d.date}</td><td>{d.count}</td></tr>
            ))}
          </tbody></table>
        </Section>
      </div>
    </div>
  );
}
