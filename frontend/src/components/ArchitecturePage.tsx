import { Fragment, useEffect, useMemo, useState } from 'react';
import { MindStyle } from './mindUi';

type Box = {
  id: string;
  name: string;
  tier: string;
  purpose: string;
  confidence?: string;
  module_count: number;
  frontend_file_count: number;
  loc: number;
  routes: number;
  modules: string[];
  frontend_files: string[];
};

type EvidenceResult = {
  evidence: string;
  exists: boolean;
  actual_component?: string | null;
  component_ok?: boolean;
  symbol_ok?: boolean | null;
  ok: boolean;
};

type ProcessStep = {
  index: number;
  actor: string;
  component: string;
  does: string;
  state_change?: string;
  guardrail?: string;
  evidence: string[];
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type ArchitectureProcess = {
  id: string;
  name: string;
  availability: string;
  trigger: string;
  cadence: string;
  why: string;
  outcome: string;
  through: string[];
  steps: ProcessStep[];
  evidence_errors: number;
  evidence_ok: boolean;
};

type RuntimeNode = {
  id: string;
  name: string;
  kind: string;
  status: string;
  when: string;
  purpose: string;
  technology: string;
  evidence: string[];
  evidence_ok: boolean;
};

type RuntimeConnection = {
  from: string;
  to: string;
  label: string;
  when: string;
};

type MemoryEngineZone = {
  id: string;
  name: string;
  boundary: 'client' | 'backend' | 'datastore' | 'cache' | string;
  purpose: string;
  details: string[];
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type MemoryEngineTruthNote = {
  id: string;
  label: string;
  value: string;
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type MemoryEngine = {
  title: string;
  summary: string;
  flow: string[];
  zones: MemoryEngineZone[];
  truth_notes: MemoryEngineTruthNote[];
};

type SystemParticipant = {
  id: string;
  name: string;
  kind: string;
  role: string;
  trust: string;
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type SystemRelationship = {
  from: string;
  to: string;
  label: string;
  when: string;
  data: string;
};

type BoundedContext = {
  id: string;
  name: string;
  kind: string;
  purpose: string;
  ownership: string;
  concepts: string[];
  canonical_state: string[];
  invariants: string[];
  components: string[];
  integrates_with: { context: string; contract: string }[];
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type Deployment = {
  id: string;
  name: string;
  status: string;
  purpose: string;
  trust_boundary: string;
  nodes: string[];
  failure_modes: { failure: string; visible_as: string; recovery: string }[];
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type ArchitectureDecision = {
  id: string;
  title: string;
  status: string;
  recorded: string;
  context: string;
  decision: string;
  consequences: string[];
  alternatives: string[];
  record: string;
  bounded_contexts: string[];
  evidence_results: EvidenceResult[];
  evidence_ok: boolean;
};

type Violation = {
  id: string;
  statement: string;
  check: string;
  count: number;
  hits: any[];
  truncated: number;
  baseline_count: number;
  regression_count: number;
};

type Payload = {
  schema_version: number;
  totals: Record<string, number>;
  review: {
    reviewed_at?: string;
    method?: string;
    scope?: string;
    truth_order?: string[];
    limitations?: string[];
  };
  system_summary: {
    identity?: string;
    primary_loop?: string;
    governing_principle?: string;
    current_tenants?: string[];
  };
  memory_engine: MemoryEngine;
  system_context: {
    system: {
      id: string;
      name: string;
      purpose: string;
      boundary: string;
      evidence_results: EvidenceResult[];
      evidence_ok: boolean;
    };
    participants: SystemParticipant[];
    relationships: SystemRelationship[];
  };
  runtime_nodes: RuntimeNode[];
  runtime_connections: RuntimeConnection[];
  bounded_contexts: BoundedContext[];
  deployments: Deployment[];
  decisions: ArchitectureDecision[];
  freshness: {
    fresh: boolean;
    stored_fingerprint?: string;
    current_fingerprint?: string;
    stored_files?: number;
    current_files?: number;
  };
  processes: ArchitectureProcess[];
  boxes: Box[];
  unclassified: { module: string; file: string; loc: number; lang: string }[];
  violations: Violation[];
  unchecked_invariants: { id: string; statement: string }[];
  phantom_boxes: string[];
  manifest_problems: string[];
  import_cycles: string[][];
  route_join: {
    available?: boolean;
    totals?: Record<string, number>;
    route_callers?: Record<string, string[]>;
    routes_with_no_frontend_caller?: string[];
    frontend_urls_unmatched?: string[];
  };
};

type View = 'overview' | 'engine' | 'processes' | 'domains' | 'decisions' | 'components' | 'drift' | 'routes';
type ProcessLane = 'all' | 'core' | 'scheduled' | 'operator' | 'special';

const TIER_ORDER = ['interface', 'hot', 'governance', 'background', 'eval', 'infra'];
const TIER_LABEL: Record<string, string> = {
  interface: 'Interface — where people and harnesses touch Corvus',
  hot: 'Hot path — inside recall or answer delivery',
  governance: 'Governance — judgment, trust, and durable write control',
  background: 'Background — ingestion, maintenance, and projection',
  eval: 'Evidence — metrics, architecture, tests, and benchmarks',
  infra: 'Infrastructure — tenant, schema, process, and startup',
};

const VIEW_LABELS: Record<View, string> = {
  overview: 'System',
  engine: 'Memory Engine',
  processes: 'Processes',
  domains: 'Domains',
  decisions: 'Decisions',
  components: 'Components',
  drift: 'Drift',
  routes: 'Routes',
};

const PROCESS_LANES: Array<{ id: ProcessLane; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'core', label: 'Core loop' },
  { id: 'scheduled', label: 'Scheduled' },
  { id: 'operator', label: 'Operator' },
  { id: 'special', label: 'Historical · lab · release' },
];

function pct(n: number, d: number) {
  return d ? Math.round((100 * n) / d) : 0;
}

function processLane(process: ArchitectureProcess): Exclude<ProcessLane, 'all'> {
  const value = process.availability.toLowerCase();
  if (value.includes('scheduled')) return 'scheduled';
  if (
    value.includes('operator') ||
    value.includes('manual') ||
    process.id.includes('proposal') ||
    process.id.includes('roadmap-ledger')
  ) return 'operator';
  if (
    value.includes('developer') ||
    value.includes('historical') ||
    value.includes('release') ||
    value.includes('optional')
  ) return 'special';
  return 'core';
}

function basename(path: string) {
  const [file, symbol] = path.split('::');
  const short = file.split('/').slice(-2).join('/');
  return symbol ? `${short}::${symbol}` : short;
}

function StatusPill({ children, tone = 'neutral' }: {
  children: React.ReactNode;
  tone?: 'neutral' | 'good' | 'warn' | 'accent';
}) {
  const color = tone === 'good'
    ? 'var(--mm-good, #70d6a0)'
    : tone === 'warn'
      ? 'var(--mm-warn, #f1b86a)'
      : tone === 'accent'
        ? 'var(--mm-accent-text, #8fc7ff)'
        : 'var(--mm-dim, #9aa6b2)';
  return (
    <span className="arch-pill" style={{ color }}>
      {children}
    </span>
  );
}

function EvidenceChips({ results }: { results: EvidenceResult[] }) {
  return (
    <div className="arch-evidence">
      {(results ?? []).map(item => (
        <span key={item.evidence} className={item.ok || item.exists ? 'ok' : 'bad'} title={item.evidence}>
          {item.ok || item.exists ? '✓' : '×'} {basename(item.evidence)}
        </span>
      ))}
    </div>
  );
}

export default function ArchitecturePage() {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState('');
  const [view, setView] = useState<View>('overview');
  const [openBox, setOpenBox] = useState<string | null>(null);
  const [selectedProcess, setSelectedProcess] = useState<string | null>(null);
  const [processSearch, setProcessSearch] = useState('');
  const [processLaneFilter, setProcessLaneFilter] = useState<ProcessLane>('all');

  useEffect(() => {
    fetch('/admin/architecture')
      .then(r => r.ok ? r.json() : r.json().then(b => Promise.reject(b.detail || r.statusText)))
      .then((payload: Payload) => {
        setData(payload);
        setSelectedProcess(payload.processes?.[0]?.id ?? null);
      })
      .catch(e => setError(String(e)));
  }, []);

  const byTier = useMemo(() => {
    const groups = new Map<string, Box[]>();
    for (const box of data?.boxes ?? []) {
      if (!groups.has(box.tier)) groups.set(box.tier, []);
      groups.get(box.tier)!.push(box);
    }
    return groups;
  }, [data]);

  const componentById = useMemo(
    () => new Map((data?.boxes ?? []).map(box => [box.id, box])),
    [data],
  );

  const runtimeById = useMemo(
    () => new Map((data?.runtime_nodes ?? []).map(node => [node.id, node])),
    [data],
  );

  const filteredProcesses = useMemo(() => {
    const query = processSearch.trim().toLowerCase();
    return (data?.processes ?? []).filter(process => {
      if (processLaneFilter !== 'all' && processLane(process) !== processLaneFilter) {
        return false;
      }
      if (!query) return true;
      const haystack = [
        process.name,
        process.trigger,
        process.why,
        process.outcome,
        process.availability,
        ...(process.through ?? []),
      ].join(' ').toLowerCase();
      return haystack.includes(query);
    });
  }, [data, processLaneFilter, processSearch]);

  const activeProcess = useMemo(() => {
    const visible = filteredProcesses.find(process => process.id === selectedProcess);
    return visible ?? filteredProcesses[0] ?? null;
  }, [filteredProcesses, selectedProcess]);

  if (error) return <div className="error-msg">{error}</div>;
  if (!data) return <div className="mm-empty">Loading architecture atlas…</div>;

  const totals = data.totals;
  const coverage = pct(totals.classified, totals.units);

  return (
    <div className="mm-root arch-root">
      <MindStyle />
      <style>{ARCH_CSS}</style>

      <div className="arch-title-row">
        <div>
          <div className="arch-eyebrow">CODE-FORENSIC SYSTEM ATLAS</div>
          <h2>Architecture — how Corvus actually works</h2>
          <div className="mm-sub arch-lede">
            Source-derived inventory joined to a reviewed process model. Start with the system,
            then open a process to see its trigger, actor, state change, guardrail, and code evidence.
          </div>
        </div>
        <div className="arch-review-stamp">
          <span>{data.freshness?.fresh ? 'reviewed · source-current' : 'reviewed · stale extraction'}</span>
          <strong>{data.review?.reviewed_at ?? 'unknown'}</strong>
          <small>{coverage}% source coverage · schema v{data.schema_version}</small>
        </div>
      </div>

      <div className="arch-stats">
        <Stat label="source units" value={`${totals.classified}/${totals.units}`} sub="classified" />
        <Stat label="processes" value={String(totals.processes ?? data.processes.length)}
          sub={`${totals.process_evidence_errors ?? 0} evidence errors`}
          warn={(totals.process_evidence_errors ?? 0) > 0} />
        <Stat label="domains" value={String(totals.bounded_contexts ?? data.bounded_contexts.length)}
          sub={`${totals.boxes} components · ${totals.runtime_nodes ?? 0} runtime nodes`} />
        <Stat label="decisions" value={String(totals.decisions ?? data.decisions.length)}
          sub={`${totals.deployments ?? data.deployments.length} deployments`} />
        <Stat label="known drift" value={String(totals.violated)}
          sub={`${totals.fitness_regressions ?? 0} beyond accepted baseline`}
          warn={(totals.fitness_regressions ?? 0) > 0} />
      </div>

      <div className="arch-view-tabs" role="tablist" aria-label="Architecture views">
        {(Object.keys(VIEW_LABELS) as View[]).map(item => (
          <button
            key={item}
            role="tab"
            aria-selected={view === item}
            onClick={() => setView(item)}
            className={view === item ? 'active' : ''}
          >
            {VIEW_LABELS[item]}
            {item === 'processes' && <span>{data.processes.length}</span>}
            {item === 'domains' && <span>{data.bounded_contexts.length}</span>}
            {item === 'decisions' && <span>{data.decisions.length}</span>}
            {item === 'drift' && totals.violated > 0 && <span>{totals.violated}</span>}
          </button>
        ))}
      </div>

      {view === 'overview' && (
        <SystemOverview
          data={data}
          runtimeById={runtimeById}
          onOpenEngine={() => setView('engine')}
          onOpenProcesses={() => setView('processes')}
        />
      )}

      {view === 'engine' && <MemoryEngineView engine={data.memory_engine} />}

      {view === 'processes' && (
        <div className="arch-process-layout">
          <aside className="arch-process-index">
            <input
              value={processSearch}
              onChange={event => setProcessSearch(event.target.value)}
              placeholder="Search trigger, purpose, component…"
              aria-label="Search architecture processes"
            />
            <div className="arch-lane-tabs">
              {PROCESS_LANES.map(lane => (
                <button
                  key={lane.id}
                  className={processLaneFilter === lane.id ? 'active' : ''}
                  onClick={() => setProcessLaneFilter(lane.id)}
                >
                  {lane.label}
                </button>
              ))}
            </div>
            <div className="arch-process-count">
              {filteredProcesses.length} of {data.processes.length} processes
            </div>
            <div className="arch-process-list">
              {filteredProcesses.map(process => (
                <button
                  key={process.id}
                  className={(activeProcess?.id === process.id) ? 'active' : ''}
                  onClick={() => setSelectedProcess(process.id)}
                >
                  <span className="arch-process-list-name">{process.name}</span>
                  <span className="arch-process-list-meta">
                    {process.steps.length} steps · {process.availability}
                  </span>
                </button>
              ))}
              {filteredProcesses.length === 0 && (
                <div className="mm-empty">No process matches this filter.</div>
              )}
            </div>
          </aside>

          <main className="arch-process-detail">
            {activeProcess && (
              <ProcessDetail process={activeProcess} componentById={componentById} />
            )}
          </main>
        </div>
      )}

      {view === 'domains' && (
        <DomainsView contexts={data.bounded_contexts} componentById={componentById} />
      )}

      {view === 'decisions' && (
        <DecisionsView decisions={data.decisions} contexts={data.bounded_contexts} />
      )}

      {view === 'components' && (
        <div>
          <div className="arch-section-intro">
            <div>
              <h3>Functional components</h3>
              <p>
                Every Python or frontend source unit belongs to exactly one first-match component.
                Open a card to see the files that make it real.
              </p>
            </div>
            <StatusPill tone={totals.unclassified ? 'warn' : 'good'}>
              {totals.unclassified ? `${totals.unclassified} unclassified` : 'complete source coverage'}
            </StatusPill>
          </div>

          {data.manifest_problems.length > 0 && (
            <div className="arch-callout warn">
              Manifest problems: {data.manifest_problems.join('; ')}
            </div>
          )}

          {TIER_ORDER.filter(tier => byTier.has(tier)).map(tier => (
            <section key={tier} className="arch-tier">
              <div className="arch-tier-heading">{TIER_LABEL[tier] ?? tier}</div>
              <div className="arch-component-grid">
                {byTier.get(tier)!.map(box => {
                  const fileCount = box.module_count + box.frontend_file_count;
                  return (
                    <button
                      key={box.id}
                      className="arch-component-card"
                      onClick={() => setOpenBox(openBox === box.id ? null : box.id)}
                      aria-expanded={openBox === box.id}
                    >
                      <div className="arch-card-title">
                        <strong>{box.name}</strong>
                        <StatusPill>{box.confidence ?? 'unrated'}</StatusPill>
                      </div>
                      <p>{box.purpose}</p>
                      <div className="arch-card-metrics">
                        <span>{fileCount} files</span>
                        <span>{box.loc.toLocaleString()} lines</span>
                        {box.routes > 0 && <span>{box.routes} routes</span>}
                      </div>
                      {openBox === box.id && (
                        <div className="arch-file-list">
                          {[...box.modules, ...box.frontend_files].map(module => (
                            <div key={module}>{module}</div>
                          ))}
                        </div>
                      )}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      {view === 'drift' && <DriftView data={data} />}
      {view === 'routes' && <RoutesView data={data} />}
    </div>
  );
}

function MemoryEngineView({ engine }: { engine: MemoryEngine }) {
  const client = engine.zones.filter(zone => zone.boundary === 'client');
  const backend = engine.zones.filter(zone => zone.boundary === 'backend');
  const state = engine.zones.filter(
    zone => zone.boundary === 'datastore' || zone.boundary === 'cache',
  );
  const allEvidenceOk = [...engine.zones, ...engine.truth_notes].every(item => item.evidence_ok);

  return (
    <div className="arch-engine">
      <div className="arch-section-intro">
        <div>
          <div className="arch-eyebrow">MEMORY-SUBSYSTEM DRILL-DOWN</div>
          <h3>{engine.title}</h3>
          <p>{engine.summary}</p>
        </div>
        <StatusPill tone={allEvidenceOk ? 'good' : 'warn'}>
          {allEvidenceOk ? 'all claims evidence-linked' : 'evidence gap'}
        </StatusPill>
      </div>

      <div className="arch-engine-flow" aria-label="Memory engine lifecycle">
        {engine.flow.map((step, index) => (
          <Fragment key={step}>
            <div>
              <span>{String(index + 1).padStart(2, '0')}</span>
              <strong>{step}</strong>
            </div>
            {index < engine.flow.length - 1 && <b>→</b>}
          </Fragment>
        ))}
      </div>

      <EngineBoundary label="Client · evidence enters here" zones={client} />
      <div className="arch-engine-boundary-arrow">
        <span>HTTP / MCP</span>
        <b>↓</b>
      </div>
      <EngineBoundary label="Corvus-Mind backend" zones={backend} />
      <div className="arch-engine-boundary-arrow">
        <span>canonical writes · revision-checked reads</span>
        <b>↓</b>
      </div>
      <EngineBoundary label="State and derivatives" zones={state} />

      <section className="arch-engine-truth">
        <div className="arch-section-intro">
          <div>
            <h3>Precision notes</h3>
            <p>
              These are the implementation details most likely to be flattened,
              mislabeled, or carried forward from an older diagram.
            </p>
          </div>
        </div>
        <div className="arch-engine-truth-grid">
          {engine.truth_notes.map(note => (
            <article key={note.id}>
              <div className="arch-card-title">
                <strong>{note.label}</strong>
                <StatusPill tone={note.evidence_ok ? 'good' : 'warn'}>
                  {note.evidence_ok ? 'verified' : 'gap'}
                </StatusPill>
              </div>
              <p>{note.value}</p>
              <EvidenceChips results={note.evidence_results} />
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}

function EngineBoundary({ label, zones }: {
  label: string;
  zones: MemoryEngineZone[];
}) {
  return (
    <section className="arch-engine-boundary">
      <div className="arch-engine-boundary-label">{label}</div>
      <div className={`arch-engine-zone-grid zones-${zones.length}`}>
        {zones.map(zone => (
          <article key={zone.id} className={`arch-engine-zone boundary-${zone.boundary}`}>
            <div className="arch-card-title">
              <div>
                <span className="arch-runtime-kind">{zone.boundary}</span>
                <h4>{zone.name}</h4>
              </div>
              <StatusPill tone={zone.evidence_ok ? 'good' : 'warn'}>
                {zone.evidence_ok ? 'evidence' : 'gap'}
              </StatusPill>
            </div>
            <p>{zone.purpose}</p>
            <ul>
              {zone.details.map(detail => <li key={detail}>{detail}</li>)}
            </ul>
            <EvidenceChips results={zone.evidence_results} />
          </article>
        ))}
      </div>
    </section>
  );
}

function SystemOverview({ data, runtimeById, onOpenEngine, onOpenProcesses }: {
  data: Payload;
  runtimeById: Map<string, RuntimeNode>;
  onOpenEngine: () => void;
  onOpenProcesses: () => void;
}) {
  const summary = data.system_summary;
  return (
    <div>
      <section className="arch-system-hero">
        <div>
          <div className="arch-eyebrow">SYSTEM IDENTITY</div>
          <h3>{summary.identity}</h3>
        </div>
        <div className="arch-principle">
          <span>Governing principle</span>
          <p>{summary.governing_principle}</p>
        </div>
      </section>

      <SystemContextMap context={data.system_context} />

      <section className="arch-loop">
        <div className="arch-section-intro">
          <div>
            <h3>The primary learning loop</h3>
            <p>{summary.primary_loop}</p>
          </div>
          <div className="arch-inline-actions">
            <button className="arch-text-button" onClick={onOpenEngine}>
              Open memory engine →
            </button>
            <button className="arch-text-button" onClick={onOpenProcesses}>
              Walk every process →
            </button>
          </div>
        </div>
        <div className="arch-loop-rail" aria-label="Corvus primary learning loop">
          {[
            ['01', 'Capture', 'redacted deeds + outcomes'],
            ['02', 'Distill', 'frontier-grade framed extraction'],
            ['03', 'Govern', 'dedup + write gate + Action Bus'],
            ['04', 'Recall', 'hybrid search + score + cool'],
            ['05', 'Attribute', 'reward, contradiction, or no-op'],
            ['06', 'Maintain', 'janitor + auditor + projection'],
          ].map(([number, label, detail], index, rows) => (
            <Fragment key={number}>
              <div className="arch-loop-step">
                <span>{number}</span>
                <strong>{label}</strong>
                <small>{detail}</small>
              </div>
              {index < rows.length - 1 && <div className="arch-loop-arrow">→</div>}
            </Fragment>
          ))}
        </div>
      </section>

      <section>
        <div className="arch-section-intro">
          <div>
            <h3>Runtime topology</h3>
            <p>
              These are deployment/process boundaries, not code folders. Status describes how each
              boundary participates in the current product.
            </p>
          </div>
          <StatusPill tone={data.runtime_nodes.every(node => node.evidence_ok) ? 'good' : 'warn'}>
            {data.runtime_nodes.filter(node => node.evidence_ok).length}/{data.runtime_nodes.length} evidence-linked
          </StatusPill>
        </div>

        <div className="arch-runtime-grid">
          {data.runtime_nodes.map(node => (
            <article key={node.id} className="arch-runtime-node">
              <div className="arch-card-title">
                <span className="arch-runtime-kind">{node.kind}</span>
                <StatusPill tone={node.status.startsWith('active') ? 'good' : 'neutral'}>
                  {node.status}
                </StatusPill>
              </div>
              <h4>{node.name}</h4>
              <p>{node.purpose}</p>
              <dl>
                <dt>When</dt><dd>{node.when}</dd>
                <dt>Built with</dt><dd>{node.technology}</dd>
              </dl>
            </article>
          ))}
        </div>

        <div className="arch-connections">
          {data.runtime_connections.map((connection, index) => (
            <div key={`${connection.from}-${connection.to}-${index}`} className="arch-connection">
              <strong>{runtimeById.get(connection.from)?.name ?? connection.from}</strong>
              <span className="arch-connection-arrow">→</span>
              <strong>{runtimeById.get(connection.to)?.name ?? connection.to}</strong>
              <p>{connection.label}</p>
              <small>{connection.when}</small>
            </div>
          ))}
        </div>
      </section>

      <DeploymentView deployments={data.deployments} runtimeById={runtimeById} />

      <section className="arch-review-notes">
        <div>
          <h3>Scope</h3>
          <p>{data.review?.scope}</p>
          <div className="arch-tenant-list">
            {(summary.current_tenants ?? []).map(tenant => (
              <StatusPill key={tenant} tone="accent">{tenant}</StatusPill>
            ))}
          </div>
        </div>
        <div>
          <h3>How to trust this page</h3>
          <ol>
            {(data.review?.truth_order ?? []).map(item => <li key={item}>{item}</li>)}
          </ol>
        </div>
        <div>
          <h3>Known limits</h3>
          <ul>
            {(data.review?.limitations ?? []).map(item => <li key={item}>{item}</li>)}
          </ul>
        </div>
      </section>
    </div>
  );
}

function SystemContextMap({ context }: { context: Payload['system_context'] }) {
  const names = new Map([
    [context.system.id, context.system.name],
    ...context.participants.map(participant => [participant.id, participant.name] as const),
  ]);
  return (
    <section className="arch-context-section">
      <div className="arch-section-intro">
        <div>
          <h3>C4 · system context</h3>
          <p>
            Corvus as one system boundary, surrounded by the people and external
            systems that exchange authority, evidence, triggers, and generated capability.
          </p>
        </div>
        <StatusPill tone={
          context.system.evidence_ok && context.participants.every(item => item.evidence_ok)
            ? 'good'
            : 'warn'
        }>
          context evidence linked
        </StatusPill>
      </div>

      <div className="arch-context-map">
        <article className="arch-context-system">
          <span>SYSTEM OF INTEREST</span>
          <h4>{context.system.name}</h4>
          <p>{context.system.purpose}</p>
          <small>{context.system.boundary}</small>
        </article>
        <div className="arch-context-participants">
          {context.participants.map(participant => (
            <article key={participant.id}>
              <div className="arch-card-title">
                <span className="arch-runtime-kind">{participant.kind}</span>
                <StatusPill tone={participant.evidence_ok ? 'good' : 'warn'}>
                  {participant.evidence_ok ? 'evidence' : 'gap'}
                </StatusPill>
              </div>
              <h4>{participant.name}</h4>
              <p>{participant.role}</p>
              <small>{participant.trust}</small>
            </article>
          ))}
        </div>
      </div>

      <div className="arch-context-relationships">
        {context.relationships.map((relationship, index) => (
          <div key={`${relationship.from}-${relationship.to}-${index}`}>
            <div>
              <strong>{names.get(relationship.from) ?? relationship.from}</strong>
              <span>→</span>
              <strong>{names.get(relationship.to) ?? relationship.to}</strong>
            </div>
            <p>{relationship.label}</p>
            <small>{relationship.when} · {relationship.data}</small>
          </div>
        ))}
      </div>
    </section>
  );
}

function DeploymentView({ deployments, runtimeById }: {
  deployments: Deployment[];
  runtimeById: Map<string, RuntimeNode>;
}) {
  return (
    <section className="arch-deployment-section">
      <div className="arch-section-intro">
        <div>
          <h3>Deployment and failure topology</h3>
          <p>
            Environments select different runtime boundaries and trust zones.
            Failure modes state what degrades, what remains alive, and how recovery works.
          </p>
        </div>
        <StatusPill tone={deployments.every(item => item.evidence_ok) ? 'good' : 'warn'}>
          {deployments.length} deployments
        </StatusPill>
      </div>
      <div className="arch-deployment-grid">
        {deployments.map(deployment => (
          <article key={deployment.id} className="arch-deployment-card">
            <div className="arch-card-title">
              <div>
                <span className="arch-runtime-kind">deployment</span>
                <h4>{deployment.name}</h4>
              </div>
              <StatusPill tone={deployment.status === 'active' ? 'good' : 'neutral'}>
                {deployment.status}
              </StatusPill>
            </div>
            <p>{deployment.purpose}</p>
            <div className="arch-boundary-note">
              <span>TRUST BOUNDARY</span>
              <p>{deployment.trust_boundary}</p>
            </div>
            <div className="arch-node-pills">
              {deployment.nodes.map(node => (
                <StatusPill key={node} tone="accent">
                  {runtimeById.get(node)?.name ?? node}
                </StatusPill>
              ))}
            </div>
            <div className="arch-failure-list">
              {deployment.failure_modes.map((mode, index) => (
                <details key={index}>
                  <summary>{mode.failure}</summary>
                  <dl>
                    <dt>Visible as</dt><dd>{mode.visible_as}</dd>
                    <dt>Recovery</dt><dd>{mode.recovery}</dd>
                  </dl>
                </details>
              ))}
            </div>
            <EvidenceChips results={deployment.evidence_results} />
          </article>
        ))}
      </div>
    </section>
  );
}

function DomainsView({ contexts, componentById }: {
  contexts: BoundedContext[];
  componentById: Map<string, Box>;
}) {
  const contextById = new Map(contexts.map(context => [context.id, context]));
  return (
    <div>
      <div className="arch-section-intro">
        <div>
          <h3>Domain and bounded-context ownership</h3>
          <p>
            Components say where code lives. Contexts say who owns a concept,
            which state is canonical, what must remain true, and how neighbors integrate.
          </p>
        </div>
        <StatusPill tone={contexts.every(item => item.evidence_ok) ? 'good' : 'warn'}>
          {contexts.length} ownership boundaries
        </StatusPill>
      </div>
      <div className="arch-domain-grid">
        {contexts.map(context => (
          <article key={context.id} className="arch-domain-card">
            <div className="arch-card-title">
              <div>
                <span className="arch-runtime-kind">{context.kind}</span>
                <h4>{context.name}</h4>
              </div>
              <StatusPill tone={context.evidence_ok ? 'good' : 'warn'}>
                {context.evidence_ok ? 'evidence' : 'gap'}
              </StatusPill>
            </div>
            <p>{context.purpose}</p>
            <div className="arch-boundary-note">
              <span>OWNS</span>
              <p>{context.ownership}</p>
            </div>
            <div className="arch-node-pills">
              {context.components.map(component => (
                <StatusPill key={component} tone="accent">
                  {componentById.get(component)?.name ?? component}
                </StatusPill>
              ))}
            </div>
            <details>
              <summary>Concepts · {context.concepts.length}</summary>
              <ul>{context.concepts.map(item => <li key={item}>{item}</li>)}</ul>
            </details>
            <details>
              <summary>Canonical state · {context.canonical_state.length}</summary>
              <ul>{context.canonical_state.map(item => <li key={item}>{item}</li>)}</ul>
            </details>
            <details>
              <summary>Invariants · {context.invariants.length}</summary>
              <ul>{context.invariants.map(item => <li key={item}>{item}</li>)}</ul>
            </details>
            <details>
              <summary>Integration contracts · {context.integrates_with.length}</summary>
              <div className="arch-contract-list">
                {context.integrates_with.map(integration => (
                  <div key={integration.context}>
                    <strong>{contextById.get(integration.context)?.name ?? integration.context}</strong>
                    <p>{integration.contract}</p>
                  </div>
                ))}
              </div>
            </details>
            <EvidenceChips results={context.evidence_results} />
          </article>
        ))}
      </div>
    </div>
  );
}

function DecisionsView({ decisions, contexts }: {
  decisions: ArchitectureDecision[];
  contexts: BoundedContext[];
}) {
  const contextById = new Map(contexts.map(context => [context.id, context]));
  return (
    <div>
      <div className="arch-section-intro">
        <div>
          <h3>Architecture Decision Records</h3>
          <p>
            Diagrams describe the present shape. ADRs preserve why the shape
            exists, its tradeoffs, and which later decision may supersede it.
          </p>
        </div>
        <StatusPill tone={decisions.every(item => item.evidence_ok) ? 'good' : 'warn'}>
          {decisions.length} recorded decisions
        </StatusPill>
      </div>
      <div className="arch-decision-list">
        {decisions.map(decision => (
          <article key={decision.id} className="arch-decision-card">
            <div className="arch-decision-id">{decision.id}</div>
            <div>
              <div className="arch-card-title">
                <div>
                  <span className="arch-runtime-kind">{decision.recorded}</span>
                  <h4>{decision.title}</h4>
                </div>
                <StatusPill tone={decision.evidence_ok ? 'good' : 'warn'}>
                  {decision.status}
                </StatusPill>
              </div>
              <div className="arch-decision-context">
                {decision.bounded_contexts.map(context => (
                  <StatusPill key={context} tone="accent">
                    {contextById.get(context)?.name ?? context}
                  </StatusPill>
                ))}
              </div>
              <p>{decision.context}</p>
              <div className="arch-boundary-note">
                <span>DECISION</span>
                <p>{decision.decision}</p>
              </div>
              <div className="arch-decision-columns">
                <details>
                  <summary>Consequences · {decision.consequences.length}</summary>
                  <ul>{decision.consequences.map(item => <li key={item}>{item}</li>)}</ul>
                </details>
                <details>
                  <summary>Alternatives · {decision.alternatives.length}</summary>
                  <ul>{decision.alternatives.map(item => <li key={item}>{item}</li>)}</ul>
                </details>
              </div>
              <div className="arch-record-path">{decision.record}</div>
              <EvidenceChips results={decision.evidence_results} />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function ProcessDetail({ process, componentById }: {
  process: ArchitectureProcess;
  componentById: Map<string, Box>;
}) {
  return (
    <article>
      <div className="arch-process-header">
        <div>
          <div className="arch-eyebrow">{process.availability}</div>
          <h3>{process.name}</h3>
        </div>
        <StatusPill tone={process.evidence_ok ? 'good' : 'warn'}>
          {process.evidence_ok ? 'evidence verified' : `${process.evidence_errors} evidence errors`}
        </StatusPill>
      </div>

      <div className="arch-generated-flow" aria-label={`Generated event flow for ${process.name}`}>
        <div className="arch-generated-flow-label">Generated event flow</div>
        <div className="arch-generated-flow-rail">
          {process.steps.map((step, index) => (
            <Fragment key={step.index}>
              <div className="arch-generated-hop">
                <span>{String(step.index).padStart(2, '0')}</span>
                <strong>{step.actor}</strong>
                <small>{componentById.get(step.component)?.name ?? step.component}</small>
              </div>
              {index < process.steps.length - 1 && (
                <div className="arch-generated-arrow">→</div>
              )}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="arch-process-facts">
        <div><span>Trigger</span><p>{process.trigger}</p></div>
        <div><span>When</span><p>{process.cadence}</p></div>
        <div><span>Why</span><p>{process.why}</p></div>
        <div><span>Result</span><p>{process.outcome}</p></div>
      </div>

      <div className="arch-through">
        <span>Component path</span>
        <div>
          {process.through.map((component, index) => (
            <Fragment key={`${component}-${index}`}>
              <StatusPill tone="accent">
                {componentById.get(component)?.name ?? component}
              </StatusPill>
              {index < process.through.length - 1 && <b>→</b>}
            </Fragment>
          ))}
        </div>
      </div>

      <div className="arch-step-list">
        {process.steps.map(step => (
          <section key={step.index} className="arch-step">
            <div className="arch-step-number">{String(step.index).padStart(2, '0')}</div>
            <div className="arch-step-body">
              <div className="arch-step-meta">
                <StatusPill tone="accent">
                  {componentById.get(step.component)?.name ?? step.component}
                </StatusPill>
                <span>{step.actor}</span>
              </div>
              <h4>{step.does}</h4>
              {step.state_change && (
                <div className="arch-step-field">
                  <span>STATE CHANGE</span>
                  <p>{step.state_change}</p>
                </div>
              )}
              {step.guardrail && (
                <div className="arch-step-field guardrail">
                  <span>GUARDRAIL</span>
                  <p>{step.guardrail}</p>
                </div>
              )}
              <div className="arch-evidence">
                {step.evidence_results.map(item => (
                  <span key={item.evidence} className={item.ok ? 'ok' : 'bad'} title={item.evidence}>
                    {item.ok ? '✓' : '×'} {basename(item.evidence)}
                  </span>
                ))}
              </div>
            </div>
          </section>
        ))}
      </div>
    </article>
  );
}

function DriftView({ data }: { data: Payload }) {
  return (
    <div>
      <div className="arch-section-intro">
        <div>
          <h3>Contradictions and model limits</h3>
          <p>
            Red here means the code contradicts a stated rule. It does not mean the checker
            automatically knows the repair; these are the places architecture and implementation disagree.
          </p>
        </div>
        <StatusPill tone={(data.totals.fitness_regressions ?? 0) ? 'warn' : 'good'}>
          {data.totals.fitness_regressions ?? 0} beyond accepted baseline
        </StatusPill>
      </div>

      <div className={`arch-callout ${data.freshness?.fresh ? 'good' : 'warn'}`}>
        Source extraction {data.freshness?.fresh ? 'matches' : 'does not match'} the current
        {' '}{data.freshness?.current_files ?? 0}-file inventory.
        {data.freshness?.fresh
          ? ' Architecture claims are being checked against the code now on disk.'
          : ' Regenerate the source artifact before trusting coverage or drift counts.'}
      </div>

      {data.violations.map(violation => (
        <section key={violation.id} className="arch-violation">
          <div className="arch-card-title">
            <div>
              <strong>{violation.id}</strong>
              <StatusPill tone="warn">{violation.check}</StatusPill>
            </div>
            <span className="arch-hit-count">
              {violation.count} hit{violation.count === 1 ? '' : 's'}
              {' '}· baseline {violation.baseline_count}
              {violation.regression_count > 0 && ` · +${violation.regression_count} regression`}
            </span>
          </div>
          <p>{violation.statement}</p>
          <div className="arch-hit-list">
            {violation.hits.map((hit, index) => (
              <div key={index}>
                <code>{hit.file ?? JSON.stringify(hit.cycle)}{hit.line ? `:${hit.line}` : ''}</code>
                <span>{hit.imports ?? hit.reaches ?? hit.model ?? hit.note ?? ''}</span>
              </div>
            ))}
            {violation.truncated > 0 && <small>… +{violation.truncated} more</small>}
          </div>
        </section>
      ))}

      <div className="arch-review-notes two">
        <div>
          <h3>Stated, not statically provable</h3>
          <p className="mm-sub">
            These rules still matter. The current extractor lacks a sound machine check for them.
          </p>
          {data.unchecked_invariants.map(invariant => (
            <div key={invariant.id} className="arch-unchecked">
              <strong>{invariant.id}</strong>
              <p>{invariant.statement}</p>
            </div>
          ))}
        </div>
        <div>
          <h3>Coverage residue</h3>
          {data.unclassified.length === 0 ? (
            <div className="arch-callout good">Every extracted Python and TypeScript source unit has a component.</div>
          ) : (
            <div className="arch-hit-list">
              {data.unclassified.map(item => (
                <div key={item.module}><code>{item.file}</code><span>{item.loc} lines</span></div>
              ))}
            </div>
          )}
          <h3>Import cycles</h3>
          <p className="mm-sub">
            {data.import_cycles.length} strongly connected import group{data.import_cycles.length === 1 ? '' : 's'}.
            Framework registration cycles can be deliberate; hot-path service cycles deserve more scrutiny.
          </p>
          <div className="arch-cycle-list">
            {data.import_cycles.map((cycle, index) => (
              <details key={index}>
                <summary>Cycle group {index + 1} · {cycle.length} modules</summary>
                <code>{cycle.join(' ↔ ')}</code>
              </details>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function RoutesView({ data }: { data: Payload }) {
  const totals = data.route_join?.totals ?? {};
  return (
    <div>
      <div className="arch-section-intro">
        <div>
          <h3>Frontend ↔ backend route join</h3>
          <p>
            Direct fetches and api.ts wrappers are normalized against full FastAPI paths.
            “No UI caller” includes deliberate hook, MCP, timer, external, and admin-only routes.
          </p>
        </div>
        <StatusPill tone={(totals.frontend_urls_unmatched ?? 0) > 0 ? 'warn' : 'good'}>
          {totals.frontend_urls_unmatched ?? 0} unmatched frontend URLs
        </StatusPill>
      </div>

      <div className="arch-stats compact">
        <Stat label="backend routes" value={String(totals.backend_routes ?? 0)} />
        <Stat label="called by UI" value={String(totals.routes_called_by_frontend ?? 0)} />
        <Stat label="headless/no UI" value={String(totals.routes_with_no_frontend_caller ?? 0)} />
        <Stat label="unmatched URLs" value={String(totals.frontend_urls_unmatched ?? 0)}
          warn={(totals.frontend_urls_unmatched ?? 0) > 0} />
      </div>

      <div className="arch-route-columns">
        <section>
          <h4>Routes with no frontend caller</h4>
          <div className="arch-route-list">
            {(data.route_join?.routes_with_no_frontend_caller ?? []).map(route => (
              <code key={route}>{route}</code>
            ))}
          </div>
        </section>
        <section>
          <h4>Routes called by the frontend</h4>
          <div className="arch-route-callers">
            {Object.entries(data.route_join?.route_callers ?? {}).map(([route, callers]) => (
              <div key={route}>
                <code>{route}</code>
                <span>{callers.map(caller => caller.replace('frontend/src/', '')).join(', ')}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value, sub, warn }: {
  label: string;
  value: string;
  sub?: string;
  warn?: boolean;
}) {
  return (
    <div className="arch-stat">
      <span>{label}</span>
      <strong style={{ color: warn ? 'var(--mm-warn)' : undefined }}>{value}</strong>
      {sub && <small>{sub}</small>}
    </div>
  );
}

const ARCH_CSS = `
.arch-root { --arch-card: rgba(12, 18, 26, .46); --arch-card-strong: rgba(16, 24, 35, .78); }
.arch-root h2, .arch-root h3, .arch-root h4, .arch-root p { margin-top: 0; }
.arch-title-row { display:flex; justify-content:space-between; gap:24px; align-items:flex-start; }
.arch-title-row h2 { margin:.18rem 0 .35rem; }
.arch-lede { max-width:880px; }
.arch-eyebrow { color:var(--mm-accent-text, #8fc7ff); font-size:.68rem; letter-spacing:.16em; text-transform:uppercase; font-weight:700; }
.arch-review-stamp { min-width:150px; border:1px solid var(--mm-line, #334); padding:10px 12px; text-align:right; background:var(--arch-card); }
.arch-review-stamp span, .arch-review-stamp small { display:block; opacity:.65; font-size:.67rem; text-transform:uppercase; letter-spacing:.08em; }
.arch-review-stamp strong { display:block; margin:3px 0; font-size:1rem; }
.arch-stats { display:grid; grid-template-columns:repeat(5, minmax(110px, 1fr)); gap:1px; margin:18px 0; border:1px solid var(--mm-line, #334); background:var(--mm-line, #334); }
.arch-stats.compact { grid-template-columns:repeat(4, minmax(120px, 1fr)); }
.arch-stat { background:var(--arch-card-strong); padding:10px 12px; min-height:68px; }
.arch-stat > span, .arch-stat > small { display:block; font-size:.66rem; opacity:.65; text-transform:uppercase; letter-spacing:.06em; }
.arch-stat > strong { display:block; font-size:1.35rem; margin:2px 0; font-variant-numeric:tabular-nums; }
.arch-view-tabs { display:flex; gap:2px; border-bottom:1px solid var(--mm-line, #334); margin-bottom:20px; overflow-x:auto; }
.arch-view-tabs button { border:0; border-bottom:2px solid transparent; background:transparent; color:inherit; padding:9px 14px; cursor:pointer; opacity:.62; font:inherit; }
.arch-view-tabs button:hover, .arch-view-tabs button.active { opacity:1; }
.arch-view-tabs button.active { border-bottom-color:var(--mm-accent-text, #8fc7ff); }
.arch-view-tabs button span { margin-left:7px; font-size:.65rem; opacity:.7; }
.arch-pill { display:inline-flex; align-items:center; border:1px solid currentColor; border-radius:999px; padding:2px 7px; font-size:.64rem; line-height:1.2; letter-spacing:.03em; white-space:nowrap; }
.arch-system-hero { display:grid; grid-template-columns:minmax(0, 1.55fr) minmax(260px, .75fr); gap:16px; margin-bottom:18px; }
.arch-system-hero > div { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:18px; }
.arch-system-hero h3 { font-size:1.2rem; line-height:1.5; margin:.5rem 0 0; font-weight:500; }
.arch-principle span { font-size:.67rem; letter-spacing:.1em; text-transform:uppercase; color:var(--mm-warn, #f1b86a); }
.arch-principle p { margin:.7rem 0 0; line-height:1.55; font-size:.84rem; }
.arch-loop { margin:22px 0 28px; }
.arch-section-intro { display:flex; justify-content:space-between; gap:18px; align-items:flex-start; margin:0 0 12px; }
.arch-section-intro h3 { margin:0 0 4px; }
.arch-section-intro p { margin:0; opacity:.68; font-size:.8rem; line-height:1.5; max-width:850px; }
.arch-text-button { border:0; background:transparent; color:var(--mm-accent-text, #8fc7ff); cursor:pointer; font:inherit; white-space:nowrap; }
.arch-inline-actions { display:flex; flex-wrap:wrap; justify-content:flex-end; gap:12px; }
.arch-engine-flow { display:flex; align-items:stretch; gap:5px; overflow-x:auto; margin:16px 0 22px; padding-bottom:4px; }
.arch-engine-flow > div { min-width:150px; flex:1; border:1px solid var(--mm-line, #334); background:var(--arch-card-strong); padding:10px; }
.arch-engine-flow span, .arch-engine-flow strong { display:block; }
.arch-engine-flow span { color:var(--mm-accent-text, #8fc7ff); font-size:.61rem; }
.arch-engine-flow strong { margin-top:5px; font-size:.74rem; line-height:1.35; }
.arch-engine-flow > b { align-self:center; opacity:.38; font-weight:400; }
.arch-engine-boundary { border:1px solid var(--mm-line, #334); background:rgba(8, 13, 20, .18); padding:10px; }
.arch-engine-boundary-label { margin-bottom:8px; color:var(--mm-accent-text, #8fc7ff); font-size:.64rem; font-weight:700; letter-spacing:.11em; text-transform:uppercase; }
.arch-engine-zone-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(285px, 1fr)); gap:8px; }
.arch-engine-zone-grid.zones-1 { grid-template-columns:1fr; }
.arch-engine-zone { border:1px solid var(--mm-line, #334); border-top:2px solid var(--mm-accent-text, #8fc7ff); background:var(--arch-card); padding:12px; min-width:0; }
.arch-engine-zone.boundary-datastore { border-top-color:var(--mm-good, #70d6a0); }
.arch-engine-zone.boundary-cache { border-top-color:var(--mm-warn, #f1b86a); }
.arch-engine-zone h4 { margin:5px 0 0; }
.arch-engine-zone > p { margin:8px 0; font-size:.75rem; line-height:1.5; opacity:.78; }
.arch-engine-zone ul { margin:8px 0 0; padding-left:18px; }
.arch-engine-zone li { margin:5px 0; font-size:.69rem; line-height:1.45; opacity:.72; }
.arch-engine-boundary-arrow { display:flex; flex-direction:column; align-items:center; gap:2px; padding:7px 0; color:var(--mm-accent-text, #8fc7ff); }
.arch-engine-boundary-arrow span { font-size:.59rem; letter-spacing:.08em; text-transform:uppercase; opacity:.7; }
.arch-engine-boundary-arrow b { font-weight:400; }
.arch-engine-truth { margin-top:24px; }
.arch-engine-truth-grid { display:grid; grid-template-columns:repeat(2, minmax(260px, 1fr)); gap:8px; }
.arch-engine-truth-grid article { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:11px; }
.arch-engine-truth-grid article > p { margin:8px 0 0; font-size:.72rem; line-height:1.5; opacity:.76; }
.arch-loop-rail { display:flex; align-items:stretch; gap:5px; overflow-x:auto; padding-bottom:4px; }
.arch-loop-step { min-width:135px; flex:1; border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:10px; }
.arch-loop-step span { display:block; font-size:.62rem; color:var(--mm-accent-text, #8fc7ff); }
.arch-loop-step strong { display:block; margin:5px 0; }
.arch-loop-step small { display:block; opacity:.62; line-height:1.35; }
.arch-loop-arrow { align-self:center; opacity:.45; }
.arch-runtime-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(250px, 1fr)); gap:10px; }
.arch-runtime-node { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:12px; }
.arch-runtime-node h4 { margin:8px 0 6px; }
.arch-runtime-node > p { opacity:.72; font-size:.77rem; line-height:1.45; min-height:45px; }
.arch-runtime-kind { font-size:.63rem; text-transform:uppercase; letter-spacing:.08em; opacity:.62; }
.arch-card-title { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
.arch-runtime-node dl { display:grid; grid-template-columns:64px 1fr; gap:4px 8px; margin:10px 0 0; font-size:.68rem; }
.arch-runtime-node dt { opacity:.5; text-transform:uppercase; }
.arch-runtime-node dd { margin:0; opacity:.82; }
.arch-connections { display:grid; grid-template-columns:repeat(auto-fill, minmax(310px, 1fr)); gap:1px; background:var(--mm-line, #334); border:1px solid var(--mm-line, #334); margin:12px 0 26px; }
.arch-connection { display:grid; grid-template-columns:auto 18px 1fr; gap:4px; align-items:center; background:var(--arch-card-strong); padding:10px; font-size:.72rem; }
.arch-connection-arrow { opacity:.45; }
.arch-connection p, .arch-connection small { grid-column:1 / -1; margin:3px 0 0; opacity:.67; }
.arch-connection small { font-size:.64rem; }
.arch-review-notes { display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-top:24px; }
.arch-review-notes.two { grid-template-columns:repeat(2, 1fr); }
.arch-review-notes > div { border-top:2px solid var(--mm-line, #334); padding-top:10px; }
.arch-review-notes h3 { font-size:.85rem; }
.arch-review-notes p, .arch-review-notes li { font-size:.72rem; opacity:.72; line-height:1.5; }
.arch-review-notes ol, .arch-review-notes ul { padding-left:18px; }
.arch-tenant-list { display:flex; flex-wrap:wrap; gap:5px; }
.arch-process-layout { display:grid; grid-template-columns:minmax(250px, .72fr) minmax(440px, 1.7fr); gap:16px; min-height:640px; }
.arch-process-index { border-right:1px solid var(--mm-line, #334); padding-right:14px; }
.arch-process-index input { width:100%; box-sizing:border-box; background:var(--arch-card); color:inherit; border:1px solid var(--mm-line, #334); padding:9px 10px; font:inherit; font-size:.75rem; }
.arch-lane-tabs { display:flex; flex-wrap:wrap; gap:4px; margin:8px 0; }
.arch-lane-tabs button { border:1px solid var(--mm-line, #334); background:transparent; color:inherit; padding:4px 7px; cursor:pointer; font-size:.64rem; opacity:.65; }
.arch-lane-tabs button.active { opacity:1; border-color:var(--mm-accent-text, #8fc7ff); color:var(--mm-accent-text, #8fc7ff); }
.arch-process-count { font-size:.64rem; opacity:.5; margin:10px 0 6px; text-transform:uppercase; letter-spacing:.06em; }
.arch-process-list { display:flex; flex-direction:column; gap:2px; max-height:700px; overflow-y:auto; }
.arch-process-list > button { text-align:left; border:0; border-left:2px solid transparent; background:transparent; color:inherit; padding:8px 9px; cursor:pointer; }
.arch-process-list > button:hover { background:var(--arch-card); }
.arch-process-list > button.active { border-left-color:var(--mm-accent-text, #8fc7ff); background:var(--arch-card-strong); }
.arch-process-list-name, .arch-process-list-meta { display:block; }
.arch-process-list-name { font-size:.76rem; }
.arch-process-list-meta { font-size:.62rem; opacity:.5; margin-top:3px; }
.arch-process-detail { min-width:0; }
.arch-process-header { display:flex; justify-content:space-between; align-items:flex-start; gap:14px; border-bottom:1px solid var(--mm-line, #334); padding-bottom:10px; }
.arch-process-header h3 { margin:4px 0 0; font-size:1.25rem; }
.arch-process-facts { display:grid; grid-template-columns:repeat(2, 1fr); gap:1px; margin:12px 0; background:var(--mm-line, #334); border:1px solid var(--mm-line, #334); }
.arch-process-facts > div { background:var(--arch-card-strong); padding:10px; }
.arch-process-facts span, .arch-through > span, .arch-step-field > span { font-size:.62rem; opacity:.54; letter-spacing:.09em; text-transform:uppercase; }
.arch-process-facts p { margin:5px 0 0; font-size:.76rem; line-height:1.45; }
.arch-through { display:flex; align-items:flex-start; gap:12px; margin:14px 0 20px; }
.arch-through > span { padding-top:3px; white-space:nowrap; }
.arch-through > div { display:flex; gap:5px; align-items:center; flex-wrap:wrap; }
.arch-through b { opacity:.35; font-weight:400; }
.arch-step-list { position:relative; }
.arch-step { display:grid; grid-template-columns:38px 1fr; gap:10px; position:relative; padding-bottom:12px; }
.arch-step:not(:last-child)::before { content:''; position:absolute; left:18px; top:31px; bottom:-4px; width:1px; background:var(--mm-line, #334); }
.arch-step-number { width:36px; height:28px; display:flex; align-items:center; justify-content:center; border:1px solid var(--mm-line, #334); background:var(--arch-card-strong); color:var(--mm-accent-text, #8fc7ff); font-size:.67rem; z-index:1; }
.arch-step-body { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:11px; min-width:0; }
.arch-step-meta { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
.arch-step-meta > span { font-size:.67rem; opacity:.62; }
.arch-step-body h4 { font-size:.82rem; line-height:1.5; margin:0 0 9px; font-weight:550; }
.arch-step-field { display:grid; grid-template-columns:88px 1fr; gap:8px; border-top:1px solid var(--mm-line, #334); padding-top:7px; margin-top:7px; }
.arch-step-field.guardrail > span { color:var(--mm-warn, #f1b86a); opacity:.9; }
.arch-step-field p { margin:0; font-size:.7rem; opacity:.7; line-height:1.45; }
.arch-evidence { display:flex; flex-wrap:wrap; gap:4px; margin-top:9px; }
.arch-evidence span { max-width:100%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-family:monospace; font-size:.61rem; padding:3px 5px; border:1px solid var(--mm-line, #334); opacity:.72; }
.arch-evidence span.ok { color:var(--mm-good, #70d6a0); }
.arch-evidence span.bad { color:var(--mm-warn, #f1b86a); }
.arch-tier { margin-bottom:24px; }
.arch-tier-heading { font-size:.68rem; letter-spacing:.08em; text-transform:uppercase; opacity:.58; margin-bottom:7px; }
.arch-component-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:8px; }
.arch-component-card { text-align:left; color:inherit; border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:11px; cursor:pointer; }
.arch-component-card:hover { background:var(--arch-card-strong); }
.arch-component-card p { min-height:48px; margin:6px 0 8px; font-size:.73rem; line-height:1.45; opacity:.68; }
.arch-card-metrics { display:flex; flex-wrap:wrap; gap:10px; font-size:.64rem; opacity:.65; }
.arch-file-list { max-height:260px; overflow:auto; border-top:1px solid var(--mm-line, #334); margin-top:9px; padding-top:7px; font-family:monospace; font-size:.61rem; opacity:.72; }
.arch-file-list div { margin:2px 0; }
.arch-callout { border:1px solid var(--mm-line, #334); padding:9px 10px; margin:10px 0; font-size:.73rem; }
.arch-callout.warn { color:var(--mm-warn, #f1b86a); }
.arch-callout.good { color:var(--mm-good, #70d6a0); }
.arch-violation { border:1px solid var(--mm-line, #334); border-left:3px solid var(--mm-warn, #f1b86a); background:var(--arch-card); padding:12px; margin-bottom:10px; }
.arch-violation .arch-card-title > div { display:flex; align-items:center; gap:8px; }
.arch-violation > p { font-size:.76rem; opacity:.72; margin:8px 0; }
.arch-hit-count { color:var(--mm-warn, #f1b86a); font-size:.72rem; }
.arch-hit-list { display:flex; flex-direction:column; gap:3px; }
.arch-hit-list > div, .arch-route-callers > div { display:grid; grid-template-columns:minmax(220px, .8fr) minmax(180px, 1fr); gap:9px; border-top:1px solid var(--mm-line, #334); padding:5px 0; font-size:.66rem; }
.arch-hit-list code, .arch-route-callers code { overflow-wrap:anywhere; }
.arch-hit-list span, .arch-route-callers span { opacity:.65; }
.arch-unchecked { margin-bottom:10px; }
.arch-unchecked p { margin:3px 0; }
.arch-cycle-list details { border-top:1px solid var(--mm-line, #334); padding:6px 0; font-size:.67rem; }
.arch-cycle-list code { display:block; white-space:normal; overflow-wrap:anywhere; opacity:.65; margin-top:5px; }
.arch-route-columns { display:grid; grid-template-columns:minmax(260px, .7fr) minmax(420px, 1.4fr); gap:18px; margin-top:18px; }
.arch-route-list { columns:2; column-gap:14px; }
.arch-route-list code { display:block; break-inside:avoid; font-size:.62rem; padding:2px 0; opacity:.72; overflow-wrap:anywhere; }
.arch-route-callers { max-height:760px; overflow:auto; }
.arch-context-section, .arch-deployment-section { margin:24px 0 30px; }
.arch-context-map { display:grid; grid-template-columns:minmax(240px, .7fr) minmax(520px, 1.7fr); gap:12px; align-items:stretch; }
.arch-context-system { display:flex; flex-direction:column; justify-content:center; border:2px solid var(--mm-accent-text, #8fc7ff); background:rgba(34, 91, 140, .12); padding:18px; }
.arch-context-system > span, .arch-boundary-note > span { color:var(--mm-accent-text, #8fc7ff); font-size:.62rem; letter-spacing:.1em; text-transform:uppercase; }
.arch-context-system h4 { font-size:1.1rem; margin:8px 0; }
.arch-context-system p, .arch-context-system small { font-size:.75rem; line-height:1.5; opacity:.76; }
.arch-context-participants { display:grid; grid-template-columns:repeat(2, minmax(230px, 1fr)); gap:8px; }
.arch-context-participants article { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:10px; }
.arch-context-participants h4 { margin:7px 0 5px; }
.arch-context-participants p, .arch-context-participants small { font-size:.68rem; line-height:1.4; opacity:.7; }
.arch-context-relationships { display:grid; grid-template-columns:repeat(auto-fill, minmax(310px, 1fr)); gap:1px; border:1px solid var(--mm-line, #334); background:var(--mm-line, #334); margin-top:10px; }
.arch-context-relationships > div { background:var(--arch-card-strong); padding:9px; }
.arch-context-relationships > div > div { display:flex; gap:7px; align-items:center; font-size:.69rem; }
.arch-context-relationships p { margin:5px 0 2px; font-size:.7rem; }
.arch-context-relationships small { display:block; opacity:.55; font-size:.61rem; line-height:1.4; }
.arch-deployment-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(330px, 1fr)); gap:10px; }
.arch-deployment-card, .arch-domain-card { border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:12px; min-width:0; }
.arch-deployment-card h4, .arch-domain-card h4, .arch-decision-card h4 { margin:5px 0 0; }
.arch-deployment-card > p, .arch-domain-card > p, .arch-decision-card > div > p { font-size:.73rem; line-height:1.5; opacity:.72; }
.arch-boundary-note { border-left:2px solid var(--mm-accent-text, #8fc7ff); padding:6px 0 6px 9px; margin:9px 0; background:rgba(34, 91, 140, .07); }
.arch-boundary-note p { margin:4px 0 0; font-size:.69rem; line-height:1.45; opacity:.72; }
.arch-node-pills, .arch-decision-context { display:flex; flex-wrap:wrap; gap:4px; margin:9px 0; }
.arch-failure-list details, .arch-domain-card details, .arch-decision-card details { border-top:1px solid var(--mm-line, #334); padding:7px 0; font-size:.68rem; }
.arch-failure-list summary, .arch-domain-card summary, .arch-decision-card summary { cursor:pointer; }
.arch-failure-list dl { display:grid; grid-template-columns:70px 1fr; gap:5px 8px; margin:8px 0 0; }
.arch-failure-list dt { opacity:.52; text-transform:uppercase; }
.arch-failure-list dd { margin:0; opacity:.76; line-height:1.4; }
.arch-domain-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(390px, 1fr)); gap:10px; }
.arch-domain-card ul, .arch-decision-card ul { margin:8px 0 0; padding-left:18px; }
.arch-domain-card li, .arch-decision-card li { margin:3px 0; line-height:1.4; opacity:.75; }
.arch-contract-list > div { border-top:1px solid var(--mm-line, #334); padding:6px 0; }
.arch-contract-list > div:first-child { border-top:0; }
.arch-contract-list p { margin:3px 0 0; line-height:1.4; opacity:.7; }
.arch-decision-list { display:flex; flex-direction:column; gap:9px; }
.arch-decision-card { display:grid; grid-template-columns:78px 1fr; gap:12px; border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:12px; }
.arch-decision-id { color:var(--mm-accent-text, #8fc7ff); font-family:monospace; font-size:.76rem; border-right:1px solid var(--mm-line, #334); padding-right:10px; }
.arch-decision-columns { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.arch-record-path { font-family:monospace; font-size:.62rem; opacity:.56; margin-top:8px; overflow-wrap:anywhere; }
.arch-generated-flow { margin:12px 0; border:1px solid var(--mm-line, #334); background:var(--arch-card); padding:9px; }
.arch-generated-flow-label { font-size:.61rem; letter-spacing:.09em; text-transform:uppercase; opacity:.5; margin-bottom:7px; }
.arch-generated-flow-rail { display:flex; align-items:stretch; gap:5px; overflow-x:auto; padding-bottom:3px; }
.arch-generated-hop { min-width:150px; max-width:210px; border:1px solid var(--mm-line, #334); background:var(--arch-card-strong); padding:8px; }
.arch-generated-hop > span { color:var(--mm-accent-text, #8fc7ff); font-size:.6rem; }
.arch-generated-hop > strong, .arch-generated-hop > small { display:block; }
.arch-generated-hop > strong { font-size:.69rem; margin:4px 0; }
.arch-generated-hop > small { font-size:.6rem; opacity:.58; line-height:1.35; }
.arch-generated-arrow { align-self:center; opacity:.4; }
@media (max-width: 980px) {
  .arch-stats { grid-template-columns:repeat(3, 1fr); }
  .arch-system-hero, .arch-process-layout, .arch-route-columns, .arch-context-map { grid-template-columns:1fr; }
  .arch-process-index { border-right:0; border-bottom:1px solid var(--mm-line, #334); padding:0 0 12px; }
  .arch-process-list { max-height:260px; }
  .arch-review-notes { grid-template-columns:1fr; }
}
@media (max-width: 640px) {
  .arch-title-row { flex-direction:column; }
  .arch-review-stamp { width:100%; box-sizing:border-box; text-align:left; }
  .arch-stats, .arch-stats.compact, .arch-process-facts, .arch-review-notes.two { grid-template-columns:1fr 1fr; }
  .arch-section-intro { flex-direction:column; }
  .arch-step-field { grid-template-columns:1fr; }
  .arch-route-list { columns:1; }
  .arch-context-participants, .arch-domain-grid, .arch-deployment-grid, .arch-decision-columns, .arch-engine-truth-grid { grid-template-columns:1fr; }
  .arch-decision-card { grid-template-columns:1fr; }
  .arch-decision-id { border-right:0; border-bottom:1px solid var(--mm-line, #334); padding:0 0 7px; }
}
`;
