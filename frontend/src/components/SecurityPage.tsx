export default function SecurityPage() {
  type Status = 'addressed' | 'partial' | 'missing';

  interface NistItem {
    id: string;
    title: string;
    status: Status;
    detail: string;
  }

  // ── Group 1: Security & Adversarial Testing ──
  const securityAdversarialItems: NistItem[] = [
    { id: 'MEA-2.7', title: 'Security & Resilience', status: 'missing',
      detail: 'No red-teaming for prompt injection. No adversarial query testing. No countermeasure implementation. Minimal attack surface (single-user).' },
    { id: 'MEA-2.6', title: 'Safety Risk Evaluation', status: 'missing',
      detail: 'No stress testing under adversarial or edge-case queries. No chaos engineering. No safety statistics tracking.' },
    { id: 'GOV-4.1', title: 'Critical Thinking & Safety Culture', status: 'partial',
      detail: 'Refine process requires human review before commit. No formal red-teaming or adversarial testing process.' },
    { id: 'MAP-2.3', title: 'TEVV Considerations', status: 'partial',
      detail: 'Blind A/B evaluation framework validates quality. Missing: stress testing, adversarial testing, chaos engineering, formal experiment design.' },
  ];

  // ── Group 2: Production Monitoring & Feedback Loop ──
  const monitoringFeedbackItems: NistItem[] = [
    { id: 'MEA-2.4', title: 'Production Monitoring', status: 'missing',
      detail: 'No automated drift detection. No alerting on scoring distribution shifts. No anomaly detection. Planned: signal histograms over time.' },
    { id: 'MAN-4.1', title: 'Post-Deployment Monitoring', status: 'missing',
      detail: 'No user feedback mechanism (thumbs up/down). No post-deployment TEVV tasks. No regular red-teaming schedule.' },
    { id: 'MAN-4.2', title: 'Continuous Improvement', status: 'partial',
      detail: 'Refine process exists for neuron improvement. Missing: systematic triggers for when to refine, feedback-driven improvement loop.' },
    { id: 'MAN-3.1', title: 'Third-Party Monitoring', status: 'partial',
      detail: 'Anthropic dependency documented. A/B framework can detect model quality drift. Missing: automated monitoring, version pinning alerts.' },
    { id: 'MAN-1.1', title: 'System Viability Assessment', status: 'partial',
      detail: 'A/B framework can assess quality. No formal go/no-go criteria for deployment decisions.' },
  ];

  // ── Group 3: Metrics & Evaluation Rigor ──
  const metricsEvalItems: NistItem[] = [
    { id: 'MEA-1.1', title: 'Metrics Selection & Documentation', status: 'partial',
      detail: 'A/B quality scores and neuron hit rates tracked. Missing: formal metric selection rationale, coverage metrics, scoring distribution baselines.' },
    { id: 'MEA-2.5', title: 'Validity & Reliability', status: 'partial',
      detail: 'Blind A/B framework validates model quality. Missing: construct validity measures, confidence intervals, cross-validation, robustness testing.' },
    { id: 'MEA-2.10', title: 'Privacy Risk', status: 'partial',
      detail: 'Policy prohibits PII in neurons. Query data stored locally only. Missing: automated PII scanning, formal privacy audit, multi-user anonymization.' },
    { id: 'MEA-2.11', title: 'Fairness & Bias', status: 'missing',
      detail: 'No formal bias assessment. Department coverage is uneven (Data Eng: 242 neurons vs others: <50). Single-author perspective. No disaggregated analysis.' },
  ];

  // ── Group 4: Independent Assessment ──
  const independentItems: NistItem[] = [
    { id: 'MEA-1.3', title: 'Independent Assessment', status: 'missing',
      detail: 'All testing done by sole developer. No external review, no separation of development and testing functions.' },
  ];

  // ── Group 5: Personnel Training ──
  const trainingItems: NistItem[] = [
    { id: 'GOV-2.2', title: 'Personnel Training', status: 'partial',
      detail: 'Getting Started page provides setup guidance and training walkthroughs for query inspection, exploration, health monitoring, and evaluation. Operational checklist included. Missing: role-based training paths, assessment/certification, video walkthroughs.' },
  ];

  // ── Already Addressed ──
  const addressedGovernItems: NistItem[] = [
    { id: 'GOV-1.3', title: 'Risk Tolerance & Prioritization', status: 'addressed',
      detail: 'Defined acceptable/unacceptable risks, escalation thresholds (A/B quality drop >15% = investigate, >30% = stop), and risk severity levels in governance.md.' },
    { id: 'GOV-1.4', title: 'Transparent Documentation', status: 'addressed',
      detail: 'Full query provenance (intent, scores, neuron hits, assembled prompt, response) logged and visible in UI. System card documents architecture and data practices.' },
    { id: 'GOV-1.5', title: 'Ongoing Monitoring & Review', status: 'addressed',
      detail: 'Blind A/B framework for periodic quality checks. Scoring Health Monitor on Dashboard tracks per-signal distributions (baseline vs recent window) with automated z-score drift detection and visual alerting. Management review infrastructure with cadence tracking enforces periodic reviews across 7 review types. Compliance snapshots capture posture over time.' },
    { id: 'GOV-1.6', title: 'AI System Inventory', status: 'addressed',
      detail: 'System card (system-card.md) documents all components, models used, data flows, and dependencies.' },
    { id: 'GOV-1.7', title: 'Decommissioning Procedures', status: 'addressed',
      detail: 'Contingency plan for model deprecation, vendor lock-in mitigation (model-agnostic architecture), fallback strategies documented.' },
    { id: 'GOV-2.1', title: 'Roles & Responsibilities', status: 'addressed',
      detail: 'System Owner, Neuron Content Author, Query Reviewer roles defined with separation of concerns matrix.' },
    { id: 'GOV-4.3', title: 'Incident Response', status: 'addressed',
      detail: 'P1-P4 severity levels defined with response procedures. Incident log format specified (docs/incidents.md).' },
    { id: 'GOV-6.1', title: 'Third-Party Risk Management', status: 'addressed',
      detail: 'Anthropic API risks documented with mitigation. Model-agnostic architecture prevents vendor lock-in. Python dependency risks assessed.' },
    { id: 'MAP-1.1', title: 'Intended Purpose Documentation', status: 'addressed',
      detail: 'System purpose, scope, deployment context, and out-of-scope uses documented in system card and risk map.' },
    { id: 'MAP-2.1', title: 'Technical Task Definition', status: 'addressed',
      detail: 'Two-stage pipeline (classify + execute) with deterministic scoring/assembly documented. Component dependency diagram in system card.' },
    { id: 'MAP-2.2', title: 'Knowledge Limits Documentation', status: 'addressed',
      detail: 'Coverage gaps documented (software eng, supply chain, HR, international programs). Out-of-scope topics listed.' },
    { id: 'MAP-3.2', title: 'Negative Impact Analysis', status: 'addressed',
      detail: 'Failure mode analysis covers scoring failures (5 modes), assembly failures (3 modes), execution failures (4 modes), and human factors (3 modes).' },
    { id: 'MAP-3.3', title: 'Targeted Application Scope', status: 'addressed',
      detail: 'Narrowly scoped to aerospace domain knowledge retrieval. Deployment constraints (unclassified, single-user, local, English-only) documented.' },
    { id: 'MAP-3.5', title: 'Human Oversight Definition', status: 'addressed',
      detail: 'Oversight levels defined per activity: query execution (none, advisory), refinement (required), weight changes (required), bulk import (required).' },
    { id: 'MAP-4.1', title: 'Third-Party Technology Risk', status: 'addressed',
      detail: 'Anthropic API risks mapped (deprecation, pricing, policy, behavior drift) with likelihood, impact, and mitigation.' },
    { id: 'MEA-2.8', title: 'Transparency & Accountability', status: 'addressed',
      detail: 'Full audit trail: query logs with intent, neuron scores (6 signals decomposed), assembled prompt, response. Radial visualization. Provenance chain visible in UI.' },
    { id: 'MEA-2.9', title: 'Model Explainability', status: 'addressed',
      detail: '6-signal gated scoring is inherently interpretable. Spread activation visible. Neuron content inspectable. Token budget transparent.' },
    { id: 'MAN-1.4', title: 'Residual Risk Documentation', status: 'addressed',
      detail: 'Known limitations documented in system card: no drift detection, coverage gaps, no adversarial testing, English-only.' },
    { id: 'MAN-2.4', title: 'System Disengagement Criteria', status: 'addressed',
      detail: 'Red thresholds defined: A/B quality drop >30%, >40% zero-hit queries, >25% bad answer reports triggers stop-serving.' },
    { id: 'MAN-4.3', title: 'Incident Communication', status: 'addressed',
      detail: 'Incident log format and response procedures defined. Severity levels with response times. Documentation requirements specified.' },
  ];

  const allItems: NistItem[] = [
    ...securityAdversarialItems, ...monitoringFeedbackItems, ...metricsEvalItems,
    ...independentItems, ...trainingItems, ...addressedGovernItems,
  ];
  const addressed = allItems.filter(i => i.status === 'addressed').length;
  const partial = allItems.filter(i => i.status === 'partial').length;
  const missing = allItems.filter(i => i.status === 'missing').length;

  const statusColor = (s: Status) =>
    s === 'addressed' ? '#22c55e' : s === 'partial' ? '#fb923c' : '#ef4444';

  const statusLabel = (s: Status) =>
    s === 'addressed' ? 'Addressed' : s === 'partial' ? 'Partial' : 'Gap';

  const renderSection = (title: string, description: string, items: NistItem[]) => (
    <section className="security-section">
      <h3>{title}</h3>
      <p className="security-section-desc">{description}</p>
      <div className="security-items">
        {items.map(item => (
          <div key={item.id} className="security-item">
            <div className="security-item-header">
              <code className="security-item-id">{item.id}</code>
              <strong className="security-item-title">{item.title}</strong>
              <span className="security-badge" style={{ background: statusColor(item.status) }}>
                {statusLabel(item.status)}
              </span>
            </div>
            <p className="security-item-detail">{item.detail}</p>
          </div>
        ))}
      </div>
    </section>
  );

  return (
    <div className="security-page">
      <h2>NIST AI RMF Compliance</h2>
      <p className="security-intro">
        Gap analysis against <strong>NIST AI 100-1</strong> (Artificial Intelligence Risk Management Framework 1.0).
        Corvus's neuron provenance architecture was designed with traceability and explainability as core principles.
        Requirements are organized by implementation group rather than NIST function, so related items can be addressed cohesively.
      </p>

      <div className="security-summary">
        <div className="security-summary-item" style={{ borderColor: '#22c55e' }}>
          <span className="security-summary-count" style={{ color: '#22c55e' }}>{addressed}</span>
          <span className="security-summary-label">Addressed</span>
        </div>
        <div className="security-summary-item" style={{ borderColor: '#fb923c' }}>
          <span className="security-summary-count" style={{ color: '#fb923c' }}>{partial}</span>
          <span className="security-summary-label">Partial</span>
        </div>
        <div className="security-summary-item" style={{ borderColor: '#ef4444' }}>
          <span className="security-summary-count" style={{ color: '#ef4444' }}>{missing}</span>
          <span className="security-summary-label">Gaps</span>
        </div>
        <div className="security-summary-item" style={{ borderColor: '#94a3b8' }}>
          <span className="security-summary-count" style={{ color: '#94a3b8' }}>{allItems.length}</span>
          <span className="security-summary-label">Total</span>
        </div>
      </div>

      {renderSection(
        'Group 1: Security & Adversarial Testing',
        'Input validation, prompt injection defense, stress testing, and adversarial robustness. Converges on building an input filtering layer, running adversarial tests, and documenting the process. Also unblocks AIUC-1 B001, B002, B005.',
        securityAdversarialItems
      )}

      {renderSection(
        'Group 2: Production Monitoring & Feedback Loop',
        'Automated drift alerting, user feedback mechanisms, systematic refinement triggers, and third-party API monitoring. Builds on the existing Scoring Health Monitor and user rating infrastructure.',
        monitoringFeedbackItems
      )}

      {renderSection(
        'Group 3: Metrics & Evaluation Rigor',
        'Formal metric baselines, bias assessment, privacy scanning, and statistical validity. Mostly documentation and analysis work: define what "good" looks like numerically, assess coverage bias, and add PII detection.',
        metricsEvalItems
      )}

      {renderSection(
        'Group 4: Independent Assessment',
        'External review and separation of development/testing functions. Requires engaging an independent assessor \u2014 cannot be self-certified. Best tackled after Groups 1\u20133 make the system audit-ready.',
        independentItems
      )}

      {renderSection(
        'Group 5: Personnel Training',
        'Role-based training paths, assessment, and certification. Already partially addressed by the Getting Started page. Best completed after Group 2 when monitoring/feedback features exist to train people on.',
        trainingItems
      )}

      {renderSection(
        'Addressed',
        'Requirements already satisfied by existing architecture, documentation, and tooling. Organized by original NIST function (GOVERN, MAP, MEASURE, MANAGE).',
        addressedGovernItems
      )}

      <section className="security-section">
        <h3>Documentation</h3>
        <p className="security-section-desc">
          Full governance documentation is maintained in the <code>docs/</code> directory:
        </p>
        <ul className="security-doc-list">
          <li><code>docs/governance.md</code> &mdash; Risk tolerance, roles, incident response, change management</li>
          <li><code>docs/risk-map.md</code> &mdash; Failure mode analysis, knowledge boundaries, deployment constraints</li>
          <li><code>docs/system-card.md</code> &mdash; System card with architecture, data practices, performance claims, ethical considerations</li>
        </ul>
      </section>
    </div>
  );
}
