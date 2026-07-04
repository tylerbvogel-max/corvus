/**
 * NeuronTreeViz — Organic neural cascade tree visualization.
 * Shared component used by QueryLab and Visual Graph experiment pages.
 * Accepts neuronScores + queryId as props; fetches spread trail and
 * ancestors internally.
 */

import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import * as d3 from 'd3';
import { fetchSpreadTrail } from '../api';
import type { NeuronScoreResponse, SpreadTrailResponse } from '../types';
import { DEPT_COLORS } from '../constants';

// ────────── Constants ──────────

const LAYER_LABELS = ['Dept', 'Role', 'Task', 'System', 'Decision', 'Output'];
const PROMPT_COLOR = '#3b82f6';
const SPREAD_RING_COLOR = '#e8a735';
const MIN_R = 3;
const MAX_R = 13;
const NODE_X_SPACING = 30;
const LAYER_Y_SPACING = 130;
const EDGE_DRAW_MS = 90;
const NODE_FADE_MS = 150;
const SPLAY_FACTOR = 3;
const CENTER_PULL = 0.3;
const TRUNK_CHILD_THRESH = 3;
const TRUNK_PCT = 0.3;
const DOF_ZOOM_THRESH = 1.5;

// ────────── Helpers ──────────

function deptColor(dept: string | null): string {
  return DEPT_COLORS[dept ?? ''] ?? '#4a5568';
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

function bezierEdge(x1: number, y1: number, x2: number, y2: number): string {
  const my = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
}


/** Deterministic jitter seeded by id */
function jitter(id: number): number {
  return ((id * 7919) % 100 - 50) / 50 * 12;
}

// ────────── Tree data ──────────

interface TreeNodeData {
  key: string;
  neuron_id?: number;
  label: string;
  department: string;
  layer: number;
  combined: number;
  spread_boost: number;
  isPrompt?: boolean;
  isBridge?: boolean;
  isEngram?: boolean;
  summary?: string | null;
  burst?: number;
  impact?: number;
  precision?: number;
  novelty?: number;
  recency?: number;
  relevance?: number;
  children: TreeNodeData[];
}

type AncestorMap = Map<number, { id: number; label: string; department: string | null; layer: number; parent_id: number | null }>;
type HNode = d3.HierarchyPointNode<TreeNodeData>;

function buildHierarchyTree(
  scores: NeuronScoreResponse[],
  ancestorData: AncestorMap,
): TreeNodeData {
  const root: TreeNodeData = {
    key: '__prompt__', label: 'Prompt', department: '', layer: -1,
    combined: 0, spread_boost: 0, isPrompt: true, children: [],
  };
  const nodeMap = new Map<number, TreeNodeData>();

  for (const [, anc] of ancestorData) {
    nodeMap.set(anc.id, {
      key: `n-${anc.id}`, neuron_id: anc.id, label: anc.label || `#${anc.id}`,
      department: anc.department ?? 'Unknown', layer: anc.layer,
      combined: 0, spread_boost: 0, isBridge: true, children: [],
    });
  }
  for (const n of scores) {
    const isEngram = n.entity_type === 'engram';
    nodeMap.set(n.neuron_id, {
      key: `${isEngram ? 'e' : 'n'}-${n.neuron_id}`, neuron_id: n.neuron_id,
      label: n.label || `#${n.neuron_id}`,
      department: n.department ?? (isEngram ? 'Engram' : 'Unknown'), layer: n.layer,
      combined: n.combined, spread_boost: n.spread_boost,
      isEngram,
      summary: n.summary, burst: n.burst, impact: n.impact,
      precision: n.precision, novelty: n.novelty, recency: n.recency,
      relevance: n.relevance, children: [],
    });
  }

  for (const [id, node] of nodeMap) {
    const scoreEntry = scores.find(s => s.neuron_id === id);
    const ancEntry = ancestorData.get(id);
    const parentId = scoreEntry?.parent_id ?? ancEntry?.parent_id ?? null;
    if (parentId != null && nodeMap.has(parentId)) {
      nodeMap.get(parentId)!.children.push(node);
    } else {
      root.children.push(node);
    }
  }

  const sortChildren = (node: TreeNodeData) => {
    node.children.sort((a, b) => {
      if (a.department !== b.department) return a.department.localeCompare(b.department);
      return b.combined - a.combined;
    });
    for (const child of node.children) sortChildren(child);
  };
  sortChildren(root);
  return root;
}

// ────────── Post-processing ──────────

/** Find the hottest path (highest cumulative score root→leaf) */
function findHottestPath(root: HNode): Set<HNode> {
  let bestScore = -1;
  let bestLeaf: HNode | null = null;
  for (const leaf of root.leaves()) {
    let score = 0;
    let cur: HNode | null = leaf;
    while (cur) { score += cur.data.combined; cur = cur.parent; }
    if (score > bestScore) { bestScore = score; bestLeaf = leaf; }
  }
  const path = new Set<HNode>();
  let cur: HNode | null = bestLeaf;
  while (cur) { path.add(cur); cur = cur.parent; }
  return path;
}

function postProcessLayout(nodes: HNode[]) {
  const root = nodes[0];
  const hottestPath = findHottestPath(root);

  for (const n of nodes) {
    if (n.data.isPrompt) continue;

    // 1. Gravity-weighted depth: deeper layers compress
    n.y = n.y * (1 - n.depth * 0.06);

    // 2. River delta splay: children fan outward with depth
    if (n.parent && n.parent.children) {
      const siblings = n.parent.children;
      const idx = siblings.indexOf(n);
      const mid = (siblings.length - 1) / 2;
      n.x += (idx - mid) * SPLAY_FACTOR * n.depth;
    }

    // 3. Organic jitter
    if (n.data.neuron_id != null) {
      n.y += jitter(n.data.neuron_id);
    }
  }

  // 4. Department vertical stagger — each department's subtree starts below the previous one
  const promptChildren = root.children ?? [];
  // Group prompt's direct children by department
  const deptGroups = new Map<string, HNode[]>();
  for (const child of promptChildren) {
    const dept = child.data.department;
    if (!deptGroups.has(dept)) deptGroups.set(dept, []);
    deptGroups.get(dept)!.push(child);
  }

  // Compute subtree extent (width and height)
  function subtreeExtent(node: HNode): { minX: number; maxX: number; minY: number; maxY: number } {
    let minX = node.x, maxX = node.x, minY = node.y, maxY = node.y;
    const walk = (n: HNode) => {
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
      if (n.children) for (const c of n.children) walk(c);
    };
    walk(node);
    return { minX, maxX, minY, maxY };
  }

  // 4. Zigzag column layout — high/low/high/low to fill rectangular space
  // Sort departments by neuron count (largest first) for better packing
  const deptEntries = [...deptGroups.entries()].sort((a, b) => {
    const countA = a[1].reduce((s, n) => s + (n.descendants?.()?.length ?? 1), 0);
    const countB = b[1].reduce((s, n) => s + (n.descendants?.()?.length ?? 1), 0);
    return countB - countA;
  });

  // 2-column grid layout to fill portrait (2:1 height:width) viewport
  const NUM_COLS = 2;
  const COL_GAP_X = 100;
  const ROW_GAP_Y = 80;

  // Compute extent of each department's subtrees
  const deptExtents: { dept: string; children: HNode[]; ext: { minX: number; maxX: number; minY: number; maxY: number } }[] = [];
  for (const [dept, children] of deptEntries) {
    const ext = { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
    for (const child of children) {
      const e = subtreeExtent(child);
      ext.minX = Math.min(ext.minX, e.minX);
      ext.maxX = Math.max(ext.maxX, e.maxX);
      ext.minY = Math.min(ext.minY, e.minY);
      ext.maxY = Math.max(ext.maxY, e.maxY);
    }
    deptExtents.push({ dept, children, ext });
  }

  // Assign to columns: alternate left/right, filling rows top-to-bottom
  const colNextY = new Array(NUM_COLS).fill(0);
  const colX = new Array(NUM_COLS).fill(0);

  // Compute column X positions based on max width per column
  // First pass: assign departments to columns (round-robin by index)
  const colAssignments: number[] = deptExtents.map((_, i) => i % NUM_COLS);

  // Compute max width per column for X positioning
  const colMaxWidth = new Array(NUM_COLS).fill(0);
  for (let i = 0; i < deptExtents.length; i++) {
    const col = colAssignments[i];
    const w = deptExtents[i].ext.maxX - deptExtents[i].ext.minX;
    colMaxWidth[col] = Math.max(colMaxWidth[col], w);
  }
  // Set column X origins
  let cumX = 0;
  for (let c = 0; c < NUM_COLS; c++) {
    colX[c] = cumX;
    cumX += colMaxWidth[c] + COL_GAP_X;
  }

  // Place each department in its assigned column
  for (let i = 0; i < deptExtents.length; i++) {
    const col = colAssignments[i];
    const { children, ext } = deptExtents[i];

    const shiftX = colX[col] + (colMaxWidth[col] / 2) - ((ext.minX + ext.maxX) / 2);
    const shiftY = colNextY[col] - ext.minY;

    const shiftSubtree = (n: HNode) => {
      n.x += shiftX;
      n.y += shiftY;
      if (n.children) for (const c of n.children) shiftSubtree(c);
    };
    for (const child of children) shiftSubtree(child);

    const groupHeight = ext.maxY - ext.minY;
    colNextY[col] += groupHeight + ROW_GAP_Y;
  }

  // 5. Center pull — shift hottest path toward center
  const allX = nodes.filter(n => !n.data.isPrompt).map(n => n.x);
  const centerX = ((d3.min(allX) ?? 0) + (d3.max(allX) ?? 0)) / 2;
  for (const n of nodes) {
    if (n.data.isPrompt) continue;
    if (hottestPath.has(n)) {
      n.x = n.x + (centerX - n.x) * CENTER_PULL;
    }
  }

  // Place prompt above center
  const globalMinY = d3.min(nodes.filter(n => !n.data.isPrompt), n => n.y) ?? 0;
  root.y = globalMinY - LAYER_Y_SPACING;
  root.x = centerX;
}

// ────────── DFS order (complete each subtree before next sibling) ──────────

function computeDfsOrder(root: HNode): Map<string, number> {
  const order = new Map<string, number>();
  let idx = 0;
  const walk = (node: HNode) => {
    order.set(node.data.key, idx++);
    if (node.children) {
      for (const child of node.children) walk(child);
    }
  };
  walk(root);
  return order;
}

// ────────── Component ──────────

interface NeuronTreeVizProps {
  neuronScores: NeuronScoreResponse[];
  queryId?: number;
  onNavigateToNeuron?: (id: number) => void;
}

export default function NeuronTreeViz({ neuronScores, queryId, onNavigateToNeuron }: NeuronTreeVizProps) {
  const [trailData, setTrailData] = useState<SpreadTrailResponse | null>(null);
  const [ancestorData, setAncestorData] = useState<AncestorMap>(new Map());
  const [hoverNode, setHoverNode] = useState<TreeNodeData | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const zoomRef = useRef<d3.ZoomBehavior<SVGSVGElement, unknown> | null>(null);
  const pulseIdRef = useRef(0);
  const currentZoomRef = useRef(1);
  const [containerReady, setContainerReady] = useState(false);

  // Wait for container to have real dimensions before rendering
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
          setContainerReady(true);
        }
      }
    });
    ro.observe(el);
    return () => { ro.disconnect(); setContainerReady(false); };
  }, []);

  // Fetch spread trail
  useEffect(() => {
    if (queryId) fetchSpreadTrail(queryId).then(setTrailData).catch(() => setTrailData(null));
  }, [queryId]);

  // Fetch ancestor bridge nodes
  useEffect(() => {
    if (neuronScores.length === 0) return;
    const neuronIdSet = new Set(neuronScores.map(n => n.neuron_id));
    const missingParents = new Set<number>();
    for (const n of neuronScores) {
      // Engrams have no parent — skip ancestor fetching for them
      if (n.entity_type === 'engram') continue;
      if (n.parent_id != null && !neuronIdSet.has(n.parent_id)) missingParents.add(n.parent_id);
    }
    if (missingParents.size === 0) { setAncestorData(new Map()); return; }
    const fetchAncestors = async () => {
      const map: AncestorMap = new Map();
      const toFetch = [...missingParents];
      for (let depth = 0; depth < 6 && toFetch.length > 0; depth++) {
        const batch = toFetch.splice(0, toFetch.length);
        const results = await Promise.all(
          batch.map(id => fetch(`/neurons/${id}`).then(r => r.ok ? r.json() : null).catch(() => null))
        );
        for (const r of results) {
          if (!r) continue;
          if (!map.has(r.id)) map.set(r.id, { id: r.id, label: r.label, department: r.department, layer: r.layer, parent_id: r.parent_id });
          if (r.parent_id != null && !neuronIdSet.has(r.parent_id) && !map.has(r.parent_id)) toFetch.push(r.parent_id);
        }
      }
      setAncestorData(map);
    };
    fetchAncestors();
  }, [neuronScores]);

  // ── Tree layout with post-processing ──

  const treeData = useMemo(() => {
    if (neuronScores.length === 0) return null;
    const rootData = buildHierarchyTree(neuronScores, ancestorData);
    const hierarchy = d3.hierarchy(rootData, d => d.children.length > 0 ? d.children : undefined);

    const treeLayout = d3.tree<TreeNodeData>()
      .nodeSize([NODE_X_SPACING, LAYER_Y_SPACING])
      .separation((a, b) => a.data.department !== b.data.department ? 2.2 : 1);

    const laidOut = treeLayout(hierarchy);
    const nodes = laidOut.descendants();
    const links = laidOut.links();

    // Post-process: gravity, splay, jitter, dept gaps, center pull
    postProcessLayout(nodes);

    // Recompute bounds after post-processing
    let minX = Infinity, maxX = -Infinity, minY = 0, maxY = 0;
    for (const n of nodes) {
      if (n.x < minX) minX = n.x;
      if (n.x > maxX) maxX = n.x;
      if (n.y < minY) minY = n.y;
      if (n.y > maxY) maxY = n.y;
    }

    // BFS order for animation
    const dfsOrder = computeDfsOrder(laidOut);

    // Position lookup for co-firing edges
    const posMap = new Map<number, { x: number; y: number }>();
    for (const n of nodes) {
      if (n.data.neuron_id != null) posMap.set(n.data.neuron_id, { x: n.x, y: n.y });
    }

    // Department bounding boxes for color wash
    const deptBounds = new Map<string, { minX: number; maxX: number; minY: number; maxY: number }>();
    for (const n of nodes) {
      if (n.data.isPrompt || !n.data.department) continue;
      const dept = n.data.department;
      const b = deptBounds.get(dept) ?? { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
      b.minX = Math.min(b.minX, n.x);
      b.maxX = Math.max(b.maxX, n.x);
      b.minY = Math.min(b.minY, n.y);
      b.maxY = Math.max(b.maxY, n.y);
      deptBounds.set(dept, b);
    }

    // Layer Y positions for grid lines
    const layerYs = new Map<number, number>();
    for (const n of nodes) {
      if (n.data.isPrompt) continue;
      const depth = n.depth;
      if (!layerYs.has(depth)) layerYs.set(depth, n.y);
      else layerYs.set(depth, Math.min(layerYs.get(depth)!, n.y));
    }

    return { nodes, links, posMap, dfsOrder, deptBounds, layerYs, bounds: { minX, maxX, minY, maxY } };
  }, [neuronScores, ancestorData]);

  const maxScore = useMemo(() => Math.max(...neuronScores.map(s => s.combined), 0.001), [neuronScores]);

  // ── Build ancestry/descendant lookup for click highlighting ──
  const ancestryMap = useMemo(() => {
    if (!treeData) return { ancestors: new Map<string, Set<string>>(), descendants: new Map<string, Set<string>>() };
    const ancestors = new Map<string, Set<string>>();
    const descendants = new Map<string, Set<string>>();
    for (const n of treeData.nodes) {
      // Ancestors
      const anc = new Set<string>();
      let cur: HNode | null = n.parent;
      while (cur) { anc.add(cur.data.key); cur = cur.parent; }
      ancestors.set(n.data.key, anc);
      // Descendants
      const desc = new Set<string>();
      const walk = (node: HNode) => {
        if (node.children) for (const c of node.children) { desc.add(c.data.key); walk(c); }
      };
      walk(n);
      descendants.set(n.data.key, desc);
    }
    return { ancestors, descendants };
  }, [treeData]);

  // ── Render D3 SVG ──

  const renderTree = useCallback(() => {
    if (!svgRef.current || !treeData || !containerRef.current) return;
    try {

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const { nodes, dfsOrder, bounds } = treeData;
    const treeWidth = bounds.maxX - bounds.minX + 200;
    const treeHeight = bounds.maxY - bounds.minY + 100;

    // ── Defs ──
    const defs = svg.append('defs');
    const edgeGlow = defs.append('filter').attr('id', 'edge-glow2').attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
    edgeGlow.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', '2').attr('result', 'blur');
    const em = edgeGlow.append('feMerge'); em.append('feMergeNode').attr('in', 'blur'); em.append('feMergeNode').attr('in', 'SourceGraphic');

    const nodeGlow = defs.append('filter').attr('id', 'node-glow2').attr('x', '-100%').attr('y', '-100%').attr('width', '300%').attr('height', '300%');
    nodeGlow.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', '3').attr('result', 'blur');
    const nm = nodeGlow.append('feMerge'); nm.append('feMergeNode').attr('in', 'blur'); nm.append('feMergeNode').attr('in', 'SourceGraphic');

    const particleGlow = defs.append('filter').attr('id', 'particle-glow2').attr('x', '-200%').attr('y', '-200%').attr('width', '500%').attr('height', '500%');
    particleGlow.append('feGaussianBlur').attr('in', 'SourceGraphic').attr('stdDeviation', '4').attr('result', 'blur');
    const pm = particleGlow.append('feMerge'); pm.append('feMergeNode').attr('in', 'blur'); pm.append('feMergeNode').attr('in', 'SourceGraphic');

    // Main group
    const g = svg.append('g').attr('class', 'tree-main');

    // DOF blur — defined early so zoom handler can reference it
    let dofReady = false;
    let nodeGroupRef: d3.Selection<SVGGElement, unknown, null, undefined> | null = null;
    const containerRect = containerRef.current.getBoundingClientRect();

    function applyDepthOfField(transform: d3.ZoomTransform) {
      if (!dofReady || !nodeGroupRef) return;
      if (transform.k < DOF_ZOOM_THRESH) {
        nodeGroupRef.selectAll('.tree-node').style('filter', 'none');
        return;
      }
      const vw = containerRect.width;
      const vh = containerRect.height;
      const vcx = vw / 2;
      const vcy = vh / 2;
      const maxDist = Math.sqrt(vcx * vcx + vcy * vcy) * 0.6;
      nodeGroupRef.selectAll<SVGGElement, unknown>('.tree-node').each(function () {
        const el = d3.select(this);
        const t = el.attr('transform');
        const match = t?.match(/translate\(([-\d.]+),([-\d.]+)\)/);
        if (!match) return;
        const nx = parseFloat(match[1]);
        const ny = parseFloat(match[2]);
        const sx = transform.applyX(nx);
        const sy = transform.applyY(ny);
        const dist = Math.sqrt((sx - vcx) ** 2 + (sy - vcy) ** 2);
        const blur = dist > maxDist ? Math.min(2, ((dist - maxDist) / maxDist) * 2) : 0;
        el.style('filter', blur > 0.1 ? `blur(${blur.toFixed(1)}px)` : 'none');
      });
    }

    // Zoom
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.08, 5])
      .on('zoom', (event) => {
        g.attr('transform', event.transform);
        currentZoomRef.current = event.transform.k;
        applyDepthOfField(event.transform);
      });
    svg.call(zoom);
    zoomRef.current = zoom;

    // Deselect on background click
    svg.on('click', (event) => {
      if (event.target === svgRef.current) setSelectedKey(null);
    });

    // Initial zoom-to-fit
    if (containerRect.width > 0 && containerRect.height > 0 && treeWidth > 0 && treeHeight > 0) {
      const scaleX = containerRect.width / (treeWidth + 80);
      const scaleY = containerRect.height / (treeHeight + 80);
      const initScale = Math.min(scaleX, scaleY, 1);
      const centerX = (bounds.minX + bounds.maxX) / 2;
      const ty = 40 - bounds.minY * initScale;
      svg.call(zoom.transform, d3.zoomIdentity.translate(containerRect.width / 2 - centerX * initScale, ty).scale(initScale));
    }


    // ── Edge glow layer ──
    const glowGroup = g.append('g').attr('class', 'edge-glow-layer').style('opacity', '0');

    // ── 9. Trunk+fan edge rendering ──
    const coreGroup = g.append('g').attr('class', 'edge-core-layer');

    for (const node of nodes) {
      if (!node.children || node.children.length === 0) continue;
      const px = node.x, py = node.y;
      const children = node.children;
      const useTrunk = !node.data.isPrompt && children.length >= TRUNK_CHILD_THRESH;

      if (useTrunk) {
        // Trunk segment from parent downward
        const avgChildY = d3.mean(children, c => c.y) ?? py + LAYER_Y_SPACING;
        const trunkEndY = py + (avgChildY - py) * TRUNK_PCT;
        const trunkColor = deptColor(children[0].data.department);
        const trunkWidth = Math.min(4, 0.8 + children.length * 0.4);

        // Glow trunk
        glowGroup.append('path')
          .attr('d', `M ${px} ${py} L ${px} ${trunkEndY}`)
          .attr('fill', 'none').attr('stroke', trunkColor)
          .attr('stroke-width', trunkWidth + 3).attr('stroke-opacity', 0.06)
          .attr('filter', 'url(#edge-glow2)');

        // Core trunk
        coreGroup.append('path')
          .attr('d', `M ${px} ${py} L ${px} ${trunkEndY}`)
          .attr('fill', 'none').attr('stroke', trunkColor)
          .attr('stroke-width', trunkWidth).attr('stroke-opacity', 0.35)
          .attr('class', 'hierarchy-edge')
          .attr('data-source', node.data.key).attr('data-target', '')
          .attr('data-dfs', dfsOrder.get(node.data.key) ?? 0);

        // Fan arms from trunk end to each child
        for (const child of children) {
          const color = deptColor(child.data.department);
          const scoreT = maxScore > 0 ? child.data.combined / maxScore : 0;
          const sw = 0.8 + scoreT * 1.5;
          const opacity = child.data.isBridge ? 0.15 : 0.25 + scoreT * 0.4;
          const d = bezierEdge(px, trunkEndY, child.x, child.y);

          glowGroup.append('path')
            .attr('d', d).attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', sw + 3).attr('stroke-opacity', 0.06)
            .attr('filter', 'url(#edge-glow2)');

          coreGroup.append('path')
            .attr('d', d).attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', sw).attr('stroke-opacity', opacity)
            .attr('class', 'hierarchy-edge')
            .attr('data-source', node.data.key).attr('data-target', child.data.key)
            .attr('data-dfs', dfsOrder.get(child.data.key) ?? 0);
        }
      } else {
        // Normal individual bezier per child
        for (const child of children) {
          const color = deptColor(child.data.department);
          const scoreT = maxScore > 0 ? child.data.combined / maxScore : 0;
          const sw = 0.8 + scoreT * 1.5;
          const opacity = child.data.isBridge ? 0.15 : 0.25 + scoreT * 0.4;
          const d = bezierEdge(px, py, child.x, child.y);

          glowGroup.append('path')
            .attr('d', d).attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', sw + 3).attr('stroke-opacity', 0.06)
            .attr('filter', 'url(#edge-glow2)');

          coreGroup.append('path')
            .attr('d', d).attr('fill', 'none').attr('stroke', color)
            .attr('stroke-width', sw).attr('stroke-opacity', opacity)
            .attr('class', 'hierarchy-edge')
            .attr('data-source', node.data.key).attr('data-target', child.data.key)
            .attr('data-dfs', dfsOrder.get(child.data.key) ?? 0);
        }
      }
    }


    // ── Node layer (18: start invisible for fade-in) ──
    const nodeGroup = g.append('g').attr('class', 'node-layer');
    for (const n of nodes) {
      const d = n.data;
      const isPrompt = !!d.isPrompt;
      const scoreT = maxScore > 0 ? d.combined / maxScore : 0;
      const r = isPrompt ? 14 : MIN_R + scoreT * (MAX_R - MIN_R);
      const color = isPrompt ? PROMPT_COLOR : deptColor(d.department);
      const nodeOpacity = d.isBridge ? 0.35 : 0.6 + scoreT * 0.4;

      const ng = nodeGroup.append('g')
        .attr('class', 'tree-node')
        .attr('data-key', d.key)
        .attr('transform', `translate(${n.x},${n.y})`)
        .style('cursor', 'pointer')
        .style('opacity', isPrompt ? 1 : 0) // hidden for fade-in
        .on('mouseenter', () => {
          setHoverNode(d);
          reverseHighlight(d.key);
        })
        .on('mouseleave', () => {
          setHoverNode(null);
          clearReverseHighlight();
        })
        .on('click', (event) => {
          event.stopPropagation();
          setSelectedKey(prev => prev === d.key ? null : d.key);
        })
        .on('dblclick', (event) => {
          event.stopPropagation();
          if (d.neuron_id != null && onNavigateToNeuron) onNavigateToNeuron(d.neuron_id);
        });

      if (d.isEngram) {
        // Engram: diamond shape (rotated square)
        const s = r * 1.3;
        ng.append('rect').attr('x', -s - 3).attr('y', -s - 3).attr('width', (s + 3) * 2).attr('height', (s + 3) * 2)
          .attr('fill', color).attr('fill-opacity', 0.08).attr('filter', 'url(#node-glow2)')
          .attr('transform', 'rotate(45)');
        if (d.spread_boost > 0) {
          ng.append('rect').attr('x', -s - 1).attr('y', -s - 1).attr('width', (s + 1) * 2).attr('height', (s + 1) * 2)
            .attr('fill', 'none').attr('stroke', SPREAD_RING_COLOR).attr('stroke-width', 1.5).attr('stroke-opacity', 0.7)
            .attr('transform', 'rotate(45)');
        }
        ng.append('rect').attr('x', -s).attr('y', -s).attr('width', s * 2).attr('height', s * 2)
          .attr('fill', color).attr('fill-opacity', nodeOpacity)
          .attr('stroke', color).attr('stroke-width', 0.5).attr('stroke-opacity', 0.5)
          .attr('transform', 'rotate(45)');
      } else {
        // Neuron: circle shape
        ng.append('circle').attr('r', r + 4).attr('fill', color)
          .attr('fill-opacity', 0.08).attr('filter', 'url(#node-glow2)');

        if (d.spread_boost > 0) {
          ng.append('circle').attr('r', r + 2).attr('fill', 'none')
            .attr('stroke', SPREAD_RING_COLOR).attr('stroke-width', 1.5).attr('stroke-opacity', 0.7);
        }

        ng.append('circle').attr('r', r).attr('fill', color)
          .attr('fill-opacity', nodeOpacity)
          .attr('stroke', isPrompt ? '#fff' : color)
          .attr('stroke-width', isPrompt ? 2 : 0.5).attr('stroke-opacity', 0.5);
      }

      if (!d.isBridge && (isPrompt || scoreT > 0.15)) {
        const labelText = d.label.length > 16 ? d.label.slice(0, 15) + '…' : d.label;
        ng.append('text').attr('y', r + 11).attr('text-anchor', 'middle')
          .attr('font-size', isPrompt ? '10px' : '8px')
          .attr('font-weight', isPrompt ? '700' : '500')
          .attr('fill', 'var(--text, #e2e8f0)').attr('fill-opacity', 0.8)
          .text(labelText);
      }
    }

    // Particle group (for animation dots)
    g.append('g').attr('class', 'particle-layer');

    // Enable DOF now that nodeGroup exists
    nodeGroupRef = nodeGroup;
    dofReady = true;

    // ── 20. Reverse pulse on hover ──
    function reverseHighlight(key: string) {
      const ancs = ancestryMap.ancestors.get(key);
      if (!ancs) return;
      const allKeys = new Set([key, ...ancs]);
      // Brighten ancestry edges
      coreGroup.selectAll<SVGPathElement, unknown>('.hierarchy-edge').each(function () {
        const el = d3.select(this);
        const src = el.attr('data-source');
        const tgt = el.attr('data-target');
        if (allKeys.has(src ?? '') && allKeys.has(tgt ?? '')) {
          el.attr('stroke-opacity', 0.8).attr('stroke-width', 2.5);
        }
      });
    }

    function clearReverseHighlight() {
      // Restore original stroke properties
      coreGroup.selectAll<SVGPathElement, unknown>('.hierarchy-edge').each(function () {
        const el = d3.select(this);
        const tgt = el.attr('data-target');
        if (!tgt) return;
        const targetNode = nodes.find(n => n.data.key === tgt);
        if (!targetNode) return;
        const scoreT = maxScore > 0 ? targetNode.data.combined / maxScore : 0;
        el.attr('stroke-opacity', targetNode.data.isBridge ? 0.15 : 0.25 + scoreT * 0.4)
          .attr('stroke-width', 0.8 + scoreT * 1.5);
      });
    }
    } catch (err) { console.error('[VG2] renderTree error:', err); }
  }, [treeData, trailData, maxScore, neuronScores, ancestryMap, onNavigateToNeuron]);

  // ── 24. Path highlighting on click (via selectedKey) ──
  useEffect(() => {
    if (!svgRef.current || !treeData) return;
    const svg = d3.select(svgRef.current);
    const coreGroup = svg.select('.edge-core-layer');
    const glowGroup = svg.select('.edge-glow-layer');
    const nodeGroup = svg.select('.node-layer');

    if (!selectedKey) {
      // Restore all
      coreGroup.selectAll('.hierarchy-edge').each(function () {
        const el = d3.select(this);
        const tgt = el.attr('data-target');
        const targetNode = treeData.nodes.find(n => n.data.key === tgt);
        if (!targetNode) return;
        const scoreT = maxScore > 0 ? targetNode.data.combined / maxScore : 0;
        el.attr('stroke-opacity', targetNode.data.isBridge ? 0.15 : 0.25 + scoreT * 0.4)
          .attr('stroke-width', 0.8 + scoreT * 1.5);
      });
      glowGroup.selectAll('path').style('opacity', '');
      nodeGroup.selectAll('.tree-node').style('opacity', function () {
        return d3.select(this).attr('data-key') === '__prompt__' ? '1' : '';
      });
      return;
    }

    const ancs = ancestryMap.ancestors.get(selectedKey) ?? new Set<string>();
    const descs = ancestryMap.descendants.get(selectedKey) ?? new Set<string>();
    const highlighted = new Set([selectedKey, ...ancs, ...descs]);

    // Dim/highlight edges
    coreGroup.selectAll<SVGPathElement, unknown>('.hierarchy-edge').each(function () {
      const el = d3.select(this);
      const src = el.attr('data-source') ?? '';
      const tgt = el.attr('data-target') ?? '';
      const isAncestry = (ancs.has(src) || src === selectedKey) && (ancs.has(tgt) || tgt === selectedKey);
      const isDescendant = (descs.has(tgt) || tgt === selectedKey) && (descs.has(src) || src === selectedKey);
      if (isAncestry) {
        el.attr('stroke-opacity', 0.9).attr('stroke-width', 2.5);
      } else if (isDescendant) {
        el.attr('stroke-opacity', 0.7).attr('stroke-width', 2);
      } else {
        el.attr('stroke-opacity', 0.04).attr('stroke-width', 0.5);
      }
    });
    glowGroup.selectAll('path').style('opacity', 0.02);
    nodeGroup.selectAll<SVGGElement, unknown>('.tree-node').each(function () {
      const key = d3.select(this).attr('data-key');
      d3.select(this).style('opacity', highlighted.has(key ?? '') ? '' : '0.1');
    });
  }, [selectedKey, treeData, maxScore, ancestryMap]);

  // ── Pulse animation (16, 17, 18: BFS cascade + particle + fade-in) ──

  const runPulse = useCallback(() => {
    if (!svgRef.current || !treeData) return;
    const id = ++pulseIdRef.current;
    const svg = d3.select(svgRef.current);
    const edges = svg.selectAll<SVGPathElement, unknown>('.hierarchy-edge');
    const particleLayer = svg.select('.particle-layer');
    const nodeGroup = svg.select('.node-layer');

    // Reset: hide glow layer, hide all edges, reset nodes to invisible
    const glowLayer = svg.select('.edge-glow-layer');
    glowLayer.style('opacity', '0');
    edges.each(function () {
      const path = this;
      const len = path.getTotalLength();
      d3.select(path).attr('stroke-dasharray', `${len}`).attr('stroke-dashoffset', `${len}`);
    });
    nodeGroup.selectAll<SVGGElement, unknown>('.tree-node').each(function () {
      const key = d3.select(this).attr('data-key');
      d3.select(this).style('opacity', key === '__prompt__' ? '1' : '0');
    });
    particleLayer.selectAll('*').remove();

    // Compute total animation duration to reveal glow layer after
    const maxDfs = d3.max(treeData.nodes, n => treeData.dfsOrder.get(n.data.key) ?? 0) ?? 0;
    const totalAnimMs = (maxDfs + 1) * EDGE_DRAW_MS + NODE_FADE_MS;
    setTimeout(() => {
      if (pulseIdRef.current === id) glowLayer.transition().duration(400).style('opacity', '1');
    }, totalAnimMs);

    // Animate each edge by BFS order with particle trail
    edges.each(function () {
      const path = this;
      const el = d3.select(path);
      const dfs = Number(el.attr('data-dfs')) || 0;
      const targetKey = el.attr('data-target') ?? '';
      const len = path.getTotalLength();
      const delay = dfs * EDGE_DRAW_MS;

      // Edge draw
      el.transition()
        .delay(delay)
        .duration(EDGE_DRAW_MS)
        .ease(d3.easeLinear)
        .attr('stroke-dashoffset', '0')
        .on('end', function () {
          if (pulseIdRef.current !== id) return;
          d3.select(this).attr('stroke-dasharray', '');
          // Fade in target node
          if (targetKey) {
            nodeGroup.selectAll<SVGGElement, unknown>('.tree-node')
              .filter(function () { return d3.select(this).attr('data-key') === targetKey; })
              .transition().duration(NODE_FADE_MS).style('opacity', '1');
          }
        });

      // Particle dot traveling along the edge
      if (len > 5) {
        const dot = particleLayer.append('circle')
          .attr('r', 2.5).attr('fill', '#fff').attr('fill-opacity', 0.9)
          .attr('filter', 'url(#particle-glow2)');

        const startPt = path.getPointAtLength(0);
        dot.attr('cx', startPt.x).attr('cy', startPt.y).style('opacity', 0);

        dot.transition()
          .delay(delay)
          .duration(0)
          .style('opacity', '1')
          .transition()
          .duration(EDGE_DRAW_MS)
          .ease(d3.easeLinear)
          .attrTween('cx', () => (t: number) => String(path.getPointAtLength(t * len).x))
          .attrTween('cy', () => (t: number) => String(path.getPointAtLength(t * len).y))
          .on('end', function () {
            if (pulseIdRef.current === id) d3.select(this).remove();
          });
      }
    });
  }, [treeData]);

  // Render + pulse — only after container has real dimensions
  useEffect(() => {
    if (!containerReady) return;
    renderTree();
    const timer = setTimeout(() => runPulse(), 150);
    return () => {
      clearTimeout(timer);
      if (svgRef.current) {
        d3.select(svgRef.current).selectAll('.hierarchy-edge').interrupt();
        d3.select(svgRef.current).selectAll('.particle-layer circle').interrupt();
      }
    };
  }, [containerReady, renderTree, runPulse]);

  // ── Zoom controls ──

  const zoomIn = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 1.4);
  }, []);
  const zoomOut = useCallback(() => {
    if (!svgRef.current || !zoomRef.current) return;
    d3.select(svgRef.current).transition().duration(300).call(zoomRef.current.scaleBy, 0.7);
  }, []);
  const zoomFit = useCallback(() => {
    if (!svgRef.current || !zoomRef.current || !containerRef.current || !treeData) return;
    const rect = containerRef.current.getBoundingClientRect();
    const { bounds } = treeData;
    const tw = bounds.maxX - bounds.minX + 200;
    const th = bounds.maxY - bounds.minY + 100;
    const s = Math.min(rect.width / (tw + 80), rect.height / (th + 80), 1);
    const cx = (bounds.minX + bounds.maxX) / 2;
    const ty = 40 - bounds.minY * s;
    d3.select(svgRef.current).transition().duration(500).call(
      zoomRef.current.transform,
      d3.zoomIdentity.translate(rect.width / 2 - cx * s, ty).scale(s),
    );
  }, [treeData]);

  if (neuronScores.length === 0) return null;

  const spreadCount = neuronScores.filter(n => n.spread_boost > 0).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 400, gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: '0.72rem', color: 'var(--text-dim)', flexShrink: 0 }}>
        <span>{neuronScores.length} neurons</span>
        <span>&middot;</span>
        <span>{spreadCount} spread-boosted</span>
        <span>&middot;</span>
        <span style={{ color: 'var(--accent)' }}>click to trace paths &middot; double-click to navigate</span>
      </div>
      <div ref={containerRef} style={{ flex: 1, minHeight: 0, position: 'relative', borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)', background: 'var(--bg)' }}>
          <svg ref={svgRef} width="100%" height="100%" />

          <div style={{ position: 'absolute', top: 8, right: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {[
              { label: '+', fn: zoomIn, title: 'Zoom in' },
              { label: '\u2212', fn: zoomOut, title: 'Zoom out' },
              { label: '\u2b1c', fn: zoomFit, title: 'Fit' },
              { label: '\u21bb', fn: runPulse, title: 'Replay pulse' },
            ].map(b => (
              <button key={b.label} onClick={b.fn} title={b.title} style={{
                width: 28, height: 28, borderRadius: 4, border: '1px solid var(--border)',
                background: 'var(--bg-card)', color: 'var(--text)', cursor: 'pointer',
                fontSize: '0.85rem', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>{b.label}</button>
            ))}
          </div>

          {hoverNode && !hoverNode.isPrompt && (
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0,
              background: 'rgba(15, 23, 42, 0.92)', backdropFilter: 'blur(8px)',
              borderTop: '1px solid rgba(59, 130, 246, 0.2)',
              padding: '10px 16px', fontSize: '0.78rem', color: '#e2e8f0',
              zIndex: 20, pointerEvents: 'none',
              display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap',
            }}>
              <div style={{ minWidth: 160 }}>
                <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 2 }}>
                  {hoverNode.isEngram && <span style={{ color: '#f59e0b', marginRight: 6, fontSize: '0.72rem' }}>ENGRAM</span>}
                  {hoverNode.label}
                </div>
                <div style={{ color: deptColor(hoverNode.department), fontSize: '0.72rem' }}>
                  {hoverNode.isEngram ? 'Regulatory Source' : shortDept(hoverNode.department)} &middot; {hoverNode.isEngram ? 'eCFR' : (LAYER_LABELS[hoverNode.layer] ?? `L${hoverNode.layer}`)}
                </div>
                {hoverNode.summary && (
                  <div style={{ color: '#94a3b8', fontSize: '0.7rem', marginTop: 4, maxWidth: 300 }}>{hoverNode.summary}</div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <div><span style={{ color: '#64748b' }}>Combined</span> <strong>{hoverNode.combined.toFixed(3)}</strong></div>
                {hoverNode.burst != null && <div><span style={{ color: '#64748b' }}>Burst</span> {hoverNode.burst.toFixed(3)}</div>}
                {hoverNode.impact != null && <div><span style={{ color: '#64748b' }}>Impact</span> {hoverNode.impact.toFixed(3)}</div>}
                {hoverNode.precision != null && <div><span style={{ color: '#64748b' }}>Precision</span> {hoverNode.precision.toFixed(3)}</div>}
                {hoverNode.novelty != null && <div><span style={{ color: '#64748b' }}>Novelty</span> {hoverNode.novelty.toFixed(3)}</div>}
                {hoverNode.recency != null && <div><span style={{ color: '#64748b' }}>Recency</span> {hoverNode.recency.toFixed(3)}</div>}
                {hoverNode.relevance != null && <div><span style={{ color: '#64748b' }}>Relevance</span> {hoverNode.relevance.toFixed(3)}</div>}
              </div>
              {hoverNode.spread_boost > 0 && (
                <div style={{ color: SPREAD_RING_COLOR }}>Spread +{hoverNode.spread_boost.toFixed(3)}</div>
              )}
            </div>
          )}
        </div>
    </div>
  );
}
