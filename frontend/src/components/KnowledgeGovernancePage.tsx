import { useState, useEffect } from 'react';
import {
  fetchStats,
  fetchSourceDocuments,
  fetchAuthoritySummary,
  fetchProvenanceStale,
  fetchLearningAnalytics,
  fetchRefinementHistory,
  fetchAuditLogSummary,
  fetchComplianceAudit,
} from '../api';
import type {
  SourceDocumentOut,
  AuthoritySummaryItem,
  StaleProvenanceNeuron,
  ComplianceAuditResponse,
  AuditLogSummary,
} from '../api';
import type { NeuronStats, LearningAnalytics, LearningEventOut, NeuronRefinementEntry } from '../types';

interface GovernanceData {
  stats: NeuronStats;
  sources: SourceDocumentOut[];
  authority: AuthoritySummaryItem[];
  stale: StaleProvenanceNeuron[];
  learning: LearningAnalytics;
  refinements: NeuronRefinementEntry[];
  auditSummary: AuditLogSummary;
  compliance: ComplianceAuditResponse;
}

const AUTHORITY_ORDER = ['informational', 'organizational', 'industry_practice', 'regulatory', 'binding_standard'];
const AUTHORITY_COLORS: Record<string, string> = {
  informational: '#94a3b8',
  organizational: '#60a5fa',
  industry_practice: '#a78bfa',
  regulatory: '#fb923c',
  binding_standard: '#22c55e',
};

function statusColor(status: string): string {
  if (status === 'active') return '#22c55e';
  if (status === 'superseded') return '#64748b';
  if (status === 'draft') return '#fb923c';
  if (status === 'withdrawn') return '#ef4444';
  return 'var(--text-dim)';
}

function formatDate(d: string | null): string {
  if (!d) return '\u2014';
  return d.split('T')[0];
}

export default function KnowledgeGovernancePage() {
  const [data, setData] = useState<GovernanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Show-all toggles for each capped table
  const [showAllSources, setShowAllSources] = useState(false);
  const [showAllMissing, setShowAllMissing] = useState(false);
  const [showAllStaleNeurons, setShowAllStaleNeurons] = useState(false);
  const [showAllLearning, setShowAllLearning] = useState(false);
  const [showAllRefinements, setShowAllRefinements] = useState(false);

  useEffect(() => {
    Promise.all([
      fetchStats(),
      fetchSourceDocuments(),
      fetchAuthoritySummary(),
      fetchProvenanceStale(),
      fetchLearningAnalytics(),
      fetchRefinementHistory(),
      fetchAuditLogSummary(),
      fetchComplianceAudit(),
    ])
      .then(([stats, sources, authority, stale, learning, refinements, auditSummary, compliance]) => {
        setData({ stats, sources, authority, stale, learning, refinements, auditSummary, compliance });
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;
  if (loading || !data) return <div className="loading">Loading governance data...</div>;

  const { stats, sources, authority, stale, learning, refinements, auditSummary, compliance } = data;
  const prov = compliance.provenance_audit;

  return (
    <div className="security-page">
      <h2>Knowledge Governance</h2>
      <p className="security-intro">
        Auditable provenance, authority, and learning transparency for the neuron knowledge graph.
        Every knowledge source, every learning event, and every change is tracked and inspectable.
      </p>

      {/* Section 1: Knowledge Inventory */}
      <div className="stat-cards" style={{ marginBottom: 24 }}>
        <div className="stat-card">
          <div className="card-value">{stats.total_neurons}</div>
          <div className="card-label">Total Neurons</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{sources.length}</div>
          <div className="card-label">Source Documents</div>
        </div>
        <div className="stat-card">
          <div className="card-value">{learning.total_events}</div>
          <div className="card-label">Learning Events</div>
        </div>
        <div className="stat-card">
          <div className="card-value" style={{ color: prov.missing_citations_count > 0 ? '#ef4444' : '#22c55e' }}>
            {prov.missing_citations_count}
          </div>
          <div className="card-label">Missing Citations</div>
        </div>
        <div className="stat-card">
          <div className="card-value" style={{ color: stale.length > 0 ? '#ef4444' : '#22c55e' }}>
            {stale.length}
          </div>
          <div className="card-label">Stale Sources</div>
        </div>
      </div>

      {/* Section 2: Authority & Provenance */}
      <section className="security-section">
        <h3>Authority &amp; Provenance</h3>
        <p className="security-section-desc">
          Distribution of knowledge sources by authority level and type. Higher authority levels carry more regulatory weight.
        </p>

        {/* Authority Level Distribution */}
        {authority.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>Authority Level Distribution</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {AUTHORITY_ORDER.map(level => {
                const item = authority.find(a => a.authority_level === level);
                if (!item) return null;
                return (
                  <span key={level} style={{
                    padding: '4px 10px', borderRadius: 12, fontSize: '0.75rem', fontWeight: 600,
                    background: (AUTHORITY_COLORS[level] ?? '#94a3b8') + '22',
                    color: AUTHORITY_COLORS[level] ?? '#94a3b8',
                    border: `1px solid ${(AUTHORITY_COLORS[level] ?? '#94a3b8')}44`,
                  }}>
                    {level.replace(/_/g, ' ')}: {item.count}
                  </span>
                );
              })}
              {/* Show any levels not in the predefined order */}
              {authority.filter(a => !AUTHORITY_ORDER.includes(a.authority_level ?? '')).map(a => (
                <span key={a.authority_level ?? 'null'} style={{
                  padding: '4px 10px', borderRadius: 12, fontSize: '0.75rem', fontWeight: 600,
                  background: '#94a3b822', color: '#94a3b8', border: '1px solid #94a3b844',
                }}>
                  {a.authority_level ?? 'unclassified'}: {a.count}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Source Type Distribution */}
        {Object.keys(prov.source_type_distribution).length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>Source Type Distribution</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {Object.entries(prov.source_type_distribution).map(([type, count]) => (
                <span key={type} style={{
                  padding: '4px 10px', borderRadius: 12, fontSize: '0.75rem', fontWeight: 600,
                  background: '#60a5fa22', color: '#60a5fa', border: '1px solid #60a5fa44',
                }}>
                  {type}: {count}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Source Documents Table */}
        {sources.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              Source Documents ({sources.length})
            </div>
            <table className="about-table" style={{ fontSize: '0.78rem' }}>
              <thead>
                <tr>
                  <th>Canonical ID</th><th>Family</th><th>Status</th><th>Authority</th><th>Issuing Body</th><th>Effective Date</th>
                </tr>
              </thead>
              <tbody>
                {(showAllSources ? sources : sources.slice(0, 30)).map(s => (
                  <tr key={s.id}>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.72rem' }}>{s.canonical_id}</td>
                    <td>{s.family}</td>
                    <td style={{ color: statusColor(s.status), fontWeight: 600 }}>{s.status}</td>
                    <td style={{ color: AUTHORITY_COLORS[s.authority_level ?? ''] ?? 'var(--text-dim)' }}>
                      {s.authority_level?.replace(/_/g, ' ') ?? '\u2014'}
                    </td>
                    <td>{s.issuing_body ?? '\u2014'}</td>
                    <td>{formatDate(s.effective_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {sources.length > 30 && (
              <button onClick={() => setShowAllSources(!showAllSources)} style={toggleStyle}>
                {showAllSources ? 'Show less' : `Show all (${sources.length})`}
              </button>
            )}
          </div>
        )}

        {/* Missing Citations */}
        {prov.missing_citations.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#ef4444', marginBottom: 8 }}>
              Missing Citations ({prov.missing_citations_count})
            </div>
            <table className="about-table" style={{ fontSize: '0.78rem' }}>
              <thead>
                <tr><th>Neuron ID</th><th>Label</th><th>Department</th><th>Source Type</th></tr>
              </thead>
              <tbody>
                {(showAllMissing ? prov.missing_citations : prov.missing_citations.slice(0, 20)).map((mc, i) => (
                  <tr key={i}>
                    <td>#{mc.neuron_id}</td>
                    <td>{mc.label}</td>
                    <td>{mc.department ?? '\u2014'}</td>
                    <td>{mc.source_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {prov.missing_citations.length > 20 && (
              <button onClick={() => setShowAllMissing(!showAllMissing)} style={toggleStyle}>
                {showAllMissing ? 'Show less' : `Show all (${prov.missing_citations.length})`}
              </button>
            )}
          </div>
        )}

        {/* Stale Neurons (from compliance audit) */}
        {prov.stale_neurons.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#fb923c', marginBottom: 8 }}>
              Stale Neurons ({prov.stale_neurons_count})
            </div>
            <table className="about-table" style={{ fontSize: '0.78rem' }}>
              <thead>
                <tr><th>Neuron ID</th><th>Label</th><th>Department</th><th>Last Verified</th><th>Days Since</th></tr>
              </thead>
              <tbody>
                {(showAllStaleNeurons ? prov.stale_neurons : prov.stale_neurons.slice(0, 20)).map((sn, i) => (
                  <tr key={i}>
                    <td>#{sn.neuron_id}</td>
                    <td>{sn.label}</td>
                    <td>{sn.department ?? '\u2014'}</td>
                    <td>{formatDate(sn.last_verified)}</td>
                    <td style={{ color: sn.days_since_verified > 365 ? '#ef4444' : 'var(--text)', fontWeight: sn.days_since_verified > 365 ? 600 : 400 }}>
                      {sn.days_since_verified}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {prov.stale_neurons.length > 20 && (
              <button onClick={() => setShowAllStaleNeurons(!showAllStaleNeurons)} style={toggleStyle}>
                {showAllStaleNeurons ? 'Show less' : `Show all (${prov.stale_neurons.length})`}
              </button>
            )}
          </div>
        )}
      </section>

      {/* Section 3: Learning Transparency */}
      <section className="security-section">
        <h3>Learning Transparency</h3>
        <p className="security-section-desc">
          Every evaluation produces synaptic learning events. Neurons that contribute to winning responses are rewarded;
          losing responses produce penalties. The delta column shows the actual change applied, accounting for diminishing
          returns at high utility levels.
        </p>

        <div className="stat-cards" style={{ marginBottom: 16 }}>
          <div className="stat-card">
            <div className="card-value">{learning.total_events}</div>
            <div className="card-label">Total Events</div>
          </div>
          <div className="stat-card">
            <div className="card-value" style={{ color: '#22c55e' }}>{learning.total_wins}</div>
            <div className="card-label">Wins</div>
          </div>
          <div className="stat-card">
            <div className="card-value" style={{ color: '#ef4444' }}>{learning.total_losses}</div>
            <div className="card-label">Losses</div>
          </div>
          <div className="stat-card">
            <div className="card-value" style={{ color: '#22c55e' }}>+{learning.avg_reward.toFixed(5)}</div>
            <div className="card-label">Avg Reward</div>
          </div>
          <div className="stat-card">
            <div className="card-value" style={{ color: '#ef4444' }}>-{learning.avg_penalty.toFixed(5)}</div>
            <div className="card-label">Avg Penalty</div>
          </div>
        </div>

        {learning.recent_events.length > 0 && (
          <div>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              Recent Learning Events ({learning.recent_events.length})
            </div>
            <table className="about-table" style={{ fontSize: '0.75rem' }}>
              <thead>
                <tr>
                  <th>Date</th><th>Neuron</th><th>Type</th><th>Outcome</th>
                  <th>Old Utility</th><th></th><th>New Utility</th><th>Delta</th><th>Winner Mode</th>
                </tr>
              </thead>
              <tbody>
                {(showAllLearning ? learning.recent_events : learning.recent_events.slice(0, 20)).map((e: LearningEventOut) => (
                  <tr key={e.id}>
                    <td style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{formatDate(e.created_at)}</td>
                    <td style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.neuron_label ?? `#${e.neuron_id}`}
                    </td>
                    <td>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: '0.65rem', fontWeight: 600,
                        background: e.event_type === 'reward' ? '#22c55e22' : '#ef444422',
                        color: e.event_type === 'reward' ? '#22c55e' : '#ef4444',
                      }}>
                        {e.event_type}
                      </span>
                    </td>
                    <td>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: '0.65rem', fontWeight: 600,
                        background: e.outcome === 'win' ? '#22c55e11' : '#ef444411',
                        color: e.outcome === 'win' ? '#22c55e' : '#ef4444',
                      }}>
                        {e.outcome}
                      </span>
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', textAlign: 'right', color: 'var(--text-dim)' }}>
                      {e.old_avg_utility.toFixed(3)}
                    </td>
                    <td style={{ textAlign: 'center', color: 'var(--text-dim)' }}>&rarr;</td>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', textAlign: 'right' }}>
                      {e.new_avg_utility.toFixed(3)}
                    </td>
                    <td style={{
                      fontFamily: 'var(--font-mono, monospace)', textAlign: 'right',
                      color: e.effective_delta >= 0 ? '#22c55e' : '#ef4444',
                    }}>
                      {e.effective_delta >= 0 ? '+' : ''}{e.effective_delta.toFixed(5)}
                    </td>
                    <td style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{e.winner_mode ?? '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {learning.recent_events.length > 20 && (
              <button onClick={() => setShowAllLearning(!showAllLearning)} style={toggleStyle}>
                {showAllLearning ? 'Show less' : `Show all (${learning.recent_events.length})`}
              </button>
            )}
          </div>
        )}
      </section>

      {/* Section 4: Change History */}
      <section className="security-section">
        <h3>Change History</h3>
        <p className="security-section-desc">
          Recent neuron refinements and system audit activity. Every graph modification is logged with reason and provenance.
        </p>

        {/* Refinements */}
        {refinements.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>
              Recent Refinements ({refinements.length})
            </div>
            <table className="about-table" style={{ fontSize: '0.78rem' }}>
              <thead>
                <tr><th>Date</th><th>Neuron</th><th>Action</th><th>Field</th><th>Reason</th></tr>
              </thead>
              <tbody>
                {(showAllRefinements ? refinements : refinements.slice(0, 20)).map(r => (
                  <tr key={r.id}>
                    <td style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>{formatDate(r.created_at)}</td>
                    <td>{r.neuron_label ?? `#${r.neuron_id}`}</td>
                    <td>
                      <span style={{
                        padding: '1px 6px', borderRadius: 3, fontSize: '0.65rem', fontWeight: 600,
                        background: r.action === 'create' ? '#22c55e22' : '#60a5fa22',
                        color: r.action === 'create' ? '#22c55e' : '#60a5fa',
                      }}>
                        {r.action}
                      </span>
                    </td>
                    <td style={{ color: 'var(--text-dim)' }}>{r.field ?? '\u2014'}</td>
                    <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-dim)' }}>
                      {r.reason ?? '\u2014'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {refinements.length > 20 && (
              <button onClick={() => setShowAllRefinements(!showAllRefinements)} style={toggleStyle}>
                {showAllRefinements ? 'Show less' : `Show all (${refinements.length})`}
              </button>
            )}
          </div>
        )}

        {/* Audit Log Summary */}
        <div>
          <div style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text)', marginBottom: 8 }}>Audit Log Summary</div>
          <div className="stat-cards" style={{ marginBottom: 12 }}>
            <div className="stat-card">
              <div className="card-value">{auditSummary.total_records.toLocaleString()}</div>
              <div className="card-label">Total Records</div>
            </div>
            <div className="stat-card">
              <div className="card-value" style={{ color: auditSummary.error_count > 0 ? '#ef4444' : '#22c55e' }}>
                {auditSummary.error_count}
              </div>
              <div className="card-label">Errors</div>
            </div>
            <div className="stat-card">
              <div className="card-value" style={{ fontSize: '0.85rem' }}>
                {auditSummary.latest_entry ? formatDate(auditSummary.latest_entry) : '\u2014'}
              </div>
              <div className="card-label">Latest Entry</div>
            </div>
          </div>

          {auditSummary.top_endpoints.length > 0 && (
            <table className="about-table" style={{ fontSize: '0.78rem' }}>
              <thead>
                <tr><th>Top Endpoints</th><th>Count</th></tr>
              </thead>
              <tbody>
                {auditSummary.top_endpoints.map((ep, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '0.72rem' }}>{ep.endpoint}</td>
                    <td>{ep.count.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

    </div>
  );
}

const toggleStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: '#60a5fa',
  fontSize: '0.75rem',
  cursor: 'pointer',
  padding: '6px 0',
  textDecoration: 'underline',
};
