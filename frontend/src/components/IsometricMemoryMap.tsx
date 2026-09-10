import { useId, useState } from 'react';
import './IsometricMemoryMap.css';
import { layoutZones, reviewedFlows, flowCurve } from './isometricModel';

type Zone = {
  id: string;
  name: string;
  boundary: string;
  purpose: string;
  details: string[];
  evidence_ok: boolean;
  evidence_results: { evidence: string; exists: boolean; ok?: boolean }[];
};

type Engine = { summary: string; zones: Zone[]; flow: string[] };

function shortName(name: string) {
  const words = name.split(' ');
  const lines = [''];
  for (const word of words) {
    const index = lines.length - 1;
    if (lines[index].length + word.length > 22) lines.push(word);
    else lines[index] += `${lines[index] ? ' ' : ''}${word}`;
  }
  return lines;
}

/** Logical memory zones, not hosts, live traffic, or health telemetry. */
export default function IsometricMemoryMap({ engine, fresh }: { engine: Engine; fresh: boolean }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [sizing, setSizing] = useState('evidence');
  const [flowMode, setFlowMode] = useState('all');
  const [selectedFlow, setSelectedFlow] = useState<string | null>(null);
  const id = useId();
  const zones = engine?.zones ?? [];
  const active = zones.find(zone => zone.id === selected) ?? zones[0];
  if (!active) return <p className="mm-empty">Memory zone model unavailable.</p>;

  const scene = layoutZones(zones, sizing === 'evidence');
  const { tiles, perimeter, footprint } = scene;
  const flows = reviewedFlows(zones);
  const visibleFlows = flows.filter(flow => flowMode === 'all' || (flowMode === 'selected'
    ? flow.from === active.id || flow.to === active.id : flow.loops.includes(flowMode)));
  const flowDetail = visibleFlows.find(flow => flow.id === selectedFlow);
  const chooseZone = (zoneId: string) => { setSelected(zoneId); setSelectedFlow(null); setFlowMode('selected'); };

  return (
    <section className="iso-map" aria-label="Isometric memory architecture">
      <header className="iso-map__header">
        <div><span className="iso-map__eyebrow">CORVUS / SYSTEM ANATOMY</span>
          <h3>Memory, under governance.</h3></div>
        <span className={`iso-map__stamp${fresh ? '' : ' iso-map__stamp--stale'}`}>
          {fresh ? 'SOURCE MODEL CURRENT' : 'SOURCE MODEL STALE'}
        </span>
      </header>
      <div className="iso-map__controls">
        <label>Slab sizing<select value={sizing} onChange={event => setSizing(event.target.value)}>
          <option value="evidence">Exclusive evidence-linked files</option><option value="equal">Equal areas</option>
        </select></label>
        <label>Visible flows<select value={flowMode} onChange={event => { setFlowMode(event.target.value); setSelectedFlow(null); }}>
          <option value="all">All reviewed flows</option><option value="selected">Selected zone</option>
          <option value="capture">Capture and governed write</option><option value="recall">Recall and context delivery</option><option value="maintenance">Maintenance and projection</option>
        </select></label>
      </div>
      <p className="iso-map__caption">Area represents exclusive source-evidence file count, not total implementation size or RAM. Minimum visible area is 20.25% of the largest slab; exact counts remain authoritative. {footprint.shared.length} shared files are excluded from zone attribution. Arrows show reviewed data/control flows, not live traffic.</p>
      <div className="iso-map__layout">
        <div className="iso-map__field">
          <div className="iso-map__coordinates"><span>01 / LOGICAL ZONES</span><span>ISOMETRIC / NOT TO SCALE</span></div>
          <svg viewBox={scene.viewBox} role="group" aria-label="Select a memory zone">
            <defs>
              <pattern id={`${id}-grid`} width="48" height="28" patternUnits="userSpaceOnUse">
                <path d="M0 14L24 0L48 14L24 28Z" fill="none" stroke="currentColor" strokeWidth="0.5" />
              </pattern>
              <marker id={`${id}-arrow`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0L10 5L0 10Z" fill="var(--iso-accent)" /></marker>
            </defs>
            <rect x={scene.left} y={scene.top} width={scene.width} height={scene.height} fill={`url(#${id}-grid)`} className="iso-map__grid" />
            {perimeter.length > 0 && <>
              <polygon className="iso-map__perimeter" points={perimeter.map(point => `${point.x},${point.y}`).join(' ')} />
              <text x={perimeter[0].x} y={perimeter[0].y - 16} textAnchor="middle" className="iso-map__number">BACKEND PROCESS / MODULAR MONOLITH</text>
            </>}
            {visibleFlows.map(flow => {
              const from = tiles.find(tile => tile.zone.id === flow.from)!;
              const to = tiles.find(tile => tile.zone.id === flow.to)!;
              const curve = flowCurve(from, to);
              return <g key={flow.id} className={`iso-map__flow${flowDetail?.id === flow.id ? ' is-selected' : ''}`} data-flow={flow.id}>
                <path d={curve.path} markerEnd={`url(#${id}-arrow)`} />
                <path className="iso-map__flow-hit" d={curve.path} role="button" tabIndex={0} aria-label={`Inspect flow: ${flow.label}`}
                  onClick={() => setSelectedFlow(flow.id)} onKeyDown={event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); setSelectedFlow(flow.id); } }} />
                <text x={curve.label.x} y={curve.label.y - 7} textAnchor="middle">{flow.label}</text>
              </g>;
            })}
            {tiles.map(({ zone, index, x, y, scale }) => (
              <g key={zone.id} transform={`translate(${x} ${y})`}
                data-zone={zone.id} data-boundary={zone.boundary} data-scale={scale}
                className={`iso-map__tile${zone.id === active.id ? ' is-selected' : ''}`}
                role="button" tabIndex={0} aria-label={`Inspect ${zone.name}`}
                aria-pressed={zone.id === active.id} aria-controls={`${id}-inspector`}
                onClick={() => chooseZone(zone.id)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault(); chooseZone(zone.id);
                  }
                }}>
                <title>{zone.name}: {zone.boundary} boundary</title>
                <g transform={`scale(${scale})`}>
                <path className="iso-map__shadow" d="M0 -38L116 20L0 78L-116 20Z" />
                <path className="iso-map__wall" d="M-116 0L0 58L0 78L-116 20Z" />
                <path className="iso-map__wall iso-map__wall--right" d="M0 58L116 0L116 20L0 78Z" />
                <path className="iso-map__top" d="M0 -58L116 0L0 58L-116 0Z" />
                </g>
                <text className="iso-map__number" textAnchor="middle" y={-31 * scale}>{String(index + 1).padStart(2, '0')} / {zone.boundary.toUpperCase()}</text>
                {shortName(zone.name).map((line, lineIndex) => (
                  <text key={lineIndex} className="iso-map__label" textAnchor="middle" y={-4 + lineIndex * 15}>{line}</text>
                ))}
                <text className="iso-map__number" textAnchor="middle" y={78 * scale + 16}>{footprint.exclusive.get(zone.id)?.length ?? 0} exclusive files</text>
                <path transform={`scale(${scale})`} className="iso-map__selection" d="M-125 -5L-125 7L-108 16M108 16L125 7L125 -5M-14 -62L0 -69L14 -62" />
              </g>
            ))}
          </svg>
          <p className="iso-map__caption">Select a slab to inspect its responsibility. Backend zones share one modular monolith; slabs do not imply separate services.</p>
          <div className="iso-map__zone-buttons" aria-label="Memory zones">
            {zones.map((zone, index) => <button key={zone.id} type="button"
              aria-pressed={active.id === zone.id} onClick={() => chooseZone(zone.id)}>
              <span>{String(index + 1).padStart(2, '0')}</span>{zone.name}
            </button>)}
          </div>
          <div className="iso-map__flow-buttons" aria-label="Visible architectural connections">
            {visibleFlows.map(flow => <button type="button" key={flow.id} aria-pressed={flowDetail?.id === flow.id} onClick={() => setSelectedFlow(flow.id)}>{flow.label}</button>)}
          </div>
        </div>
        <aside className="iso-map__inspector" id={`${id}-inspector`} aria-live="polite" aria-atomic="true">
          {flowDetail && <section className="iso-map__flow-detail">
            <span className="iso-map__eyebrow">CONNECTION / {flowDetail.transport}</span>
            <h4>{flowDetail.label}</h4>
            <p>{zones.find(zone => zone.id === flowDetail.from)?.name} → {zones.find(zone => zone.id === flowDetail.to)?.name}</p>
            <p>{flowDetail.why}</p>
            <div className="iso-map__evidence">{flowDetail.evidence.map(path => <code key={path}>{path}</code>)}</div>
            <p className="iso-map__disclosure">Reviewed architectural relationship. References are inspectable source pointers, not live tracing or a new mechanical proof.</p>
          </section>}
          <span className="iso-map__eyebrow">02 / BOUNDARY INSPECTOR</span>
          <div className="iso-map__boundary">{active.boundary}</div>
          <h4>{active.name}</h4>
          <div className="iso-map__boundary">{active.boundary === 'datastore' ? 'Canonical state boundary'
            : active.boundary === 'cache' ? 'Derived state / inside backend process'
            : active.boundary === 'backend' ? 'Responsibility inside the shared backend'
            : 'Outside the backend process'}</div>
          <p>{active.purpose}</p>
          <details className="iso-map__sizing-detail"><summary>{footprint.exclusive.get(active.id)?.length ?? 0} exclusively referenced code files</summary>
            <div className="iso-map__evidence">{footprint.exclusive.get(active.id)?.map(path => <code key={path}>{path}</code>)}</div>
            <p className="iso-map__disclosure">Evidence scope, not a complete ownership inventory. Shared references are not apportioned to this zone.</p>
          </details>
          <ul>{active.details.map(detail => <li key={detail}>{detail}</li>)}</ul>
          <div className="iso-map__evidence-heading">{active.evidence_ok ? 'Source evidence linked' : 'Source evidence gap'}</div>
          <div className="iso-map__evidence">{active.evidence_results.map(evidence => (
            <code key={evidence.evidence} data-valid={evidence.ok ?? evidence.exists}>
              {(evidence.ok ?? evidence.exists) ? '+ ' : '! '}{evidence.evidence}
            </code>
          ))}</div>
          <p className="iso-map__disclosure">Evidence linkage is not runtime health or a security certification.</p>
        </aside>
      </div>
      <footer className="iso-map__footer"><span>03 / REVIEWED LIFECYCLE</span>
        <details><summary>{footprint.shared.length} shared source references (not counted twice)</summary><div className="iso-map__evidence">{footprint.shared.map(path => <code key={path}>{path}</code>)}</div></details>
        <ol>{engine.flow.map((step, index) => <li key={`${index}-${step}`}><b>{String(index + 1).padStart(2, '0')}</b>{step}</li>)}</ol>
        <p>{engine.summary}</p>
      </footer>
    </section>
  );
}
