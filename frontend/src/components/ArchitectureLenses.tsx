import { useEffect, useId, useState } from 'react';
import IsometricMemoryMap from './IsometricMemoryMap';
import { fetchJobHealth } from '../api/memory';
import './ArchitectureLenses.css';

type Box = { id: string; name: string; tier: string; purpose: string; loc: number; module_count: number; frontend_file_count: number; routes: number; modules: string[]; frontend_files: string[] };
type Step = { index: number; actor: string; component: string; does: string; state_change?: string; guardrail?: string; evidence: string[]; evidence_ok: boolean };
type Process = { id: string; name: string; trigger: string; cadence: string; availability: string; outcome: string; steps: Step[] };
type Deployment = { id: string; name: string; trust_boundary: string; failure_modes: { failure: string; visible_as: string; recovery: string }[] };
type Data = {
  memory_engine: Parameters<typeof IsometricMemoryMap>[0]['engine'];
  boxes: Box[]; processes: Process[]; deployments: Deployment[];
  freshness: { fresh: boolean; stored_fingerprint?: string; current_fingerprint?: string };
  review: { reviewed_at?: string };
};
type Mode = 'boundaries' | 'process' | 'footprint' | 'operations';
const labels: Record<Mode, string> = { boundaries: 'Boundaries', process: 'Process paths', footprint: 'Code footprint', operations: 'Operational receipts' };

function asObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
function literal(value: unknown) { return typeof value === 'string' && value.trim() ? value : 'Unavailable'; }

export default function ArchitectureLenses({ data }: { data: Data }) {
  const [mode, setMode] = useState<Mode>('boundaries');
  const [processId, setProcessId] = useState('episode-distillation');
  const [stepIndex, setStepIndex] = useState(0);
  const [tier, setTier] = useState('hot');
  const [boxId, setBoxId] = useState<string | null>(null);
  const process = data.processes.find(item => item.id === processId) ?? data.processes[0];
  const step = process?.steps[stepIndex] ?? process?.steps[0];
  const boxes = data.boxes.filter(box => tier === 'all' || box.tier === tier);
  const box = boxes.find(item => item.id === boxId) ?? boxes[0];
  const maxLoc = Math.max(1, ...boxes.map(item => Math.max(0, item.loc)));
  const marker = useId();

  return <section className="atlas-lenses" aria-label="Architecture lenses">
    <div className="atlas-lenses__identity">
      <span>MODEL {data.freshness.fresh ? 'SOURCE-CURRENT' : 'STALE'}</span>
      <span title={data.freshness.stored_fingerprint}>Fingerprint: {data.freshness.stored_fingerprint?.slice(0, 12) ?? 'unavailable'}</span>
      <span>Review: {data.review.reviewed_at ?? 'unavailable'}</span>
      <span>Deployed source SHA: unavailable from this API</span>
    </div>
    <nav className="atlas-lenses__tabs" aria-label="Architecture lenses">
      {(Object.keys(labels) as Mode[]).map(item => <button type="button" key={item} aria-pressed={mode === item} onClick={() => setMode(item)}>{labels[item]}</button>)}
    </nav>
    {mode === 'boundaries' && <IsometricMemoryMap engine={data.memory_engine} fresh={data.freshness.fresh} />}
    {mode === 'process' && <div className="atlas-lenses__panel">
      <label className="atlas-lenses__select">Follow a reviewed process
        <select value={process?.id ?? ''} onChange={event => { setProcessId(event.target.value); setStepIndex(0); }}>
          {data.processes.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {process && <>
        <p>{process.trigger}</p>
        <p className="atlas-lenses__note">{process.availability} / {process.cadence}</p>
        <p className="atlas-lenses__note">Arrows mean reviewed sequence, not observed traffic, imports, or a synchronous call graph. Repeated components are separate steps, not separate services.</p>
        <div className="atlas-lenses__trace-scroll">
          <svg className="atlas-lenses__trace" viewBox={`0 0 ${Math.max(1, process.steps.length) * 210} 190`} style={{ minWidth: Math.max(1, process.steps.length) * 170 }} aria-label="Reviewed process sequence">
            <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="currentColor" /></marker></defs>
            {process.steps.map((item, index) => <g key={`${process.id}-${item.index}`}>
              {index < process.steps.length - 1 && <path d={`M${index * 210 + 185} 85H${index * 210 + 225}`} fill="none" stroke="currentColor" markerEnd={`url(#${marker})`} />}
              <g transform={`translate(${index * 210 + 105} 85)`} role="button" tabIndex={0} aria-label={`Step ${item.index}: ${item.actor}`} aria-pressed={step === item}
                onClick={() => setStepIndex(index)} onKeyDown={event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); setStepIndex(index); } }}>
                <path className={step === item ? 'atlas-lenses__slab selected' : 'atlas-lenses__slab'} d="M0 -50L80 -8L80 10L0 52L-80 10L-80 -8Z" />
                <path d="M-80 -8L0 34L80 -8M0 34V52" fill="none" stroke="currentColor" opacity=".35" />
                <text textAnchor="middle" y="-10">STEP {item.index}</text><text textAnchor="middle" y="10">{item.evidence_ok ? 'evidence linked' : 'evidence gap'}</text>
              </g>
              <text x={index * 210 + 105} y="166" textAnchor="middle">{item.component}</text>
            </g>)}
          </svg>
        </div>
        <div className="atlas-lenses__step-buttons">{process.steps.map((item, index) => <button type="button" key={item.index} aria-pressed={step === item} onClick={() => setStepIndex(index)}>{item.index}. {item.actor}</button>)}</div>
        {step && <article className="atlas-lenses__inspector" aria-live="polite">
          <h3>{step.actor}</h3><p>{step.does}</p>
          <dl><dt>Owning component</dt><dd>{data.boxes.find(item => item.id === step.component)?.name ?? step.component}</dd>
            <dt>State change / write effect</dt><dd>{step.state_change || 'Not documented for this step.'}</dd>
            <dt>Trust / recovery guardrail</dt><dd>{step.guardrail || 'Not documented for this step.'}</dd></dl>
          <div className="atlas-lenses__evidence">{step.evidence.map(path => <code key={path}>{path}</code>)}</div>
          <p className="atlas-lenses__note">These are reviewed descriptions. Evidence linkage checks do not mechanically prove every stated transaction or recovery guarantee.</p>
        </article>}
        <p><strong>Expected outcome:</strong> {process.outcome}</p>
      </>}
      <h3>Failure and recovery by deployment</h3>
      <p className="atlas-lenses__note">Deployment-wide failure modes, not inferred failure predictions for the selected component.</p>
      {data.deployments.map(deployment => <details key={deployment.id} className="atlas-lenses__failure"><summary>{deployment.name}</summary>
        <p>{deployment.trust_boundary}</p>{deployment.failure_modes.map(item => <article key={item.failure}><h4>{item.failure}</h4><p><strong>Visible as:</strong> {item.visible_as}</p><p><strong>Recovery:</strong> {item.recovery}</p></article>)}
      </details>)}
    </div>}
    {mode === 'footprint' && <div className="atlas-lenses__panel">
      <label className="atlas-lenses__select">Component group<select value={tier} onChange={event => { setTier(event.target.value); setBoxId(null); }}>
        <option value="all">All responsibilities</option>{Array.from(new Set(data.boxes.map(item => item.tier))).map(item => <option key={item}>{item}</option>)}
      </select></label>
      <p className="atlas-lenses__note">Top-face area represents source lines relative to this group. Tiny components have a 4% minimum area for visibility; exact counts remain authoritative. Height is decorative. Neither size nor routes measure runtime cost or importance.</p>
      <div className="atlas-lenses__footprint">{boxes.map(item => {
        const scale = Math.max(.2, Math.sqrt(Math.max(0, item.loc) / maxLoc));
        return <button type="button" key={item.id} aria-pressed={item.id === box?.id} onClick={() => setBoxId(item.id)}>
          <svg viewBox="0 0 200 130" aria-hidden="true"><g transform={`translate(100 60) scale(${scale})`}><path className="atlas-lenses__slab" d="M0 -50L90 0L90 12L0 62L-90 12L-90 0Z" /><path d="M-90 0L0 50L90 0M0 50V62" stroke="currentColor" fill="none" /></g></svg>
          <strong>{item.name}</strong><span>{item.loc.toLocaleString()} lines / {item.module_count + item.frontend_file_count} files</span>
        </button>;
      })}</div>
      {box && <article className="atlas-lenses__inspector"><h3>{box.name}</h3><p>{box.purpose}</p><p>{box.routes} routes / {box.module_count} Python modules / {box.frontend_file_count} frontend files</p>
        <details><summary>Owned source units</summary><div className="atlas-lenses__evidence">{[...box.modules, ...box.frontend_files].map(path => <code key={path}>{path}</code>)}</div></details>
      </article>}
    </div>}
    {mode === 'operations' && <OperationalReceipts />}
  </section>;
}

function OperationalReceipts() {
  const [report, setReport] = useState<unknown>(null);
  const [failed, setFailed] = useState(false);
  const [loadedAt, setLoadedAt] = useState('');
  useEffect(() => {
    let cancelled = false;
    fetchJobHealth<unknown>().then(value => { if (!cancelled) { setReport(value); setLoadedAt(new Date().toISOString()); } })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, []);
  const payload = asObject(report);
  const jobs = Array.isArray(payload.jobs) ? payload.jobs.map(asObject) : [];
  return <div className="atlas-lenses__panel">
    <h3>Recorded operations, not simulated telemetry</h3>
    <p className="atlas-lenses__note">Latest persisted job receipts from this backend. Historical outcomes are not current CPU load or proof of continuous health. Read at {loadedAt || 'not yet available'}.</p>
    {failed ? <p role="alert">Operational receipts unavailable. No healthy state inferred.</p> : report === null ? <p role="status">Loading receipts...</p> : jobs.length === 0 ? <p>No usable job receipts supplied.</p> :
      <div className="atlas-lenses__jobs">{jobs.map((job, index) => {
        const receipt = asObject(job.receipt);
        const duration = receipt.duration_ms;
        return <article key={`${literal(job.name)}-${index}`}><h4>{literal(job.name)}</h4>
          <dl><dt>Owner</dt><dd>{literal(job.owner)}</dd><dt>Inventory status</dt><dd>{literal(job.status)}</dd>
            <dt>Receipt outcome</dt><dd>{literal(receipt.outcome)}</dd><dt>Finished</dt><dd>{literal(receipt.finished_at)}</dd>
            <dt>Recorded duration</dt><dd>{typeof duration === 'number' && Number.isFinite(duration) && duration >= 0 ? `${duration.toLocaleString()} ms` : 'Unavailable'}</dd>
            <dt>Provider/model spend</dt><dd>Unavailable: no supported cost/model fields in this receipt contract.</dd>
            <dt>CPU / memory / disk</dt><dd>Unavailable: not measured by this endpoint.</dd></dl>
          <p className="atlas-lenses__note">{literal(job.retry_contract)}</p>
        </article>;
      })}</div>}
    <p className="atlas-lenses__note">No component-level allocation is inferred from a batch receipt. Private exception bodies, paths and arbitrary receipt detail are deliberately not rendered here.</p>
  </div>;
}
