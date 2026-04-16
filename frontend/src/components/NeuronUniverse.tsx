import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import { fetchGraph3D, type Graph3DNode, type Graph3DEdge } from '../api';
import { DEPT_COLORS } from '../constants';
import * as THREE from 'three';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { useUrlSync, codecs, type UrlSchema } from '../hooks/useUrlSync';
import { UniverseSearchOverlay } from './UniverseSearchOverlay';

interface GraphNode extends Graph3DNode {
  x?: number; y?: number; z?: number;
  __threeObj?: THREE.Object3D;
}

interface GraphLink {
  source: number | GraphNode;
  target: number | GraphNode;
  weight: number;
  co_fire_count: number;
  _phantom?: boolean;  // gravity-only links: affect layout but never rendered
}

const LAYER_LABELS = ['Department', 'Role', 'Task', 'System', 'Decision', 'Output'];
const CONCEPT_COLOR = '#e879f9';

// Cross-department affinity (mirrors backend DEPT_AFFINITY). Used for phantom gravity links.
const DEPT_AFFINITY: Record<string, Record<string, number>> = {
  'Engineering': { 'Manufacturing & Operations': 0.70, 'Regulatory': 0.55, 'Program Management': 0.50, 'Contracts & Compliance': 0.35, 'Business Development': 0.30, 'Finance': 0.20, 'Executive Leadership': 0.20 },
  'Manufacturing & Operations': { 'Regulatory': 0.65, 'Program Management': 0.45, 'Contracts & Compliance': 0.30, 'Finance': 0.25, 'Executive Leadership': 0.20 },
  'Contracts & Compliance': { 'Finance': 0.60, 'Program Management': 0.55, 'Business Development': 0.50, 'Regulatory': 0.45, 'Executive Leadership': 0.35 },
  'Finance': { 'Program Management': 0.50, 'Executive Leadership': 0.45, 'Business Development': 0.35, 'Regulatory': 0.25 },
  'Program Management': { 'Business Development': 0.45, 'Executive Leadership': 0.50, 'Regulatory': 0.40 },
  'Business Development': { 'Executive Leadership': 0.50, 'Regulatory': 0.20 },
  'Regulatory': { 'Executive Leadership': 0.30 },
};

function getDeptAffinity(a: string, b: string): number {
  return DEPT_AFFINITY[a]?.[b] ?? DEPT_AFFINITY[b]?.[a] ?? 0;
}

export default function NeuronUniverse() {
  const [neurons, setNeurons] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<Graph3DEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<GraphNode | null>(null);
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [minWeight, setMinWeight] = useState(0.3);
  const [maxEdges, setMaxEdges] = useState(5000);
  const [colorBy, setColorBy] = useState<'department' | 'layer'>('department');
  const [showEdges, setShowEdges] = useState(false);
  const [hideDisconnected, setHideDisconnected] = useState(true);
  const [deptFilter, setDeptFilter] = useState<string>('');
  const [conceptSpread, setConceptSpread] = useState(1200);
  const [warpIntensity, setWarpIntensity] = useState(4);
  const [bloomEnabled, setBloomEnabled] = useState(true);
  const [bfsDepth, setBfsDepth] = useState(2);
  // Refs so the force closures can read live values without re-registering
  // (re-registering forces risks the alpha=1 blast pattern on every slider tick).
  const conceptSpreadRef = useRef(conceptSpread);
  const warpIntensityRef = useRef(warpIntensity);
  useEffect(() => { conceptSpreadRef.current = conceptSpread; }, [conceptSpread]);
  useEffect(() => { warpIntensityRef.current = warpIntensity; }, [warpIntensity]);
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

  // Snapshot of original force strength functions, captured once
  const forceSnapshotRef = useRef<{ chargeStrength: any; linkStrength: any } | null>(null);
  // Whether the A2 warp has been applied (so we know whether to restore on deselect)
  const warpAppliedRef = useRef(false);
  // Currently-attached hover ring (so we can remove it cleanly on hover change)
  const hoverRingRef = useRef<{ nodeId: number; mesh: THREE.Mesh; parent: THREE.Object3D } | null>(null);
  // Camera position to restore from URL hash after data load
  const pendingCamRef = useRef<number[] | null>(null);
  // Pending selection id to apply once neurons load (from URL restore)
  const pendingSelIdRef = useRef<number | null>(null);
  // Bloom post-processing: idempotent insert/remove guards
  const bloomPassRef = useRef<UnrealBloomPass | null>(null);
  const renderPassEnsuredRef = useRef(false);

  // Track container size
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      const h = el.clientHeight;
      if (w > 0 && h > 0) setDimensions(prev => (prev?.width === w && prev?.height === h) ? prev : { width: w, height: h });
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    // Delay initial to let layout settle
    requestAnimationFrame(measure);
    return () => ro.disconnect();
  }, []);

  const load = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await fetchGraph3D(minWeight, maxEdges);
      setNeurons(data.neurons);
      setEdges(data.edges);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load graph');
    } finally {
      setLoading(false);
    }
  }, [minWeight, maxEdges]);

  useEffect(() => { load(); }, [load]);

  // ── G1 URL sync ──
  // Fields round-trip through the hash `#universe?sel=...&dept=...&...`.
  // Selection is change-push (browser back/forward walks prior selections);
  // filter changes are replace (no history spam).
  // `cam` is intentionally NOT in the schema — it's managed by a separate
  // polling interval below (see preserveKeys on the useUrlSync call).
  const urlState = useMemo(() => ({
    sel: selectedNode?.id ?? null,
    dept: deptFilter,
    min: minWeight,
    max: maxEdges,
    color: colorBy as string,
    edges: showEdges,
    connected: hideDisconnected,
    spread: conceptSpread,
    warp: warpIntensity,
    bloom: bloomEnabled,
    bfsDepth: bfsDepth,
  }), [selectedNode, deptFilter, minWeight, maxEdges, colorBy, showEdges, hideDisconnected, conceptSpread, warpIntensity, bloomEnabled, bfsDepth]);

  const urlSchema: UrlSchema<typeof urlState> = {
    sel: codecs.nullableInt,
    dept: codecs.str,
    min: codecs.num,
    max: codecs.int,
    color: codecs.str,
    edges: codecs.bool,
    connected: codecs.bool,
    spread: codecs.int,
    warp: codecs.num,
    bloom: codecs.bool,
    bfsDepth: codecs.int,
  };

  const handleRestore = useCallback((r: Partial<typeof urlState>) => {
    if (r.dept !== undefined) setDeptFilter(r.dept);
    if (r.min !== undefined) setMinWeight(r.min);
    if (r.max !== undefined) setMaxEdges(r.max);
    if (r.color !== undefined && (r.color === 'department' || r.color === 'layer')) setColorBy(r.color);
    if (r.edges !== undefined) setShowEdges(r.edges);
    if (r.connected !== undefined) setHideDisconnected(r.connected);
    if (r.spread !== undefined) setConceptSpread(r.spread);
    if (r.warp !== undefined) setWarpIntensity(r.warp);
    if (r.bloom !== undefined) setBloomEnabled(r.bloom);
    // Bounds-check defensive clamp — URL could carry any integer.
    if (r.bfsDepth !== undefined) setBfsDepth(Math.max(2, Math.min(4, r.bfsDepth)));
    if (r.sel !== undefined) {
      if (r.sel === null) {
        setSelectedNode(null);
      } else if (neurons.length > 0) {
        const node = neurons.find(n => n.id === r.sel);
        if (node) setSelectedNode(node);
      } else {
        // Data not loaded yet — stash for the post-load hook
        pendingSelIdRef.current = r.sel;
      }
    }
  }, [neurons]);

  useUrlSync('universe', urlState, urlSchema, {
    pushOnKeys: ['sel'],
    preserveKeys: ['cam'],
    onRestore: handleRestore,
  });

  // Parse the initial `cam` param directly from the hash on mount (it's not
  // managed by useUrlSync). Stash into pendingCamRef; applied by the post-load
  // zoom-to-fit effect once neurons are loaded.
  useEffect(() => {
    const hash = window.location.hash;
    if (!hash.startsWith('#universe')) return;
    const qIdx = hash.indexOf('?');
    if (qIdx < 0) return;
    const params = new URLSearchParams(hash.slice(qIdx + 1));
    const camStr = params.get('cam');
    if (!camStr) return;
    const parts = camStr.split(',').map(s => parseFloat(s));
    if (parts.length === 6 && parts.every(Number.isFinite)) {
      pendingCamRef.current = parts;
    }
  }, []);

  // Write camera into the hash occasionally (on idle / when user stops interacting).
  // Polling every 2s is cheap and avoids listening to every mouse/wheel event.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading) return;
    const interval = window.setInterval(() => {
      const pos = fg.cameraPosition?.();
      if (!pos) return;
      const cam = [pos.x, pos.y, pos.z, pos.lookAt?.x ?? 0, pos.lookAt?.y ?? 0, pos.lookAt?.z ?? 0];
      // Merge into hash without rewriting everything
      const hash = window.location.hash;
      if (!hash.startsWith('#universe')) return;
      const qIdx = hash.indexOf('?');
      const qs = qIdx >= 0 ? hash.slice(qIdx + 1) : '';
      const parts = qs ? qs.split('&').filter(p => !p.startsWith('cam=')) : [];
      parts.push('cam=' + cam.map(n => (Number.isFinite(n) ? +n.toFixed(2) : 0)).join(','));
      const nextHash = '#universe?' + parts.join('&');
      if (nextHash !== hash) window.history.replaceState(null, '', nextHash);
    }, 2000);
    return () => window.clearInterval(interval);
  }, [loading]);

  // Available departments for filter (derived from loaded neurons)
  const departments = useMemo(() => {
    const depts = new Set<string>();
    neurons.forEach(n => { if (n.department) depts.add(n.department); });
    return Array.from(depts).sort();
  }, [neurons]);

  // Volume-distributed fixed positions for concept neurons.
  // Uses a 3D Halton sequence (bases 2/3/5) to pick low-discrepancy points inside
  // a cube, then rejects candidates within MIN_DIST of an already-placed concept.
  // These positions are pinned via fx/fy/fz so concepts stay put during simulation,
  // sidestepping the edge-attraction clumping and the alpha=1 reheat blast pattern.
  const conceptTargets = useMemo(() => {
    const targets = new Map<number, { x: number; y: number; z: number }>();
    const conceptList = neurons.filter(n => n.node_type === 'concept' && n.layer === -1);
    if (conceptList.length === 0) return targets;

    const HALF = 1200;       // cube half-side: positions span [-1200, 1200]
    const MIN_DIST = 360;    // minimum separation between concepts
    const MAX_TRIES = 2000;

    const halton = (i: number, base: number) => {
      let f = 1, r = 0, idx = i;
      while (idx > 0) {
        f /= base;
        r += f * (idx % base);
        idx = Math.floor(idx / base);
      }
      return r;
    };

    const placed: Array<{ x: number; y: number; z: number }> = [];
    let haltonIdx = 1;
    for (const c of conceptList) {
      let chosen: { x: number; y: number; z: number } | null = null;
      for (let tries = 0; tries < MAX_TRIES; tries++) {
        const p = {
          x: (halton(haltonIdx, 2) * 2 - 1) * HALF,
          y: (halton(haltonIdx, 3) * 2 - 1) * HALF,
          z: (halton(haltonIdx, 5) * 2 - 1) * HALF,
        };
        haltonIdx++;
        let ok = true;
        for (const q of placed) {
          const dx = p.x - q.x, dy = p.y - q.y, dz = p.z - q.z;
          if (dx * dx + dy * dy + dz * dz < MIN_DIST * MIN_DIST) { ok = false; break; }
        }
        if (ok) { chosen = p; break; }
      }
      if (!chosen) {
        // Fallback: use the raw Halton point regardless of spacing
        chosen = {
          x: (halton(haltonIdx, 2) * 2 - 1) * HALF,
          y: (halton(haltonIdx, 3) * 2 - 1) * HALF,
          z: (halton(haltonIdx, 5) * 2 - 1) * HALF,
        };
        haltonIdx++;
      }
      placed.push(chosen);
      targets.set(c.id, chosen);
    }
    return targets;
  }, [neurons]);

  // Pin concepts via fx/fy/fz — d3-force treats these as fixed coordinates, so
  // no force-engine tweaks and no reheat are needed to keep them in place.
  const neuronsPositioned = useMemo(() => {
    if (!neurons.length || conceptTargets.size === 0) return neurons;
    return neurons.map(n => {
      const t = conceptTargets.get(n.id);
      return t ? { ...n, x: t.x, y: t.y, z: t.z } : n;
    });
  }, [neurons, conceptTargets]);

  // Full adjacency map over the UNFILTERED edge set — selection BFS uses this
  // so the "what relates to this?" subgraph can cross dept-filter boundaries.
  // The filter is then overridden for display (see graphData below) to reveal
  // everything the BFS reaches.
  const fullAdjacencyMap = useMemo(() => {
    const adj = new Map<number, Set<number>>();
    for (const e of edges) {
      if (!adj.has(e.source)) adj.set(e.source, new Set());
      if (!adj.has(e.target)) adj.set(e.target, new Set());
      adj.get(e.source)!.add(e.target);
      adj.get(e.target)!.add(e.source);
    }
    return adj;
  }, [edges]);

  const conceptIdSet = useMemo(
    () => new Set(neurons.filter(n => n.node_type === 'concept' && n.layer === -1).map(n => n.id)),
    [neurons],
  );

  // BFS-bounded subgraph from the selected node. Concepts are terminals (their
  // neighbors are not expanded) unless the selected root is itself a concept.
  // Traverses the full adjacency so selection can pull in cofiring neurons
  // outside the current dept filter.
  const selectionBFS = useMemo(() => {
    const result = new Map<number, number>();
    if (!selectedNode) return result;
    const rootId = selectedNode.id;
    const rootIsConcept = conceptIdSet.has(rootId);
    result.set(rootId, 0);
    const queue: Array<[number, number]> = [[rootId, 0]];
    while (queue.length) {
      const [id, hop] = queue.shift()!;
      if (hop >= bfsDepth) continue;
      if (!rootIsConcept && id !== rootId && conceptIdSet.has(id)) continue;
      const neighbors = fullAdjacencyMap.get(id);
      if (!neighbors) continue;
      for (const nb of neighbors) {
        if (!result.has(nb)) {
          result.set(nb, hop + 1);
          queue.push([nb, hop + 1]);
        }
      }
    }
    return result;
  }, [selectedNode, fullAdjacencyMap, bfsDepth, conceptIdSet]);

  // Build graph data for force-graph (applies dept filter + connectivity filter
  // + dept gravity). When a selection is active, nodes in the BFS subgraph are
  // forced in regardless of dept filter so the "what relates to this?" view is
  // complete.
  const graphData = useMemo(() => {
    if (!neuronsPositioned.length) return { nodes: [], links: [] };

    // Apply department filter: keep matching neurons + concept neurons (always visible)
    let filtered = deptFilter
      ? neuronsPositioned.filter(n => n.department === deptFilter || (n.node_type === 'concept' && n.layer === -1))
      : neuronsPositioned;

    // Selection overrides filter: union in every BFS node that isn't already
    // included. This is what makes selection "cross filter boundaries".
    if (selectedNode && selectionBFS.size > 0 && deptFilter) {
      const present = new Set(filtered.map(n => n.id));
      const extras: GraphNode[] = [];
      for (const id of selectionBFS.keys()) {
        if (present.has(id)) continue;
        const n = neuronsPositioned.find(x => x.id === id);
        if (n) extras.push(n);
      }
      if (extras.length) filtered = filtered.concat(extras);
    }

    const nodeIds = new Set(filtered.map(n => n.id));

    const links: GraphLink[] = edges
      .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map(e => ({ source: e.source, target: e.target, weight: e.weight, co_fire_count: e.co_fire_count, _phantom: false }));

    // Inject phantom gravity links between departments based on affinity.
    // Connect multiple anchor nodes (L0 + all L1 role-level) per department pair
    // with strong invisible links to create real gravitational clustering.
    if (!deptFilter) {
      const deptAnchors: Record<string, number[]> = {};
      for (const n of filtered) {
        if ((n.layer === 0 || n.layer === 1) && n.department && nodeIds.has(n.id)) {
          (deptAnchors[n.department] ??= []).push(n.id);
        }
      }
      const anchorDepts = Object.keys(deptAnchors);
      for (let i = 0; i < anchorDepts.length; i++) {
        for (let j = i + 1; j < anchorDepts.length; j++) {
          const affinity = getDeptAffinity(anchorDepts[i], anchorDepts[j]);
          if (affinity < 0.20) continue;
          // Cross-connect all anchors from dept A to all anchors from dept B
          const anchorsA = deptAnchors[anchorDepts[i]];
          const anchorsB = deptAnchors[anchorDepts[j]];
          for (const a of anchorsA) {
            for (const b of anchorsB) {
              links.push({
                source: a,
                target: b,
                weight: affinity * 1.5,  // amplified so gravity is tangible
                co_fire_count: 0,
                _phantom: true,
              });
            }
          }
        }
      }
    }

    if (!hideDisconnected) return { nodes: filtered as GraphNode[], links };
    const connectedIds = new Set<number>();
    links.forEach(l => {
      connectedIds.add(typeof l.source === 'number' ? l.source : l.source.id);
      connectedIds.add(typeof l.target === 'number' ? l.target : l.target.id);
    });
    return { nodes: filtered.filter(n => connectedIds.has(n.id)) as GraphNode[], links };
  }, [neuronsPositioned, edges, hideDisconnected, deptFilter, selectedNode, selectionBFS]);

  // 1-hop adjacency map over the CURRENTLY-RENDERED graph (post dept filter,
  // excluding phantom gravity links). Used by B2 hover-and-dim effects — they
  // want "what's visibly connected right now", which is filter-aware.
  const adjacencyMap = useMemo(() => {
    const adj = new Map<number, Set<number>>();
    for (const link of graphData.links as GraphLink[]) {
      if (link._phantom) continue;
      const src = typeof link.source === 'number' ? link.source : link.source.id;
      const tgt = typeof link.target === 'number' ? link.target : link.target.id;
      if (!adj.has(src)) adj.set(src, new Set());
      if (!adj.has(tgt)) adj.set(tgt, new Set());
      adj.get(src)!.add(tgt);
      adj.get(tgt)!.add(src);
    }
    return adj;
  }, [graphData]);

  const layerColors = ['#2dd4bf', '#60a5fa', '#a78bfa', '#f472b6', '#fb923c', '#facc15'];

  const isConcept = (node: GraphNode) => node.node_type === 'concept' && node.layer === -1;

  const getNodeColor = useCallback((node: GraphNode) => {
    if (isConcept(node)) return CONCEPT_COLOR;
    if (colorBy === 'department') {
      return DEPT_COLORS[node.department] || '#c8d0dc';
    }
    return layerColors[node.layer] || '#c8d0dc';
  }, [colorBy]);

  const getNodeSize = useCallback((node: GraphNode) => {
    // Concept neurons: largest in the graph (hub nodes)
    if (isConcept(node)) {
      const edgeBoost = Math.min(node.invocations * 0.12, 4);
      return 10 + edgeBoost;
    }
    // Size by layer: departments large, outputs small. Boost by invocations.
    const baseSize = [8, 6, 4, 3, 2.5, 2][node.layer] || 2;
    const invoBoost = Math.min(node.invocations * 0.3, 4);
    return baseSize + invoBoost;
  }, []);

  // Stamp a material's baseOpacity so the A1/B2 dim effect can scale it later
  // without losing the original design intent (different glow shells have
  // different native opacities).
  function withBase<M extends THREE.Material & { opacity: number }>(m: M): M {
    m.userData.baseOpacity = m.opacity;
    return m;
  }

  // Custom node rendering with glow
  const nodeThreeObject = useCallback((node: GraphNode) => {
    const color = getNodeColor(node);
    const size = getNodeSize(node);
    const isC = node.node_type === 'concept' && node.layer === -1;

    const group = new THREE.Group();

    if (isC) {
      // ── Black hole effect for concept neurons ──
      // Dark core — nearly black with faint purple tint
      const coreGeo = new THREE.SphereGeometry(size, 32, 24);
      const coreMat = withBase(new THREE.MeshPhongMaterial({
        color: new THREE.Color('#08050e'),
        emissive: new THREE.Color('#1a0a2e'),
        emissiveIntensity: 0.3,
        transparent: true,
        opacity: 0.97,
      }));
      group.add(new THREE.Mesh(coreGeo, coreMat));

      // Event horizon glow — thin bright ring at the surface
      const horizonGeo = new THREE.RingGeometry(size * 0.95, size * 1.15, 64);
      const horizonMat = withBase(new THREE.MeshBasicMaterial({
        color: new THREE.Color(CONCEPT_COLOR),
        transparent: true,
        opacity: 0.7,
        side: THREE.DoubleSide,
      }));
      const horizon = new THREE.Mesh(horizonGeo, horizonMat);
      group.add(horizon);

      // Outer diffuse glow
      const glowGeo = new THREE.SphereGeometry(size * 2.2, 16, 12);
      const glowMat = withBase(new THREE.MeshBasicMaterial({
        color: new THREE.Color(CONCEPT_COLOR),
        transparent: true,
        opacity: 0.06,
      }));
      group.add(new THREE.Mesh(glowGeo, glowMat));
    } else {
      // ── Standard neuron rendering ──
      const geometry = new THREE.SphereGeometry(size, 16, 12);
      const material = withBase(new THREE.MeshPhongMaterial({
        color: new THREE.Color(color),
        emissive: new THREE.Color(color),
        emissiveIntensity: 0.4,
        transparent: true,
        opacity: 0.9,
      }));
      group.add(new THREE.Mesh(geometry, material));

      // Outer glow
      const glowGeometry = new THREE.SphereGeometry(size * 1.6, 12, 8);
      const glowMaterial = withBase(new THREE.MeshBasicMaterial({
        color: new THREE.Color(color),
        transparent: true,
        opacity: 0.08,
      }));
      group.add(new THREE.Mesh(glowGeometry, glowMaterial));
    }

    // Cache the node's primary size + color on the group so effects can reuse them
    group.userData.baseSize = size;
    group.userData.baseColor = color;

    return group;
  }, [getNodeColor, getNodeSize]);

  // Tooltip
  const nodeLabel = useCallback((node: GraphNode) => {
    const isConceptNode = node.node_type === 'concept' && node.layer === -1;
    const typeLabel = isConceptNode
      ? `<span style="color:#e879f9;font-weight:600">Concept</span>`
      : `<span style="color:${DEPT_COLORS[node.department] || '#c8d0dc'}">${node.department || 'Unknown'}</span>
         &middot; L${node.layer} ${LAYER_LABELS[node.layer] || ''}`;
    return `<div style="background:#131926;border:1px solid ${isConceptNode ? '#e879f944' : '#1e2d4a'};border-radius:6px;padding:8px 12px;font-size:0.8rem;max-width:300px;color:#f8fafc;font-family:Inter,sans-serif">
      <div style="font-weight:600;margin-bottom:4px">${node.label}</div>
      <div style="color:#c8d0dc;font-size:0.75rem">
        ${typeLabel}
        ${node.role_key ? `&middot; ${node.role_key}` : ''}
      </div>
      <div style="color:#c8d0dc;font-size:0.7rem;margin-top:4px">
        Invocations: ${node.invocations} &middot; Utility: ${node.avg_utility.toFixed(3)}
      </div>
    </div>`;
  }, []);

  // ── A1 selection dim + B2 hover-preview dim ──
  // Scales each material's opacity relative to its baseOpacity and toggles
  // visibility per BFS hop tier. Selection hides out-of-set nodes entirely and
  // scales in-set nodes by hop (root 1.4×, hop-1 1.25×, hop-2 1.10×, hop-3 1.0×)
  // with matching opacity dimming. Hover path is the original non-neighbor 0.35
  // dim, used only when nothing is selected.
  useEffect(() => {
    if (!graphData.nodes.length) return;
    const selId = selectedNode?.id ?? null;
    const hovId = hoveredNode?.id ?? null;
    const hovNeighbors = hovId !== null ? adjacencyMap.get(hovId) : null;

    for (const n of graphData.nodes as GraphNode[]) {
      const obj = n.__threeObj;
      if (!obj) continue;

      let dim = 1.0;
      let scale = 1.0;
      let visible = true;

      if (selId !== null) {
        const hop = selectionBFS.get(n.id);
        if (hop === undefined) {
          visible = false;
        } else if (hop === 0) { dim = 1.0; scale = 1.4; }
        else if (hop === 1) { dim = 1.0; scale = 1.25; }
        else if (hop === 2) { dim = 0.65; scale = 1.10; }
        else                { dim = 0.35; scale = 1.0; }
      } else if (hovId !== null) {
        if (n.id !== hovId && !(hovNeighbors && hovNeighbors.has(n.id))) dim = 0.35;
      }

      obj.visible = visible;
      obj.scale.setScalar(scale);

      obj.traverse((child: THREE.Object3D) => {
        if ((child as THREE.Mesh).userData?.isHoverRing) return;
        const mesh = child as THREE.Mesh;
        const mat = mesh.material as THREE.Material & { opacity: number };
        if (!mat || typeof mat.opacity !== 'number') return;
        const base = mat.userData?.baseOpacity;
        if (typeof base !== 'number') return;
        mat.opacity = base * dim;
        mat.transparent = true;
      });
    }
  }, [selectedNode, hoveredNode, adjacencyMap, graphData, selectionBFS]);

  // ── A2 gravity warp ──
  // On selection: link strength scales with warpIntensity for BFS-incident edges
  // (b × warp) and √warp for other in-set edges; out-of-set links go to 0.
  // Charge strength stays ~baseline for in-set nodes and is boosted ×1.5 on
  // hidden nodes (cheap extra shove; they're invisible anyway). On deselect:
  // restore originals and let the layout relax. Only reheats on actual apply/
  // restore transitions — never on initial mount or unrelated graphData changes.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !graphData.nodes.length) return;

    const chargeForce: any = fg.d3Force ? fg.d3Force('charge') : null;
    const linkForce: any = fg.d3Force ? fg.d3Force('link') : null;
    if (!chargeForce || !linkForce) return;

    const selId = selectedNode?.id ?? null;
    const applying = selId !== null;
    const restoring = selId === null && warpAppliedRef.current;

    // No-op path: nothing to apply, nothing to restore. Don't touch forces or reheat.
    if (!applying && !restoring) return;

    // Snapshot originals the first time we ever touch the forces
    if (!forceSnapshotRef.current) {
      forceSnapshotRef.current = {
        chargeStrength: chargeForce.strength(),
        linkStrength: linkForce.strength(),
      };
    }
    const snap = forceSnapshotRef.current;

    if (applying) {
      const selNeighbors = adjacencyMap.get(selId as number);
      const baseCharge = (node: any) => (
        typeof snap.chargeStrength === 'function' ? snap.chargeStrength(node) : snap.chargeStrength ?? -30
      );
      const baseLink = (link: any) => (
        typeof snap.linkStrength === 'function' ? snap.linkStrength(link) : snap.linkStrength ?? 1
      );
      // On selection: only links fully inside the BFS subgraph retain any gravity.
      // Links touching hidden nodes go weightless so the subgraph settles on its
      // own. Incident edges (hop 0↔1) get full warp pulling planets tight to the
      // sun; outer-shell edges (hop 1↔2+) get sqrt(warp) so moons tag along with
      // their planets rather than being yanked to the center.
      linkForce.strength((link: any) => {
        const src = typeof link.source === 'number' ? link.source : link.source?.id;
        const tgt = typeof link.target === 'number' ? link.target : link.target?.id;
        if (!selectionBFS.has(src) || !selectionBFS.has(tgt)) return 0;
        const incident = src === selId || tgt === selId;
        const b = baseLink(link);
        const w = warpIntensityRef.current;
        return incident ? b * w : b * Math.sqrt(Math.max(1, w));
      });
      chargeForce.strength((node: any) => {
        const isNeighbor = node.id === selId || (selNeighbors && selNeighbors.has(node.id));
        const b = baseCharge(node);
        return isNeighbor ? b : b * 1.5;
      });
      warpAppliedRef.current = true;
    } else {
      // restoring
      linkForce.strength(snap.linkStrength);
      chargeForce.strength(snap.chargeStrength);
      warpAppliedRef.current = false;
    }
    fg.d3ReheatSimulation?.();
  }, [selectedNode, adjacencyMap, graphData, warpIntensity, selectionBFS]);

  // ── Focus pinning ──
  // Pin the selected neuron at its current position so it behaves as a stable
  // "sun". Release it on deselect. d3-force honors fx/fy/fz by skipping the
  // usual velocity integration for that node. We track by id (not by node ref)
  // so graphData re-renders that swap node object identity still unpin/re-pin
  // the correct live node rather than mutating a stale reference.
  const pinnedFocusIdRef = useRef<number | null>(null);
  useEffect(() => {
    const prevId = pinnedFocusIdRef.current;
    if (prevId !== null) {
      const stale: any = (graphData.nodes as GraphNode[]).find(n => n.id === prevId);
      if (stale) { stale.fx = undefined; stale.fy = undefined; stale.fz = undefined; }
      pinnedFocusIdRef.current = null;
    }
    if (!selectedNode) return;
    const target: any = (graphData.nodes as GraphNode[]).find(n => n.id === selectedNode.id);
    if (!target) return;
    target.fx = target.x ?? 0;
    target.fy = target.y ?? 0;
    target.fz = target.z ?? 0;
    pinnedFocusIdRef.current = selectedNode.id;
  }, [selectedNode, graphData]);

  // ── Solar pull ──
  // Per-tick force pulling in-set nodes toward the pinned focus. Strength scales
  // with (warp - 1) and 1/hop so hop-1 neighbors collapse into tight orbit while
  // hop-2+ gets a gentler nudge and still orbits its local planet. At warp=1 this
  // contributes nothing (baseline behavior preserved). Reads refs so slider ticks
  // don't re-register the force.
  const solarPullRef = useRef<{ selId: number | null; bfs: Map<number, number>; warp: number }>({
    selId: null, bfs: new Map(), warp: 1,
  });
  useEffect(() => {
    solarPullRef.current = {
      selId: selectedNode?.id ?? null,
      bfs: selectionBFS,
      warp: warpIntensity,
    };
  }, [selectedNode, selectionBFS, warpIntensity]);

  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !graphData.nodes.length || !fg.d3Force) return;
    let allNodes: any[] = [];
    const force: any = (alpha: number) => {
      const s = solarPullRef.current;
      if (s.selId == null || s.warp <= 1) return;
      const sun = allNodes.find(n => n.id === s.selId);
      if (!sun) return;
      const k = (s.warp - 1) * 0.008;  // tuned: warp=10 → k=0.072, noticeable collapse
      const MAX_STEP = 5;
      for (const n of allNodes) {
        if (n.id === s.selId) continue;
        const hop = s.bfs.get(n.id);
        if (hop === undefined) continue;
        const hopMul = 1 / hop;  // hop1:1.0, hop2:0.5, hop3:0.33
        const dx = (sun.x ?? 0) - (n.x ?? 0);
        const dy = (sun.y ?? 0) - (n.y ?? 0);
        const dz = (sun.z ?? 0) - (n.z ?? 0);
        const pull = k * hopMul * alpha;
        const vx = dx * pull, vy = dy * pull, vz = dz * pull;
        n.vx = (n.vx ?? 0) + Math.max(-MAX_STEP, Math.min(MAX_STEP, vx));
        n.vy = (n.vy ?? 0) + Math.max(-MAX_STEP, Math.min(MAX_STEP, vy));
        n.vz = (n.vz ?? 0) + Math.max(-MAX_STEP, Math.min(MAX_STEP, vz));
      }
    };
    force.initialize = (nodes: any[]) => { allNodes = nodes; };
    fg.d3Force('solarPull', force);
  }, [graphData]);

  // ── Concept anti-gravity ──
  // Custom O(n²) repulsion force that only acts between concept neurons. The
  // default charge force uses Barnes-Hut with one strength per node, so it can't
  // selectively amplify concept↔concept repulsion without also over-repelling
  // concept↔regular. Running our own force just over the ~49 concepts costs
  // ~2k ops/tick — negligible — and keeps concepts from re-clumping when edge
  // attraction pulls them toward the mass center.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !graphData.nodes.length || !fg.d3Force) return;

    const conceptIds = new Set<number>(
      (graphData.nodes as GraphNode[])
        .filter(n => n.node_type === 'concept' && n.layer === -1)
        .map(n => n.id)
    );
    if (conceptIds.size === 0) return;

    let conceptNodes: any[] = [];
    const force: any = (alpha: number) => {
      // Linear spring-style push below MIN_DIST only. Capped per-tick delta so
      // we can't eject nodes during alpha=1 warmup/reheat ticks.
      const STRENGTH = 0.15;        // fraction of (MIN_DIST - d) applied per tick
      const MIN_DIST = conceptSpreadRef.current;
      const MAX_STEP = 8;           // hard cap on per-tick velocity delta per axis
      for (let i = 0; i < conceptNodes.length; i++) {
        const a = conceptNodes[i];
        for (let j = i + 1; j < conceptNodes.length; j++) {
          const b = conceptNodes[j];
          const dx = (a.x ?? 0) - (b.x ?? 0);
          const dy = (a.y ?? 0) - (b.y ?? 0);
          const dz = (a.z ?? 0) - (b.z ?? 0);
          const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 0.01;
          if (d >= MIN_DIST) continue;
          const overlap = MIN_DIST - d;
          const mag = Math.min(MAX_STEP, overlap * STRENGTH * alpha);
          const ux = dx / d, uy = dy / d, uz = dz / d;
          a.vx = (a.vx ?? 0) + ux * mag;
          a.vy = (a.vy ?? 0) + uy * mag;
          a.vz = (a.vz ?? 0) + uz * mag;
          b.vx = (b.vx ?? 0) - ux * mag;
          b.vy = (b.vy ?? 0) - uy * mag;
          b.vz = (b.vz ?? 0) - uz * mag;
        }
      }
    };
    force.initialize = (nodes: any[]) => {
      conceptNodes = nodes.filter(n => conceptIds.has(n.id));
    };

    fg.d3Force('conceptRepel', force);
  }, [graphData]);

  // Nudge the simulation when the concept-spread slider moves so the new
  // MIN_DIST (read live from the ref) actually repositions concepts.
  // Gentler than d3ReheatSimulation's alpha=1 — sets alpha=0.4 directly on
  // the underlying simulation if we can reach it; falls back to reheat.
  const conceptSpreadPrev = useRef(conceptSpread);
  useEffect(() => {
    if (conceptSpreadPrev.current === conceptSpread) return;
    conceptSpreadPrev.current = conceptSpread;
    const fg = fgRef.current;
    if (!fg) return;
    fg.d3ReheatSimulation?.();
  }, [conceptSpread]);

  // ── Scene lighting + fog ──
  // Regular neurons use MeshPhongMaterial with emissiveIntensity 0.4 but the
  // scene has no lights by default, so they render emissive-only (flat). Adding
  // ambient + key + rim gives real diffuse/specular shading. Exp² fog gives
  // depth perception across the ~2400-unit volume.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !neurons.length) return;
    const scene: THREE.Scene | undefined = fg.scene?.();
    if (!scene) return;

    const ambient = new THREE.AmbientLight(0xbfd4ff, 0.55);
    const dir = new THREE.DirectionalLight(0xffffff, 0.85);
    dir.position.set(800, 1200, 600);
    const rim = new THREE.DirectionalLight(0xe879f9, 0.25);
    rim.position.set(-900, -400, -800);
    scene.add(ambient); scene.add(dir); scene.add(rim);

    return () => {
      scene.remove(ambient); scene.remove(dir); scene.remove(rim);
    };
  }, [loading, neurons.length]);

  // ── Bloom post-processing (toggleable) ──
  // Adds UnrealBloomPass to the force-graph composer. Pass insertion/removal is
  // idempotent via bloomPassRef; RenderPass is ensured once via renderPassEnsuredRef.
  // Re-creates on resize (dimensions dep) so the pass uses the correct viewport.
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !neurons.length || !dimensions) return;
    const composer = fg.postProcessingComposer?.();
    const scene = fg.scene?.();
    const camera = fg.camera?.();
    if (!composer || !scene || !camera) return;

    if (!renderPassEnsuredRef.current) {
      const passes = composer.passes ?? [];
      const hasRender = passes.some((p: any) => p?.isRenderPass || p instanceof RenderPass);
      if (!hasRender) composer.insertPass(new RenderPass(scene, camera), 0);
      renderPassEnsuredRef.current = true;
    }

    if (bloomEnabled && !bloomPassRef.current) {
      const bloom = new UnrealBloomPass(
        new THREE.Vector2(dimensions.width, dimensions.height),
        0.55,   // strength — low enough that zoom-in doesn't wash out
        0.6,    // radius
        0.3,    // threshold — only bright emissive surfaces bloom
      );
      const passes = composer.passes;
      const outIdx = passes.findIndex((p: any) => p instanceof OutputPass);
      if (outIdx === -1) {
        composer.addPass(bloom);
        composer.addPass(new OutputPass());
      } else {
        composer.insertPass(bloom, outIdx);
      }
      bloomPassRef.current = bloom;
    } else if (!bloomEnabled && bloomPassRef.current) {
      composer.removePass(bloomPassRef.current);
      bloomPassRef.current.dispose?.();
      bloomPassRef.current = null;
    }

    return () => {
      if (bloomPassRef.current) {
        composer.removePass(bloomPassRef.current);
        bloomPassRef.current.dispose?.();
        bloomPassRef.current = null;
      }
    };
  }, [bloomEnabled, loading, neurons.length, dimensions]);

  // ── B1 hover ring ──
  // One ring mesh, re-parented on hover change. Rotated by the animation loop below.
  useEffect(() => {
    // Remove previous ring
    const prev = hoverRingRef.current;
    if (prev) {
      prev.parent.remove(prev.mesh);
      prev.mesh.geometry.dispose();
      (prev.mesh.material as THREE.Material).dispose();
      hoverRingRef.current = null;
    }
    if (!hoveredNode || !hoveredNode.__threeObj) return;

    const size = (hoveredNode.__threeObj.userData?.baseSize as number) || getNodeSize(hoveredNode);
    const color = (hoveredNode.__threeObj.userData?.baseColor as string) || getNodeColor(hoveredNode);
    const thickness = Math.max(0.15, size * 0.08);
    const geo = new THREE.TorusGeometry(size * 1.8, thickness, 12, 48);
    const mat = new THREE.MeshBasicMaterial({
      color: new THREE.Color(color),
      transparent: true,
      opacity: 0.65,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.userData.isHoverRing = true;
    hoveredNode.__threeObj.add(mesh);
    hoverRingRef.current = { nodeId: hoveredNode.id, mesh, parent: hoveredNode.__threeObj };
  }, [hoveredNode, getNodeColor, getNodeSize]);

  // rAF loop: spin the hover ring (if any) around Y. One loop for the life of the component.
  useEffect(() => {
    let raf = 0;
    let lastT = performance.now();
    function tick(t: number) {
      const dt = (t - lastT) / 1000;
      lastT = t;
      const ring = hoverRingRef.current;
      if (ring) {
        ring.mesh.rotation.y += 0.5 * dt;
        ring.mesh.rotation.x += 0.2 * dt;
      }
      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // ── D1 Ctrl+F search trigger ──
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setIsSearchOpen(true);
        return;
      }
      if (e.key === 'Escape') {
        // If search is open, the overlay handles Esc itself. Otherwise clear selection.
        if (!isSearchOpen && selectedNode) setSelectedNode(null);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isSearchOpen, selectedNode]);

  // Camera fly-to for overlay selection
  const flyToNode = useCallback((nodeId: number) => {
    const fg = fgRef.current;
    if (!fg) return;
    const node = (graphData.nodes as GraphNode[]).find(n => n.id === nodeId);
    if (!node || node.x === undefined || node.y === undefined || node.z === undefined) return;
    const dist = 80;
    const nr = Math.hypot(node.x, node.y, node.z) || 1;
    fg.cameraPosition?.(
      { x: node.x * (1 + dist / nr), y: node.y * (1 + dist / nr), z: node.z * (1 + dist / nr) },
      node,
      800,
    );
  }, [graphData]);

  // Zoom to fit on first load. Under React StrictMode the effect double-invokes;
  // we clear the previous timeout in cleanup so zoomToFit only runs once per
  // real mount (avoiding a jarring second snap after the user has moved the cam).
  useEffect(() => {
    if (loading || !fgRef.current || neurons.length === 0) return;
    const handle = window.setTimeout(() => {
      // If a camera position is pending from URL restore, skip zoomToFit
      if (pendingCamRef.current) {
        const [x, y, z, tx, ty, tz] = pendingCamRef.current;
        pendingCamRef.current = null;
        fgRef.current?.cameraPosition?.(
          { x, y, z },
          { x: tx, y: ty, z: tz },
          0,
        );
      } else {
        fgRef.current?.zoomToFit?.(800, 60);
      }
      // Apply pending selection if any
      if (pendingSelIdRef.current !== null) {
        const id = pendingSelIdRef.current;
        pendingSelIdRef.current = null;
        const node = neurons.find(n => n.id === id) || null;
        if (node) setSelectedNode(node);
      }
    }, 2000);
    return () => window.clearTimeout(handle);
  }, [loading, neurons]);

  // Stats
  const deptCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    let conceptCount = 0;
    neurons.forEach(n => {
      if (n.node_type === 'concept' && n.layer === -1) { conceptCount++; return; }
      const dept = n.department || 'Unknown';
      counts[dept] = (counts[dept] || 0) + 1;
    });
    const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    if (conceptCount > 0) entries.push(['Concepts', conceptCount]);
    return entries;
  }, [neurons]);

  const ready = !loading && !error && dimensions;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden' }} ref={containerRef}>
      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#c8d0dc', position: 'absolute', inset: 0, zIndex: 10 }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '1.2rem', marginBottom: 8 }}>Loading neuron universe...</div>
            <div style={{ fontSize: '0.8rem' }}>Fetching {'>'}2,000 neurons and co-firing edges</div>
          </div>
        </div>
      )}
      {error && (
        <div style={{ color: '#ef4444', padding: 20, position: 'absolute', inset: 0, zIndex: 10 }}>{error}</div>
      )}
      {/* 3D Graph */}
      {ready && <ForceGraph3D
        ref={fgRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        nodeId="id"
        nodeLabel={nodeLabel as any}
        nodeThreeObject={nodeThreeObject as any}
        nodeThreeObjectExtend={false}
        linkSource="source"
        linkTarget="target"
        linkWidth={(link: any) => Math.max(0.2, link.weight * 2)}
        linkOpacity={0.15}
        linkColor={() => '#334155'}
        linkVisibility={(link: any) => {
          if (link._phantom) return false;
          if (!showEdges) return false;
          if (!selectedNode) return true;
          const src = typeof link.source === 'number' ? link.source : link.source?.id;
          const tgt = typeof link.target === 'number' ? link.target : link.target?.id;
          return selectionBFS.has(src) && selectionBFS.has(tgt);
        }}
        linkDirectionalParticles={0}
        onNodeHover={(node: any) => setHoveredNode(node ?? null)}
        onNodeClick={(node: any) => setSelectedNode(node?.id === selectedNode?.id ? null : node)}
        onBackgroundClick={() => setSelectedNode(null)}
        backgroundColor="#0a0e17"
        showNavInfo={false}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        warmupTicks={80}
        cooldownTicks={200}
      />}

      {/* Controls overlay */}
      {ready && <>
      <div style={{
        position: 'absolute', top: 12, left: 12, background: '#131926dd',
        border: '1px solid #1e2d4a', borderRadius: 8, padding: '12px 16px',
        backdropFilter: 'blur(8px)', fontSize: '0.8rem', minWidth: 200,
      }}>
        <div style={{ fontWeight: 600, marginBottom: 10, fontSize: '0.9rem' }}>
          Neuron Universe
        </div>
        <div style={{ color: '#c8d0dc', marginBottom: 10, fontSize: '0.75rem' }}>
          {graphData.nodes.length.toLocaleString()} neurons &middot; {graphData.links.filter((l: any) => !l._phantom).length.toLocaleString()} edges
          {hideDisconnected && <span style={{ color: '#fb923c' }}> (of {neurons.length.toLocaleString()})</span>}
        </div>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Color by
          <select
            value={colorBy}
            onChange={e => setColorBy(e.target.value as 'department' | 'layer')}
            style={{
              marginLeft: 8, background: '#1a2136', color: '#f8fafc',
              border: '1px solid #1e2d4a', borderRadius: 3, padding: '2px 6px', fontSize: '0.75rem',
            }}
          >
            <option value="department">Department</option>
            <option value="layer">Layer</option>
          </select>
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Department
          <select
            value={deptFilter}
            onChange={e => setDeptFilter(e.target.value)}
            style={{
              marginLeft: 8, background: '#1a2136', color: '#f8fafc',
              border: '1px solid #1e2d4a', borderRadius: 3, padding: '2px 6px', fontSize: '0.75rem',
            }}
          >
            <option value="">All</option>
            {departments.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          <input type="checkbox" checked={showEdges} onChange={e => setShowEdges(e.target.checked)} />
          Show edges
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          <input type="checkbox" checked={bloomEnabled} onChange={e => setBloomEnabled(e.target.checked)} />
          Bloom
        </label>

        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          <input type="checkbox" checked={hideDisconnected} onChange={e => setHideDisconnected(e.target.checked)} />
          Connected only
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Min weight: {minWeight.toFixed(2)}
          <input
            type="range" min={0.1} max={0.8} step={0.05} value={minWeight}
            onChange={e => setMinWeight(parseFloat(e.target.value))}
            style={{ width: '100%', marginTop: 4 }}
          />
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Max edges: {maxEdges}
          <input
            type="range" min={500} max={25000} step={500} value={maxEdges}
            onChange={e => setMaxEdges(parseInt(e.target.value))}
            style={{ width: '100%', marginTop: 4 }}
          />
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Concept spread: {conceptSpread}
          <input
            type="range" min={400} max={2400} step={100} value={conceptSpread}
            onChange={e => setConceptSpread(parseInt(e.target.value))}
            style={{ width: '100%', marginTop: 4 }}
          />
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          Selection warp: {warpIntensity.toFixed(1)}×
          <input
            type="range" min={1} max={10} step={0.5} value={warpIntensity}
            onChange={e => setWarpIntensity(parseFloat(e.target.value))}
            style={{ width: '100%', marginTop: 4 }}
          />
        </label>

        <label style={{ display: 'block', marginBottom: 8, fontSize: '0.75rem', color: '#c8d0dc' }}>
          BFS depth: {bfsDepth}
          <input
            type="range" min={2} max={4} step={1} value={bfsDepth}
            onChange={e => setBfsDepth(parseInt(e.target.value))}
            style={{ width: '100%', marginTop: 4 }}
          />
        </label>

        <button
          onClick={() => fgRef.current?.zoomToFit?.(600, 40)}
          style={{
            width: '100%', background: '#60a5fa22', border: '1px solid #60a5fa44',
            borderRadius: 4, color: '#60a5fa', padding: '4px 0', fontSize: '0.75rem',
            cursor: 'pointer', marginBottom: 4,
          }}
        >
          Reset View
        </button>
      </div>

      {/* Legend */}
      <div style={{
        position: 'absolute', bottom: 12, left: 12, background: '#131926dd',
        border: '1px solid #1e2d4a', borderRadius: 8, padding: '10px 14px',
        backdropFilter: 'blur(8px)', fontSize: '0.7rem',
      }}>
        {colorBy === 'department' ? (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', maxWidth: 400 }}>
            {deptCounts.map(([dept, count]) => (
              <div key={dept} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: DEPT_COLORS[dept] || '#c8d0dc', display: 'inline-block',
                }} />
                <span style={{ color: '#c8d0dc' }}>{dept} ({count})</span>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ display: 'flex', gap: 12 }}>
            {LAYER_LABELS.map((label, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: layerColors[i], display: 'inline-block',
                }} />
                <span style={{ color: '#c8d0dc' }}>L{i} {label}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Selected node detail */}
      {selectedNode && (
        <div style={{
          position: 'absolute', top: 12, right: 12, background: '#131926dd',
          border: '1px solid #1e2d4a', borderRadius: 8, padding: '14px 18px',
          backdropFilter: 'blur(8px)', fontSize: '0.8rem', maxWidth: 320, minWidth: 240,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 8 }}>
            <div style={{ fontWeight: 600, fontSize: '0.9rem', lineHeight: 1.3 }}>
              {selectedNode.label}
            </div>
            <button
              onClick={() => setSelectedNode(null)}
              style={{ background: 'none', border: 'none', color: '#c8d0dc', cursor: 'pointer', fontSize: '1rem', padding: 0 }}
            >
              x
            </button>
          </div>
          <div style={{ display: 'grid', gap: 6, fontSize: '0.75rem' }}>
            {selectedNode.node_type === 'concept' && selectedNode.layer === -1 ? (
              <div>
                <span style={{ color: '#c8d0dc' }}>Type: </span>
                <span style={{ color: CONCEPT_COLOR, fontWeight: 600 }}>Concept</span>
              </div>
            ) : (<>
              <div>
                <span style={{ color: '#c8d0dc' }}>Department: </span>
                <span style={{ color: DEPT_COLORS[selectedNode.department] || '#f8fafc' }}>{selectedNode.department}</span>
              </div>
              <div>
                <span style={{ color: '#c8d0dc' }}>Layer: </span>
                <span>L{selectedNode.layer} {LAYER_LABELS[selectedNode.layer]}</span>
              </div>
            </>)}
            {selectedNode.role_key && (
              <div>
                <span style={{ color: '#c8d0dc' }}>Role: </span>
                <span>{selectedNode.role_key}</span>
              </div>
            )}
            <div>
              <span style={{ color: '#c8d0dc' }}>Type: </span>
              <span>{selectedNode.node_type}</span>
            </div>
            <div>
              <span style={{ color: '#c8d0dc' }}>Invocations: </span>
              <span>{selectedNode.invocations}</span>
            </div>
            <div>
              <span style={{ color: '#c8d0dc' }}>Avg Utility: </span>
              <span>{selectedNode.avg_utility.toFixed(4)}</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: '#c8d0dc', marginTop: 4 }}>
              ID: {selectedNode.id}
              {selectedNode.parent_id && ` · Parent: ${selectedNode.parent_id}`}
            </div>
          </div>
        </div>
      )}

      {/* Controls hint */}
      <div style={{
        position: 'absolute', bottom: 12, right: 12, color: '#c8d0dc44',
        fontSize: '0.65rem', textAlign: 'right',
      }}>
        Left-drag: rotate · Right-drag: pan · Scroll: zoom · Click: select · Ctrl+F: search
      </div>

      {/* D1 Search overlay */}
      {isSearchOpen && (
        <UniverseSearchOverlay
          neurons={graphData.nodes as GraphNode[]}
          onClose={() => setIsSearchOpen(false)}
          onSelect={(nodeId) => {
            const node = (graphData.nodes as GraphNode[]).find(n => n.id === nodeId);
            if (node) {
              setSelectedNode(node);
              flyToNode(nodeId);
            }
            setIsSearchOpen(false);
          }}
        />
      )}
      </>}
    </div>
  );
}
