/**
 * Full-page Sigma.js visualization of the entire neuron graph.
 * Fetches data from /neurons/graph3d endpoint, renders in 2D with ForceAtlas2 layout.
 * Controls: zoom, layout toggle, depth filter, edge type filter, community coloring, search.
 */

import { useEffect, useState, useMemo, useCallback } from 'react';
import { DEPT_COLORS } from '../constants';
import SigmaGraph from './SigmaGraph';
import { graph3DToGraphology } from '../utils/graphology-adapter';
import type { Graph3DResponse } from '../utils/graphology-adapter';
import { fetchClusters, fetchGraph3d } from '../api/knowledge_graph';

const LAYER_LABELS = ['L0 Dept', 'L1 Role', 'L2 Task', 'L3 System', 'L4 Decision', 'L5 Output'];

export default function SigmaGraphPage() {
  const [rawData, setRawData] = useState<Graph3DResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [maxLayer, setMaxLayer] = useState(5);
  const [showStellate, setShowStellate] = useState(true);
  const [showPyramidal, setShowPyramidal] = useState(true);
  const [searchTerm, setSearchTerm] = useState('');

  // Community coloring
  const [clusters, setClusters] = useState<{ cluster_id: number; neuron_ids: number[]; suggested_label: string }[] | null>(null);
  const [useCommunityColors, setUseCommunityColors] = useState(false);

  // Fetch graph data
  useEffect(() => {
    setLoading(true);
    fetchGraph3d<any>()
      .then(r => { if (!r.ok) throw new Error(`${r.status}`); return r.json(); })
      .then((data: any) => { setRawData({ nodes: data.neurons ?? data.nodes ?? [], edges: data.edges ?? [] }); setError(null); })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Fetch clusters for community coloring
  useEffect(() => {
    fetchClusters<any>()
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.clusters) setClusters(data.clusters); })
      .catch(() => {});
  }, []);

  // Build filtered Graphology graph
  const graph = useMemo(() => {
    if (!rawData) return null;

    // Filter nodes by layer and search
    const searchLower = searchTerm.toLowerCase();
    const filteredNodes = rawData.nodes.filter(n => {
      if (n.layer > maxLayer && n.layer >= 0) return false;
      if (searchLower && !n.label.toLowerCase().includes(searchLower)) return false;
      return true;
    });
    const nodeIdSet = new Set(filteredNodes.map(n => n.id));

    // Filter edges by type and node presence
    const filteredEdges = rawData.edges.filter(e => {
      if (!nodeIdSet.has(e.source_id) || !nodeIdSet.has(e.target_id)) return false;
      if (!showStellate && e.edge_type === 'stellate') return false;
      if (!showPyramidal && e.edge_type === 'pyramidal') return false;
      return true;
    });

    const g = graph3DToGraphology({ nodes: filteredNodes, edges: filteredEdges });

    // Apply community colors if enabled
    if (useCommunityColors && clusters) {
      const CLUSTER_COLORS = [
        '#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4',
        '#3b82f6', '#8b5cf6', '#ec4899', '#14b8a6', '#f59e0b',
        '#6366f1', '#10b981', '#f43f5e', '#0ea5e9', '#a855f7',
      ];
      const neuronToCluster = new Map<number, number>();
      for (const c of clusters) {
        for (const nid of c.neuron_ids) {
          neuronToCluster.set(nid, c.cluster_id);
        }
      }
      g.forEachNode((node, attrs) => {
        const nid = attrs.neuron_id as number;
        const cid = neuronToCluster.get(nid);
        if (cid !== undefined) {
          g.setNodeAttribute(node, 'color', CLUSTER_COLORS[cid % CLUSTER_COLORS.length]);
        }
      });
    }

    return g;
  }, [rawData, maxLayer, showStellate, showPyramidal, searchTerm, useCommunityColors, clusters]);

  const handleNodeClick = useCallback((_key: string, attrs: Record<string, unknown>) => {
    const nid = attrs.neuron_id;
    if (nid) {
      console.log('Sigma graph node clicked:', nid, attrs.label);
    }
  }, []);

  if (loading) {
    return (
      <div style={{ padding: 32, color: 'var(--text-muted)' }}>Loading graph data...</div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: 32, color: '#ef4444' }}>Failed to load graph: {error}</div>
    );
  }

  const nodeCount = graph?.order ?? 0;
  const edgeCount = graph?.size ?? 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 12 }}>
        <h3 style={{ margin: 0, color: 'var(--text)' }}>Sigma Graph</h3>
        <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
          {nodeCount} nodes &middot; {edgeCount} edges
        </span>
      </div>

      {/* Controls panel */}
      <div style={{
        display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap',
        padding: '8px 12px', marginBottom: 8,
        background: 'var(--bg-card)', borderRadius: 8,
        border: '1px solid var(--border)',
        fontSize: '0.75rem', color: 'var(--text)',
      }}>
        {/* Depth filter */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: '#64748b' }}>Max Layer</span>
          <input
            type="range"
            min={0} max={5} value={maxLayer}
            onChange={e => setMaxLayer(Number(e.target.value))}
            style={{ width: 80 }}
          />
          <span style={{ width: 55 }}>{LAYER_LABELS[maxLayer]}</span>
        </label>

        {/* Edge type filters */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={showStellate} onChange={e => setShowStellate(e.target.checked)} />
          <span>Stellate</span>
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={showPyramidal} onChange={e => setShowPyramidal(e.target.checked)} />
          <span>Pyramidal</span>
        </label>

        {/* Community coloring */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
          <input type="checkbox" checked={useCommunityColors} onChange={e => setUseCommunityColors(e.target.checked)} />
          <span>Community Colors</span>
        </label>

        {/* Search */}
        <input
          type="text"
          placeholder="Search nodes..."
          value={searchTerm}
          onChange={e => setSearchTerm(e.target.value)}
          style={{
            padding: '4px 8px', borderRadius: 4,
            border: '1px solid var(--border)',
            background: 'var(--bg)', color: 'var(--text)',
            fontSize: '0.75rem', width: 160,
          }}
        />
      </div>

      {/* Department legend */}
      <div style={{
        display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8,
        fontSize: '0.68rem', color: '#94a3b8',
      }}>
        {Object.entries(DEPT_COLORS).map(([dept, color]) => (
          <span key={dept} style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' }} />
            {dept}
          </span>
        ))}
      </div>

      {/* Graph */}
      <div style={{ flex: 1, minHeight: 0 }}>
        {graph && graph.order > 0 ? (
          <SigmaGraph
            graph={graph}
            onNodeClick={handleNodeClick}
            autoLayout={true}
          />
        ) : (
          <div style={{ padding: 32, color: '#64748b', textAlign: 'center' }}>
            No nodes match current filters
          </div>
        )}
      </div>
    </div>
  );
}
