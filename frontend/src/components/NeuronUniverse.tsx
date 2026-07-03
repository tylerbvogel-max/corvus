import { useState, useEffect, useRef, useCallback, useMemo, type CSSProperties } from 'react';
import ForceGraph3D from 'react-force-graph-3d';
import * as THREE from 'three';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { fetchGraph3D, type Graph3DNode, type Graph3DEdge } from '../api';
import { useUrlSync, codecs, type UrlSchema } from '../hooks/useUrlSync';
import { UniverseSearchOverlay } from './UniverseSearchOverlay';

/**
 * Neuron Universe — the living connectome view.
 *
 * Rendering strategy: react-force-graph-3d supplies ONLY the d3-force-3d
 * simulation, camera/controls and the postprocessing composer. All visible
 * geometry is custom instanced layers added to its scene:
 *   - one InstancedMesh for every neuron core (1 draw call)
 *   - one InstancedMesh for every glow shell (1 draw call, additive)
 *   - one LineSegments buffer for every synapse (1 draw call, additive)
 *   - one Points buffer for traveling signal pulses (1 draw call, shader)
 * so the full graph (~2.3k neurons / ~12k edges) renders at interactive
 * frame rates with edges ON by default.
 *
 * Interaction: picking is a raycast against the core InstancedMesh
 * (per-instance sphere test — cheap), not per-node scene objects.
 * Clicking a neuron enters FOCUS mode: the BFS neighborhood (depth 1–3)
 * becomes the whole universe, the selected neuron is pinned at the origin
 * and hop shells settle into concentric orbits — a dendritic-tree view.
 *
 * Color: categorical region slots validated for the dark surface
 * (dataviz palette; identity is never color-alone — chips legend, hover
 * tooltip and focus labels carry names).
 */

// ── Palette (validated against #05070f, dark mode) ───────────────────
const REGION_SLOTS = [
  '#3987e5', '#199e70', '#c98500', '#008300',
  '#9085e9', '#e66767', '#d55181', '#d95926',
];
const OVERFLOW_REGION_COLOR = '#5c6478';
const CONCEPT_COLOR = '#e879f9';
const ABSTRACTION_ORDER = ['principle', 'process', 'procedure', 'artifact', 'structural', 'concept'];
const ABSTRACTION_COLORS: Record<string, string> = {
  principle: '#3987e5', process: '#199e70', procedure: '#c98500',
  artifact: '#d55181', structural: '#5c6478', concept: CONCEPT_COLOR,
};
// Sequential (single hue) ramp for activity — dark→bright = low→high
const ACTIVITY_RAMP = ['#184f95', '#1c5cab', '#256abf', '#2a78d6', '#3987e5', '#6da7ec', '#9ec5f4'];
const BG_COLOR = '#05070f';
const INK = '#e6e9f2';
const INK_DIM = '#9aa3b5';
const PANEL_BG = '#0b101ce8';
const PANEL_BORDER = '#1c2740';

const SHELL_RADII = [0, 150, 295, 450, 610];
const PULSE_POOL = 420;
const MAX_FOCUS_LABELS = 30;

interface GNode extends Graph3DNode {
  x?: number; y?: number; z?: number;
  fx?: number | null; fy?: number | null; fz?: number | null;
}
interface GLink {
  source: number | GNode; target: number | GNode;
  weight: number; edge_type: string;
}
interface Pulse { edge: number; t: number; speed: number; flip: boolean; live: boolean; }

interface SceneState {
  nodes: GNode[];
  links: GLink[];
  indexById: Map<number, number>;
  endpoints: Int32Array;          // per edge: [aIdx, bIdx]
  core: THREE.InstancedMesh;
  glow: THREE.InstancedMesh;
  lines: THREE.LineSegments;
  linePos: Float32Array;
  lineColBase: Float32Array;      // designed edge colors (weight-scaled)
  pulsePoints: THREE.Points;
  pulsePos: Float32Array;
  pulseCol: Float32Array;
  pulseSize: Float32Array;
  pulses: Pulse[];
  pulseCursor: number;
  excite: Float32Array;           // per node 0..1, decays; drives flare
  baseCol: Float32Array;          // per node rgb
  baseSize: Float32Array;         // per node radius
  incident: number[][];           // node idx -> edge indices
  seedCdf: Float32Array;          // centrality-weighted spawn distribution
  labelGroup: THREE.Group | null; // focus-mode name sprites
  labelNodeIdx: number[];
}

const idOf = (e: number | GNode): number => (typeof e === 'number' ? e : e.id);

function hexToRgb(hex: string): [number, number, number] {
  const c = new THREE.Color(hex);
  return [c.r, c.g, c.b];
}

function makeLabelSprite(text: string, sub: string): THREE.Sprite {
  const scale = 2;
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d')!;
  const font = `600 ${13 * scale}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  const subFont = `400 ${10 * scale}px system-ui, -apple-system, "Segoe UI", sans-serif`;
  ctx.font = font;
  const main = text.length > 30 ? text.slice(0, 29) + '…' : text;
  const w = Math.max(ctx.measureText(main).width, 40) + 16 * scale;
  canvas.width = Math.ceil(w);
  canvas.height = 34 * scale;
  ctx.font = font;
  ctx.textBaseline = 'top';
  ctx.shadowColor = 'rgba(0,0,0,0.9)';
  ctx.shadowBlur = 6 * scale;
  ctx.fillStyle = INK;
  ctx.fillText(main, 8 * scale, 2 * scale);
  if (sub) {
    ctx.font = subFont;
    ctx.fillStyle = INK_DIM;
    ctx.fillText(sub, 8 * scale, 19 * scale);
  }
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, transparent: true, depthWrite: false, depthTest: false,
  }));
  const h = 15;
  sprite.scale.set((canvas.width / canvas.height) * h, h, 1);
  sprite.renderOrder = 10;
  return sprite;
}

const PULSE_VERT = `
attribute float psize;
varying vec3 vColor;
void main() {
  vColor = color;
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = psize * (260.0 / max(20.0, -mv.z));
  gl_Position = projectionMatrix * mv;
}`;
const PULSE_FRAG = `
varying vec3 vColor;
void main() {
  vec2 uv = gl_PointCoord - vec2(0.5);
  float d = length(uv);
  float a = smoothstep(0.5, 0.05, d);
  gl_FragColor = vec4(vColor * a, a);
}`;

export default function NeuronUniverse() {
  const [neurons, setNeurons] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<Graph3DEdge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState<{ width: number; height: number } | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [focusDepth, setFocusDepth] = useState(2);
  const [hoverId, setHoverId] = useState<number | null>(null);
  const [isSearchOpen, setIsSearchOpen] = useState(false);

  const [regionFilter, setRegionFilter] = useState<Set<string>>(new Set());
  const [colorBy, setColorBy] = useState<'region' | 'abstraction' | 'activity'>('region');
  const [sizeBy, setSizeBy] = useState<'centrality' | 'invocations'>('centrality');
  const [edgeWeightView, setEdgeWeightView] = useState(0.3);
  const [regionGravity, setRegionGravity] = useState(0.45);
  const [pulseRate, setPulseRate] = useState(1.0);
  const [bloomEnabled, setBloomEnabled] = useState(true);

  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);
  const sceneStateRef = useRef<SceneState | null>(null);
  const starsRef = useRef<THREE.Points | null>(null);
  const bloomPassRef = useRef<UnrealBloomPass | null>(null);
  const renderPassEnsuredRef = useRef(false);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const hoverIdRef = useRef<number | null>(null);
  const pulseRateRef = useRef(pulseRate);
  const regionGravityRef = useRef(regionGravity);
  const edgeWeightViewRef = useRef(edgeWeightView);
  const focusMapRef = useRef<Map<number, number> | null>(null);
  const selectedIdRef = useRef<number | null>(null);
  const pendingSelIdRef = useRef<number | null>(null);
  const pointerDownRef = useRef<{ x: number; y: number } | null>(null);
  const mouseNdcRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => { pulseRateRef.current = pulseRate; }, [pulseRate]);
  useEffect(() => { regionGravityRef.current = regionGravity; }, [regionGravity]);
  useEffect(() => { selectedIdRef.current = selectedId; }, [selectedId]);

  // ── Container sizing ──
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (w > 0 && h > 0) setDimensions(prev => (prev?.width === w && prev?.height === h) ? prev : { width: w, height: h });
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    requestAnimationFrame(measure);
    return () => ro.disconnect();
  }, []);

  // ── Data load ──
  useEffect(() => {
    (async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchGraph3D(0.25, 12000, 3);
        // Harden against schema drift / a stale backend: coerce the numeric
        // fields the render does NaN-prone math on. centrality feeds the spawn
        // CDF and node sizing — a single undefined turns the whole distribution
        // to NaN and blanks the universe — and .toFixed() throws on hover.
        const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
        setNeurons(data.neurons.map(n => ({
          ...n,
          centrality: num(n.centrality),
          invocations: num(n.invocations),
          avg_utility: num(n.avg_utility),
          abstraction_type: n.abstraction_type ?? null,
        })) as GNode[]);
        setEdges(data.edges.map(e => ({
          ...e, edge_type: e.edge_type ?? 'pyramidal', weight: num(e.weight),
        })));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load graph');
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const isConcept = (n: GNode) => n.node_type === 'concept' && n.layer === -1;

  // ── Region palette: fixed slot order by neuron count (stable per dataset;
  // filters never repaint survivors) ──
  const { regionOrder, regionColor, regionCounts } = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of neurons) {
      if (isConcept(n)) continue;
      const r = n.department || 'Unassigned';
      counts.set(r, (counts.get(r) || 0) + 1);
    }
    const order = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([r]) => r);
    const color = new Map<string, string>();
    order.forEach((r, i) => color.set(r, i < REGION_SLOTS.length ? REGION_SLOTS[i] : OVERFLOW_REGION_COLOR));
    return { regionOrder: order, regionColor: color, regionCounts: counts };
  }, [neurons]);

  const conceptCount = useMemo(() => neurons.filter(isConcept).length, [neurons]);

  // ── Full adjacency over every loaded edge (selection BFS crosses filters) ──
  const fullAdj = useMemo(() => {
    const adj = new Map<number, Array<{ peer: number; weight: number; type: string }>>();
    for (const e of edges) {
      if (!adj.has(e.source)) adj.set(e.source, []);
      if (!adj.has(e.target)) adj.set(e.target, []);
      adj.get(e.source)!.push({ peer: e.target, weight: e.weight, type: e.edge_type });
      adj.get(e.target)!.push({ peer: e.source, weight: e.weight, type: e.edge_type });
    }
    for (const list of adj.values()) list.sort((a, b) => b.weight - a.weight);
    return adj;
  }, [edges]);

  const nodeById = useMemo(() => {
    const m = new Map<number, GNode>();
    neurons.forEach(n => m.set(n.id, n));
    return m;
  }, [neurons]);

  // ── Focus BFS (depth-bounded neighborhood of the selected neuron) ──
  const focusMap = useMemo(() => {
    if (selectedId === null || !nodeById.has(selectedId)) return null;
    const hops = new Map<number, number>();
    hops.set(selectedId, 0);
    const queue: Array<[number, number]> = [[selectedId, 0]];
    while (queue.length) {
      const [id, hop] = queue.shift()!;
      if (hop >= focusDepth) continue;
      for (const nb of fullAdj.get(id) || []) {
        if (!hops.has(nb.peer)) {
          hops.set(nb.peer, hop + 1);
          queue.push([nb.peer, hop + 1]);
        }
      }
    }
    return hops;
  }, [selectedId, focusDepth, fullAdj, nodeById]);
  useEffect(() => { focusMapRef.current = focusMap; }, [focusMap]);

  // ── Stable link objects (d3 mutates source/target into node refs) ──
  const allLinks = useMemo<GLink[]>(
    () => edges.map(e => ({ source: e.source, target: e.target, weight: e.weight, edge_type: e.edge_type })),
    [edges],
  );

  // ── Graph data: full universe (filtered) or the focus neighborhood ──
  const graphData = useMemo(() => {
    if (!neurons.length) return { nodes: [] as GNode[], links: [] as GLink[] };
    if (focusMap) {
      const nodes = neurons.filter(n => focusMap.has(n.id));
      const links = allLinks.filter(l => focusMap.has(idOf(l.source)) && focusMap.has(idOf(l.target)));
      return { nodes, links };
    }
    const regionOk = (n: GNode) =>
      regionFilter.size === 0 ||
      (isConcept(n) ? regionFilter.has('__concepts__') : regionFilter.has(n.department || 'Unassigned'));
    const nodes = neurons.filter(regionOk);
    const kept = new Set(nodes.map(n => n.id));
    const links = allLinks.filter(l => kept.has(idOf(l.source)) && kept.has(idOf(l.target)));
    return { nodes, links };
  }, [neurons, allLinks, focusMap, regionFilter]);

  // ── Node styling resolved at scene-build time ──
  const nodeColorHex = useCallback((n: GNode): string => {
    if (colorBy === 'abstraction') {
      const key = isConcept(n) ? 'concept' : (n.abstraction_type || 'structural');
      return ABSTRACTION_COLORS[key] || OVERFLOW_REGION_COLOR;
    }
    if (colorBy === 'activity') {
      const t = Math.min(1, Math.log1p(n.invocations) / Math.log(200));
      return ACTIVITY_RAMP[Math.min(ACTIVITY_RAMP.length - 1, Math.floor(t * ACTIVITY_RAMP.length))];
    }
    if (isConcept(n)) return CONCEPT_COLOR;
    return regionColor.get(n.department || 'Unassigned') || OVERFLOW_REGION_COLOR;
  }, [colorBy, regionColor]);

  const nodeRadius = useCallback((n: GNode): number => {
    if (isConcept(n)) return 6.5;
    const raw = sizeBy === 'centrality'
      ? 2.2 + Math.sqrt(n.centrality) * 9
      : 2.2 + Math.min(4.5, Math.log1p(n.invocations) * 1.1);
    return raw;
  }, [sizeBy]);

  // ── Edge base color: endpoint hues, weight-scaled for additive blending.
  // Pyramidal (cross-region) fibers cool toward blue-white; instantiates
  // (concept links) tint violet — synapse types stay visually distinct. ──
  const edgeVertexColors = useCallback((
    link: GLink, ca: [number, number, number], cb: [number, number, number],
  ): [number, number, number, number, number, number] => {
    const alpha = 0.055 + Math.min(0.36, link.weight * 0.42);
    let ta: [number, number, number] = ca, tb: [number, number, number] = cb, mix = 0;
    if (link.edge_type === 'pyramidal') { ta = [0.62, 0.72, 1.0]; tb = [0.62, 0.72, 1.0]; mix = 0.45; }
    else if (link.edge_type === 'instantiates') { ta = hexToRgb(CONCEPT_COLOR); tb = ta; mix = 0.6; }
    const l = (a: number, b: number) => a + (b - a) * mix;
    return [
      l(ca[0], ta[0]) * alpha, l(ca[1], ta[1]) * alpha, l(ca[2], ta[2]) * alpha,
      l(cb[0], tb[0]) * alpha, l(cb[1], tb[1]) * alpha, l(cb[2], tb[2]) * alpha,
    ];
  }, []);

  // ── Scene build: instanced layers for the current graphData ──
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !dimensions || !graphData.nodes.length) return;
    const scene: THREE.Scene | undefined = fg.scene?.();
    if (!scene) return;

    const nodes = graphData.nodes;
    const links = graphData.links;
    const N = nodes.length, E = links.length;
    const indexById = new Map<number, number>();
    nodes.forEach((n, i) => indexById.set(n.id, i));

    // Node cores + glow shells
    const coreGeo = new THREE.SphereGeometry(1, 12, 10);
    coreGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1);
    const coreMat = new THREE.MeshBasicMaterial({ toneMapped: false });
    const core = new THREE.InstancedMesh(coreGeo, coreMat, N);
    core.frustumCulled = false;
    const glowGeo = new THREE.SphereGeometry(1, 8, 6);
    const glowMat = new THREE.MeshBasicMaterial({
      transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending,
      depthWrite: false, toneMapped: false,
    });
    const glow = new THREE.InstancedMesh(glowGeo, glowMat, N);
    glow.frustumCulled = false;

    const baseCol = new Float32Array(N * 3);
    const baseSize = new Float32Array(N);
    const excite = new Float32Array(N);
    const tmpColor = new THREE.Color();
    for (let i = 0; i < N; i++) {
      const [r, g, b] = hexToRgb(nodeColorHex(nodes[i]));
      baseCol[i * 3] = r; baseCol[i * 3 + 1] = g; baseCol[i * 3 + 2] = b;
      baseSize[i] = nodeRadius(nodes[i]);
      core.setColorAt(i, tmpColor.setRGB(r, g, b));
      glow.setColorAt(i, tmpColor.setRGB(r, g, b));
    }

    // Synapse lines (single buffer, additive)
    const linePos = new Float32Array(E * 2 * 3);
    const lineColBase = new Float32Array(E * 2 * 3);
    const endpoints = new Int32Array(E * 2);
    const incident: number[][] = Array.from({ length: N }, () => []);
    for (let e = 0; e < E; e++) {
      const a = indexById.get(idOf(links[e].source))!;
      const b = indexById.get(idOf(links[e].target))!;
      endpoints[e * 2] = a; endpoints[e * 2 + 1] = b;
      incident[a].push(e); incident[b].push(e);
      const ca: [number, number, number] = [baseCol[a * 3], baseCol[a * 3 + 1], baseCol[a * 3 + 2]];
      const cb: [number, number, number] = [baseCol[b * 3], baseCol[b * 3 + 1], baseCol[b * 3 + 2]];
      const vc = edgeVertexColors(links[e], ca, cb);
      lineColBase.set(vc, e * 6);
    }
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
    lineGeo.setAttribute('color', new THREE.BufferAttribute(lineColBase.slice(), 3));
    const lines = new THREE.LineSegments(lineGeo, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, blending: THREE.AdditiveBlending,
      depthWrite: false, toneMapped: false,
    }));
    lines.frustumCulled = false;

    // Signal pulses
    const pulsePos = new Float32Array(PULSE_POOL * 3);
    const pulseCol = new Float32Array(PULSE_POOL * 3);
    const pulseSize = new Float32Array(PULSE_POOL);
    const pulseGeo = new THREE.BufferGeometry();
    pulseGeo.setAttribute('position', new THREE.BufferAttribute(pulsePos, 3));
    pulseGeo.setAttribute('color', new THREE.BufferAttribute(pulseCol, 3));
    pulseGeo.setAttribute('psize', new THREE.BufferAttribute(pulseSize, 1));
    pulseGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 1e6);
    const pulsePoints = new THREE.Points(pulseGeo, new THREE.ShaderMaterial({
      vertexShader: PULSE_VERT, fragmentShader: PULSE_FRAG,
      vertexColors: true, transparent: true, blending: THREE.AdditiveBlending,
      depthWrite: false,
    }));
    pulsePoints.frustumCulled = false;

    // Centrality-weighted spawn distribution
    const seedCdf = new Float32Array(N);
    let acc = 0;
    for (let i = 0; i < N; i++) {
      acc += 0.12 + nodes[i].centrality + (incident[i].length > 0 ? 0.05 : 0);
      seedCdf[i] = acc;
    }

    // Focus-mode name sprites for the inner shell
    let labelGroup: THREE.Group | null = null;
    const labelNodeIdx: number[] = [];
    if (focusMapRef.current) {
      labelGroup = new THREE.Group();
      const fm = focusMapRef.current;
      const inner = nodes
        .map((n, i) => ({ n, i, hop: fm.get(n.id) ?? 9 }))
        .filter(x => x.hop <= 1)
        .sort((x, y) => x.hop - y.hop || y.n.centrality - x.n.centrality)
        .slice(0, MAX_FOCUS_LABELS);
      for (const { n, i } of inner) {
        const spr = makeLabelSprite(n.label, isConcept(n) ? 'concept' : (n.department || ''));
        labelGroup.add(spr);
        labelNodeIdx.push(i);
      }
    }

    scene.add(core); scene.add(glow); scene.add(lines); scene.add(pulsePoints);
    if (labelGroup) scene.add(labelGroup);
    scene.fog = new THREE.FogExp2(new THREE.Color(BG_COLOR).getHex(), 0.00036);

    sceneStateRef.current = {
      nodes, links, indexById, endpoints, core, glow, lines,
      linePos, lineColBase, pulsePoints, pulsePos, pulseCol, pulseSize,
      pulses: Array.from({ length: PULSE_POOL }, () => ({ edge: -1, t: 0, speed: 1, flip: false, live: false })),
      pulseCursor: 0,
      excite, baseCol, baseSize, incident, seedCdf,
      labelGroup, labelNodeIdx,
    };
    applyEdgeVisibility(sceneStateRef.current, edgeWeightViewRef.current);
    syncPositions();

    return () => {
      scene.remove(core); scene.remove(glow); scene.remove(lines); scene.remove(pulsePoints);
      if (labelGroup) {
        labelGroup.traverse(o => {
          const spr = o as THREE.Sprite;
          if (spr.material) {
            (spr.material as THREE.SpriteMaterial).map?.dispose();
            (spr.material as THREE.Material).dispose();
          }
        });
        scene.remove(labelGroup);
      }
      coreGeo.dispose(); coreMat.dispose(); glowGeo.dispose(); glowMat.dispose();
      lineGeo.dispose(); (lines.material as THREE.Material).dispose();
      pulseGeo.dispose(); (pulsePoints.material as THREE.Material).dispose();
      if (sceneStateRef.current?.core === core) sceneStateRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, loading, dimensions, nodeColorHex, nodeRadius, edgeVertexColors]);

  // ── Edge visibility slider: zero out colors below threshold (no rebuild) ──
  function applyEdgeVisibility(s: SceneState, minW: number) {
    const attr = s.lines.geometry.getAttribute('color') as THREE.BufferAttribute;
    const arr = attr.array as Float32Array;
    for (let e = 0; e < s.links.length; e++) {
      const show = s.links[e].weight >= minW || focusMapRef.current !== null;
      for (let k = 0; k < 6; k++) arr[e * 6 + k] = show ? s.lineColBase[e * 6 + k] : 0;
    }
    attr.needsUpdate = true;
  }
  useEffect(() => {
    edgeWeightViewRef.current = edgeWeightView;
    const s = sceneStateRef.current;
    if (s) applyEdgeVisibility(s, edgeWeightView);
  }, [edgeWeightView]);

  // ── Position sync (engine tick): matrices + line endpoints + labels ──
  const tmpM = useRef(new THREE.Matrix4()).current;
  const tmpV = useRef(new THREE.Vector3()).current;
  const tmpS = useRef(new THREE.Vector3()).current;
  const tmpQ = useRef(new THREE.Quaternion()).current;
  const syncPositions = useCallback(() => {
    const s = sceneStateRef.current;
    if (!s) return;
    const { nodes } = s;
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const flare = 1 + 0.4 * s.excite[i];
      tmpV.set(n.x || 0, n.y || 0, n.z || 0);
      tmpS.setScalar(s.baseSize[i] * flare);
      tmpM.compose(tmpV, tmpQ, tmpS);
      s.core.setMatrixAt(i, tmpM);
      tmpS.setScalar(s.baseSize[i] * flare * 2.4);
      tmpM.compose(tmpV, tmpQ, tmpS);
      s.glow.setMatrixAt(i, tmpM);
    }
    s.core.instanceMatrix.needsUpdate = true;
    s.glow.instanceMatrix.needsUpdate = true;
    for (let e = 0; e < s.links.length; e++) {
      const a = s.nodes[s.endpoints[e * 2]], b = s.nodes[s.endpoints[e * 2 + 1]];
      s.linePos[e * 6] = a.x || 0; s.linePos[e * 6 + 1] = a.y || 0; s.linePos[e * 6 + 2] = a.z || 0;
      s.linePos[e * 6 + 3] = b.x || 0; s.linePos[e * 6 + 4] = b.y || 0; s.linePos[e * 6 + 5] = b.z || 0;
    }
    (s.lines.geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
    if (s.labelGroup) {
      s.labelGroup.children.forEach((spr, k) => {
        const i = s.labelNodeIdx[k];
        const n = s.nodes[i];
        spr.position.set(n.x || 0, (n.y || 0) + s.baseSize[i] * 2.2 + 9, n.z || 0);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── The living-brain loop: excitation decay, flares, signal pulses ──
  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    let fireTimer = 0;
    let frame = 0;
    const tmpC = new THREE.Color();
    const raycaster = new THREE.Raycaster();

    const spawnPulse = (s: SceneState, edge: number, fromIdx: number) => {
      const p = s.pulses[s.pulseCursor];
      s.pulseCursor = (s.pulseCursor + 1) % PULSE_POOL;
      p.edge = edge;
      p.flip = s.endpoints[edge * 2] !== fromIdx;
      p.t = 0;
      p.speed = 0.5 + s.links[edge].weight * 1.1 + Math.random() * 0.4;
      p.live = true;
    };

    const pickSpawnEdge = (s: SceneState, nodeIdx: number): number => {
      const inc = s.incident[nodeIdx];
      if (!inc.length) return -1;
      // weight-biased pick: a few tries, keep the strongest sampled
      let best = inc[(Math.random() * inc.length) | 0];
      for (let k = 0; k < 2; k++) {
        const cand = inc[(Math.random() * inc.length) | 0];
        if (s.links[cand].weight > s.links[best].weight) best = cand;
      }
      return best;
    };

    const seedNode = (s: SceneState): number => {
      const fm = focusMapRef.current;
      if (fm && selectedIdRef.current !== null && Math.random() < 0.72) {
        return s.indexById.get(selectedIdRef.current) ?? 0;
      }
      const total = s.seedCdf[s.seedCdf.length - 1];
      const r = Math.random() * total;
      let lo = 0, hi = s.seedCdf.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (s.seedCdf[mid] < r) lo = mid + 1; else hi = mid; }
      return lo;
    };

    const tick = (t: number) => {
      raf = requestAnimationFrame(tick);
      const dt = Math.min(0.05, (t - last) / 1000);
      last = t;
      const s = sceneStateRef.current;
      const fg = fgRef.current;
      if (!s || !fg) return;
      frame++;

      // Hover picking (every other frame)
      if (frame % 2 === 0 && mouseNdcRef.current) {
        const cam = fg.camera?.();
        if (cam) {
          raycaster.setFromCamera(mouseNdcRef.current as any, cam);
          const hits = raycaster.intersectObject(s.core, false);
          const id = hits.length ? s.nodes[hits[0].instanceId!]?.id ?? null : null;
          if (id !== hoverIdRef.current) {
            hoverIdRef.current = id;
            setHoverId(id);
          }
        }
      }

      // Excitation decay + hover/root floors
      const decay = Math.exp(-3.1 * dt);
      for (let i = 0; i < s.excite.length; i++) s.excite[i] *= decay;
      if (hoverIdRef.current !== null) {
        const i = s.indexById.get(hoverIdRef.current);
        if (i !== undefined) s.excite[i] = Math.max(s.excite[i], 0.6);
      }
      if (selectedIdRef.current !== null) {
        const i = s.indexById.get(selectedIdRef.current);
        if (i !== undefined) s.excite[i] = Math.max(s.excite[i], 0.45);
      }

      // Instance colors from excitation (flare toward white-hot)
      for (let i = 0; i < s.nodes.length; i++) {
        const ex = s.excite[i];
        const boost = 0.82 + 1.9 * ex;
        tmpC.setRGB(
          Math.min(1.6, s.baseCol[i * 3] * boost + ex * 0.35),
          Math.min(1.6, s.baseCol[i * 3 + 1] * boost + ex * 0.35),
          Math.min(1.6, s.baseCol[i * 3 + 2] * boost + ex * 0.35),
        );
        s.core.setColorAt(i, tmpC);
        s.glow.setColorAt(i, tmpC);
      }
      if (s.core.instanceColor) s.core.instanceColor.needsUpdate = true;
      if (s.glow.instanceColor) s.glow.instanceColor.needsUpdate = true;

      // Flare scale ride (matrices) — cheap, and keeps motion alive post-cooldown
      syncPositions();

      // Auto-fire: ambient thought traffic
      fireTimer += dt * pulseRateRef.current;
      while (fireTimer > 0.42) {
        fireTimer -= 0.42;
        const seed = seedNode(s);
        const burst = 2 + ((Math.random() * 2) | 0);
        for (let k = 0; k < burst; k++) {
          const e = pickSpawnEdge(s, seed);
          if (e >= 0) spawnPulse(s, e, seed);
        }
        s.excite[seed] = Math.min(1, s.excite[seed] + 0.5);
      }

      // Advance pulses; cascade on arrival
      const minW = edgeWeightViewRef.current;
      for (let pi = 0; pi < s.pulses.length; pi++) {
        const p = s.pulses[pi];
        if (!p.live) { s.pulseSize[pi] = 0; continue; }
        p.t += dt * p.speed * Math.max(0.25, pulseRateRef.current);
        const aIdx = s.endpoints[p.edge * 2 + (p.flip ? 1 : 0)];
        const bIdx = s.endpoints[p.edge * 2 + (p.flip ? 0 : 1)];
        if (p.t >= 1) {
          s.excite[bIdx] = Math.min(1, s.excite[bIdx] + 0.55);
          if (Math.random() < 0.62) {
            const e = pickSpawnEdge(s, bIdx);
            if (e >= 0 && s.links[e].weight >= minW * 0.6) { p.edge = e; p.flip = s.endpoints[e * 2] !== bIdx; p.t = 0; p.speed = 0.5 + s.links[e].weight * 1.1 + Math.random() * 0.4; continue; }
          }
          p.live = false; s.pulseSize[pi] = 0; continue;
        }
        const a = s.nodes[aIdx], b = s.nodes[bIdx];
        const u = p.t;
        s.pulsePos[pi * 3] = (a.x || 0) + ((b.x || 0) - (a.x || 0)) * u;
        s.pulsePos[pi * 3 + 1] = (a.y || 0) + ((b.y || 0) - (a.y || 0)) * u;
        s.pulsePos[pi * 3 + 2] = (a.z || 0) + ((b.z || 0) - (a.z || 0)) * u;
        const ci = bIdx * 3;
        const heat = 0.55 + 0.45 * Math.sin(Math.PI * u);
        s.pulseCol[pi * 3] = Math.min(1.5, s.baseCol[ci] * 1.5 + 0.5) * heat;
        s.pulseCol[pi * 3 + 1] = Math.min(1.5, s.baseCol[ci + 1] * 1.5 + 0.5) * heat;
        s.pulseCol[pi * 3 + 2] = Math.min(1.5, s.baseCol[ci + 2] * 1.5 + 0.5) * heat;
        s.pulseSize[pi] = 2.6 + s.links[p.edge].weight * 3.4;
      }
      (s.pulsePoints.geometry.getAttribute('position') as THREE.BufferAttribute).needsUpdate = true;
      (s.pulsePoints.geometry.getAttribute('color') as THREE.BufferAttribute).needsUpdate = true;
      (s.pulsePoints.geometry.getAttribute('psize') as THREE.BufferAttribute).needsUpdate = true;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [syncPositions]);

  // ── Pointer handling on the WebGL canvas (custom picking) ──
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !dimensions) return;
    const dom: HTMLElement | undefined = fg.renderer?.()?.domElement;
    if (!dom) return;

    const onMove = (ev: PointerEvent) => {
      const rect = dom.getBoundingClientRect();
      mouseNdcRef.current = {
        x: ((ev.clientX - rect.left) / rect.width) * 2 - 1,
        y: -((ev.clientY - rect.top) / rect.height) * 2 + 1,
      };
      const tip = tooltipRef.current;
      if (tip) {
        tip.style.left = `${ev.clientX - rect.left + 16}px`;
        tip.style.top = `${ev.clientY - rect.top + 12}px`;
      }
    };
    const onDown = (ev: PointerEvent) => {
      if (ev.button === 0) pointerDownRef.current = { x: ev.clientX, y: ev.clientY };
    };
    const onUp = (ev: PointerEvent) => {
      const d = pointerDownRef.current;
      pointerDownRef.current = null;
      if (!d || ev.button !== 0) return;
      if (Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 5) return; // drag, not click
      const hid = hoverIdRef.current;
      if (hid !== null) setSelectedId(prev => (prev === hid ? null : hid));
      else setSelectedId(null);
    };
    const onLeave = () => { mouseNdcRef.current = null; if (hoverIdRef.current !== null) { hoverIdRef.current = null; setHoverId(null); } };

    dom.addEventListener('pointermove', onMove);
    dom.addEventListener('pointerdown', onDown);
    dom.addEventListener('pointerup', onUp);
    dom.addEventListener('pointerleave', onLeave);
    return () => {
      dom.removeEventListener('pointermove', onMove);
      dom.removeEventListener('pointerdown', onDown);
      dom.removeEventListener('pointerup', onUp);
      dom.removeEventListener('pointerleave', onLeave);
    };
  }, [loading, dimensions]);

  // ── Forces: link/charge tuning + region gravity (universe) or
  // concentric hop shells (focus) ──
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !graphData.nodes.length || !fg.d3Force) return;
    const inFocus = focusMap !== null;

    fg.d3Force('link')?.distance((l: GLink) => 26 + (1 - l.weight) * 64)
      .strength((l: GLink) => 0.05 + l.weight * 0.22);
    fg.d3Force('charge')?.strength(inFocus ? -75 : -40).distanceMax(1100);

    if (inFocus) {
      fg.d3Force('regionGravity', null);
      let simNodes: GNode[] = [];
      const shell: any = (alpha: number) => {
        const fm = focusMapRef.current;
        if (!fm) return;
        for (const n of simNodes) {
          const hop = fm.get(n.id);
          if (hop === undefined || hop === 0) continue;
          const rT = SHELL_RADII[Math.min(hop, SHELL_RADII.length - 1)];
          const x = n.x || 0, y = n.y || 0, z = n.z || 0;
          const r = Math.sqrt(x * x + y * y + z * z) || 1;
          const f = ((rT - r) / r) * 0.16 * alpha;
          (n as any).vx = ((n as any).vx ?? 0) + x * f;
          (n as any).vy = ((n as any).vy ?? 0) + y * f;
          (n as any).vz = ((n as any).vz ?? 0) + z * f;
        }
      };
      shell.initialize = (ns: GNode[]) => { simNodes = ns; };
      fg.d3Force('focusShell', shell);
    } else {
      fg.d3Force('focusShell', null);
      let simNodes: GNode[] = [];
      const centroids = new Map<string, { x: number; y: number; z: number; n: number }>();
      const gravity: any = (alpha: number) => {
        const k = regionGravityRef.current * 0.026 * alpha;
        if (k <= 0) return;
        centroids.clear();
        for (const n of simNodes) {
          const key = n.department || '';
          if (!key || (n.node_type === 'concept' && n.layer === -1)) continue;
          let c = centroids.get(key);
          if (!c) { c = { x: 0, y: 0, z: 0, n: 0 }; centroids.set(key, c); }
          c.x += n.x || 0; c.y += n.y || 0; c.z += n.z || 0; c.n++;
        }
        for (const c of centroids.values()) { c.x /= c.n; c.y /= c.n; c.z /= c.n; }
        for (const n of simNodes) {
          const c = centroids.get(n.department || '');
          if (!c || c.n < 2) continue;
          (n as any).vx = ((n as any).vx ?? 0) + (c.x - (n.x || 0)) * k;
          (n as any).vy = ((n as any).vy ?? 0) + (c.y - (n.y || 0)) * k;
          (n as any).vz = ((n as any).vz ?? 0) + (c.z - (n.z || 0)) * k;
        }
      };
      gravity.initialize = (ns: GNode[]) => { simNodes = ns; };
      fg.d3Force('regionGravity', gravity);
    }
    fg.d3ReheatSimulation?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphData, focusMap]);

  // ── Focus enter/exit: pin the sun, fly the camera ──
  const prevFocusRef = useRef(false);
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const inFocus = focusMap !== null && selectedId !== null;

    for (const n of neurons) { n.fx = null; n.fy = null; n.fz = null; }
    if (inFocus) {
      const root = nodeById.get(selectedId!);
      if (root) { root.fx = 0; root.fy = 0; root.fz = 0; root.x = 0; root.y = 0; root.z = 0; }
      const dist = SHELL_RADII[Math.min(focusDepth, SHELL_RADII.length - 1)] + 330;
      fg.cameraPosition?.({ x: dist * 0.18, y: dist * 0.3, z: dist }, { x: 0, y: 0, z: 0 }, 1300);
    } else if (prevFocusRef.current) {
      window.setTimeout(() => fg.zoomToFit?.(1100, 60), 450);
    }
    prevFocusRef.current = inFocus;
  }, [focusMap, selectedId, focusDepth, neurons, nodeById]);

  // ── Bloom ──
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !dimensions) return;
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
      const bloom = new UnrealBloomPass(new THREE.Vector2(dimensions.width, dimensions.height), 0.95, 0.72, 0.12);
      const outIdx = composer.passes.findIndex((p: any) => p instanceof OutputPass);
      if (outIdx === -1) { composer.addPass(bloom); composer.addPass(new OutputPass()); }
      else composer.insertPass(bloom, outIdx);
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
  }, [bloomEnabled, loading, dimensions]);

  // ── Starfield backdrop (once) ──
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || loading || !dimensions || starsRef.current) return;
    const scene = fg.scene?.();
    if (!scene) return;
    const STAR_N = 1300;
    const pos = new Float32Array(STAR_N * 3);
    const col = new Float32Array(STAR_N * 3);
    for (let i = 0; i < STAR_N; i++) {
      const r = 2600 + Math.random() * 1800;
      const th = Math.random() * Math.PI * 2;
      const ph = Math.acos(2 * Math.random() - 1);
      pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
      pos[i * 3 + 1] = r * Math.cos(ph);
      pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
      const b = 0.12 + Math.random() * 0.2;
      col[i * 3] = b * 0.8; col[i * 3 + 1] = b * 0.9; col[i * 3 + 2] = b * 1.25;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    const stars = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 2.4, vertexColors: true, transparent: true, opacity: 0.85,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    }));
    stars.frustumCulled = false;
    scene.add(stars);
    starsRef.current = stars;
    return () => {
      scene.remove(stars);
      geo.dispose();
      (stars.material as THREE.Material).dispose();
      starsRef.current = null;
    };
  }, [loading, dimensions]);

  // ── Initial zoom-to-fit + pending URL selection ──
  useEffect(() => {
    if (loading || !fgRef.current || neurons.length === 0) return;
    const handle = window.setTimeout(() => {
      fgRef.current?.zoomToFit?.(900, 70);
      if (pendingSelIdRef.current !== null) {
        const id = pendingSelIdRef.current;
        pendingSelIdRef.current = null;
        if (nodeById.has(id)) setSelectedId(id);
      }
    }, 1700);
    return () => window.clearTimeout(handle);
  }, [loading, neurons, nodeById]);

  // ── Keyboard: Esc exits focus, Ctrl+F searches ──
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setIsSearchOpen(true);
        return;
      }
      if (e.key === 'Escape' && !isSearchOpen && selectedIdRef.current !== null) setSelectedId(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isSearchOpen]);

  // ── URL sync ──
  const urlState = useMemo(() => ({
    sel: selectedId,
    d: focusDepth,
    r: Array.from(regionFilter).join('|'),
    c: colorBy as string,
    s: sizeBy as string,
    ew: edgeWeightView,
    g: regionGravity,
    b: bloomEnabled,
  }), [selectedId, focusDepth, regionFilter, colorBy, sizeBy, edgeWeightView, regionGravity, bloomEnabled]);
  const urlSchema: UrlSchema<typeof urlState> = {
    sel: codecs.nullableInt, d: codecs.int, r: codecs.str, c: codecs.str,
    s: codecs.str, ew: codecs.num, g: codecs.num, b: codecs.bool,
  };
  const handleRestore = useCallback((r: Partial<typeof urlState>) => {
    if (r.d !== undefined) setFocusDepth(Math.max(1, Math.min(3, r.d)));
    if (r.r !== undefined) setRegionFilter(new Set(r.r ? r.r.split('|') : []));
    if (r.c === 'region' || r.c === 'abstraction' || r.c === 'activity') setColorBy(r.c);
    if (r.s === 'centrality' || r.s === 'invocations') setSizeBy(r.s);
    if (r.ew !== undefined) setEdgeWeightView(Math.max(0.25, Math.min(0.8, r.ew)));
    if (r.g !== undefined) setRegionGravity(Math.max(0, Math.min(1, r.g)));
    if (r.b !== undefined) setBloomEnabled(r.b);
    if (r.sel !== undefined) {
      if (r.sel === null) setSelectedId(null);
      else if (nodeById.has(r.sel)) setSelectedId(r.sel);
      else pendingSelIdRef.current = r.sel;
    }
  }, [nodeById]);
  useUrlSync('universe', urlState, urlSchema, { pushOnKeys: ['sel'], onRestore: handleRestore });

  // ── Derived UI data ──
  const hoverNode = hoverId !== null ? nodeById.get(hoverId) ?? null : null;
  const selectedNode = selectedId !== null ? nodeById.get(selectedId) ?? null : null;
  const topConnections = useMemo(() => {
    if (!selectedNode) return [];
    return (fullAdj.get(selectedNode.id) || []).slice(0, 8)
      .map(c => ({ ...c, node: nodeById.get(c.peer) }))
      .filter(c => c.node);
  }, [selectedNode, fullAdj, nodeById]);

  const visibleCounts = { nodes: graphData.nodes.length, links: graphData.links.length };
  const ready = !loading && !error && dimensions;

  const toggleRegion = (key: string) => {
    setRegionFilter(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const chipStyle = (active: boolean, dimmed: boolean): CSSProperties => ({
    display: 'inline-flex', alignItems: 'center', gap: 6,
    padding: '3px 10px', borderRadius: 999, cursor: 'pointer',
    border: `1px solid ${active ? '#3a4a70' : PANEL_BORDER}`,
    background: active ? '#16203a' : '#0c1220',
    opacity: dimmed ? 0.42 : 1, fontSize: '0.72rem', color: INK,
    userSelect: 'none', whiteSpace: 'nowrap',
  });

  const sliderRow = (label: string, value: string, input: React.ReactNode) => (
    <label style={{ display: 'block', marginBottom: 8, fontSize: '0.72rem', color: INK_DIM }}>
      <span style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>{label}</span><span style={{ color: INK }}>{value}</span>
      </span>
      {input}
    </label>
  );

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%', overflow: 'hidden', background: BG_COLOR }} ref={containerRef}>
      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: INK_DIM, position: 'absolute', inset: 0, zIndex: 10 }}>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '1.15rem', marginBottom: 8, color: INK }}>Waking the connectome…</div>
            <div style={{ fontSize: '0.8rem' }}>Loading neurons and synapses</div>
          </div>
        </div>
      )}
      {error && <div style={{ color: '#e66767', padding: 20, position: 'absolute', inset: 0, zIndex: 10 }}>{error}</div>}

      {ready && (
        <ForceGraph3D
          ref={fgRef}
          width={dimensions.width}
          height={dimensions.height}
          graphData={graphData as any}
          nodeId="id"
          nodeThreeObject={() => new THREE.Group()}
          nodeThreeObjectExtend={false}
          linkVisibility={false}
          linkWidth={0}
          linkPositionUpdate={() => true}
          enableNodeDrag={false}
          onEngineTick={syncPositions}
          backgroundColor={BG_COLOR}
          showNavInfo={false}
          d3AlphaDecay={0.021}
          d3VelocityDecay={0.32}
          warmupTicks={90}
          cooldownTicks={260}
        />
      )}

      {/* Hover tooltip (custom picking — the lib never sees our instanced nodes) */}
      <div
        ref={tooltipRef}
        style={{
          position: 'absolute', pointerEvents: 'none', zIndex: 20,
          display: hoverNode ? 'block' : 'none',
          background: PANEL_BG, border: `1px solid ${PANEL_BORDER}`, borderRadius: 8,
          padding: '8px 12px', maxWidth: 320, fontSize: '0.78rem', color: INK,
          backdropFilter: 'blur(6px)',
        }}
      >
        {hoverNode && (
          <>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{hoverNode.label}</div>
            <div style={{ color: INK_DIM, fontSize: '0.72rem', display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: nodeColorHex(hoverNode), display: 'inline-block' }} />
              {isConcept(hoverNode) ? 'Concept' : (hoverNode.department || 'Unassigned')}
              {hoverNode.abstraction_type && !isConcept(hoverNode) && <span>· {hoverNode.abstraction_type}</span>}
            </div>
            <div style={{ color: INK_DIM, fontSize: '0.7rem', marginTop: 4 }}>
              fired {hoverNode.invocations}× · utility {hoverNode.avg_utility.toFixed(2)} · centrality {hoverNode.centrality.toFixed(2)}
            </div>
            <div style={{ color: '#6da7ec', fontSize: '0.68rem', marginTop: 4 }}>click to focus</div>
          </>
        )}
      </div>

      {ready && (
        <>
          {/* Control panel */}
          <div style={{
            position: 'absolute', top: 12, left: 12, background: PANEL_BG,
            border: `1px solid ${PANEL_BORDER}`, borderRadius: 10, padding: '12px 14px',
            backdropFilter: 'blur(8px)', fontSize: '0.8rem', width: 228, zIndex: 15, color: INK,
          }}>
            <div style={{ fontWeight: 600, marginBottom: 4, fontSize: '0.92rem' }}>Neuron Universe</div>
            <div style={{ color: INK_DIM, marginBottom: 10, fontSize: '0.72rem' }}>
              {visibleCounts.nodes.toLocaleString()} neurons · {visibleCounts.links.toLocaleString()} synapses
              {focusMap && <span style={{ color: '#c98500' }}> · focus</span>}
            </div>

            <label style={{ display: 'block', marginBottom: 8, fontSize: '0.72rem', color: INK_DIM }}>
              Color by
              <select value={colorBy} onChange={e => setColorBy(e.target.value as any)}
                style={{ marginLeft: 8, background: '#101828', color: INK, border: `1px solid ${PANEL_BORDER}`, borderRadius: 4, padding: '2px 6px', fontSize: '0.72rem' }}>
                <option value="region">Region</option>
                <option value="abstraction">Abstraction</option>
                <option value="activity">Activity</option>
              </select>
            </label>
            <label style={{ display: 'block', marginBottom: 10, fontSize: '0.72rem', color: INK_DIM }}>
              Size by
              <select value={sizeBy} onChange={e => setSizeBy(e.target.value as any)}
                style={{ marginLeft: 8, background: '#101828', color: INK, border: `1px solid ${PANEL_BORDER}`, borderRadius: 4, padding: '2px 6px', fontSize: '0.72rem' }}>
                <option value="centrality">Centrality</option>
                <option value="invocations">Invocations</option>
              </select>
            </label>

            {sliderRow('Synapse visibility ≥', edgeWeightView.toFixed(2),
              <input type="range" min={0.25} max={0.8} step={0.05} value={edgeWeightView}
                onChange={e => setEdgeWeightView(parseFloat(e.target.value))} style={{ width: '100%', marginTop: 4 }} />)}
            {sliderRow('Region pull', regionGravity.toFixed(2),
              <input type="range" min={0} max={1} step={0.05} value={regionGravity}
                onChange={e => setRegionGravity(parseFloat(e.target.value))} style={{ width: '100%', marginTop: 4 }} />)}
            {sliderRow('Signal rate', `${pulseRate.toFixed(1)}×`,
              <input type="range" min={0} max={3} step={0.1} value={pulseRate}
                onChange={e => setPulseRate(parseFloat(e.target.value))} style={{ width: '100%', marginTop: 4 }} />)}

            <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10, fontSize: '0.72rem', color: INK_DIM }}>
              <input type="checkbox" checked={bloomEnabled} onChange={e => setBloomEnabled(e.target.checked)} /> Bloom
            </label>

            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={() => { setSelectedId(null); fgRef.current?.zoomToFit?.(800, 60); }}
                style={{ flex: 1, background: '#16203a', border: `1px solid ${PANEL_BORDER}`, borderRadius: 5, color: '#6da7ec', padding: '4px 0', fontSize: '0.72rem', cursor: 'pointer' }}>
                Reset view
              </button>
              <button onClick={() => setIsSearchOpen(true)}
                style={{ flex: 1, background: '#16203a', border: `1px solid ${PANEL_BORDER}`, borderRadius: 5, color: '#6da7ec', padding: '4px 0', fontSize: '0.72rem', cursor: 'pointer' }}>
                Search ⌃F
              </button>
            </div>
          </div>

          {/* Region chips: legend + filter in one (fixed slot order) */}
          <div style={{
            position: 'absolute', bottom: 12, left: 12, right: 12, zIndex: 15,
            display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center',
          }}>
            {regionOrder.map(r => {
              const active = regionFilter.size === 0 || regionFilter.has(r);
              return (
                <span key={r} style={chipStyle(regionFilter.has(r), !active)} onClick={() => toggleRegion(r)}
                  title={regionFilter.size ? 'Toggle region' : 'Click to isolate region'}>
                  <span style={{ width: 9, height: 9, borderRadius: '50%', background: regionColor.get(r), display: 'inline-block' }} />
                  {r} <span style={{ color: INK_DIM }}>{regionCounts.get(r)}</span>
                </span>
              );
            })}
            {conceptCount > 0 && (
              <span style={chipStyle(regionFilter.has('__concepts__'), !(regionFilter.size === 0 || regionFilter.has('__concepts__')))}
                onClick={() => toggleRegion('__concepts__')}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: CONCEPT_COLOR, display: 'inline-block' }} />
                Concepts <span style={{ color: INK_DIM }}>{conceptCount}</span>
              </span>
            )}
            {regionFilter.size > 0 && (
              <span style={{ ...chipStyle(false, false), color: '#6da7ec', borderColor: '#2a3a5f' }} onClick={() => setRegionFilter(new Set())}>
                Show all
              </span>
            )}
          </div>

          {/* Selection / focus panel */}
          {selectedNode && (
            <div style={{
              position: 'absolute', top: 12, right: 12, background: PANEL_BG,
              border: `1px solid ${PANEL_BORDER}`, borderRadius: 10, padding: '14px 16px',
              backdropFilter: 'blur(8px)', fontSize: '0.8rem', width: 300, zIndex: 15, color: INK,
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 6 }}>
                <div style={{ fontWeight: 600, fontSize: '0.9rem', lineHeight: 1.3, paddingRight: 8 }}>{selectedNode.label}</div>
                <button onClick={() => setSelectedId(null)}
                  style={{ background: 'none', border: 'none', color: INK_DIM, cursor: 'pointer', fontSize: '1rem', padding: 0, lineHeight: 1 }}>×</button>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8, fontSize: '0.72rem', color: INK_DIM }}>
                <span style={{ width: 9, height: 9, borderRadius: '50%', background: nodeColorHex(selectedNode), display: 'inline-block' }} />
                {isConcept(selectedNode) ? 'Concept' : (selectedNode.department || 'Unassigned')}
                {selectedNode.abstraction_type && !isConcept(selectedNode) && <span>· {selectedNode.abstraction_type}</span>}
                {selectedNode.role_key && <span>· {selectedNode.role_key}</span>}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 6, marginBottom: 10, fontSize: '0.7rem', color: INK_DIM }}>
                <div><div style={{ color: INK, fontSize: '0.85rem' }}>{selectedNode.invocations}</div>fired</div>
                <div><div style={{ color: INK, fontSize: '0.85rem' }}>{selectedNode.avg_utility.toFixed(2)}</div>utility</div>
                <div><div style={{ color: INK, fontSize: '0.85rem' }}>{selectedNode.centrality.toFixed(2)}</div>centrality</div>
              </div>

              {sliderRow('Focus depth (hops)', String(focusDepth),
                <input type="range" min={1} max={3} step={1} value={focusDepth}
                  onChange={e => setFocusDepth(parseInt(e.target.value))} style={{ width: '100%', marginTop: 4 }} />)}

              {topConnections.length > 0 && (
                <div style={{ marginTop: 6 }}>
                  <div style={{ fontSize: '0.7rem', color: INK_DIM, marginBottom: 6 }}>Strongest synapses</div>
                  {topConnections.map(c => (
                    <div key={c.peer} onClick={() => setSelectedId(c.peer)}
                      style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 4px', borderRadius: 4, cursor: 'pointer', fontSize: '0.72rem' }}
                      onMouseEnter={e => (e.currentTarget.style.background = '#141d33')}
                      onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}>
                      <span style={{ width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: c.node ? nodeColorHex(c.node) : INK_DIM }} />
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{c.node!.label}</span>
                      <span style={{ color: INK_DIM, flexShrink: 0 }}>{c.weight.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              )}
              <button onClick={() => setSelectedId(null)}
                style={{ width: '100%', marginTop: 10, background: '#16203a', border: `1px solid ${PANEL_BORDER}`, borderRadius: 5, color: '#6da7ec', padding: '5px 0', fontSize: '0.72rem', cursor: 'pointer' }}>
                Exit focus (Esc)
              </button>
            </div>
          )}

          {/* Abstraction legend (only when that channel carries color) */}
          {colorBy === 'abstraction' && (
            <div style={{
              position: 'absolute', bottom: 44, left: 12, zIndex: 14,
              display: 'flex', gap: 12, fontSize: '0.68rem', color: INK_DIM,
              background: PANEL_BG, border: `1px solid ${PANEL_BORDER}`,
              borderRadius: 8, padding: '5px 10px',
            }}>
              {ABSTRACTION_ORDER.map(a => (
                <span key={a} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: ABSTRACTION_COLORS[a], display: 'inline-block' }} />
                  {a}
                </span>
              ))}
            </div>
          )}

          {/* Hint */}
          <div style={{ position: 'absolute', bottom: 44, right: 12, fontSize: '0.65rem', textAlign: 'right', zIndex: 14, color: '#4a5470' }}>
            drag: rotate · scroll: zoom · click neuron: focus · Esc: exit
          </div>
        </>
      )}

      {isSearchOpen && (
        <UniverseSearchOverlay
          neurons={neurons}
          onClose={() => setIsSearchOpen(false)}
          onSelect={(nodeId) => { setSelectedId(nodeId); setIsSearchOpen(false); }}
        />
      )}
    </div>
  );
}
