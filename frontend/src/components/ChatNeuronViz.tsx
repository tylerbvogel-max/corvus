import { useEffect, useState, useMemo } from 'react';
import { fetchSpreadTrail } from '../api';
import type { NeuronScoreResponse, SpreadTrailResponse } from '../types';
import { DEPT_COLORS } from '../constants';
import SigmaGraph from './SigmaGraph';
import { neuronScoresToGraph } from '../utils/graphology-adapter';

/**
 * 2D Sigma.js graph visualization for neuron firing.
 * Hierarchical parent->child edges create a real tree topology.
 * Prompt node pinned at top; hover shows full details in a bottom popup.
 */

const LAYER_LABELS = ['Dept', 'Role', 'Task', 'System', 'Decision', 'Output'];

interface Props {
  neuronScores: NeuronScoreResponse[];
  neuronsActivated: number;
  queryId?: number;
  onNavigateToNeuron?: (id: number) => void;
  collapsed?: boolean;
  fullSize?: boolean;
}

function shortDept(dept: string): string {
  return dept
    .replace('Manufacturing & Operations', 'Mfg & Ops')
    .replace('Contracts & Compliance', 'Contracts')
    .replace('Business Development', 'BD')
    .replace('Executive Leadership', 'Executive')
    .replace('Administrative & Support', 'Admin')
    .replace('Program Management', 'Program Mgmt');
}

export default function ChatNeuronViz({ neuronScores, neuronsActivated, queryId, onNavigateToNeuron, collapsed, fullSize }: Props) {
  const [trailData, setTrailData] = useState<SpreadTrailResponse | null>(null);
  const [hoverAttrs, setHoverAttrs] = useState<Record<string, unknown> | null>(null);

  const neurons = neuronScores;
  const isExpanded = !collapsed;

  // Fetch spread trail for edges
  useEffect(() => {
    if (queryId) {
      fetchSpreadTrail(queryId).then(setTrailData).catch(() => setTrailData(null));
    }
  }, [queryId]);

  // Fetch ancestor neurons for hierarchy bridge nodes
  const [ancestorData, setAncestorData] = useState<Map<number, { id: number; label: string; department: string | null; layer: number; parent_id: number | null }>>(new Map());

  useEffect(() => {
    if (neurons.length === 0) return;
    const neuronIdSet = new Set(neurons.map(n => n.neuron_id));
    const missingParents = new Set<number>();
    for (const n of neurons) {
      if (n.parent_id != null && !neuronIdSet.has(n.parent_id)) {
        missingParents.add(n.parent_id);
      }
    }
    if (missingParents.size === 0) { setAncestorData(new Map()); return; }

    const fetchAncestors = async () => {
      const map = new Map<number, { id: number; label: string; department: string | null; layer: number; parent_id: number | null }>();
      const toFetch = [...missingParents];
      for (let depth = 0; depth < 6 && toFetch.length > 0; depth++) {
        const batch = toFetch.splice(0, toFetch.length);
        const results = await Promise.all(
          batch.map(id =>
            fetch(`/neurons/${id}`).then(r => r.ok ? r.json() : null).catch(() => null)
          )
        );
        for (const r of results) {
          if (!r) continue;
          if (!map.has(r.id)) {
            map.set(r.id, { id: r.id, label: r.label, department: r.department, layer: r.layer, parent_id: r.parent_id });
          }
          if (r.parent_id != null && !neuronIdSet.has(r.parent_id) && !map.has(r.parent_id)) {
            toFetch.push(r.parent_id);
          }
        }
      }
      setAncestorData(map);
    };
    fetchAncestors();
  }, [neurons]);

  // Build Graphology graph
  const graph = useMemo(() => {
    return neuronScoresToGraph(neurons, trailData ?? undefined, ancestorData);
  }, [neurons, trailData, ancestorData]);

  if (neurons.length === 0) return null;

  const deptCounts = new Map<string, number>();
  for (const n of neurons) {
    const d = n.department || 'Unknown';
    deptCounts.set(d, (deptCounts.get(d) || 0) + 1);
  }
  const spreadCount = neurons.filter(n => n.spread_boost > 0).length;
  const edgeCount = trailData?.edges?.length ?? 0;

  return (
    <div className="chat-neuron-viz" style={{
      ...(fullSize ? { marginBottom: 16 } : {}),
      width: '100%',
      height: '100%',
    }}>
      {fullSize ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <span style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text)' }}>Neuron Firing</span>
          <span style={{ fontSize: '0.7rem', color: '#64748b' }}>
            {neuronsActivated} activated &middot; {edgeCount} edges &middot; {spreadCount} spread-boosted
          </span>
        </div>
      ) : null}
      {isExpanded && (
        <div className="chat-neuron-viz-canvas-wrap" style={{ height: '100%' }}>
          <div style={{ flex: 1, minWidth: 0, position: 'relative', height: '100%', width: '100%' }}>
            <SigmaGraph
              graph={graph}
              autoLayout={false}
              onNodeClick={(_key, attrs) => {
                const nid = attrs.neuron_id as number | undefined;
                if (nid && onNavigateToNeuron) onNavigateToNeuron(nid);
              }}
              onNodeHover={(_key, attrs) => setHoverAttrs(attrs)}
            />
            {/* Bottom hover detail panel */}
            {hoverAttrs && !hoverAttrs.isPrompt && (
              <div style={{
                position: 'absolute', bottom: 0, left: 0, right: 0,
                background: 'rgba(15, 23, 42, 0.92)',
                backdropFilter: 'blur(8px)',
                borderTop: '1px solid rgba(59, 130, 246, 0.2)',
                padding: '10px 16px',
                fontSize: '0.78rem',
                color: '#e2e8f0',
                zIndex: 20,
                pointerEvents: 'none',
                display: 'flex',
                gap: 24,
                alignItems: 'flex-start',
                flexWrap: 'wrap',
              }}>
                {/* Left: name + department + layer */}
                <div style={{ minWidth: 160 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 2 }}>
                    {String(hoverAttrs.label ?? '')}
                  </div>
                  <div style={{ color: String(hoverAttrs.color ?? '#94a3b8'), fontSize: '0.72rem' }}>
                    {hoverAttrs.layer === -1 ? 'Concept' : shortDept(String(hoverAttrs.department ?? ''))}
                    {' · '}
                    {hoverAttrs.layer === -1 ? 'Concept' : (LAYER_LABELS[Number(hoverAttrs.layer)] ?? `L${hoverAttrs.layer}`)}
                  </div>
                  {typeof hoverAttrs.summary === 'string' && hoverAttrs.summary && (
                    <div style={{ color: '#94a3b8', fontSize: '0.7rem', marginTop: 4, maxWidth: 300 }}>
                      {hoverAttrs.summary}
                    </div>
                  )}
                </div>
                {/* Center: signal scores */}
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <div><span style={{ color: '#64748b' }}>Combined</span> <strong>{Number(hoverAttrs.combined ?? 0).toFixed(3)}</strong></div>
                  {hoverAttrs.burst != null && <div><span style={{ color: '#64748b' }}>Burst</span> {Number(hoverAttrs.burst).toFixed(3)}</div>}
                  {hoverAttrs.impact != null && <div><span style={{ color: '#64748b' }}>Impact</span> {Number(hoverAttrs.impact).toFixed(3)}</div>}
                  {hoverAttrs.precision != null && <div><span style={{ color: '#64748b' }}>Precision</span> {Number(hoverAttrs.precision).toFixed(3)}</div>}
                  {hoverAttrs.novelty != null && <div><span style={{ color: '#64748b' }}>Novelty</span> {Number(hoverAttrs.novelty).toFixed(3)}</div>}
                  {hoverAttrs.recency != null && <div><span style={{ color: '#64748b' }}>Recency</span> {Number(hoverAttrs.recency).toFixed(3)}</div>}
                  {hoverAttrs.relevance != null && <div><span style={{ color: '#64748b' }}>Relevance</span> {Number(hoverAttrs.relevance).toFixed(3)}</div>}
                </div>
                {/* Right: spread boost */}
                {Number(hoverAttrs.spread_boost ?? 0) > 0 && (
                  <div style={{ color: '#e8a735' }}>
                    Spread +{Number(hoverAttrs.spread_boost).toFixed(3)}
                  </div>
                )}
              </div>
            )}
          </div>
          {fullSize && (
            <div className="chat-neuron-viz-legend" style={{ width: 175 }}>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: '0.68rem', color: '#64748b', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Departments</div>
                {Array.from(deptCounts.entries()).sort((a, b) => b[1] - a[1]).map(([dept, count]) => (
                  <div key={dept} className="chat-neuron-viz-legend-row">
                    <span className="chat-neuron-viz-dept-dot" style={{ background: DEPT_COLORS[dept] || '#4a5568' }} />
                    <span className="chat-neuron-viz-legend-label">{shortDept(dept)}</span>
                    <span className="chat-neuron-viz-legend-count">{count}</span>
                  </div>
                ))}
                {spreadCount > 0 && (
                  <div className="chat-neuron-viz-legend-row">
                    <span className="chat-neuron-viz-dept-dot" style={{ border: '1.5px solid #e8a735', background: 'transparent' }} />
                    <span className="chat-neuron-viz-legend-label" style={{ color: '#e8a735' }}>Spread</span>
                    <span className="chat-neuron-viz-legend-count">{spreadCount}</span>
                  </div>
                )}
              </div>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: '0.68rem', color: '#64748b', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Layers</div>
                {LAYER_LABELS.map((lbl, layer) => {
                  const count = neurons.filter(s => s.layer === layer).length;
                  const pct = neurons.length > 0 ? (count / neurons.length) * 100 : 0;
                  return (
                    <div key={layer} style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
                      <span style={{ fontSize: '0.58rem', color: '#64748b', width: 46 }}>L{layer} {lbl}</span>
                      <div style={{ flex: 1, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                        <div style={{ height: '100%', borderRadius: 2, background: '#3b82f6', width: `${pct}%`, transition: 'width 0.5s ease' }} />
                      </div>
                      <span style={{ fontSize: '0.58rem', color: count > 0 ? 'var(--text)' : '#334155', width: 14, textAlign: 'right' }}>{count}</span>
                    </div>
                  );
                })}
              </div>
              <div>
                <div style={{ fontSize: '0.68rem', color: '#64748b', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Strongest</div>
                {neurons.slice(0, 5).map((n, i) => (
                  <div key={n.neuron_id} style={{
                    padding: '3px 6px', marginBottom: 2, borderRadius: 4,
                    background: i === 0 ? 'rgba(59,130,246,0.08)' : 'transparent',
                    border: i === 0 ? '1px solid rgba(59,130,246,0.15)' : '1px solid transparent',
                    cursor: onNavigateToNeuron ? 'pointer' : 'default',
                  }} onClick={() => onNavigateToNeuron?.(n.neuron_id)}>
                    <div style={{ fontSize: '0.68rem', fontWeight: i === 0 ? 600 : 400, color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {n.label || `#${n.neuron_id}`}
                    </div>
                    <div style={{ fontSize: '0.58rem', color: '#64748b', display: 'flex', gap: 6 }}>
                      <span>{n.combined.toFixed(3)}</span>
                      <span style={{ color: DEPT_COLORS[n.department || ''] || '#64748b' }}>{shortDept(n.department || '')}</span>
                      {n.spread_boost > 0 && <span style={{ color: '#e8a735' }}>+{n.spread_boost.toFixed(3)}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
