import { useEffect, useState } from 'react';
import { fetchLayerFlow, type LayerFlowNode, type LayerFlowLink } from '../api/knowledge_graph';
import { DEPT_COLORS } from '../constants';

const LAYER_LABELS = ['L0 Dept', 'L1 Role', 'L2 Task', 'L3 System', 'L4 Decision', 'L5 Output'];

interface CellData {
  sourceKey: string;
  targetKey: string;
  sourceLabel: string;
  targetLabel: string;
  totalWeight: number;
  edgeCount: number;
}

export default function LayerHeatmap() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [minWeight, setMinWeight] = useState(0.15);
  const [nodes, setNodes] = useState<LayerFlowNode[]>([]);
  const [links, setLinks] = useState<LayerFlowLink[]>([]);
  const [hoverCell, setHoverCell] = useState<CellData | null>(null);
  const [selectedPair, setSelectedPair] = useState<[number, number] | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchLayerFlow(minWeight).then(data => {
      setNodes(data.nodes);
      setLinks(data.links);
      setLoading(false);
    }).catch(e => {
      setError(e instanceof Error ? e.message : 'Failed to load');
      setLoading(false);
    });
  }, [minWeight]);

  // Build layer-pair summary: for each (layerA, layerB) pair, aggregate by department
  const layerPairs: { sourceLayer: number; targetLayer: number; totalWeight: number; edgeCount: number }[] = [];
  const layerSet = [...new Set(nodes.map(n => n.layer))].sort();

  for (const sl of layerSet) {
    for (const tl of layerSet) {
      if (tl < sl) continue; // only upper triangle + diagonal
      let tw = 0;
      let ec = 0;
      for (const l of links) {
        const sn = nodes.find(n => n.key === l.source);
        const tn = nodes.find(n => n.key === l.target);
        if (!sn || !tn) continue;
        if ((sn.layer === sl && tn.layer === tl) || (sn.layer === tl && tn.layer === sl)) {
          tw += l.total_weight;
          ec += l.edge_count;
        }
      }
      if (tw > 0) {
        layerPairs.push({ sourceLayer: sl, targetLayer: tl, totalWeight: tw, edgeCount: ec });
      }
    }
  }

  const maxPairWeight = Math.max(...layerPairs.map(p => p.totalWeight), 1);

  // Detail view: when a layer pair is selected, show department×department breakdown
  let detailCells: CellData[] = [];
  let detailRows: string[] = [];
  let detailCols: string[] = [];
  let detailMax = 1;

  if (selectedPair) {
    const [sl, tl] = selectedPair;
    const cellMap = new Map<string, CellData>();

    for (const l of links) {
      const sn = nodes.find(n => n.key === l.source);
      const tn = nodes.find(n => n.key === l.target);
      if (!sn || !tn) continue;

      let srcDept: string | null = null;
      let tgtDept: string | null = null;

      if (sn.layer === sl && tn.layer === tl) {
        srcDept = sn.department || 'Unknown';
        tgtDept = tn.department || 'Unknown';
      } else if (sn.layer === tl && tn.layer === sl) {
        srcDept = tn.department || 'Unknown';
        tgtDept = sn.department || 'Unknown';
      }

      if (srcDept && tgtDept) {
        const key = `${srcDept}|${tgtDept}`;
        const existing = cellMap.get(key);
        if (existing) {
          existing.totalWeight += l.total_weight;
          existing.edgeCount += l.edge_count;
        } else {
          cellMap.set(key, {
            sourceKey: srcDept,
            targetKey: tgtDept,
            sourceLabel: srcDept,
            targetLabel: tgtDept,
            totalWeight: l.total_weight,
            edgeCount: l.edge_count,
          });
        }
      }
    }

    detailCells = [...cellMap.values()];
    detailRows = [...new Set(detailCells.map(c => c.sourceLabel))].sort();
    detailCols = [...new Set(detailCells.map(c => c.targetLabel))].sort();
    detailMax = Math.max(...detailCells.map(c => c.totalWeight), 1);
  }

  const cellSize = 64;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 16, overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0, color: 'var(--text)', fontSize: '1rem' }}>
          Co-Firing Layer Heatmap
        </h3>
        <label style={{ fontSize: '0.75rem', color: '#c8d0dc', display: 'flex', alignItems: 'center', gap: 6 }}>
          Min Weight:
          <input
            type="range" min={0} max={0.5} step={0.01} value={minWeight}
            onChange={e => setMinWeight(Number(e.target.value))}
            style={{ width: 100 }}
          />
          <span style={{ color: 'var(--text)', minWidth: 32 }}>{minWeight.toFixed(2)}</span>
        </label>
        {loading && <span style={{ fontSize: '0.75rem', color: '#fb923c' }}>Loading...</span>}
      </div>

      {error && <div style={{ color: '#ef4444', fontSize: '0.8rem', marginBottom: 8 }}>{error}</div>}

      {/* Overview: Layer × Layer grid */}
      <div style={{ marginBottom: 24 }}>
        <p style={{ fontSize: '0.8rem', color: '#c8d0dc', marginBottom: 12 }}>
          Each cell shows total co-firing weight between two layers. Click a cell to drill down by department.
        </p>
        <table style={{ borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ padding: '6px 8px', fontSize: '0.7rem', color: '#c8d0dc' }}></th>
              {layerSet.map(l => (
                <th key={l} style={{
                  padding: '6px 8px', fontSize: '0.7rem', color: '#c8d0dc', fontWeight: 600,
                  textAlign: 'center', minWidth: cellSize,
                }}>
                  {LAYER_LABELS[l]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {layerSet.map(sl => (
              <tr key={sl}>
                <td style={{
                  padding: '6px 8px', fontSize: '0.7rem', color: '#c8d0dc', fontWeight: 600,
                  whiteSpace: 'nowrap',
                }}>
                  {LAYER_LABELS[sl]}
                </td>
                {layerSet.map(tl => {
                  const pair = layerPairs.find(p =>
                    (p.sourceLayer === sl && p.targetLayer === tl) ||
                    (p.sourceLayer === tl && p.targetLayer === sl)
                  );
                  const weight = pair?.totalWeight || 0;
                  const intensity = weight / maxPairWeight;
                  const isSelected = selectedPair && (
                    (selectedPair[0] === sl && selectedPair[1] === tl) ||
                    (selectedPair[0] === tl && selectedPair[1] === sl)
                  );
                  const isMirror = tl < sl;

                  return (
                    <td
                      key={tl}
                      onClick={() => {
                        if (isMirror || !weight) return;
                        const pairKey: [number, number] = [Math.min(sl, tl), Math.max(sl, tl)];
                        setSelectedPair(prev =>
                          prev && prev[0] === pairKey[0] && prev[1] === pairKey[1] ? null : pairKey
                        );
                      }}
                      style={{
                        padding: 0,
                        width: cellSize,
                        height: cellSize,
                        textAlign: 'center',
                        cursor: isMirror || !weight ? 'default' : 'pointer',
                        position: 'relative',
                        border: isSelected ? '2px solid var(--accent)' : '1px solid var(--border)',
                      }}
                    >
                      {!isMirror && weight > 0 && (
                        <div style={{
                          width: '100%', height: '100%',
                          background: `rgba(56, 189, 248, ${Math.max(0.05, intensity * 0.8)})`,
                          display: 'flex', flexDirection: 'column',
                          alignItems: 'center', justifyContent: 'center',
                        }}>
                          <span style={{
                            fontSize: '0.85rem', fontWeight: 700,
                            color: intensity > 0.5 ? '#ffffff' : '#c8d0dc',
                          }}>
                            {weight >= 1000 ? `${(weight / 1000).toFixed(1)}k` : weight.toFixed(0)}
                          </span>
                          <span style={{
                            fontSize: '0.6rem',
                            color: intensity > 0.5 ? '#94a3b8' : '#64748b',
                          }}>
                            {pair?.edgeCount.toLocaleString()} edges
                          </span>
                        </div>
                      )}
                      {isMirror && (
                        <div style={{
                          width: '100%', height: '100%',
                          background: 'var(--bg)',
                        }} />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Detail: Department × Department for selected layer pair */}
      {selectedPair && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
            <h4 style={{ margin: 0, color: 'var(--text)', fontSize: '0.9rem' }}>
              {LAYER_LABELS[selectedPair[0]]} ↔ {LAYER_LABELS[selectedPair[1]]}
              {selectedPair[0] === selectedPair[1] && ' (within layer)'}
            </h4>
            <button
              onClick={() => setSelectedPair(null)}
              style={{
                background: 'none', border: '1px solid var(--border)', borderRadius: 4,
                color: '#c8d0dc', padding: '2px 8px', fontSize: '0.7rem', cursor: 'pointer',
              }}
            >
              Close
            </button>
          </div>

          {detailCells.length === 0 ? (
            <p style={{ color: '#c8d0dc', fontSize: '0.8rem' }}>No co-firing between these layers at this threshold.</p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ padding: '6px 8px', fontSize: '0.7rem', color: '#c8d0dc' }}></th>
                    {detailCols.map(col => (
                      <th key={col} style={{
                        padding: '6px 4px', fontSize: '0.65rem', color: DEPT_COLORS[col] || '#c8d0dc',
                        fontWeight: 600, textAlign: 'center', minWidth: 80, maxWidth: 100,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {col}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {detailRows.map(row => (
                    <tr key={row}>
                      <td style={{
                        padding: '6px 8px', fontSize: '0.7rem', fontWeight: 600,
                        color: DEPT_COLORS[row] || '#c8d0dc', whiteSpace: 'nowrap',
                      }}>
                        {row}
                      </td>
                      {detailCols.map(col => {
                        const cell = detailCells.find(c => c.sourceLabel === row && c.targetLabel === col);
                        const weight = cell?.totalWeight || 0;
                        const intensity = weight / detailMax;
                        const deptColor = DEPT_COLORS[row] || '#c8d0dc';

                        return (
                          <td
                            key={col}
                            onMouseEnter={() => cell ? setHoverCell(cell) : undefined}
                            onMouseLeave={() => setHoverCell(null)}
                            style={{
                              padding: 0, width: 80, height: 48,
                              textAlign: 'center',
                              border: '1px solid var(--border)',
                              position: 'relative',
                            }}
                          >
                            {weight > 0 && (
                              <div style={{
                                width: '100%', height: '100%',
                                background: `${deptColor}${Math.round(Math.max(0.08, intensity * 0.6) * 255).toString(16).padStart(2, '0')}`,
                                display: 'flex', alignItems: 'center', justifyContent: 'center',
                              }}>
                                <span style={{
                                  fontSize: '0.8rem', fontWeight: 600,
                                  color: intensity > 0.4 ? '#ffffff' : '#c8d0dc',
                                }}>
                                  {weight.toFixed(1)}
                                </span>
                              </div>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {hoverCell && (
            <div style={{
              marginTop: 8, padding: '8px 12px', fontSize: '0.75rem',
              background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 6,
              display: 'inline-block',
            }}>
              <strong style={{ color: DEPT_COLORS[hoverCell.sourceLabel] || 'var(--text)' }}>
                {hoverCell.sourceLabel}
              </strong>
              <span style={{ color: '#c8d0dc' }}> ↔ </span>
              <strong style={{ color: DEPT_COLORS[hoverCell.targetLabel] || 'var(--text)' }}>
                {hoverCell.targetLabel}
              </strong>
              <span style={{ color: '#c8d0dc', marginLeft: 12 }}>
                Weight: {hoverCell.totalWeight.toFixed(1)} | Edges: {hoverCell.edgeCount.toLocaleString()}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
