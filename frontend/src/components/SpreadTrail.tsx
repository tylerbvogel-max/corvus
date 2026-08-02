import { useEffect, useState, useMemo } from 'react';
import { fetchSpreadTrail } from '../api/knowledge_graph';
import type { NeuronScoreResponse, SpreadTrailResponse } from '../types';
import { DEPT_COLORS } from '../constants';
import SigmaGraph from './SigmaGraph';
import { neuronScoresToGraph } from '../utils/graphology-adapter';

interface Props {
  queryId: number;
  neuronScores?: NeuronScoreResponse[];
  onNavigateToNeuron?: (id: number) => void;
}

export default function SpreadTrail({ queryId, neuronScores, onNavigateToNeuron }: Props) {
  const [trailData, setTrailData] = useState<SpreadTrailResponse | null>(null);
  const neurons = neuronScores ?? [];

  useEffect(() => {
    fetchSpreadTrail(queryId)
      .then(setTrailData)
      .catch(() => setTrailData(null));
  }, [queryId]);

  const graph = useMemo(() => {
    return neuronScoresToGraph(neurons, trailData ?? undefined);
  }, [neurons, trailData]);

  if (neurons.length === 0) return null;

  const hasSpread = neurons.some(n => n.spread_boost > 0);

  // Department summary for legend
  const deptCounts = new Map<string, number>();
  for (const n of neurons) {
    const dept = n.department ?? 'Unknown';
    deptCounts.set(dept, (deptCounts.get(dept) ?? 0) + 1);
  }

  return (
    <div style={{ position: 'relative' }}>
      <div className="spread-radial-legend">
        {hasSpread && (
          <span className="spread-legend-item"><span className="spread-legend-ring" /> Spread-boosted</span>
        )}
        {Array.from(deptCounts.entries()).sort((a, b) => b[1] - a[1]).map(([dept, count]) => (
          <span key={dept} className="spread-legend-item">
            <span className="spread-legend-dot" style={{ background: DEPT_COLORS[dept] ?? '#c8d0dc' }} />
            {dept.replace('Administrative & Support', 'Admin')
              .replace('Manufacturing & Operations', 'Mfg & Ops')
              .replace('Contracts & Compliance', 'Contracts')
              .replace('Business Development', 'BD')
              .replace('Executive Leadership', 'Executive')
              .replace('Program Management', 'Program Mgmt')} ({count})
          </span>
        ))}
      </div>
      <SigmaGraph
        graph={graph}
        height={320}
        autoLayout={false}
        onNodeClick={(_key, attrs) => {
          const nid = attrs.neuron_id as number | undefined;
          if (nid && onNavigateToNeuron) onNavigateToNeuron(nid);
        }}
      />
    </div>
  );
}
