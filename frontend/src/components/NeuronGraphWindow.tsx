import { useEffect, useState } from 'react';
import NeuronTreeViz from './NeuronTreeViz';
import { CHAT_GRAPH_EVENT, getLastGraph, type GraphPayload } from '../chatBus';

/* Neuron Graph window — shows the neuron activation graph for the
   chat's latest grounded answer. The chat publishes each update through
   chatBus (with a last-value store, so opening this window late still
   shows the current graph). */

export default function NeuronGraphWindow() {
  const [graph, setGraph] = useState<GraphPayload>(getLastGraph);

  useEffect(() => {
    const onGraph = () => setGraph({ ...getLastGraph() });
    window.addEventListener(CHAT_GRAPH_EVENT, onGraph);
    return () => window.removeEventListener(CHAT_GRAPH_EVENT, onGraph);
  }, []);

  if (!graph.neuronScores || graph.neuronScores.length === 0) {
    return (
      <div style={{
        height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 24, color: 'var(--text-dim)', fontSize: '0.85rem', textAlign: 'center',
      }}>
        Ask something in the chat — the neuron graph behind the latest grounded answer appears here.
      </div>
    );
  }

  return (
    <div style={{ height: '100%', overflow: 'auto', padding: 8 }}>
      <NeuronTreeViz queryId={graph.queryId} neuronScores={graph.neuronScores} />
    </div>
  );
}
