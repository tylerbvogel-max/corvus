import { useEffect, useMemo, useState } from 'react';
import { fetchVentureTopology, type AgencyVenture, type AgencyWorker, type VentureTopology } from '../api';

type Selection = { kind: 'node'; id: string } | { kind: 'order'; id: string } | null;
type Point = { x: number; y: number };

const STATUS: Record<string, string> = {
  issued: '#7c8ca5', accepted: '#4f91e8', submitted: '#d5a63f', auditing: '#b681df',
  verified: '#4ab58b', failed: '#e16363', invalidated: '#a45b68',
};

function layout(graph: VentureTopology['graph']) {
  const incoming = new Map(graph.nodes.map(n => [n.id, 0]));
  graph.edges.forEach(e => incoming.set(e.to, (incoming.get(e.to) || 0) + 1));
  const levels = new Map<string, number>();
  let queue = graph.nodes.filter(n => !incoming.get(n.id)).map(n => n.id);
  queue.forEach(id => levels.set(id, 0));
  while (queue.length) {
    const id = queue.shift()!;
    graph.edges.filter(e => e.from === id).forEach(e => {
      levels.set(e.to, Math.max(levels.get(e.to) || 0, (levels.get(id) || 0) + 1));
      incoming.set(e.to, (incoming.get(e.to) || 1) - 1);
      if (!incoming.get(e.to)) queue.push(e.to);
    });
  }
  graph.nodes.forEach(n => { if (!levels.has(n.id)) levels.set(n.id, 0); });
  const groups = new Map<number, string[]>();
  graph.nodes.forEach(n => groups.set(levels.get(n.id)!, [...(groups.get(levels.get(n.id)!) || []), n.id]));
  const positions = new Map<string, Point>();
  groups.forEach((ids, level) => ids.forEach((id, index) => positions.set(id, { x: 170 + level * 340, y: 130 + index * 250 })));
  return positions;
}

export default function VentureGraphPage({ ventures, workers }: { ventures: AgencyVenture[]; workers: AgencyWorker[] }) {
  const [ventureKey, setVentureKey] = useState('');
  const [data, setData] = useState<VentureTopology | null>(null);
  const [selection, setSelection] = useState<Selection>(null);
  const [error, setError] = useState('');
  const [zoom, setZoom] = useState(1);
  useEffect(() => { if (!ventureKey && ventures.length) setVentureKey(ventures[0].key); }, [ventures, ventureKey]);
  useEffect(() => { if (!ventureKey) return; setError(''); fetchVentureTopology(ventureKey).then(x => { setData(x); setSelection(null); }).catch(e => setError(e.message)); }, [ventureKey]);
  const positions = useMemo(() => data ? layout(data.graph) : new Map<string, Point>(), [data]);
  const ordersByNode = useMemo(() => { const map = new Map<string, VentureTopology['work_orders']>(); data?.work_orders.forEach(w => map.set(w.plan_node_id, [...(map.get(w.plan_node_id) || []), w])); return map; }, [data]);
  const maxX = Math.max(900, ...[...positions.values()].map(p => p.x + 300));
  const maxY = Math.max(560, ...[...positions.entries()].map(([id, p]) => p.y + 160 + (ordersByNode.get(id)?.length || 0) * 58));
  const selectedNode = selection?.kind === 'node' ? data?.graph.nodes.find(n => n.id === selection.id) : null;
  const selectedOrder = selection?.kind === 'order' ? data?.work_orders.find(w => w.id === selection.id) : null;
  const worker = selectedOrder?.worker || workers.find(w => w.id === selectedOrder?.worker?.id);

  if (!ventures.length) return <div className="mm-empty">Create a mission first; its constitutional graph will appear here.</div>;
  return <div className="vg-page">
    <section className="vg-toolbar">
      <label><span>Venture</span><select value={ventureKey} onChange={e => setVentureKey(e.target.value)}>{ventures.map(v => <option key={v.key} value={v.key}>{v.title} · r{v.current_revision}</option>)}</select></label>
      <div className="vg-legend"><i className="plan" /> north-star node <i className="order" /> work order <i className="dependency" /> dependency <i className="tether" /> projection</div>
      <div className="vg-zoom"><button onClick={() => setZoom(z => Math.max(.55, z - .15))}>−</button><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom(z => Math.min(1.6, z + .15))}>+</button></div>
    </section>
    {error && <div className="al-alert serious">{error}</div>}
    {data && <><section className="vg-mission"><div><span>Mission · revision {data.venture.revision}</span><strong>{data.graph.mission || data.venture.title}</strong></div><code>{data.revision.digest.slice(0, 14)}</code>
      <div><span>Constraints</span><strong>{data.graph.constraints.length}</strong></div><div><span>Kill criteria</span><strong>{data.graph.kill_criteria.length}</strong></div><div><span>Orders</span><strong>{data.work_orders.length}</strong></div></section>
      <div className="vg-shell"><div className="vg-canvas"><svg viewBox={`0 0 ${maxX} ${maxY}`} style={{ width: `${maxX * zoom}px`, height: `${maxY * zoom}px` }} role="img" aria-label={`Venture graph for ${data.venture.title}`}>
        <defs><marker id="vg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#52647a" /></marker></defs>
        {data.graph.edges.map((edge, i) => { const a = positions.get(edge.from)!, b = positions.get(edge.to)!; return <path key={`${edge.from}-${edge.to}-${i}`} className="vg-edge" d={`M ${a.x + 220} ${a.y + 50} C ${a.x + 275} ${a.y + 50}, ${b.x - 55} ${b.y + 50}, ${b.x} ${b.y + 50}`} markerEnd="url(#vg-arrow)"><title>{edge.from} enables {edge.to}</title></path>; })}
        {data.graph.nodes.map(node => { const p = positions.get(node.id)!; const orders = ordersByNode.get(node.id) || []; return <g key={node.id}>
          <g className={`vg-node ${selection?.kind === 'node' && selection.id === node.id ? 'selected' : ''}`} tabIndex={0} role="button" onClick={() => setSelection({ kind: 'node', id: node.id })} onKeyDown={e => e.key === 'Enter' && setSelection({ kind: 'node', id: node.id })}>
            <rect x={p.x} y={p.y} width="220" height="100" rx="14" /><text x={p.x + 18} y={p.y + 25} className="eyebrow">NORTH STAR · T{node.risk_tier}</text><text x={p.x + 18} y={p.y + 52} className="title">{node.title.slice(0, 27)}</text><text x={p.x + 18} y={p.y + 76} className="sub">{node.task_class} · {orders.length} order{orders.length === 1 ? '' : 's'}</text><title>{node.outcome}</title>
          </g>
          {orders.map((order, index) => { const ox = p.x + 22 + (index % 2) * 102, oy = p.y + 138 + Math.floor(index / 2) * 62; return <g key={order.id}>
            <path className="vg-tether" d={`M ${p.x + 110} ${p.y + 100} C ${p.x + 110} ${oy - 20}, ${ox + 38} ${oy - 15}, ${ox + 38} ${oy}`} />
            <g className={`vg-order ${selection?.kind === 'order' && selection.id === order.id ? 'selected' : ''}`} tabIndex={0} role="button" onClick={() => setSelection({ kind: 'order', id: order.id })} onKeyDown={e => e.key === 'Enter' && setSelection({ kind: 'order', id: order.id })}>
              <circle cx={ox + 38} cy={oy + 28} r="27" style={{ fill: STATUS[order.status] || STATUS.issued }} /><text x={ox + 38} y={oy + 25} textAnchor="middle" className="order-mark">WO</text><text x={ox + 38} y={oy + 40} textAnchor="middle" className="order-state">{order.status.slice(0, 7)}</text><title>{order.id} · {order.worker?.display_name || 'unassigned'}</title>
            </g></g>; })}
        </g>; })}
      </svg></div><aside className="vg-inspector">{!selection && <div className="mm-empty">Select a north-star node or work order to inspect its contract surface.</div>}
        {selectedNode && <><span className="vg-kicker">Constitutional node · T{selectedNode.risk_tier}</span><h3>{selectedNode.title}</h3><p>{selectedNode.outcome}</p><h4>Acceptance contract</h4><ul>{selectedNode.acceptance.map(x => <li key={x}>{x}</li>)}</ul><h4>Attached work</h4><strong>{ordersByNode.get(selectedNode.id)?.length || 0} work orders</strong></>}
        {selectedOrder && <><span className="vg-kicker">Work order · {selectedOrder.status}</span><h3>{selectedOrder.id}</h3><p>{worker ? `${worker.display_name} · ${worker.model} · ${worker.harness}` : 'Unassigned worker'}</p><dl><dt>Contract</dt><dd>{selectedOrder.contract_digest.slice(0, 14)}</dd><dt>Audit</dt><dd>{String(selectedOrder.audit.passed ?? (selectedOrder.audit.critic_selected ? 'selected' : 'pending'))}</dd><dt>Claims locked</dt><dd>{selectedOrder.completion ? 'yes' : 'no'}</dd></dl>{Array.isArray(selectedOrder.audit.defects) && <><h4>Defects</h4><strong>{selectedOrder.audit.defects.length}</strong></>}</>}
      </aside></div></>}
    <style>{CSS}</style>
  </div>;
}

const CSS = `
.vg-page{display:flex;flex-direction:column;gap:.8rem}.vg-toolbar,.vg-mission{display:flex;align-items:center;gap:1rem;padding:.7rem .9rem;border:1px solid var(--mm-border);border-radius:9px;background:rgba(255,255,255,.02)}.vg-toolbar label{display:flex;flex-direction:column;gap:.2rem;color:var(--mm-ink3);font-size:.67rem;text-transform:uppercase}.vg-toolbar select{min-width:260px;background:#111820;color:var(--mm-ink);border:1px solid var(--mm-border);border-radius:5px;padding:.42rem}.vg-legend{display:flex;align-items:center;gap:.45rem;color:var(--mm-ink3);font-size:.7rem;flex:1}.vg-legend i{display:inline-block;width:14px;height:10px;border:2px solid #4f91e8;border-radius:3px;margin-left:.4rem}.vg-legend i.order{border:0;border-radius:50%;background:#d5a63f;width:11px;height:11px}.vg-legend i.dependency{height:0;border:0;border-top:2px solid #52647a;width:20px}.vg-legend i.tether{height:0;border:0;border-top:1px dashed #64788e;width:20px}.vg-zoom{display:flex;align-items:center;gap:.4rem}.vg-zoom button{width:28px;height:28px;border:1px solid var(--mm-border);background:transparent;color:var(--mm-ink);border-radius:5px}.vg-zoom span{font:11px monospace;color:var(--mm-ink3)}.vg-mission>div:first-child{display:flex;flex-direction:column;flex:1}.vg-mission span,.vg-kicker{color:var(--mm-ink3);font-size:.65rem;text-transform:uppercase;letter-spacing:.08em}.vg-mission>div:not(:first-child){display:flex;flex-direction:column;min-width:70px}.vg-mission code{color:var(--mm-aqua)}.vg-shell{display:grid;grid-template-columns:minmax(0,1fr) 280px;min-height:610px;border:1px solid var(--mm-border);border-radius:10px;overflow:hidden}.vg-canvas{overflow:auto;background:radial-gradient(circle at 1px 1px,rgba(120,150,180,.12) 1px,transparent 0);background-size:24px 24px}.vg-canvas svg{display:block;min-width:100%;min-height:100%}.vg-edge{fill:none;stroke:#52647a;stroke-width:2}.vg-tether{fill:none;stroke:#64788e;stroke-width:1.2;stroke-dasharray:5 5}.vg-node,.vg-order{cursor:pointer;outline:none}.vg-node rect{fill:#172333;stroke:#4f91e8;stroke-width:2}.vg-node:hover rect,.vg-node.selected rect{stroke:#75d4d0;filter:drop-shadow(0 0 7px rgba(117,212,208,.35))}.vg-node text{pointer-events:none}.vg-node .eyebrow{fill:#7f95aa;font:600 10px Inter,sans-serif;letter-spacing:.08em}.vg-node .title{fill:#eef5fb;font:600 15px Inter,sans-serif}.vg-node .sub{fill:#93a4b5;font:11px Inter,sans-serif}.vg-order circle{stroke:#d8e0e8;stroke-width:1.5}.vg-order:hover circle,.vg-order.selected circle{stroke:#fff;stroke-width:3;filter:drop-shadow(0 0 6px rgba(255,255,255,.3))}.order-mark{fill:#fff;font:700 9px Inter,sans-serif}.order-state{fill:#fff;font:600 7px Inter,sans-serif;text-transform:uppercase}.vg-inspector{border-left:1px solid var(--mm-border);background:rgba(8,13,20,.84);padding:1rem;overflow:auto}.vg-inspector h3{overflow-wrap:anywhere}.vg-inspector p,.vg-inspector li{color:var(--mm-ink2);font-size:.76rem;line-height:1.5}.vg-inspector h4{margin:1.2rem 0 .4rem;color:var(--mm-ink3);font-size:.68rem;text-transform:uppercase}.vg-inspector ul{padding-left:1.1rem}.vg-inspector dl{display:grid;grid-template-columns:80px 1fr;gap:.45rem;font-size:.72rem}.vg-inspector dt{color:var(--mm-ink3)}.vg-inspector dd{margin:0;color:var(--mm-ink);overflow-wrap:anywhere}@media(max-width:900px){.vg-shell{grid-template-columns:1fr}.vg-inspector{border-left:0;border-top:1px solid var(--mm-border)}.vg-legend{display:none}.vg-toolbar{flex-wrap:wrap}}
`;
