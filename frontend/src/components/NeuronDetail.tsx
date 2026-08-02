import { useEffect, useState } from 'react'
import { fetchNeuron, fetchScores } from '../api/knowledge_graph';
import type { NeuronDetail as NeuronDetailType, NeuronScores } from '../types'
import ScoreBars from './ScoreBars'
import NeuronEgoGraph from './NeuronEgoGraph'

const layerNames = ['Department', 'Role', 'Task', 'System', 'Decision', 'Output'];
const layerColors = [
  'var(--layer0)', 'var(--layer1)', 'var(--layer2)',
  'var(--layer3)', 'var(--layer4)', 'var(--layer5)',
];

export default function NeuronDetail({ neuronId, onSelectNeuron }: { neuronId: number; onSelectNeuron?: (id: number) => void }) {
  const [detail, setDetail] = useState<NeuronDetailType | null>(null);
  const [scores, setScores] = useState<NeuronScores | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setError('');
    Promise.all([fetchNeuron(neuronId), fetchScores(neuronId)])
      .then(([d, s]) => { setDetail(d); setScores(s); })
      .catch(e => setError(e.message));
  }, [neuronId]);

  if (error) return <div className="error-msg">{error}</div>;
  if (!detail || !scores) return <div className="loading">Loading...</div>;

  return (
    <div className="neuron-detail">
      <h2>{detail.label}</h2>

      <div className="detail-meta">
        <span className="meta-chip layer-chip" style={{ borderColor: layerColors[detail.layer], color: layerColors[detail.layer] }}>
          L{detail.layer} {layerNames[detail.layer]}
        </span>
        <span className="meta-chip">{detail.node_type}</span>
        {detail.department && <span className="meta-chip">{detail.department}</span>}
        {detail.role_key && <span className="meta-chip">{detail.role_key}</span>}
        <span className="meta-chip" style={{ color: detail.is_active ? 'var(--precision)' : 'var(--impact)' }}>
          {detail.is_active ? 'Active' : 'Inactive'}
        </span>
      </div>

      <div className="detail-stats">
        <div className="stat-item">
          <div className="stat-value">{detail.invocations}</div>
          <div className="stat-label">Invocations</div>
        </div>
        <div className="stat-item">
          <div className="stat-value">{detail.avg_utility.toFixed(3)}</div>
          <div className="stat-label">Avg Utility</div>
        </div>
        <div className="stat-item">
          <div className="stat-value">{scores.combined.toFixed(3)}</div>
          <div className="stat-label">Combined Score</div>
        </div>
      </div>

      <div className="detail-section">
        <h3>Signal Scores</h3>
        <ScoreBars scores={scores} />
      </div>

      <NeuronEgoGraph neuronId={neuronId} onSelectNeuron={onSelectNeuron} />

      {detail.content && (
        <div className="detail-section">
          <h3>Content</h3>
          <div className="detail-content">{detail.content}</div>
        </div>
      )}

      {detail.summary && (
        <div className="detail-section">
          <h3>Summary</h3>
          <div className="detail-content">{detail.summary}</div>
        </div>
      )}
    </div>
  );
}
