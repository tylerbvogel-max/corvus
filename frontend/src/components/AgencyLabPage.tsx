import { useEffect, useMemo, useState } from 'react';
import {
  createAgencyExperiment, createAgencyPolicy, createAgencyWorker,
  createAgencyVenture, createAgencyWorkOrder, fetchAgencyDashboard, fetchAgencyPolicies,
  fetchAgencyVentures, fetchAgencyWorkOrders,
  type AgencyDashboard, type AgencyPolicy, type AgencyVenture, type AgencyWorkOrder,
} from '../api';
import { Bars, MindStyle, StatTile, UsageMeter } from './mindUi';
import VentureGraphPage from './VentureGraphPage';

export type AgencyLabView = 'missions' | 'venture-graph' | 'economy' | 'workforce' | 'experiments' | 'outcomes';

const tabs: Array<[AgencyLabView, string]> = [
  ['missions', 'Missions'],
  ['venture-graph', 'Venture Graph'],
  ['economy', 'Economy'], ['workforce', 'Workforce'],
  ['experiments', 'Experiments'], ['outcomes', 'Outcomes'],
];

const CONTROL_LABELS: Record<string, string> = {
  audit: 'Random audits', disclosure: 'Self-disclosure rewards', concealment: 'Concealment clawbacks',
  delivery: 'Verified delivery', critic: 'Critic bounties', calibration: 'Confidence calibration',
  settlement: 'Delayed settlement', capital: 'Agency capital / Kelly', exploration: 'Experimental exploration',
};

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return <button onClick={() => onChange(!checked)} className="al-toggle" aria-pressed={checked}>
    {checked ? 'ON' : 'OFF'}
  </button>;
}

export default function AgencyLabPage({ view }: { view: AgencyLabView }) {
  const [data, setData] = useState<AgencyDashboard | null>(null);
  const [policies, setPolicies] = useState<AgencyPolicy[]>([]);
  const [draft, setDraft] = useState<Record<string, Record<string, unknown>>>({});
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [ventures, setVentures] = useState<AgencyVenture[]>([]);
  const [workOrders, setWorkOrders] = useState<AgencyWorkOrder[]>([]);

  const load = async () => {
    try {
      const [dashboard, rows, ventureRows, orderRows] = await Promise.all([fetchAgencyDashboard(), fetchAgencyPolicies(), fetchAgencyVentures(), fetchAgencyWorkOrders()]);
      setData(dashboard); setPolicies(rows); setDraft(structuredClone(dashboard.policy)); setError('');
      setVentures(ventureRows); setWorkOrders(orderRows);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void load(); }, []);

  const savePolicy = async () => {
    try {
      const result = await createAgencyPolicy({ name: 'Agency Lab Policy', mode: 'simulation',
        description: 'Created from the Agency Lab control surface.', config: draft });
      setMessage(`Created immutable draft policy v${result.version}`); await load();
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };

  if (!data) return <div className="mm-root"><MindStyle />{error || 'Loading Agency Lab…'}</div>;
  return <div className="mm-root al-root">
    <MindStyle /><style>{CSS}</style>
    <header className="al-header">
      <div><h2>Agency Lab</h2><div className="mm-sub">Flight Economy · earned trust, bounded authority, delayed truth</div></div>
      <span className="mm-chip">policy #{data.active_policy_id}</span>
    </header>
    <div className="al-section-title">{tabs.find(([key]) => key === view)?.[1]}</div>
    {error && <div className="al-alert serious">{error}</div>}
    {message && <div className="al-alert good">{message}</div>}
    {view === 'missions' && <Missions data={data} policies={policies} ventures={ventures} workOrders={workOrders} reload={load} setMessage={setMessage} setError={setError} />}
    {view === 'venture-graph' && <VentureGraphPage ventures={ventures} workers={data.workers} />}
    {view === 'economy' && <Economy config={draft} policies={policies} setDraft={setDraft} save={savePolicy} />}
    {view === 'workforce' && <Workforce data={data} reload={load} setMessage={setMessage} setError={setError} />}
    {view === 'experiments' && <Experiments data={data} policies={policies} reload={load} setMessage={setMessage} setError={setError} />}
    {view === 'outcomes' && <Outcomes data={data} />}
  </div>;
}

function Missions({ data, policies, ventures, workOrders, reload, setMessage, setError }: { data: AgencyDashboard; policies: AgencyPolicy[];
  ventures: AgencyVenture[]; workOrders: AgencyWorkOrder[]; reload: () => Promise<void>;
  setMessage: (x: string) => void; setError: (x: string) => void }) {
  const [key, setKey] = useState(`quick-turn-${Date.now().toString(36)}`);
  const [title, setTitle] = useState('Cheap-agent intake calibration');
  const create = async () => { try { await createAgencyVenture({ key, title, change_reason: 'Initial human-authored north star', created_by: 'user', graph: {
      mission: 'Measure truthful, useful intake behavior on one bounded turn', constraints: ['read-only intake', 'no unsupported claims'],
      kill_criteria: ['concealed uncertainty'], nodes: [{ id: 'intake', title: 'Perform bounded intake', outcome: 'A structured, evidence-linked intake report',
        acceptance: ['states what was inspected', 'separates facts from inference', 'reports limitations', 'provides evidence'], risk_tier: 1, task_class: 'intake' }], edges: [] } });
      setMessage(`Created north star ${key}`); await reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  const issue = async (venture: AgencyVenture) => { try { if (!data.workers.length || !policies.length) throw new Error('Commission a worker and policy first');
      const result = await createAgencyWorkOrder({ venture_key: venture.key, node_id: 'intake', worker_profile_id: data.workers[0].id,
        policy_id: policies[0].id, permissions: { filesystem: 'read_only', commands: ['read', 'search'], network: false }, ttl_minutes: 60 });
      setMessage(`Issued ${result.id}`); await reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  return <><section className="mm-card al-form"><h3>Author north star</h3><label><span>key</span><input value={key} onChange={e => setKey(e.target.value)} /></label>
    <label><span>mission</span><input value={title} onChange={e => setTitle(e.target.value)} /></label><button onClick={create}>Create venture</button>
    <div className="mm-sub">Intent is revisioned and immutable; work orders are disposable projections.</div></section>
    <section className="mm-card"><h3>Venture registry</h3><table className="mm-table"><thead><tr><th>North star</th><th>State</th><th>Revision</th><th></th></tr></thead><tbody>{ventures.map(v => <tr key={v.id}><td>{v.title}<div className="mm-sub">{v.key}</div></td><td>{v.status}</td><td>r{v.current_revision}</td><td><button onClick={() => void issue(v)}>Issue intake</button></td></tr>)}</tbody></table></section>
    <section className="mm-card"><h3>Agent work board</h3><table className="mm-table"><thead><tr><th>Work order</th><th>Node</th><th>Risk</th><th>State</th><th>Contract</th></tr></thead><tbody>{workOrders.map(w => <tr key={w.id}><td>{w.id}</td><td>{w.plan_node_id}</td><td>T{w.risk_tier}</td><td><span className="mm-badge">{w.status}</span></td><td><code>{w.contract_digest.slice(0, 10)}</code></td></tr>)}</tbody></table></section></>;
}

function Economy({ config, policies, setDraft, save }: { config: Record<string, Record<string, unknown>>;
  policies: AgencyPolicy[]; setDraft: (v: Record<string, Record<string, unknown>>) => void; save: () => void }) {
  const setValue = (section: string, key: string, value: unknown) => setDraft({ ...config, [section]: { ...config[section], [key]: value } });
  return <><div className="mm-grid">
    {Object.entries(config).map(([section, values]) => <section className="mm-card" key={section}>
      <div className="al-control-head"><h3>{CONTROL_LABELS[section] || section}</h3>
        <Toggle checked={Boolean(values.enabled)} onChange={v => setValue(section, 'enabled', v)} /></div>
      <div className="al-fields">{Object.entries(values).filter(([k]) => k !== 'enabled').map(([key, value]) =>
        <label key={key}><span>{key.replaceAll('_', ' ')}</span>
          {typeof value === 'number' ? <input type="number" step="0.01" value={value}
            onChange={e => setValue(section, key, Number(e.target.value))} />
          : <code>{Array.isArray(value) ? value.join(', ') : String(value)}</code>}</label>)}</div>
    </section>)}</div>
    <div className="al-actions"><button onClick={save}>Save as new draft version</button>
      <span>Live policies are immutable. Every save creates an auditable version.</span></div>
    <section className="mm-card mm-wide"><h3>Policy lineage</h3><div className="mm-tablewrap"><table className="mm-table"><thead><tr><th>Policy</th><th>Version</th><th>Mode</th><th>Status</th></tr></thead><tbody>
      {policies.map(p => <tr key={p.id}><td>{p.name}</td><td>v{p.version}</td><td>{p.mode}</td><td><span className="mm-badge">{p.status}</span></td></tr>)}</tbody></table></div></section>
  </>;
}

function Workforce({ data, reload, setMessage, setError }: { data: AgencyDashboard; reload: () => Promise<void>;
  setMessage: (x: string) => void; setError: (x: string) => void }) {
  const [form, setForm] = useState({ key: '', display_name: '', role: 'builder', model: 'opus', harness: 'codex' });
  const create = async () => { try { await createAgencyWorker({ ...form, task_classes: ['coding'], risk_tiers: [1, 2], skill_ids: [], permission_ceiling: 'project_reversible', starting_capital: 100 }); setMessage(`Created worker ${form.key}`); await reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  return <><section className="mm-card al-form"><h3>Commission worker profile</h3>
    {Object.entries(form).map(([k, v]) => <label key={k}><span>{k.replaceAll('_', ' ')}</span><input value={v} onChange={e => setForm({ ...form, [k]: e.target.value })} /></label>)}
    <button onClick={create} disabled={!form.key || !form.display_name}>Create profile</button></section>
    <div className="mm-grid">{data.workers.map(w => <section className="mm-card" key={w.id}>
      <div className="al-worker-head"><div><h3>{w.display_name}</h3><div className="mm-sub">{w.model} · {w.harness} · {w.role}</div></div><span className="mm-badge">{w.status}</span></div>
      <div className="al-capital">{w.agency_capital.toFixed(1)} <small>agency capital</small></div>
      <UsageMeter label="Kelly allocation" percent={w.wager.allocated_fraction * 100} severity="normal" sub={`${w.wager.capital_at_risk} at risk · conservative p ${w.wager.conservative_p}`} />
      <div className="mm-sub">Peak {w.peak_capital.toFixed(1)} · drawdown {w.drawdown.toFixed(1)}</div>
      <Bars rows={Object.entries(w.scores || {}).map(([label, value]) => ({ label, value: Math.max(0, value) }))} />
    </section>)}</div>{data.workers.length === 0 && <div className="mm-empty">No worker profiles yet. Commission the first member of the flock.</div>}</>;
}

function Experiments({ data, policies, reload, setMessage, setError }: { data: AgencyDashboard; policies: AgencyPolicy[];
  reload: () => Promise<void>; setMessage: (x: string) => void; setError: (x: string) => void }) {
  const [name, setName] = useState('Disclosure reward experiment');
  const create = async () => { try { const policyIds = policies.slice(0, 2).map(p => p.id); if (!policyIds.length) throw new Error('Create a policy first'); await createAgencyExperiment({ name, mode: 'simulation', policy_ids: policyIds, dimensions: { model: ['opus'], harness: ['codex'], task_class: ['coding'], risk_tier: [1, 2] }, primary_metric: 'verified_value_retained_7d', guardrails: { critic_precision_min: 0.6 }, sample_target: 30 }); setMessage(`Created experiment: ${name}`); await reload(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } };
  return <><section className="mm-card al-form"><h3>New blocked experiment</h3><label><span>name</span><input value={name} onChange={e => setName(e.target.value)} /></label><button onClick={create}>Create experiment</button><div className="mm-sub">Required blocks: model × harness × task class × risk tier</div></section>
    <section className="mm-card"><h3>Experiment registry</h3><table className="mm-table"><thead><tr><th>Name</th><th>Mode</th><th>Primary metric</th><th>Target n</th></tr></thead><tbody>{data.experiments.map(x => <tr key={x.id}><td>{x.name}</td><td>{x.mode}</td><td>{x.primary_metric}</td><td>{x.sample_target}</td></tr>)}</tbody></table></section></>;
}

function Outcomes({ data }: { data: AgencyDashboard }) {
  const rows = useMemo(() => Object.entries(data.event_counts).map(([label, value]) => ({ label, value })), [data.event_counts]);
  return <><div className="mm-tiles"><StatTile value={data.summary.events} label="score events" /><StatTile value={data.summary.settled_points} label="settled return" /><StatTile value={data.summary.escrow_points} label="escrowed" /><StatTile value={data.summary.self_disclosures} label="self-disclosures" /><StatTile value={data.summary.integrity_breaches} label="integrity breaches" /></div>
    <div className="mm-grid"><section className="mm-card"><h3>Behavior economy</h3>{rows.length ? <Bars rows={rows} /> : <div className="mm-empty">No scored behavior yet.</div>}</section>
      <section className="mm-card"><h3>Automatic diagnoses</h3>{data.diagnostics.length ? data.diagnostics.map(d => <div className={`al-diagnostic ${d.severity}`} key={d.code}><strong>{d.code.replaceAll('_', ' ')}</strong><span>{d.message}</span></div>) : <div className="mm-empty">No detectable failure pattern yet.</div>}</section>
      <section className="mm-card mm-wide"><h3>System outcomes</h3><div className="al-outcome-grid">{['Verified value retained 1/7/14/30d', 'Escaped regression rate', 'Mean time to detection', 'Mean time to recovery', 'Cost per retained deliverable', 'Project-state contradiction rate'].map(x => <div key={x}><span>Awaiting mission telemetry</span><strong>{x}</strong></div>)}</div></section></div></>;
}

const CSS = `
.al-root { --al-panel: rgba(57,135,229,.08); }
.al-header,.al-worker-head,.al-control-head { display:flex; align-items:flex-start; justify-content:space-between; gap:1rem; }
.al-section-title { color:var(--mm-ink2); font-size:.75rem; text-transform:uppercase; letter-spacing:.1em; margin:1rem 0; padding-bottom:.55rem; border-bottom:1px solid var(--mm-border); }
.al-toggle,.al-actions button,.al-form button { border:1px solid var(--mm-border); background:var(--al-panel); color:var(--mm-ink); border-radius:7px; padding:.35rem .7rem; cursor:pointer; }
.al-toggle[aria-pressed=true] { color:var(--mm-aqua); border-color:var(--mm-aqua); }
.al-fields { display:grid; grid-template-columns:1fr 1fr; gap:.45rem .8rem; }
.al-fields label,.al-form label { display:flex; justify-content:space-between; align-items:center; gap:.6rem; color:var(--mm-ink3); font-size:.72rem; text-transform:capitalize; }
.al-fields input,.al-form input { width:110px; background:rgba(0,0,0,.25); border:1px solid var(--mm-border); color:var(--mm-ink); border-radius:5px; padding:.3rem .4rem; }
.al-fields code { color:var(--mm-ink2); font-size:.68rem; }
.al-actions { display:flex; align-items:center; gap:1rem; margin:1rem 0; color:var(--mm-ink3); font-size:.75rem; }
.al-form { display:flex; flex-wrap:wrap; align-items:end; gap:.8rem; margin-bottom:1rem; }
.al-form h3 { flex-basis:100%; }.al-form label { flex-direction:column; align-items:flex-start; }.al-form input { width:180px; }
.al-capital { font:500 2rem 'JetBrains Mono',monospace; margin:.8rem 0; }.al-capital small { font:400 .68rem Inter,sans-serif; color:var(--mm-ink3); text-transform:uppercase; }
.al-alert { padding:.55rem .8rem; border:1px solid var(--mm-border); border-radius:7px; margin:.6rem 0; font-size:.78rem; }.al-alert.good { color:var(--mm-good); }.al-alert.serious { color:var(--mm-serious); }
.al-diagnostic { display:flex; flex-direction:column; padding:.6rem; border-left:3px solid var(--mm-blue); background:rgba(255,255,255,.025); margin:.4rem 0; }.al-diagnostic.warning { border-color:var(--mm-warn); }.al-diagnostic.serious { border-color:var(--mm-serious); }.al-diagnostic.good { border-color:var(--mm-good); }.al-diagnostic span { color:var(--mm-ink2); font-size:.75rem; }
.al-outcome-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:.6rem; }.al-outcome-grid div { padding:.7rem; border:1px dashed var(--mm-border); border-radius:7px; display:flex; flex-direction:column; }.al-outcome-grid span { color:var(--mm-ink3); font-size:.68rem; }.al-outcome-grid strong { font-size:.78rem; color:var(--mm-ink2); }
`;
