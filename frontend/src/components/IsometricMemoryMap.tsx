import { useId, useState } from 'react';
import './IsometricMemoryMap.css';

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
  const id = useId();
  const zones = engine?.zones ?? [];
  const active = zones.find(zone => zone.id === selected) ?? zones[0];
  if (!active) return <p className="mm-empty">Memory zone model unavailable.</p>;

  const internal = zones.filter(zone => ['backend', 'cache'].includes(zone.boundary));
  const external = zones.filter(zone => !['backend', 'cache'].includes(zone.boundary));
  const tiles = zones.map((zone, index) => {
    const inside = internal.indexOf(zone);
    const position = inside >= 0 ? inside : external.indexOf(zone);
    return { zone, index,
      x: inside >= 0 ? 470 + ((position % 2) - Math.floor(position / 2)) * 142 : 60,
      y: inside >= 0 ? 130 + ((position % 2) + Math.floor(position / 2)) * 88 : 100 + position * 220,
    };
  });
  const left = Math.min(...tiles.map(tile => tile.x)) - 150;
  const right = Math.max(...tiles.map(tile => tile.x)) + 150;
  const bottom = Math.max(...tiles.map(tile => tile.y)) + 110;

  return (
    <section className="iso-map" aria-label="Isometric memory architecture">
      <header className="iso-map__header">
        <div><span className="iso-map__eyebrow">CORVUS / SYSTEM ANATOMY</span>
          <h3>Memory, under governance.</h3></div>
        <span className={`iso-map__stamp${fresh ? '' : ' iso-map__stamp--stale'}`}>
          {fresh ? 'SOURCE MODEL CURRENT' : 'SOURCE MODEL STALE'}
        </span>
      </header>
      <div className="iso-map__layout">
        <div className="iso-map__field">
          <div className="iso-map__coordinates"><span>01 / LOGICAL ZONES</span><span>ISOMETRIC / NOT TO SCALE</span></div>
          <svg viewBox={`${left} 0 ${right - left} ${bottom}`} role="group" aria-label="Select a memory zone">
            <defs>
              <pattern id={`${id}-grid`} width="48" height="28" patternUnits="userSpaceOnUse">
                <path d="M0 14L24 0L48 14L24 28Z" fill="none" stroke="currentColor" strokeWidth="0.5" />
              </pattern>
            </defs>
            <rect x={left} width={right - left} height={bottom} fill={`url(#${id}-grid)`} className="iso-map__grid" />
            <path d={`M470 25L${right - 15} 195L470 ${bottom - 12}L190 195Z`}
              fill="none" stroke="var(--iso-accent)" strokeDasharray="5 5" opacity=".45" />
            <text x="470" y="16" textAnchor="middle" className="iso-map__number">BACKEND PROCESS / MODULAR MONOLITH</text>
            {tiles.map(({ zone, index, x, y }) => (
              <g key={zone.id} transform={`translate(${x} ${y})`}
                className={`iso-map__tile${zone.id === active.id ? ' is-selected' : ''}`}
                role="button" tabIndex={0} aria-label={`Inspect ${zone.name}`}
                aria-pressed={zone.id === active.id} aria-controls={`${id}-inspector`}
                onClick={() => setSelected(zone.id)}
                onKeyDown={event => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault(); setSelected(zone.id);
                  }
                }}>
                <title>{zone.name}: {zone.boundary} boundary</title>
                <path className="iso-map__shadow" d="M0 -38L116 20L0 78L-116 20Z" />
                <path className="iso-map__wall" d="M-116 0L0 58L0 78L-116 20Z" />
                <path className="iso-map__wall iso-map__wall--right" d="M0 58L116 0L116 20L0 78Z" />
                <path className="iso-map__top" d="M0 -58L116 0L0 58L-116 0Z" />
                <text className="iso-map__number" textAnchor="middle" y="-31">{String(index + 1).padStart(2, '0')} / {zone.boundary.toUpperCase()}</text>
                {shortName(zone.name).map((line, lineIndex) => (
                  <text key={lineIndex} className="iso-map__label" textAnchor="middle" y={-4 + lineIndex * 15}>{line}</text>
                ))}
                <path className="iso-map__selection" d="M-125 -5L-125 7L-108 16M108 16L125 7L125 -5M-14 -62L0 -69L14 -62" />
              </g>
            ))}
          </svg>
          <p className="iso-map__caption">Select a slab to inspect its responsibility. Backend zones share one modular monolith; slabs do not imply separate services.</p>
          <div className="iso-map__zone-buttons" aria-label="Memory zones">
            {zones.map((zone, index) => <button key={zone.id} type="button"
              aria-pressed={active.id === zone.id} onClick={() => setSelected(zone.id)}>
              <span>{String(index + 1).padStart(2, '0')}</span>{zone.name}
            </button>)}
          </div>
        </div>
        <aside className="iso-map__inspector" id={`${id}-inspector`} aria-live="polite" aria-atomic="true">
          <span className="iso-map__eyebrow">02 / BOUNDARY INSPECTOR</span>
          <div className="iso-map__boundary">{active.boundary}</div>
          <h4>{active.name}</h4>
          <div className="iso-map__boundary">{active.boundary === 'datastore' ? 'Canonical state boundary'
            : active.boundary === 'cache' ? 'Derived state / inside backend process'
            : active.boundary === 'backend' ? 'Responsibility inside the shared backend'
            : 'Outside the backend process'}</div>
          <p>{active.purpose}</p>
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
        <ol>{engine.flow.map((step, index) => <li key={`${index}-${step}`}><b>{String(index + 1).padStart(2, '0')}</b>{step}</li>)}</ol>
        <p>{engine.summary}</p>
      </footer>
    </section>
  );
}
