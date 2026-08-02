import { useState, useEffect } from 'react';
import { fetchLearningAnalytics } from '../api/knowledge_graph';
import type { LearningAnalytics, LearningEventOut } from '../types';

export default function SynapticLearningPage() {
  const [data, setData] = useState<LearningAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState<'all' | 'reward' | 'penalty'>('all');

  useEffect(() => {
    fetchLearningAnalytics()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ padding: 24, color: 'var(--text-dim)' }}>Loading learning analytics...</div>;
  if (error) return <div style={{ padding: 24, color: '#ef4444' }}>Error: {error}</div>;
  if (!data) return <div style={{ padding: 24, color: 'var(--text-dim)' }}>No data available.</div>;

  const filtered = filter === 'all'
    ? data.recent_events
    : data.recent_events.filter(e => e.event_type === filter);

  // Group by neuron for trend summary
  const neuronMap = new Map<number, { label: string; events: LearningEventOut[] }>();
  for (const e of data.recent_events) {
    const existing = neuronMap.get(e.neuron_id);
    if (existing) {
      existing.events.push(e);
    } else {
      neuronMap.set(e.neuron_id, { label: e.neuron_label ?? `#${e.neuron_id}`, events: [e] });
    }
  }

  const topMovers = [...neuronMap.entries()]
    .map(([id, { label, events }]) => {
      const totalDelta = events.reduce((sum, e) => sum + e.effective_delta, 0);
      const latest = events[0];
      return { id, label, totalDelta, eventCount: events.length, latestUtility: latest.new_avg_utility };
    })
    .sort((a, b) => Math.abs(b.totalDelta) - Math.abs(a.totalDelta))
    .slice(0, 20);

  return (
    <div style={{ padding: '24px 32px', maxWidth: 1200 }}>
      <h2 style={{ color: 'var(--text)', fontSize: '1.2rem', fontWeight: 600, marginBottom: 4 }}>Synaptic Learning</h2>
      <p style={{ color: 'var(--text-dim)', fontSize: '0.85rem', marginBottom: 24 }}>
        Automatic weight adjustments from eval outcomes. Neurons are rewarded when neuron-enriched responses win, and gently penalized when they lose.
      </p>

      {/* Summary Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 12, marginBottom: 24 }}>
        <StatCard label="Total Events" value={data.total_events} />
        <StatCard label="Wins" value={data.total_wins} color="#22c55e" />
        <StatCard label="Losses" value={data.total_losses} color="#ef4444" />
        <StatCard label="Avg Reward" value={`+${data.avg_reward.toFixed(5)}`} color="#22c55e" />
        <StatCard label="Avg Penalty" value={`-${data.avg_penalty.toFixed(5)}`} color="#ef4444" />
      </div>

      {/* Top Movers */}
      {topMovers.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <h3 style={{ color: 'var(--text)', fontSize: '0.95rem', fontWeight: 600, marginBottom: 10 }}>Top Movers</h3>
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ background: 'var(--bg-input)' }}>
                  <th style={{ textAlign: 'left', padding: '10px 14px', color: 'var(--text-dim)', fontWeight: 600 }}>Neuron</th>
                  <th style={{ textAlign: 'right', padding: '10px 14px', color: 'var(--text-dim)', fontWeight: 600 }}>Events</th>
                  <th style={{ textAlign: 'right', padding: '10px 14px', color: 'var(--text-dim)', fontWeight: 600 }}>Total Delta</th>
                  <th style={{ textAlign: 'right', padding: '10px 14px', color: 'var(--text-dim)', fontWeight: 600 }}>Current Utility</th>
                </tr>
              </thead>
              <tbody>
                {topMovers.map((m, i) => (
                  <tr key={m.id} style={{ background: i % 2 === 0 ? 'var(--bg-card)' : 'var(--bg-input)' }}>
                    <td style={{ padding: '8px 14px', color: 'var(--text)' }}>{m.label}</td>
                    <td style={{ padding: '8px 14px', color: 'var(--text-dim)', textAlign: 'right' }}>{m.eventCount}</td>
                    <td style={{ padding: '8px 14px', textAlign: 'right', color: m.totalDelta >= 0 ? '#22c55e' : '#ef4444', fontFamily: 'var(--font-mono, monospace)' }}>
                      {m.totalDelta >= 0 ? '+' : ''}{m.totalDelta.toFixed(5)}
                    </td>
                    <td style={{ padding: '8px 14px', textAlign: 'right', color: 'var(--text)', fontFamily: 'var(--font-mono, monospace)' }}>
                      {m.latestUtility.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Event Log */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
          <h3 style={{ color: 'var(--text)', fontSize: '0.95rem', fontWeight: 600, margin: 0 }}>Event Log</h3>
          <div style={{ display: 'flex', gap: 4 }}>
            {(['all', 'reward', 'penalty'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: '3px 10px', fontSize: '0.7rem', borderRadius: 4, cursor: 'pointer',
                  background: filter === f ? 'var(--accent)' : 'var(--bg-input)',
                  color: filter === f ? '#fff' : 'var(--text-dim)',
                  border: '1px solid var(--border)',
                }}
              >
                {f}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div style={{ padding: 20, color: 'var(--text-dim)', fontSize: '0.85rem' }}>No learning events yet. Run an eval on a query with neuron + raw slots to generate data.</div>
        ) : (
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', maxHeight: 500, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem' }}>
              <thead>
                <tr style={{ background: 'var(--bg-input)', position: 'sticky', top: 0 }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px', color: 'var(--text-dim)' }}>Query</th>
                  <th style={{ textAlign: 'left', padding: '8px 12px', color: 'var(--text-dim)' }}>Neuron</th>
                  <th style={{ textAlign: 'center', padding: '8px 12px', color: 'var(--text-dim)' }}>Type</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)' }}>Old</th>
                  <th style={{ textAlign: 'center', padding: '8px 12px', color: 'var(--text-dim)' }}></th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)' }}>New</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)' }}>Delta</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)' }}>Score</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((e, i) => (
                  <tr key={e.id} style={{ background: i % 2 === 0 ? 'var(--bg-card)' : 'var(--bg-input)' }}>
                    <td style={{ padding: '6px 12px', color: 'var(--text-dim)' }}>#{e.query_id}</td>
                    <td style={{ padding: '6px 12px', color: 'var(--text)', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.neuron_label ?? `#${e.neuron_id}`}
                    </td>
                    <td style={{ padding: '6px 12px', textAlign: 'center' }}>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: '0.65rem', fontWeight: 600,
                        background: e.event_type === 'reward' ? '#22c55e22' : '#ef444422',
                        color: e.event_type === 'reward' ? '#22c55e' : '#ef4444',
                      }}>
                        {e.event_type}
                      </span>
                    </td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text-dim)' }}>{e.old_avg_utility.toFixed(3)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'center', color: 'var(--text-dim)' }}>&rarr;</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text)' }}>{e.new_avg_utility.toFixed(3)}</td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: e.effective_delta >= 0 ? '#22c55e' : '#ef4444' }}>
                      {e.effective_delta >= 0 ? '+' : ''}{e.effective_delta.toFixed(5)}
                    </td>
                    <td style={{ padding: '6px 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--text-dim)' }}>{e.combined_score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: string | number; color?: string }) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8,
      padding: '14px 16px', textAlign: 'center',
    }}>
      <div style={{ fontSize: '1.3rem', fontWeight: 700, color: color ?? 'var(--text)', fontFamily: 'var(--font-mono, monospace)' }}>{value}</div>
      <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', marginTop: 2 }}>{label}</div>
    </div>
  );
}
