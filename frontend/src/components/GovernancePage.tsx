import { useEffect, useState } from 'react';
import { fetchGovernanceDashboard } from '../api';
import type { GovernanceDashboardResponse } from '../api';
import { getTenantConfig } from '../config';

export default function GovernancePage() {
  const orgName = getTenantConfig()?.display_name ?? 'Organization';
  const [data, setData] = useState<GovernanceDashboardResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [hoveredKpi, setHoveredKpi] = useState<string | null>(null);

  useEffect(() => {
    fetchGovernanceDashboard()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (error) return <div className="error-msg">{error}</div>;
  if (loading || !data) return <div className="loading">Loading governance dashboard...</div>;

  const kpiCard = (label: string, value: string | number, target: string, met: boolean | null, tooltip?: string) => (
    <div style={{
      background: 'var(--bg-input)', borderRadius: 8, padding: '10px 14px',
      border: met === null ? '1px solid #334155' : met ? '1px solid #22c55e44' : '1px solid #fb923c44',
      position: 'relative', cursor: tooltip ? 'help' : undefined,
    }}
      onMouseEnter={() => tooltip && setHoveredKpi(label)}
      onMouseLeave={() => setHoveredKpi(null)}
    >
      <div style={{ fontSize: '1.1rem', fontWeight: 700, color: met === null ? '#c8d0dc' : met ? '#22c55e' : '#fb923c' }}>
        {value}
      </div>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: 2 }}>{label}</div>
      <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: 2 }}>Target: {target}</div>
      {tooltip && hoveredKpi === label && (
        <div style={{
          position: 'absolute', bottom: '100%', left: '50%', transform: 'translateX(-50%)',
          marginBottom: 6, padding: '8px 12px', borderRadius: 6,
          background: '#1e293b', border: '1px solid #334155', boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
          fontSize: '0.75rem', lineHeight: 1.4, color: '#cbd5e1',
          width: 260, zIndex: 50, pointerEvents: 'none',
        }}>
          {tooltip}
          <div style={{
            position: 'absolute', top: '100%', left: '50%', transform: 'translateX(-50%)',
            width: 0, height: 0, borderLeft: '6px solid transparent',
            borderRight: '6px solid transparent', borderTop: '6px solid #334155',
          }} />
        </div>
      )}
    </div>
  );

  return (
    <div className="security-page">
      <h2>Governance Dashboard</h2>
      <p className="security-intro">
        Live governance metrics, process documentation, risk register, and failure playbooks.
        All governance information is visible and auditable. Covers NIST AI RMF Govern/Manage functions,
        ISO 42001 clauses 5-10, and AIUC-1 domain E requirements.
      </p>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-4: AI Objectives & Measurement                               */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>AI Objectives & KPIs</h3>
        <p className="security-section-desc">
          Measurable objectives with defined targets, computed from live system data.
          Green = target met. Amber = below target.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 10, marginBottom: 16 }}>
          {kpiCard('Avg Eval Overall', data.kpis.avg_eval_overall?.toFixed(1) ?? 'N/A',
            '\u2265 3.5 / 5', data.kpis.avg_eval_overall ? data.kpis.avg_eval_overall >= 3.5 : null,
            'Average blind evaluation score across all queries. A separate LLM grades each response on accuracy, completeness, clarity, and faithfulness (1-5 scale). This is the composite average.')}
          {kpiCard('Avg Faithfulness', data.kpis.avg_faithfulness?.toFixed(1) ?? 'N/A',
            '\u2265 4.0 / 5', data.kpis.avg_faithfulness ? data.kpis.avg_faithfulness >= 4.0 : null,
            'How well responses stick to the facts in the neuron context, without hallucinating. Scored 1-5 by blind evaluation. Higher = less hallucination risk.')}
          {kpiCard('Avg User Rating', data.kpis.avg_user_rating?.toFixed(2) ?? 'N/A',
            '\u2265 0.60', data.kpis.avg_user_rating ? data.kpis.avg_user_rating >= 0.60 : null,
            'Average of thumbs-up (1.0) and thumbs-down (0.0) ratings from users. A score of 0.60 means 60% of rated queries were helpful.')}
          {kpiCard('Zero-Hit Rate', `${data.kpis.zero_hit_pct}%`,
            '\u2264 20%', data.kpis.zero_hit_pct <= 20,
            'Percentage of recent queries where no neurons were activated (the knowledge graph had nothing relevant). High zero-hit rate = knowledge gaps that need filling.')}
          {kpiCard('Run Cost / 1M', data.kpis.run_cost_per_1m !== null ? `$${data.kpis.run_cost_per_1m.toFixed(2)}` : 'N/A',
            '\u2264 $4.00 (80% vs Opus)', data.kpis.run_cost_per_1m !== null ? data.kpis.run_cost_per_1m <= 4.00 : null,
            'Production cost per 1M tokens using only Haiku/Sonnet models. This is what it costs to run the system day-to-day, excluding Opus benchmark queries used during training and evaluation.')}
          {kpiCard('Parity Index', data.kpis.parity_index !== null ? `${(data.kpis.parity_index * 100).toFixed(1)}%` : 'N/A',
            '\u2265 85% of Opus quality', data.kpis.parity_index !== null ? data.kpis.parity_index >= 0.85 : null,
            'Quality parity with Opus: avg neuron-assisted eval score divided by avg Opus eval score. 100% = identical quality. Measures how close the cheaper Haiku+neurons approach gets to Opus-level answers.')}
          {kpiCard('Value Score', data.kpis.value_score !== null ? data.kpis.value_score.toFixed(2) : 'N/A',
            '\u2265 2.0 (vs Opus 0.87)', data.kpis.value_score !== null ? data.kpis.value_score >= 2.0 : null,
            'Quality-adjusted cost ratio: (neuron_eval / 5) \u00F7 (run_cost / opus_cost). Measures how much quality you get per dollar relative to Opus. Opus baseline is ~0.87. Higher = better value for money.')}
          {kpiCard('Coverage CV', data.kpis.coverage_cv.toFixed(2),
            '\u2264 0.50', data.kpis.coverage_cv <= 0.50,
            'Coefficient of variation of neuron counts across departments. Measures how evenly knowledge is distributed. CV > 0.50 indicates significant imbalance that may bias responses toward over-represented departments.')}
          {kpiCard('Active Alerts', data.active_alerts,
            '0', data.active_alerts === 0,
            'Number of unacknowledged system alerts. Alerts are generated automatically when scoring drift is detected, quality drops below thresholds, or the circuit breaker trips.')}
          {kpiCard('Departments', data.totals.departments,
            '\u2265 8', data.totals.departments >= 8,
            'Number of distinct departments with active neurons in the knowledge graph. More departments = broader organizational knowledge coverage.')}
          {kpiCard('Total Neurons', data.totals.neurons.toLocaleString(),
            '\u2265 1,500', data.totals.neurons >= 1500,
            'Total active neurons in the knowledge graph across all layers (Department, Role, Task, System, Decision, Output). More neurons = deeper domain knowledge.')}
        </div>

        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr><th>Objective</th><th>Target</th><th>Current</th><th>Status</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>Query quality (blind eval overall)</td>
              <td>{'\u2265'} 3.5 / 5</td>
              <td>{data.kpis.avg_eval_overall?.toFixed(2) ?? 'No data'}</td>
              <td style={{ color: data.kpis.avg_eval_overall && data.kpis.avg_eval_overall >= 3.5 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.avg_eval_overall && data.kpis.avg_eval_overall >= 3.5 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Hallucination prevention (faithfulness)</td>
              <td>{'\u2265'} 4.0 / 5</td>
              <td>{data.kpis.avg_faithfulness?.toFixed(2) ?? 'No data'}</td>
              <td style={{ color: data.kpis.avg_faithfulness && data.kpis.avg_faithfulness >= 4.0 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.avg_faithfulness && data.kpis.avg_faithfulness >= 4.0 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>User satisfaction (rating)</td>
              <td>{'\u2265'} 0.60</td>
              <td>{data.kpis.avg_user_rating?.toFixed(3) ?? 'No data'}</td>
              <td style={{ color: data.kpis.avg_user_rating && data.kpis.avg_user_rating >= 0.60 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.avg_user_rating && data.kpis.avg_user_rating >= 0.60 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Knowledge coverage (zero-hit rate)</td>
              <td>{'\u2264'} 20%</td>
              <td>{data.kpis.zero_hit_pct}%</td>
              <td style={{ color: data.kpis.zero_hit_pct <= 20 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.zero_hit_pct <= 20 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Run cost (production, haiku/sonnet only)</td>
              <td>{'\u2264'} $4.00 / 1M tokens</td>
              <td>{data.kpis.run_cost_per_1m !== null ? `$${data.kpis.run_cost_per_1m.toFixed(2)}` : 'N/A'}
                {data.kpis.opus_cost_per_1m !== null && (
                  <span style={{ fontSize: '0.7rem', color: '#64748b', marginLeft: 6 }}>
                    (Opus: ${data.kpis.opus_cost_per_1m.toFixed(2)})
                  </span>
                )}
              </td>
              <td style={{ color: data.kpis.run_cost_per_1m !== null && data.kpis.run_cost_per_1m <= 4.00 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.run_cost_per_1m !== null && data.kpis.run_cost_per_1m <= 4.00 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Quality parity (neuron eval / opus eval)</td>
              <td>{'\u2265'} 85%</td>
              <td>{data.kpis.parity_index !== null ? `${(data.kpis.parity_index * 100).toFixed(1)}%` : 'No data'}
                {data.kpis.avg_neuron_eval !== null && data.kpis.avg_opus_eval !== null && (
                  <span style={{ fontSize: '0.7rem', color: '#64748b', marginLeft: 6 }}>
                    ({data.kpis.avg_neuron_eval.toFixed(2)} / {data.kpis.avg_opus_eval.toFixed(2)})
                  </span>
                )}
              </td>
              <td style={{ color: data.kpis.parity_index !== null && data.kpis.parity_index >= 0.85 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.parity_index !== null && data.kpis.parity_index >= 0.85 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Value score (quality-adjusted cost ratio)</td>
              <td>{'\u2265'} 2.0</td>
              <td>{data.kpis.value_score !== null ? data.kpis.value_score.toFixed(2) : 'No data'}
                {data.kpis.opus_cost_per_1m !== null && data.kpis.cost_per_1m_tokens !== null && (
                  <span style={{ fontSize: '0.7rem', color: '#64748b', marginLeft: 6 }}>
                    (Opus baseline: 0.87)
                  </span>
                )}
              </td>
              <td style={{ color: data.kpis.value_score !== null && data.kpis.value_score >= 2.0 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.value_score !== null && data.kpis.value_score >= 2.0 ? 'Met' : 'Below target'}
              </td>
            </tr>
            <tr>
              <td>Coverage fairness (department balance)</td>
              <td>{'\u2264'} 0.50 CV</td>
              <td>{data.kpis.coverage_cv.toFixed(2)}</td>
              <td style={{ color: data.kpis.coverage_cv <= 0.50 ? '#22c55e' : '#fb923c' }}>
                {data.kpis.coverage_cv <= 0.50 ? 'Met' : 'Imbalanced'}
              </td>
            </tr>
            <tr>
              <td>Evaluation coverage</td>
              <td>{'\u2265'} 50% of queries evaluated</td>
              <td>{data.totals.queries > 0 ? `${Math.round(data.totals.evaluations / data.totals.queries * 100)}%` : 'N/A'} ({data.totals.evaluations} / {data.totals.queries})</td>
              <td style={{ color: data.totals.queries > 0 && data.totals.evaluations / data.totals.queries >= 0.5 ? '#22c55e' : '#fb923c' }}>
                {data.totals.queries > 0 && data.totals.evaluations / data.totals.queries >= 0.5 ? 'Met' : 'Below target'}
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-1: Change Management Process                                 */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Change Management</h3>
        <p className="security-section-desc">
          All changes to neuron content are tracked through the refinement system. Each change records
          the action, affected field, old/new values, and reason. Human approval is required before any
          refinement is applied.
        </p>

        <div className="stat-cards" style={{ marginBottom: 12 }}>
          <div className="stat-card">
            <div className="card-value">{data.change_activity.refinements_30d}</div>
            <div className="card-label">Refinements (30d)</div>
          </div>
          <div className="stat-card">
            <div className="card-value">{data.change_activity.autopilot_runs_30d}</div>
            <div className="card-label">Retired Autopilot Runs (30d)</div>
          </div>
          <div className="stat-card">
            <div className="card-value">{data.totals.refinements}</div>
            <div className="card-label">Total Refinements</div>
          </div>
        </div>

        {data.change_activity.recent_changes.length > 0 && (
          <>
            <h4 style={{ margin: '12px 0 6px', fontSize: '0.85rem', color: 'var(--text-dim)' }}>Recent Changes</h4>
            <table className="about-table" style={{ fontSize: '0.8rem' }}>
              <thead>
                <tr><th>Date</th><th>Action</th><th>Field</th><th>Neuron</th><th>Reason</th></tr>
              </thead>
              <tbody>
                {data.change_activity.recent_changes.map(c => (
                  <tr key={c.id}>
                    <td style={{ color: '#c8d0dc', whiteSpace: 'nowrap' }}>{c.created_at?.split('T')[0] ?? '?'}</td>
                    <td><code>{c.action}</code></td>
                    <td>{c.field || '\u2014'}</td>
                    <td>#{c.neuron_id}</td>
                    <td style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.reason || '\u2014'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        <h4 style={{ margin: '16px 0 6px', fontSize: '0.85rem', color: 'var(--text-dim)' }}>Change Control Process</h4>
        <div className="security-items">
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>1. Proposal</strong> &mdash; Changes originate from explicit refinement and governed
              maintenance workflows. Models may propose updates, but cannot silently apply them.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>2. Review</strong> &mdash; All proposed changes are displayed in the UI with old/new value diffs.
              The system owner reviews each change individually before approval.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>3. Approval</strong> &mdash; Cherry-pick approval: each update and new neuron can be
              accepted or rejected independently. No bulk auto-apply.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>4. Audit Trail</strong> &mdash; Every applied change is logged in the neuron_refinements table
              with action, field, old_value, new_value, reason, timestamp, and originating query.
            </p>
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-2: Risk Register                                             */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Risk Register</h3>
        <p className="security-section-desc">
          Formal risk register with likelihood x impact scoring and treatment decisions.
          Derived from the failure mode analysis in risk-map.md.
        </p>

        <table className="about-table" style={{ fontSize: '0.78rem' }}>
          <thead>
            <tr><th>ID</th><th>Risk</th><th>L</th><th>I</th><th>Score</th><th>Treatment</th><th>Control</th></tr>
          </thead>
          <tbody>
            {[
              { id: 'R01', risk: 'LLM hallucination in response', l: 3, i: 4, treatment: 'Mitigate', control: 'Grounding check, neuron-scoped prompts, faithfulness eval' },
              { id: 'R02', risk: 'Scoring drift degrades quality', l: 2, i: 3, treatment: 'Mitigate', control: 'Z-score drift detection, circuit breaker, health check alerts' },
              { id: 'R03', risk: 'Prompt injection attack', l: 2, i: 4, treatment: 'Mitigate', control: 'Input guard with 16 regex patterns, 24-test adversarial suite' },
              { id: 'R04', risk: 'API model version change', l: 3, i: 3, treatment: 'Monitor', control: 'Model version tracking per query, version change alerts' },
              { id: 'R05', risk: 'Department coverage imbalance', l: 4, i: 2, treatment: 'Accept', control: 'Bias assessment with CV monitoring and explicit coverage review' },
              { id: 'R06', risk: 'Neuron content staleness', l: 3, i: 3, treatment: 'Mitigate', control: 'Provenance audit, last_verified tracking, novelty signal decay' },
              { id: 'R07', risk: 'PII in neuron content', l: 1, i: 4, treatment: 'Mitigate', control: 'Automated PII scan on all neuron fields, input guard PII detection' },
              { id: 'R08', risk: 'Cost overrun from model usage', l: 2, i: 2, treatment: 'Monitor', control: 'Per-query cost tracking, token budget limits, cost dashboard' },
              { id: 'R09', risk: 'Zero-hit queries (knowledge gap)', l: 3, i: 3, treatment: 'Mitigate', control: 'Zero-hit rate monitoring and evidence-gated ingestion' },
              { id: 'R10', risk: 'Unauthorized neuron modification', l: 1, i: 4, treatment: 'Mitigate', control: 'Human approval required for all refinements, audit trail' },
              { id: 'R11', risk: 'Dual-use/ITAR content exposure', l: 1, i: 5, treatment: 'Mitigate', control: 'Output risk tagging for dual_use category, domain-scoped graph' },
              { id: 'R12', risk: 'Vendor lock-in (Anthropic)', l: 3, i: 3, treatment: 'Accept', control: 'Model-agnostic architecture, CLI wrapper abstraction' },
              { id: 'R13', risk: 'Single point of failure (sole developer)', l: 4, i: 3, treatment: 'Accept', control: 'Comprehensive documentation, governance framework, system card' },
              { id: 'R14', risk: 'Undetected regression in scoring', l: 2, i: 3, treatment: 'Mitigate', control: 'Scoring baselines, drift detection, blind A/B evaluation' },
              { id: 'R15', risk: 'Data exfiltration via query', l: 1, i: 5, treatment: 'Mitigate', control: 'Input guard blocks exfiltration patterns, local-only data storage' },
            ].map(r => {
              const score = r.l * r.i;
              const color = score >= 12 ? '#ef4444' : score >= 6 ? '#fb923c' : '#22c55e';
              return (
                <tr key={r.id}>
                  <td><code>{r.id}</code></td>
                  <td>{r.risk}</td>
                  <td style={{ textAlign: 'center' }}>{r.l}</td>
                  <td style={{ textAlign: 'center' }}>{r.i}</td>
                  <td style={{ textAlign: 'center', fontWeight: 600, color }}>{score}</td>
                  <td><span style={{
                    fontSize: '0.7rem', padding: '1px 6px', borderRadius: 3,
                    background: r.treatment === 'Mitigate' ? '#3b82f622' : r.treatment === 'Monitor' ? '#fb923c22' : '#64748b22',
                    color: r.treatment === 'Mitigate' ? '#3b82f6' : r.treatment === 'Monitor' ? '#fb923c' : '#64748b',
                  }}>{r.treatment}</span></td>
                  <td style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>{r.control}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div style={{ fontSize: '0.7rem', color: '#64748b', marginTop: 6 }}>
          L = Likelihood (1-5), I = Impact (1-5), Score = L x I.
          Red {'\u2265'} 12, Amber {'\u2265'} 6, Green {'<'} 6.
          Treatment: Mitigate (active controls), Monitor (watch), Accept (acknowledged risk).
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-3: Document Registry                                         */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Document Registry</h3>
        <p className="security-section-desc">
          Controlled governance documents with version tracking and review schedule.
        </p>

        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr><th>Document</th><th>Location</th><th>Version</th><th>Status</th><th>Review Cycle</th></tr>
          </thead>
          <tbody>
            {[
              { doc: 'Governance Framework', loc: 'docs/governance.md', ver: '1.0', status: 'current', cycle: 'Quarterly' },
              { doc: 'Risk Map & Failure Modes', loc: 'docs/risk-map.md', ver: '1.0', status: 'current', cycle: 'Quarterly' },
              { doc: 'System Card', loc: 'docs/system-card.md', ver: '1.0', status: 'current', cycle: 'On change' },
              { doc: 'NIST/ISO/AIUC-1 Gap Analysis', loc: 'Compliance page (UI)', ver: 'Live', status: 'current', cycle: 'Continuous' },
              { doc: 'Compliance Audit Report', loc: 'Audit page (UI)', ver: 'Live', status: 'current', cycle: 'On-demand' },
              { doc: 'Governance Dashboard', loc: 'Governance page (UI)', ver: 'Live', status: 'current', cycle: 'Continuous' },
              { doc: 'Scoring Health Baselines', loc: 'Dashboard + Audit (UI)', ver: 'Live', status: 'current', cycle: 'Continuous' },
              { doc: 'Neuron Checkpoint', loc: 'backend/checkpoints/*.json', ver: 'Per export', status: 'current', cycle: 'On-demand' },
            ].map((d, i) => (
              <tr key={i}>
                <td style={{ fontWeight: 500 }}>{d.doc}</td>
                <td><code style={{ fontSize: '0.72rem' }}>{d.loc}</code></td>
                <td>{d.ver}</td>
                <td><span style={{
                  fontSize: '0.68rem', padding: '1px 6px', borderRadius: 3,
                  background: '#22c55e22', color: '#22c55e',
                }}>{d.status}</span></td>
                <td>{d.cycle}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-8: Review Schedule                                           */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Review Schedule</h3>
        <p className="security-section-desc">
          Periodic review cadence for system components and governance processes.
        </p>

        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr><th>Review Activity</th><th>Frequency</th><th>Owner</th><th>Method</th></tr>
          </thead>
          <tbody>
            {[
              { activity: 'Scoring signal drift review', freq: 'Weekly', owner: 'System Owner', method: 'Dashboard Scoring Health Monitor' },
              { activity: 'Production health check', freq: 'Daily (automated)', owner: 'System', method: 'Health check endpoint + circuit breaker' },
              { activity: 'Neuron content quality audit', freq: 'Monthly', owner: 'Content Author', method: 'Compliance Audit page + manual review' },
              { activity: 'PII scan', freq: 'Monthly', owner: 'System Owner', method: 'Compliance Audit page' },
              { activity: 'Governance document review', freq: 'Quarterly', owner: 'System Owner', method: 'Manual review of docs/ directory' },
              { activity: 'Risk register reassessment', freq: 'Quarterly', owner: 'System Owner', method: 'Review against live KPIs and alerts' },
              { activity: 'Bias & coverage assessment', freq: 'Quarterly', owner: 'System Owner', method: 'Compliance Audit bias assessment' },
              { activity: 'Scoring weight review', freq: 'Semi-annually', owner: 'System Owner', method: 'Scoring Health baselines + blind eval trends' },
              { activity: 'Full compliance gap analysis', freq: 'Semi-annually', owner: 'System Owner', method: 'Compliance page review' },
            ].map((r, i) => (
              <tr key={i}>
                <td>{r.activity}</td>
                <td style={{ fontWeight: 500 }}>{r.freq}</td>
                <td style={{ color: '#c8d0dc' }}>{r.owner}</td>
                <td style={{ fontSize: '0.75rem', color: '#c8d0dc' }}>{r.method}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* E001-E003: Failure Playbooks                                     */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Failure Playbooks</h3>
        <p className="security-section-desc">
          Structured response procedures for the three primary failure modes.
          Severity thresholds trigger automatic circuit breaker actions.
        </p>

        {[
          {
            id: 'E001', title: 'Security Breach Playbook',
            trigger: 'Input guard detects sustained injection attempts OR unauthorized access pattern',
            steps: [
              'Circuit breaker trips automatically if attack rate exceeds threshold',
              'Review system_alerts for injection pattern details',
              'Analyze blocked queries in query log for attack vectors',
              'Update input guard regex patterns if new attack vectors identified',
              'Run adversarial test suite to validate updated patterns',
              'Document incident in change log with root cause analysis',
              'If data exposure suspected: audit query logs for exfiltration attempts',
            ],
            severity: 'P1 — Immediate response required',
          },
          {
            id: 'E002', title: 'Harmful Output Playbook',
            trigger: 'Output risk tagging flags dual-use/safety-critical content in >10% of responses OR user reports harmful output',
            steps: [
              'Review flagged outputs in query detail (output_checks section)',
              'Identify source neurons contributing harmful content',
              'Deactivate problematic neurons via is_active flag',
              'Add new risk category patterns to RISK_CATEGORIES if needed',
              'Run affected queries through updated pipeline to verify fix',
              'If systematic: halt service (circuit breaker) until root cause resolved',
              'Document corrective action in refinement log',
            ],
            severity: 'P1 — Immediate response, halt if systematic',
          },
          {
            id: 'E003', title: 'Hallucination Playbook',
            trigger: 'Grounding check confidence drops below 0.3 for >30% of queries OR avg faithfulness eval drops below 3.0',
            steps: [
              'Circuit breaker trips automatically when avg eval drops below threshold',
              'Review grounding check results: identify ungrounded references',
              'Check if affected neurons have been recently modified (refinement log)',
              'If caused by a historical automated refinement: review and revert the affected receipt',
              'Run blind A/B evaluation on affected query set to quantify impact',
              'Restore neuron content from most recent checkpoint if needed',
              'Re-run compliance audit to verify grounding improvement',
            ],
            severity: 'P2 — Investigate within 4 hours, halt if >50% affected',
          },
        ].map(pb => (
          <div key={pb.id} className="security-item" style={{ marginBottom: 12 }}>
            <div className="security-item-header">
              <code className="security-item-id">{pb.id}</code>
              <strong className="security-item-title">{pb.title}</strong>
              <span className="security-badge" style={{
                background: pb.severity.startsWith('P1') ? '#ef444433' : '#fb923c33',
                color: pb.severity.startsWith('P1') ? '#ef4444' : '#fb923c',
              }}>{pb.severity.split(' ')[0]}</span>
            </div>
            <div style={{ fontSize: '0.8rem', color: '#fb923c', margin: '6px 0 8px', padding: '4px 8px', background: '#fb923c11', borderRadius: 4 }}>
              <strong>Trigger:</strong> {pb.trigger}
            </div>
            <ol style={{ margin: 0, paddingLeft: 20, fontSize: '0.8rem', color: 'var(--text-dim)' }}>
              {pb.steps.map((step, i) => (
                <li key={i} style={{ marginBottom: 3 }}>{step}</li>
              ))}
            </ol>
          </div>
        ))}
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-7: Nonconformity & Corrective Action                        */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Nonconformity & Corrective Action Process</h3>
        <p className="security-section-desc">
          Process for identifying, documenting, and correcting system nonconformities.
        </p>

        <div className="security-items">
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>Detection</strong> &mdash; Nonconformities are detected through: circuit breaker triggers,
              drift alerts, low eval scores, user-reported issues, or compliance audit findings.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>Documentation</strong> &mdash; Each nonconformity is recorded as a system alert with
              type, severity, message, and detail JSON. Alerts persist in the system_alerts table.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>Root Cause Analysis</strong> &mdash; For quality-drop and circuit-breaker alerts:
              review scoring health timeline, recent refinements, and eval score trends to identify cause.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>Corrective Action</strong> &mdash; Fix via neuron refinement (content update, deactivation),
              pattern update (input guard, risk categories), or checkpoint restore. Action logged in refinement table.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <p className="security-item-detail">
              <strong>Verification</strong> &mdash; Re-run affected queries, verify via blind eval,
              check compliance audit, confirm alert resolution. Acknowledge alert to close.
            </p>
          </div>
        </div>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-6: Impact Assessment                                         */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Impact Assessment</h3>
        <p className="security-section-desc">
          Formal assessment of system impact on stakeholders and operations.
        </p>

        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr><th>Stakeholder</th><th>Positive Impact</th><th>Negative Impact</th><th>Mitigation</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>End users (engineers, analysts)</td>
              <td style={{ color: '#22c55e' }}>Faster access to domain knowledge, consistent quality</td>
              <td style={{ color: '#ef4444' }}>Over-reliance on AI, hallucinated content accepted as fact</td>
              <td>Grounding check, faithfulness eval, speculative language flagging</td>
            </tr>
            <tr>
              <td>Organization ({orgName})</td>
              <td style={{ color: '#22c55e' }}>Knowledge preservation, 92% cost reduction vs Opus</td>
              <td style={{ color: '#ef4444' }}>Incorrect regulatory guidance, compliance risk</td>
              <td>Neuron provenance tracking, citation verification, human review gates</td>
            </tr>
            <tr>
              <td>Customers / downstream</td>
              <td style={{ color: '#22c55e' }}>Faster responses, auditable reasoning chain</td>
              <td style={{ color: '#ef4444' }}>Dual-use content exposure, out-of-scope answers</td>
              <td>Output risk tagging, domain-scoped graph, ITAR/dual-use detection</td>
            </tr>
            <tr>
              <td>Regulators</td>
              <td style={{ color: '#22c55e' }}>Full transparency, NIST/ISO compliance framework</td>
              <td style={{ color: '#ef4444' }}>Incomplete compliance, unverified claims</td>
              <td>Compliance gap analysis, audit page, governance documentation</td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-5: Communication & Reporting                                 */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Communication & Reporting</h3>
        <p className="security-section-desc">
          Stakeholder communication plan and reporting structure.
        </p>

        <table className="about-table" style={{ fontSize: '0.8rem' }}>
          <thead>
            <tr><th>Report</th><th>Audience</th><th>Frequency</th><th>Content</th><th>Source</th></tr>
          </thead>
          <tbody>
            <tr>
              <td>System Health Summary</td>
              <td>System Owner</td>
              <td>Daily</td>
              <td>Go/no-go status, active alerts, circuit breaker state</td>
              <td>Dashboard Health Check widget</td>
            </tr>
            <tr>
              <td>Quality Trends</td>
              <td>System Owner</td>
              <td>Weekly</td>
              <td>Eval scores, user ratings, drift status, cost trends</td>
              <td>Performance page + Scoring Health Monitor</td>
            </tr>
            <tr>
              <td>KPI Status Report</td>
              <td>Stakeholders</td>
              <td>Monthly</td>
              <td>Objective progress, coverage stats, cost efficiency</td>
              <td>Governance page AI Objectives section</td>
            </tr>
            <tr>
              <td>Compliance Audit</td>
              <td>Compliance / Audit</td>
              <td>Quarterly</td>
              <td>PII scan, bias assessment, provenance, scoring baselines</td>
              <td>Audit page</td>
            </tr>
            <tr>
              <td>Risk Register Review</td>
              <td>Management</td>
              <td>Quarterly</td>
              <td>Risk scores, treatment effectiveness, new risks</td>
              <td>Governance page Risk Register</td>
            </tr>
            <tr>
              <td>Incident Report</td>
              <td>All stakeholders</td>
              <td>As needed</td>
              <td>Incident details, root cause, corrective actions taken</td>
              <td>System alerts + failure playbooks</td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* ══════════════════════════════════════════════════════════════════ */}
      {/* GOV-9: Concern Reporting                                         */}
      {/* ══════════════════════════════════════════════════════════════════ */}
      <section className="security-section">
        <h3>Concern Reporting</h3>
        <p className="security-section-desc">
          Channels for reporting concerns about system behavior, safety, or compliance.
        </p>

        <div className="security-items">
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <div className="security-item-header">
              <strong>User Feedback</strong>
            </div>
            <p className="security-item-detail">
              Thumbs up/down rating on every query response. Low ratings ({'\u2264'} 0.3) trigger automatic
              review via the circuit breaker mechanism. Individual query issues visible in query detail view.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <div className="security-item-header">
              <strong>System Alerts</strong>
            </div>
            <p className="security-item-detail">
              Automated alert generation for drift, quality drops, API changes, and circuit breaker events.
              All alerts visible on Dashboard with acknowledge/dismiss workflow.
            </p>
          </div>
          <div className="security-item" style={{ padding: '8px 12px' }}>
            <div className="security-item-header">
              <strong>Issue Tracking</strong>
            </div>
            <p className="security-item-detail">
              System concerns and feature requests tracked via the project repository issue tracker.
              Compliance gaps tracked on the Compliance page with framework references.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
