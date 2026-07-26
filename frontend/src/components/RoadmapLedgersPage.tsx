import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  createRoadmapLedger, getRoadmapLedger, listRoadmapAdmissions,
  listRoadmapLedgers, reconcileRoadmapNode, saveRoadmapLedger,
  type RoadmapAdmissionEvent,
  type RoadmapAssumption,
  type RoadmapAssumptionStatus, type RoadmapHorizon, type RoadmapLedger,
  type RoadmapLedgerSummary, type RoadmapNode, type RoadmapReviewCadence,
  type RoadmapSection, type RoadmapState, type RoadmapStatus,
} from '../api';
import './RoadmapLedgersPage.css';


const STATUS_META: Record<RoadmapStatus, { label: string; color: string }> = {
  done: { label: 'Done', color: '#22c55e' },
  active: { label: 'Active', color: 'var(--accent)' },
  'in-progress': { label: 'In progress', color: '#f97316' },
  planned: { label: 'Planned', color: 'var(--amber)' },
  proposed: { label: 'Proposed', color: '#60a5fa' },
  unblocked: { label: 'Unblocked', color: '#a855f7' },
  bug: { label: 'Bug', color: 'var(--red)' },
  deprioritized: { label: 'Deprioritized', color: 'var(--text-dim)' },
  polish: { label: 'Polish', color: '#14b8a6' },
  conceptual: { label: 'Conceptual', color: '#8b5cf6' },
  cancelled: { label: 'Cancelled', color: 'var(--red)' },
};

const HORIZONS: Array<{
  key: RoadmapHorizon;
  short: string;
  label: string;
  description: string;
}> = [
  { key: 'thesis', short: 'TH', label: 'Thesis', description: 'Enduring direction' },
  { key: 'horizon-3', short: 'H3', label: 'Possible', description: 'Future capability' },
  { key: 'horizon-2', short: 'H2', label: 'Bet', description: 'Validated strategic bet' },
  { key: 'horizon-1', short: 'H1', label: 'Program', description: 'Funded, measurable work' },
  { key: 'active', short: 'NOW', label: 'Active', description: 'Execution resolution' },
];

const ASSUMPTION_STATUSES: Array<{ key: RoadmapAssumptionStatus; label: string }> = [
  { key: 'standing', label: 'Standing' },
  { key: 'supported', label: 'Supported' },
  { key: 'challenged', label: 'Challenged' },
  { key: 'invalidated', label: 'Invalidated' },
];

const REVIEW_CADENCES: Array<{ key: RoadmapReviewCadence; label: string }> = [
  { key: 'monthly', label: 'Monthly' },
  { key: 'quarterly', label: 'Quarterly' },
  { key: 'semiannual', label: 'Semiannual' },
  { key: 'annual', label: 'Annual' },
  { key: 'event', label: 'Event triggered' },
  { key: 'manual', label: 'Manual' },
];

type Readiness = 'complete' | 'ready' | 'blocked' | 'dropped';
type ReviewSignal = 'due' | 'upcoming' | 'current' | 'unscheduled' | 'event' | 'manual' | 'retired';
type Dialog = 'ledger' | 'record' | 'section' | 'reconcile' | null;

function asDate(value: unknown): string {
  if (typeof value !== 'string' || !value) return '—';
  const parsed = new Date(value.length === 10 ? `${value}T12:00:00` : value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
  }).format(parsed);
}

function dateAfterMonths(months: number): string {
  const value = new Date();
  const originalDay = value.getDate();
  value.setDate(1);
  value.setMonth(value.getMonth() + months);
  const finalDay = new Date(value.getFullYear(), value.getMonth() + 1, 0).getDate();
  value.setDate(Math.min(originalDay, finalDay));
  return value.toISOString().slice(0, 10);
}

function reviewSignal(node: RoadmapNode): ReviewSignal {
  if (['done', 'deprioritized', 'cancelled'].includes(node.status)) return 'retired';
  if (!node.nextReviewAt) {
    if (node.reviewCadence === 'event') return 'event';
    if (node.reviewCadence === 'manual') return 'manual';
    return 'unscheduled';
  }
  const target = new Date(node.nextReviewAt.length === 10
    ? `${node.nextReviewAt}T23:59:59`
    : node.nextReviewAt);
  if (Number.isNaN(target.valueOf())) return 'unscheduled';
  const now = new Date();
  if (target <= now) return 'due';
  if (target.valueOf() <= now.valueOf() + 30 * 24 * 60 * 60 * 1000) return 'upcoming';
  return 'current';
}

function copyText(value: unknown): string {
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return value.map(copyText).join('\n');
  return JSON.stringify(value, null, 2);
}

function searchable(node: RoadmapNode): string {
  return JSON.stringify(node).toLowerCase();
}

function horizonMeta(value?: RoadmapHorizon) {
  return HORIZONS.find(item => item.key === value);
}

function CopyButton({ value, label = 'Copy' }: { value: unknown; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(copyText(value));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  };
  return (
    <button className="rl-copy" type="button" onClick={() => void copy()}>
      {copied ? 'Copied' : label}
    </button>
  );
}

function DetailBlock({ label, value, wide = false }: {
  label: string;
  value: unknown;
  wide?: boolean;
}) {
  if (value == null || value === '' || (Array.isArray(value) && !value.length)) return null;
  return (
    <section className={`rl-detail-block${wide ? ' wide' : ''}`}>
      <header><span>{label}</span><CopyButton value={value} /></header>
      {Array.isArray(value)
        ? <ol>{value.map((item, index) => <li key={index}>{copyText(item)}</li>)}</ol>
        : <p>{copyText(value)}</p>}
    </section>
  );
}

function LedgerDialog({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (ledger: RoadmapLedger) => void;
}) {
  const [form, setForm] = useState({ name: '', description: '', project_path: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onCreated(await createRoadmapLedger({
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        project_path: form.project_path.trim() || undefined,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rl-dialog-shade" role="presentation" onMouseDown={event => {
      if (event.currentTarget === event.target) onClose();
    }}>
      <form className="rl-dialog" onSubmit={event => void submit(event)}>
        <header>
          <div><span>NEW PROJECT REGISTER</span><h3>Create roadmap ledger</h3></div>
          <button type="button" onClick={onClose}>×</button>
        </header>
        <label>
          <span>Project name</span>
          <input required autoFocus value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Bounty Hunter" />
        </label>
        <label>
          <span>Repository path <small>optional</small></span>
          <input value={form.project_path} onChange={e => setForm({ ...form, project_path: e.target.value })} placeholder="/home/tylerbvogel/Projects/…" />
        </label>
        <label>
          <span>Mandate <small>optional</small></span>
          <textarea value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="What this project exists to accomplish." />
        </label>
        {error && <p className="rl-form-error">{error}</p>}
        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !form.name.trim()}>{busy ? 'Creating…' : 'Create ledger'}</button>
        </footer>
      </form>
    </div>
  );
}

function SectionDialog({ state, onClose, onSave }: {
  state: RoadmapState;
  onClose: () => void;
  onSave: (section: RoadmapSection) => void;
}) {
  const [form, setForm] = useState({ id: '', label: '', color: '#3987e5' });
  const [error, setError] = useState('');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const id = form.id.trim().toLowerCase()
      .replace(/[^a-z0-9._:-]+/g, '-').replace(/^-|-$/g, '');
    if (!id) return setError('Section id needs at least one letter or number.');
    if (state.sections.some(section => section.id === id)) {
      return setError('That section id already exists.');
    }
    onSave({ id, label: form.label.trim(), color: form.color });
  };
  return (
    <div className="rl-dialog-shade">
      <form className="rl-dialog compact" onSubmit={submit}>
        <header>
          <div><span>STRUCTURE</span><h3>Add section</h3></div>
          <button type="button" onClick={onClose}>×</button>
        </header>
        <label><span>Section id</span><input required autoFocus value={form.id} onChange={e => setForm({ ...form, id: e.target.value })} placeholder="engine" /></label>
        <label><span>Label</span><input required value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} placeholder="Engine" /></label>
        <label><span>Signal color</span><input type="color" value={form.color} onChange={e => setForm({ ...form, color: e.target.value })} /></label>
        {error && <p className="rl-form-error">{error}</p>}
        <footer><button type="button" onClick={onClose}>Cancel</button><button className="primary">Add section</button></footer>
      </form>
    </div>
  );
}

function RecordDialog({ state, node, onClose, onSave }: {
  state: RoadmapState;
  node: RoadmapNode | null;
  onClose: () => void;
  onSave: (node: RoadmapNode) => void;
}) {
  const [form, setForm] = useState({
    id: node?.id ?? '',
    label: node?.label ?? '',
    section: node?.section ?? state.sections[0]?.id ?? '',
    status: node?.status ?? 'planned' as RoadmapStatus,
    horizon: node?.horizon ?? 'horizon-1' as RoadmapHorizon,
    summary: node?.summary ?? '',
    prompt: node?.prompt ?? '',
    prereqs: (node?.prereqs ?? []).join(', '),
    verification: (node?.verification ?? []).join('\n'),
    reviewCadence: node?.reviewCadence ?? 'quarterly' as RoadmapReviewCadence,
    nextReviewAt: node
      ? (node.nextReviewAt?.slice(0, 10) ?? '')
      : dateAfterMonths(3),
  });
  const [assumptions, setAssumptions] = useState<RoadmapAssumption[]>(
    node?.assumptions ?? [],
  );
  const [error, setError] = useState('');

  const patchAssumption = (index: number, patch: Partial<RoadmapAssumption>) => {
    setAssumptions(current => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, ...patch } : item
    )));
  };
  const addAssumption = () => setAssumptions(current => [...current, {
    id: `assumption-${Date.now().toString(36)}`,
    statement: '',
    status: 'standing',
    confidence: 50,
    evidenceFor: [],
    evidenceAgainst: [],
  }]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const id = form.id.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/.test(id)) {
      return setError('Record id contains unsupported characters.');
    }
    if (!node && state.nodes.some(item => item.id === id)) {
      return setError('That record id already exists.');
    }
    const prereqs = form.prereqs.split(',').map(value => value.trim()).filter(Boolean);
    const missing = prereqs.find(value => !state.nodes.some(item => item.id === value));
    if (missing) return setError(`Unknown prerequisite: ${missing}`);
    if (prereqs.includes(id)) return setError('A record cannot depend on itself.');
    const incompleteAssumption = assumptions.find(item => !item.statement.trim());
    if (incompleteAssumption) return setError('Every assumption needs a statement.');

    const next: RoadmapNode = {
      ...(node ?? {}),
      id,
      label: form.label.trim(),
      section: form.section,
      status: form.status,
      horizon: form.horizon,
      summary: form.summary.trim() || undefined,
      prompt: form.prompt.trim() || undefined,
      prereqs,
      verification: form.verification.split('\n').map(value => value.trim()).filter(Boolean),
      assumptions: assumptions.map(item => ({
        ...item,
        statement: item.statement.trim(),
        invalidationTrigger: item.invalidationTrigger?.trim() || undefined,
        consequence: item.consequence?.trim() || undefined,
        evidenceFor: item.evidenceFor?.filter(Boolean),
        evidenceAgainst: item.evidenceAgainst?.filter(Boolean),
      })),
      reviewCadence: form.reviewCadence,
      nextReviewAt: form.nextReviewAt || undefined,
    };
    onSave(next);
  };

  return (
    <div className="rl-dialog-shade">
      <form className="rl-dialog record strategic" onSubmit={submit}>
        <header>
          <div><span>STRATEGIC RECORD</span><h3>{node ? 'Edit record' : 'Add record'}</h3></div>
          <button type="button" onClick={onClose}>×</button>
        </header>
        <div className="rl-form-grid">
          <label>
            <span>Record id</span>
            <input required autoFocus={!node} disabled={Boolean(node)} value={form.id} onChange={e => setForm({ ...form, id: e.target.value })} placeholder="eng-recall-v2" />
          </label>
          <label>
            <span>Status</span>
            <select value={form.status} onChange={e => setForm({ ...form, status: e.target.value as RoadmapStatus })}>
              {Object.entries(STATUS_META).map(([key, meta]) => <option key={key} value={key}>{meta.label}</option>)}
            </select>
          </label>
          <label>
            <span>Section</span>
            <select value={form.section} onChange={e => setForm({ ...form, section: e.target.value })}>
              {state.sections.map(section => <option key={section.id} value={section.id}>{section.label}</option>)}
            </select>
          </label>
          <label>
            <span>Planning horizon</span>
            <select value={form.horizon} onChange={e => setForm({ ...form, horizon: e.target.value as RoadmapHorizon })}>
              {HORIZONS.map(item => <option key={item.key} value={item.key}>{item.short} · {item.label}</option>)}
            </select>
          </label>
          <label>
            <span>Review cadence</span>
            <select value={form.reviewCadence} onChange={e => setForm({ ...form, reviewCadence: e.target.value as RoadmapReviewCadence })}>
              {REVIEW_CADENCES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span>Next review</span>
            <input type="date" value={form.nextReviewAt} onChange={e => setForm({ ...form, nextReviewAt: e.target.value })} />
          </label>
          <label className="wide">
            <span>Work item</span>
            <input required value={form.label} onChange={e => setForm({ ...form, label: e.target.value })} placeholder="What needs to exist" />
          </label>
          <label className="wide"><span>Summary</span><textarea value={form.summary} onChange={e => setForm({ ...form, summary: e.target.value })} /></label>
          <label className="wide"><span>Prerequisites <small>comma-separated record ids</small></span><input value={form.prereqs} onChange={e => setForm({ ...form, prereqs: e.target.value })} /></label>
        </div>

        <section className="rl-assumption-editor">
          <header>
            <div><span>ASSUMPTION REGISTER</span><small>Make the beliefs beneath the plan falsifiable.</small></div>
            <button type="button" onClick={addAssumption}>Add assumption</button>
          </header>
          {!assumptions.length && (
            <p>No assumptions recorded. Delivery can still be planned, but strategic review requires at least one explicit belief.</p>
          )}
          {assumptions.map((assumption, index) => (
            <article key={assumption.id}>
              <div className="rl-assumption-editor-head">
                <code>{assumption.id}</code>
                <button type="button" onClick={() => setAssumptions(current => current.filter((_, itemIndex) => itemIndex !== index))}>Remove</button>
              </div>
              <label className="wide">
                <span>What must remain true</span>
                <textarea required value={assumption.statement} onChange={e => patchAssumption(index, { statement: e.target.value })} />
              </label>
              <div className="rl-form-grid two">
                <label>
                  <span>Evidence state</span>
                  <select value={assumption.status} onChange={e => patchAssumption(index, { status: e.target.value as RoadmapAssumptionStatus })}>
                    {ASSUMPTION_STATUSES.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
                  </select>
                </label>
                <label>
                  <span>Confidence · {assumption.confidence}%</span>
                  <input type="range" min={0} max={100} value={assumption.confidence} onChange={e => patchAssumption(index, { confidence: Number(e.target.value) })} />
                </label>
                <label>
                  <span>Supporting evidence <small>one item per line</small></span>
                  <textarea value={(assumption.evidenceFor ?? []).join('\n')} onChange={e => patchAssumption(index, { evidenceFor: e.target.value.split('\n').map(value => value.trim()).filter(Boolean) })} />
                </label>
                <label>
                  <span>Contradicting evidence <small>one item per line</small></span>
                  <textarea value={(assumption.evidenceAgainst ?? []).join('\n')} onChange={e => patchAssumption(index, { evidenceAgainst: e.target.value.split('\n').map(value => value.trim()).filter(Boolean) })} />
                </label>
                <label>
                  <span>Invalidation trigger</span>
                  <textarea value={assumption.invalidationTrigger ?? ''} onChange={e => patchAssumption(index, { invalidationTrigger: e.target.value })} />
                </label>
                <label>
                  <span>Consequence if wrong</span>
                  <textarea value={assumption.consequence ?? ''} onChange={e => patchAssumption(index, { consequence: e.target.value })} />
                </label>
              </div>
            </article>
          ))}
        </section>

        <div className="rl-form-grid">
          <label className="wide"><span>Kickoff prompt / implementation context</span><textarea className="tall" value={form.prompt} onChange={e => setForm({ ...form, prompt: e.target.value })} /></label>
          <label className="wide"><span>Verification checklist <small>one check per line</small></span><textarea value={form.verification} onChange={e => setForm({ ...form, verification: e.target.value })} /></label>
        </div>
        {error && <p className="rl-form-error">{error}</p>}
        <footer><button type="button" onClick={onClose}>Cancel</button><button className="primary">{node ? 'Save record' : 'Add record'}</button></footer>
      </form>
    </div>
  );
}

function ReconciliationDialog({ ledger, node, onClose, onSaved }: {
  ledger: RoadmapLedger;
  node: RoadmapNode;
  onClose: () => void;
  onSaved: (ledger: RoadmapLedger) => void;
}) {
  const [form, setForm] = useState({
    disposition: 'partial' as 'complete' | 'partial' | 'failed' | 'blocked',
    resultRecap: '',
    verificationPassed: false,
    confidence: 80,
    claims: '',
    limitations: '',
    disclosures: '',
    evidence: '',
    verifier: '',
    acceptedBy: 'tyler',
    nextAction: '',
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const lines = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onSaved(await reconcileRoadmapNode(ledger.slug, node.id, {
        expected_revision: ledger.revision,
        disposition: form.disposition,
        result_recap: form.resultRecap.trim(),
        verification_passed: form.verificationPassed,
        confidence: form.confidence / 100,
        claims: lines(form.claims),
        limitations: lines(form.limitations),
        disclosures: lines(form.disclosures),
        evidence: lines(form.evidence),
        verifier: form.verifier.trim(),
        accepted_by: form.acceptedBy.trim(),
        next_action: form.nextAction.trim() || undefined,
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rl-dialog-shade">
      <form className="rl-dialog" onSubmit={event => void submit(event)}>
        <header>
          <div>
            <span>HARNESS RETURN → HUMAN ACCEPTANCE</span>
            <h3>Reconcile verified outcome</h3>
          </div>
          <button type="button" onClick={onClose}>×</button>
        </header>
        <div className="rl-reconcile-record">
          <code>{node.id}</code>
          <strong>{node.label}</strong>
          <span>Pinned to ledger revision {ledger.revision} · source v{ledger.state.version}</span>
        </div>
        <div className="rl-form-grid two">
          <label>
            <span>Disposition</span>
            <select value={form.disposition} onChange={e => setForm({ ...form, disposition: e.target.value as typeof form.disposition })}>
              <option value="complete">Complete</option>
              <option value="partial">Partial</option>
              <option value="failed">Failed</option>
              <option value="blocked">Blocked</option>
            </select>
          </label>
          <label>
            <span>Confidence</span>
            <input type="number" min={0} max={100} value={form.confidence} onChange={e => setForm({ ...form, confidence: Number(e.target.value) })} />
          </label>
        </div>
        <label className="rl-check"><input type="checkbox" checked={form.verificationPassed} onChange={e => setForm({ ...form, verificationPassed: e.target.checked })} /><span>Independent verifier passed the recorded checklist</span></label>
        <label><span>Result recap</span><textarea required className="tall" value={form.resultRecap} onChange={e => setForm({ ...form, resultRecap: e.target.value })} /></label>
        <div className="rl-form-grid two">
          <label><span>Verifier</span><input required value={form.verifier} onChange={e => setForm({ ...form, verifier: e.target.value })} placeholder="qa-agent / eval run / reviewer" /></label>
          <label><span>Accepted by</span><input required value={form.acceptedBy} onChange={e => setForm({ ...form, acceptedBy: e.target.value })} /></label>
        </div>
        <div className="rl-form-grid two">
          <label><span>Claims <small>one per line</small></span><textarea value={form.claims} onChange={e => setForm({ ...form, claims: e.target.value })} /></label>
          <label><span>Evidence <small>one receipt per line</small></span><textarea required={form.verificationPassed && form.disposition === 'complete'} value={form.evidence} onChange={e => setForm({ ...form, evidence: e.target.value })} /></label>
          <label><span>Limitations <small>one per line</small></span><textarea value={form.limitations} onChange={e => setForm({ ...form, limitations: e.target.value })} /></label>
          <label><span>Disclosures <small>one per line</small></span><textarea value={form.disclosures} onChange={e => setForm({ ...form, disclosures: e.target.value })} /></label>
        </div>
        <label><span>Next action <small>optional</small></span><textarea value={form.nextAction} onChange={e => setForm({ ...form, nextAction: e.target.value })} /></label>
        <DetailBlock label="Recorded verification checklist" value={node.verification} wide />
        <p className="rl-reconcile-note">
          The harness decides who acts and what permissions it receives. This receipt records only durable claims, evidence, limitations, independent verification, and human acceptance. A record closes only when disposition is complete and verification passed.
        </p>
        {error && <p className="rl-form-error">{error}</p>}
        <footer>
          <button type="button" onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !form.resultRecap.trim() || !form.verifier.trim() || !form.acceptedBy.trim()}>
            {busy ? 'Reconciling…' : 'Accept receipt'}
          </button>
        </footer>
      </form>
    </div>
  );
}

function AssumptionRegister({ assumptions }: { assumptions: RoadmapAssumption[] }) {
  if (!assumptions.length) return null;
  return (
    <section className="rl-assumption-register">
      <header>
        <span>ASSUMPTION REGISTER / {assumptions.length}</span>
        <small>Beliefs are evidence states, not hidden premises.</small>
      </header>
      <div>
        {assumptions.map(assumption => (
          <article key={assumption.id} className={assumption.status}>
            <header>
              <code>{assumption.id}</code>
              <span>{assumption.status}</span>
              <strong>{assumption.confidence}%</strong>
            </header>
            <p>{assumption.statement}</p>
            {assumption.invalidationTrigger && <dl><dt>Invalid when</dt><dd>{assumption.invalidationTrigger}</dd></dl>}
            {assumption.consequence && <dl><dt>If wrong</dt><dd>{assumption.consequence}</dd></dl>}
            {(assumption.evidenceFor?.length ?? 0) > 0 && <dl><dt>Supports</dt><dd>{assumption.evidenceFor?.join(' · ')}</dd></dl>}
            {(assumption.evidenceAgainst?.length ?? 0) > 0 && <dl><dt>Contradicts</dt><dd>{assumption.evidenceAgainst?.join(' · ')}</dd></dl>}
          </article>
        ))}
      </div>
    </section>
  );
}

export default function RoadmapLedgersPage() {
  const [ledgers, setLedgers] = useState<RoadmapLedgerSummary[]>([]);
  const [selected, setSelected] = useState(
    () => localStorage.getItem('corvus-roadmap-ledger') ?? '',
  );
  const [ledger, setLedger] = useState<RoadmapLedger | null>(null);
  const [admissions, setAdmissions] = useState<RoadmapAdmissionEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [dialog, setDialog] = useState<Dialog>(null);
  const [editing, setEditing] = useState<RoadmapNode | null>(null);
  const [reconciling, setReconciling] = useState<RoadmapNode | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<RoadmapStatus | 'all'>('all');
  const [section, setSection] = useState('all');
  const [horizon, setHorizon] = useState<RoadmapHorizon | 'all' | 'unclassified'>('all');
  const [reviewDueOnly, setReviewDueOnly] = useState(false);
  const [readyOnly, setReadyOnly] = useState(false);

  const loadList = async (prefer?: string) => {
    const rows = await listRoadmapLedgers();
    setLedgers(rows);
    const wanted = prefer || selected;
    const next = rows.some(row => row.slug === wanted) ? wanted : rows[0]?.slug ?? '';
    if (next !== selected) setSelected(next);
    if (!next) {
      setLedger(null);
      setLoading(false);
    }
    return next;
  };

  useEffect(() => {
    loadList().catch(reason => {
      setError(reason instanceof Error ? reason.message : String(reason));
      setLoading(false);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    setAdmissions([]);
    localStorage.setItem('corvus-roadmap-ledger', selected);
    Promise.all([
      getRoadmapLedger(selected),
      listRoadmapAdmissions(selected),
    ])
      .then(([value, admissionEvents]) => {
        if (!cancelled) {
          setLedger(value);
          setAdmissions(admissionEvents);
        }
      })
      .catch(reason => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const nodeById = useMemo(
    () => new Map(ledger?.state.nodes.map(node => [node.id, node]) ?? []),
    [ledger],
  );

  const readinessFor = (node: RoadmapNode): Readiness => {
    if (node.status === 'done') return 'complete';
    if (node.status === 'cancelled' || node.status === 'deprioritized') return 'dropped';
    return (node.prereqs ?? []).every(id => nodeById.get(id)?.status === 'done')
      ? 'ready'
      : 'blocked';
  };

  const filtered = useMemo(() => {
    if (!ledger) return [];
    const query = search.trim().toLowerCase();
    return ledger.state.nodes.filter(node => {
      if (status !== 'all' && node.status !== status) return false;
      if (section === '__deferred' && !node.deferred) return false;
      if (section !== 'all' && section !== '__deferred' && node.section !== section) return false;
      if (horizon === 'unclassified' && node.horizon) return false;
      if (horizon !== 'all' && horizon !== 'unclassified' && node.horizon !== horizon) return false;
      if (reviewDueOnly && reviewSignal(node) !== 'due') return false;
      if (readyOnly && readinessFor(node) !== 'ready') return false;
      return !query || searchable(node).includes(query);
    });
  }, [
    ledger, search, status, section, horizon, reviewDueOnly, readyOnly, nodeById,
  ]); // eslint-disable-line react-hooks/exhaustive-deps

  const commit = async (nextState: RoadmapState) => {
    if (!ledger || saving) return;
    setSaving(true);
    setError('');
    try {
      const saved = await saveRoadmapLedger(ledger.slug, ledger.revision, nextState);
      setLedger(saved);
      await loadList(saved.slug);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      if (message.includes('changed since')) {
        setLedger(await getRoadmapLedger(ledger.slug));
      }
    } finally {
      setSaving(false);
    }
  };

  const saveRecord = (record: RoadmapNode) => {
    if (!ledger) return;
    const exists = ledger.state.nodes.some(node => node.id === record.id);
    const nodes = exists
      ? ledger.state.nodes.map(node => node.id === record.id ? record : node)
      : [...ledger.state.nodes, record];
    setDialog(null);
    setEditing(null);
    void commit({ ...ledger.state, nodes });
  };

  const changeStatus = (node: RoadmapNode, nextStatus: RoadmapStatus) => {
    if (!ledger) return;
    const next = {
      ...node,
      status: nextStatus,
      completedAt: nextStatus === 'done'
        ? (typeof node.completedAt === 'string'
          ? node.completedAt
          : new Date().toISOString())
        : undefined,
    };
    void commit({
      ...ledger.state,
      nodes: ledger.state.nodes.map(item => item.id === node.id ? next : item),
    });
  };

  const toggle = (id: string) => setExpanded(current => {
    const next = new Set(current);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });

  const openReconciliation = (node: RoadmapNode) => {
    setReconciling(node);
    setDialog('reconcile');
  };

  const created = (value: RoadmapLedger) => {
    setDialog(null);
    setLedger(value);
    setSelected(value.slug);
    void loadList(value.slug);
  };

  const reconciliationSaved = (saved: RoadmapLedger) => {
    setDialog(null);
    setReconciling(null);
    setLedger(saved);
    void loadList(saved.slug);
  };

  const clearFilters = () => {
    setSearch('');
    setStatus('all');
    setSection('all');
    setHorizon('all');
    setReviewDueOnly(false);
    setReadyOnly(false);
  };

  if (loading && !ledger) {
    return <div className="rl-state">Opening roadmap ledgers…</div>;
  }

  return (
    <div className="rl-root">
      <aside className="rl-projects">
        <header>
          <span>PROJECT MEMORY</span>
          <button type="button" onClick={() => setDialog('ledger')}>＋</button>
        </header>
        <div className="rl-project-list">
          {ledgers.map(item => (
            <button key={item.slug} className={selected === item.slug ? 'active' : ''} type="button" onClick={() => setSelected(item.slug)}>
              <span>
                <strong>{item.name}</strong>
                <small>{item.summary.records} records · {item.summary.reviews_due} reviews due · r{item.revision}</small>
              </span>
              <i><b style={{ width: `${item.summary.completion}%` }} /></i>
            </button>
          ))}
          {!ledgers.length && <p>No ledgers yet. Create the first long-horizon register.</p>}
        </div>
        <button className="rl-new-project" type="button" onClick={() => setDialog('ledger')}>New project ledger</button>
      </aside>

      <main className="rl-ledger">
        {error && (
          <div className="rl-alert">
            <span>{error}</span>
            <button type="button" onClick={() => setError('')}>×</button>
          </div>
        )}
        {!ledger ? (
          <div className="rl-empty-project">
            <span>NO PROJECT SELECTED</span>
            <h2>Give the future somewhere durable to live.</h2>
            <button type="button" onClick={() => setDialog('ledger')}>Create a ledger</button>
          </div>
        ) : (
          <>
            <header className="rl-hero">
              <div>
                <p className="rl-kicker">PALLIUM / STRATEGY & EXECUTION REGISTER</p>
                <h1>{ledger.name} <em>Ledger</em></h1>
                <p>{ledger.description || 'Preserve direction, expose assumptions, and reconcile verified outcomes without letting execution silently rewrite the plan.'}</p>
                {ledger.project_path && <code>{ledger.project_path}</code>}
              </div>
              <div className="rl-release">
                <span>Source record</span>
                <strong>v{ledger.state.version}</strong>
                <small>Revision {ledger.revision} · {asDate(ledger.state.updatedAt)}</small>
              </div>
            </header>

            <section className="rl-instruments unified">
              <div className="rl-progress">
                <strong>{ledger.summary.completion}%</strong><span>delivered</span>
                <i><b style={{ width: `${ledger.summary.completion}%` }} /></i>
                <small>{ledger.summary.done} of {ledger.summary.in_scope} in scope complete{ledger.summary.out_of_scope ? ` · ${ledger.summary.out_of_scope} dropped` : ''}</small>
              </div>
              <div><strong>{ledger.summary.moving}</strong><span>In motion</span><small>Active or in progress</small></div>
              <div><strong>{ledger.state.nodes.filter(node => readinessFor(node) === 'ready').length}</strong><span>Ready now</span><small>No unmet prerequisites</small></div>
              <div><strong>{ledger.state.nodes.reduce((total, node) => total + (node.reconciliationHistory?.length ?? 0), 0)}</strong><span>Accepted receipts</span><small>Evidence reconciled into durable intent</small></div>
              <div><strong>{ledger.state.edges.length}</strong><span>Relations</span><small>Dependency paths retained</small></div>
            </section>

            <section className="rl-admission-controller">
              <header>
                <div>
                  <p className="rl-kicker">SESSION ADMISSION CONTROLLER</p>
                  <h2>Planning is now an execution boundary.</h2>
                </div>
                <span className="armed"><i /> ENFORCED</span>
              </header>
              <div className="rl-admission-grid">
                <article className="protocol">
                  <code>START → RESOLVE → ADMIT → MUTATE → RETURN</code>
                  <p>New coding sessions receive this ledger before work begins. Read-only discovery stays open; material changes require a record-bound admission or a reasoned off-ledger receipt pinned to revision {ledger.revision}.</p>
                  <small>{ledger.project_path ? `Target resolution: ${ledger.project_path}` : 'Add a repository path to enable automatic mutation targeting.'}</small>
                </article>
                <article>
                  <strong>{admissions.filter(item => item.event === 'PlanningAdmission' && item.mode === 'bound').length}</strong>
                  <span>Bound admissions</span>
                  <small>Recent session-to-record contracts</small>
                </article>
                <article>
                  <strong>{admissions.filter(item => item.event === 'PlanningAdmission' && item.mode === 'off-ledger').length}</strong>
                  <span>Overrides</span>
                  <small>Explicitly reasoned, never silent</small>
                </article>
                <article>
                  <strong>{admissions.filter(item => item.event === 'PlanningReturn').length}</strong>
                  <span>Return receipts</span>
                  <small>Awaiting durable reconciliation</small>
                </article>
              </div>
              {admissions.length > 0 && (
                <div className="rl-admission-stream">
                  {admissions.slice(0, 6).map((item, index) => (
                    <div key={`${item.session_id}-${item.ts}-${index}`}>
                      <span className={item.event === 'PlanningReturn' ? 'return' : item.mode ?? 'bound'}>
                        {item.event === 'PlanningReturn' ? 'RETURN' : item.mode === 'off-ledger' ? 'OVERRIDE' : 'ADMITTED'}
                      </span>
                      <code>{item.session_id.slice(0, 12)}</code>
                      <strong>{item.record_label || item.record_id || item.reason || 'Off-ledger session'}</strong>
                      <small>r{item.ledger_revision} · {item.harness || item.tools?.join(', ') || 'captured'} · {asDate(item.ts)}</small>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="rl-strategy">
              <header>
                <div>
                  <p className="rl-kicker">STRATEGIC PLASTICITY</p>
                  <h2>Resolution increases as intent approaches execution.</h2>
                </div>
                <small>{ledger.summary.unclassified_horizon} records still need a horizon</small>
              </header>
              <div className="rl-horizon-strip">
                {HORIZONS.map(item => (
                  <button key={item.key} className={horizon === item.key ? 'active' : ''} type="button" onClick={() => setHorizon(current => current === item.key ? 'all' : item.key)}>
                    <code>{item.short}</code>
                    <strong>{ledger.summary.horizons[item.key] ?? 0}</strong>
                    <span>{item.label}</span>
                    <small>{item.description}</small>
                  </button>
                ))}
                <button className={`rl-review-sensor${reviewDueOnly ? ' active' : ''}`} type="button" onClick={() => setReviewDueOnly(value => !value)}>
                  <code>REVIEW</code>
                  <strong>{ledger.summary.reviews_due}</strong>
                  <span>Due now</span>
                  <small>{ledger.summary.reviews_upcoming} upcoming · {ledger.summary.assumptions} assumptions · {ledger.summary.challenged_assumptions} challenged</small>
                </button>
              </div>
            </section>

            {(ledger.state.milestones?.length ?? 0) > 0 && (
              <section className="rl-milestones">
                <header><span>MILESTONE REGISTER</span><small>{ledger.state.milestones?.length} gates</small></header>
                <div>
                  {ledger.state.milestones?.map(milestone => {
                    const prereqs = milestone.prereqs ?? [];
                    const met = prereqs.filter(id => nodeById.get(id)?.status === 'done').length;
                    return (
                      <details key={milestone.id} style={{ '--rl-section': milestone.color ?? 'var(--accent)' } as React.CSSProperties}>
                        <summary><b>{milestone.id}</b><span>{milestone.label}</span><small>{met}/{prereqs.length}</small></summary>
                        <div><DetailBlock label="Summary" value={milestone.summary} /><DetailBlock label="Prerequisites" value={prereqs} /><CopyButton value={milestone} label="Copy milestone" /></div>
                      </details>
                    );
                  })}
                </div>
              </section>
            )}

            <section className="rl-workbench">
              <header className="rl-workbench-head">
                <div>
                  <p className="rl-kicker">WORK REGISTER</p>
                  <h2>{filtered.length} records <span>/ {ledger.state.nodes.length} total</span></h2>
                </div>
                <div>
                  <button type="button" onClick={() => setDialog('section')}>Add section</button>
                  <button className="primary" type="button" onClick={() => { setEditing(null); setDialog('record'); }}>Add record</button>
                </div>
              </header>

              <div className="rl-filters strategic">
                <label className="rl-search">
                  <svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
                  <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search every field…" />
                </label>
                <label>
                  <span>Status</span>
                  <select value={status} onChange={e => setStatus(e.target.value as RoadmapStatus | 'all')}>
                    <option value="all">All statuses</option>
                    {Object.entries(STATUS_META).map(([key, meta]) => <option key={key} value={key}>{meta.label}</option>)}
                  </select>
                </label>
                <label>
                  <span>Section</span>
                  <select value={section} onChange={e => setSection(e.target.value)}>
                    <option value="all">All sections</option>
                    {ledger.state.sections.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
                    <option value="__deferred">Later</option>
                  </select>
                </label>
                <label>
                  <span>Horizon</span>
                  <select value={horizon} onChange={e => setHorizon(e.target.value as RoadmapHorizon | 'all' | 'unclassified')}>
                    <option value="all">All horizons</option>
                    <option value="unclassified">Unclassified</option>
                    {HORIZONS.map(item => <option key={item.key} value={item.key}>{item.short} · {item.label}</option>)}
                  </select>
                </label>
                <button className={reviewDueOnly ? 'active' : ''} type="button" onClick={() => setReviewDueOnly(value => !value)}><i />Review due</button>
                <button className={readyOnly ? 'active' : ''} type="button" onClick={() => setReadyOnly(value => !value)}><i />Ready only</button>
              </div>

              <div className="rl-table-shell">
                <table className="rl-table strategic">
                  <thead>
                    <tr><th>Record</th><th>Status</th><th>Work item</th><th>Horizon</th><th>Review</th><th>Readiness</th><th>Evidence</th><th>Resolved</th><th /></tr>
                  </thead>
                  <tbody>
                    {[...ledger.state.sections, {
                      id: '__deferred',
                      label: 'Later / Benchmarks & Positioning',
                      color: '#8f8174',
                    }].map((sectionItem, sectionIndex) => {
                      const nodes = sectionItem.id === '__deferred'
                        ? filtered.filter(node => node.deferred).sort((a, b) => (a.deferredOrder ?? 0) - (b.deferredOrder ?? 0))
                        : filtered.filter(node => node.section === sectionItem.id && !node.deferred);
                      if (!nodes.length) return null;
                      return [
                        <tr className="rl-section-row" key={`${sectionItem.id}-head`} style={{ '--rl-section': sectionItem.color } as React.CSSProperties}>
                          <th colSpan={9}><span>{String(sectionIndex + 1).padStart(2, '0')}</span><strong>{sectionItem.label}</strong><small>{nodes.length} records</small></th>
                        </tr>,
                        ...nodes.flatMap(node => {
                          const open = expanded.has(node.id);
                          const prereqs = node.prereqs ?? [];
                          const receipts = node.reconciliationHistory ?? [];
                          const latestReceipt = receipts[receipts.length - 1];
                          const review = reviewSignal(node);
                          const strategicHorizon = horizonMeta(node.horizon);
                          const detailFields: Array<[string, unknown, boolean?]> = [
                            ['Summary', node.summary],
                            ['Implementation prompt', node.prompt, true],
                            ['Implementation context', node.implementationContext, true],
                            ['Final implementation context', node.finalImplementationContext, true],
                            ['Final prompt', node.finalPrompt, true],
                            ['Verification checklist', node.verification],
                            ['Verification report', node.verificationReport, true],
                            ['Verification results', node.verificationResults],
                            ['Result recap', node.resultRecap],
                            ['Latest strategic review', node.reviewResultRecap, true],
                            ['Review history', node.reviewHistory, true],
                            ['Reconciliation history', node.reconciliationHistory, true],
                            ['Disposition', node.disposition],
                          ];
                          return [
                            <tr key={node.id} className={`rl-node-row${open ? ' open' : ''}`} onClick={() => toggle(node.id)}>
                              <td data-label="Record"><code>{node.id}</code></td>
                              <td data-label="Status">
                                <select className="rl-status-select" value={node.status} disabled={saving} onClick={e => e.stopPropagation()} onChange={e => { e.stopPropagation(); changeStatus(node, e.target.value as RoadmapStatus); }}>
                                  {Object.entries(STATUS_META).map(([key, meta]) => <option key={key} value={key}>{meta.label}</option>)}
                                </select>
                              </td>
                              <td data-label="Work item"><strong>{node.label}</strong><span>{node.summary || 'No summary recorded.'}</span></td>
                              <td data-label="Horizon">
                                {strategicHorizon
                                  ? <span className={`rl-horizon ${node.horizon}`}><b>{strategicHorizon.short}</b>{strategicHorizon.label}</span>
                                  : <span className="rl-horizon unclassified">— Unclassified</span>}
                              </td>
                              <td data-label="Review">
                                <span className={`rl-review ${review}`}><i />{review === 'due' ? `Due ${asDate(node.nextReviewAt)}` : review === 'upcoming' ? `Soon ${asDate(node.nextReviewAt)}` : review}</span>
                              </td>
                              <td data-label="Readiness"><span className={`rl-readiness ${readinessFor(node)}`}><i />{{ complete: 'Complete', ready: 'Ready', blocked: 'Gated', dropped: 'Dropped' }[readinessFor(node)]}</span></td>
                              <td data-label="Evidence">
                                {latestReceipt
                                  ? <span className={`rl-work-state ${latestReceipt.verificationPassed ? 'verified' : 'failed'}`}>{latestReceipt.disposition}<small>{receipts.length > 1 ? ` +${receipts.length - 1}` : ''}</small></span>
                                  : <span className="rl-work-none">No receipt</span>}
                              </td>
                              <td data-label="Resolved">{asDate(node.completedAt)}</td>
                              <td><button type="button" aria-label={`${open ? 'Collapse' : 'Expand'} ${node.label}`} onClick={e => { e.stopPropagation(); toggle(node.id); }}>＋</button></td>
                            </tr>,
                            open ? (
                              <tr className="rl-detail-row" key={`${node.id}-detail`}>
                                <td colSpan={9}>
                                  <div className="rl-dossier">
                                    <header>
                                      <div>
                                        <span>RECORD DOSSIER / {node.id}</span>
                                        <h3>{node.label}</h3>
                                        <small>{strategicHorizon ? `${strategicHorizon.short} · ${strategicHorizon.description}` : 'Planning horizon not yet classified'} · {node.reviewCadence ?? 'No'} review cadence · next {asDate(node.nextReviewAt)}</small>
                                      </div>
                                      <div>
                                        {review !== 'retired' && (
                                          <button className="reconcile" type="button" onClick={() => openReconciliation(node)}>Reconcile outcome</button>
                                        )}
                                        <button type="button" onClick={() => { setEditing(node); setDialog('record'); }}>Edit record</button>
                                        {typeof node.href === 'string' && <a href={node.href} target="_blank" rel="noreferrer">Open reference ↗</a>}
                                        <CopyButton value={node} label="Copy record" />
                                      </div>
                                    </header>

                                    <AssumptionRegister assumptions={node.assumptions ?? []} />

                                    {receipts.length > 0 && (
                                      <section className="rl-execution">
                                        <header>
                                          <span>RECONCILIATION / {receipts.length} RECEIPT{receipts.length === 1 ? '' : 'S'}</span>
                                          <small>Harness output enters durable intent only through independent verification and human acceptance.</small>
                                        </header>
                                        {[...receipts].reverse().map(receipt => (
                                          <article key={`${receipt.acceptedAt}-${receipt.verifier}`}>
                                            <div>
                                              <code>r{receipt.ledgerRevision}</code>
                                              <span className="rl-order-kind delivery">{receipt.disposition}</span>
                                              <span className={`rl-work-state ${receipt.verificationPassed ? 'verified' : 'failed'}`}>{receipt.verificationPassed ? 'verified' : 'not passed'}</span>
                                              <small>
                                                verifier {receipt.verifier} · accepted by {receipt.acceptedBy} · {Math.round(receipt.confidence * 100)}% · {asDate(receipt.acceptedAt)}
                                              </small>
                                            </div>
                                            <div>
                                              <CopyButton value={receipt} label="Copy receipt" />
                                            </div>
                                          </article>
                                        ))}
                                      </section>
                                    )}

                                    <div className="rl-detail-grid">
                                      {detailFields.map(([label, value, wide]) => <DetailBlock key={label} label={label} value={value} wide={wide} />)}
                                      {prereqs.length > 0 && <DetailBlock label="Prerequisites" value={prereqs.map(id => `${id} — ${nodeById.get(id)?.label ?? 'Unknown record'}`)} />}
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            ) : [],
                          ];
                        }),
                      ];
                    })}
                  </tbody>
                </table>
                {!filtered.length && (
                  <div className="rl-no-results">
                    No records match this instrument setting.
                    <button type="button" onClick={clearFilters}>Clear filters</button>
                  </div>
                )}
              </div>
            </section>
          </>
        )}
      </main>

      {dialog === 'ledger' && <LedgerDialog onClose={() => setDialog(null)} onCreated={created} />}
      {dialog === 'section' && ledger && (
        <SectionDialog state={ledger.state} onClose={() => setDialog(null)} onSave={newSection => {
          setDialog(null);
          void commit({ ...ledger.state, sections: [...ledger.state.sections, newSection] });
        }} />
      )}
      {dialog === 'record' && ledger && (
        <RecordDialog state={ledger.state} node={editing} onClose={() => { setDialog(null); setEditing(null); }} onSave={saveRecord} />
      )}
      {dialog === 'reconcile' && ledger && reconciling && (
        <ReconciliationDialog ledger={ledger} node={reconciling} onClose={() => { setDialog(null); setReconciling(null); }} onSaved={reconciliationSaved} />
      )}
    </div>
  );
}
