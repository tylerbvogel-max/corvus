import type React from 'react';
import { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { fetchGraph3D, fetchNeuron, type Graph3DNode } from '../api';
// d3-force-3d ships no TypeScript types.
// @ts-ignore
import { forceSimulation, forceLink, forceManyBody, forceX, forceY, forceZ } from 'd3-force-3d';

/**
 * Neuron Universe — a 3D connectome view. Self-contained three.js render
 * (instanced glowing nodes + additive synapse lines + UnrealBloom for the
 * "brain scan" look) driven by a d3-force-3d simulation we own directly.
 *
 * Focus behaviour: click a neuron and it becomes the centre of its own
 * universe — pinned at the origin with the layout rebuilt over ONLY its
 * connected neighbours; everything unconnected fades out.
 */

type Edge = { source: number; target: number; weight: number; edge_type: string };
type SimNode = Graph3DNode & {
  x: number; y: number; z: number;
  fx?: number | null; fy?: number | null; fz?: number | null;
  _i: number;
};

const BG = 0x080a0e;
// Repulsion slider works in hundreds of charge units: displayed 3-15,
// applied as value * REPULSION_SCALE (300-1500 charge, default 1000).
const REPULSION_SCALE = 100;
const REPULSION_DEFAULT = 10;
const REPULSION_MIN = 3;
const REPULSION_MAX = 15;
const REGION_COLORS = [
  '#5b8ff9', '#61ddaa', '#f6bd16', '#e8684a', '#9270ca', '#78d3f8',
  '#f08bb4', '#ff9d4d', '#7dc9a1', '#c77dff', '#4dd0e1', '#ffd166',
];
const OVERFLOW_COLOR = '#6b7280';
const CONCEPT_COLOR = '#c77dff';
const ABSTRACTION_COLORS: Record<string, string> = {
  structural: '#94a3b8', concept: '#c77dff', principle: '#f6bd16',
  process: '#61ddaa', procedure: '#5b8ff9', artifact: '#e8684a',
};

export default function NeuronUniverse() {
  const mountRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<any>(null);

  const [neurons, setNeurons] = useState<Graph3DNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Graph3DNode | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<any>(null);
  useEffect(() => {
    setSelectedDetail(null);
    if (selected) fetchNeuron(selected.id).then(setSelectedDetail).catch(() => {});
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [hover, setHover] = useState<{ n: Graph3DNode; sx: number; sy: number } | null>(null);
  const [colorBy, setColorBy] = useState<'region' | 'abstraction'>('region');
  const [sizeBy, setSizeBy] = useState<'centrality' | 'invocations'>('centrality');
  const [bloom, setBloom] = useState(true);
  const [synapse, setSynapse] = useState(0.5); // 0..1 synapse visibility (density)
  const [nodeLight, setNodeLight] = useState(1.25); // neuron brightness
  const [synapseLight, setSynapseLight] = useState(0.5); // synapse brightness
  // Repulsion is expressed in hundreds of charge units: the slider carries 3-15
  // and the force gets value * REPULSION_SCALE (300-1500, default 1000). The old
  // 8-300 range was far too weak to separate a graph this dense — nodes piled
  // into an unreadable ball once real edges existed.
  const [repulsion, setRepulsion] = useState(REPULSION_DEFAULT);
  // Master switch for all ambient motion: camera drift, skill heartbeats,
  // the Assistant wanderer, shell rotation, synapse firing, and the sim's
  // background simmer. Off = a fully still universe.
  const [motion, setMotion] = useState(true);
  const [panelOpen, setPanelOpen] = useState(true);

  // ── Data load (hardened: coerce NaN-prone numeric fields) ──
  useEffect(() => {
    (async () => {
      try {
        setLoading(true); setError(null);
        const d = await fetchGraph3D(0.25, 12000, 3);
        const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
        setNeurons(d.neurons.map(n => ({
          ...n, centrality: num(n.centrality), invocations: num(n.invocations),
          avg_utility: num(n.avg_utility), abstraction_type: n.abstraction_type ?? null,
        })));
        setEdges(d.edges.map(e => ({
          source: e.source, target: e.target, weight: num(e.weight),
          edge_type: e.edge_type ?? 'pyramidal',
        })));
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load graph');
      } finally { setLoading(false); }
    })();
  }, []);

  const isConcept = (n: Graph3DNode) => n.node_type === 'concept' && n.layer === -1;

  // ── Region palette (stable slot order by neuron count) ──
  const { regionColor, regionCounts } = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of neurons) {
      if (isConcept(n)) continue;
      const r = n.department || 'Unassigned';
      counts.set(r, (counts.get(r) || 0) + 1);
    }
    const order = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([r]) => r);
    const color = new Map<string, string>();
    order.forEach((r, i) => color.set(r, i < REGION_COLORS.length ? REGION_COLORS[i] : OVERFLOW_COLOR));
    return { regionColor: color, regionCounts: counts };
  }, [neurons]);

  const nodeHex = useMemo(() => (n: Graph3DNode): string => {
    if (isConcept(n)) return CONCEPT_COLOR;
    if (colorBy === 'abstraction') return ABSTRACTION_COLORS[n.abstraction_type || 'structural'] || OVERFLOW_COLOR;
    return regionColor.get(n.department || 'Unassigned') || OVERFLOW_COLOR;
  }, [colorBy, regionColor]);

  // Refs let the once-built engine read the CURRENT mode at call time —
  // the dropdowns were dead because the closures captured initial values.
  const nodeHexRef = useRef(nodeHex);
  nodeHexRef.current = nodeHex;
  const sizeByRef = useRef(sizeBy);
  sizeByRef.current = sizeBy;

  // ── The engine: built once per dataset ──
  useEffect(() => {
    const mount = mountRef.current;
    if (loading || error || !neurons.length || !mount) return;

    const width = mount.clientWidth || 1200;
    const height = mount.clientHeight || 800;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG);
    scene.fog = new THREE.FogExp2(BG, 0.00055);

    const camera = new THREE.PerspectiveCamera(58, width / height, 1, 8000);
    camera.position.set(0, 0, 1100);

    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.6;
    // Gentle universe drift at a constant speed. Any user input stops it;
    // drift resumes after 15s of stillness.
    const DRIFT_BASE = 0.35;
    controls.autoRotate = true;
    controls.autoRotateSpeed = DRIFT_BASE;
    let idleTimer: ReturnType<typeof setTimeout> | null = null;
    controls.addEventListener('start', () => {
      controls.autoRotate = false;
      if (idleTimer) clearTimeout(idleTimer);
    });
    controls.addEventListener('end', () => {
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(() => {
        if (motionOn) controls.autoRotate = true;
      }, 15000);
    });

    // HalfFloat target so node colours can exceed 1.0 (HDR) and bloom strongly —
    // that's what lets the Neuron-light slider actually blaze past the synapses.
    const composer = new EffectComposer(
      renderer,
      new THREE.WebGLRenderTarget(width, height, { type: THREE.HalfFloatType }),
    );
    composer.addPass(new RenderPass(scene, camera));
    const bloomPass = new UnrealBloomPass(new THREE.Vector2(width, height), 0.9, 0.6, 0.05);
    composer.addPass(bloomPass);
    composer.addPass(new OutputPass());

    // Nodes: instanced spheres, per-instance colour, MeshBasic so bloom glows.
    const N = neurons.length;
    const nodes: SimNode[] = neurons.map((n, i) => ({
      ...(n as Graph3DNode),
      x: (Math.random() - 0.5) * 600, y: (Math.random() - 0.5) * 600, z: (Math.random() - 0.5) * 600,
      _i: i,
    })) as SimNode[];
    const byId = new Map<number, SimNode>();
    nodes.forEach(n => byId.set(n.id, n));

    // Keep only edges whose BOTH endpoints are active nodes (the endpoint can
    // return edges to inactive neurons; d3 forceLink throws "node not found").
    const E = edges.filter(e => byId.has(e.source) && byId.has(e.target));

    const adjacency = new Map<number, Set<number>>();
    nodes.forEach(n => adjacency.set(n.id, new Set()));
    for (const e of E) {
      adjacency.get(e.source)?.add(e.target);
      adjacency.get(e.target)?.add(e.source);
    }

    const sphere = new THREE.SphereGeometry(1, 12, 12);
    // NB: no `vertexColors` — the sphere has no colour attribute, and with it on
    // a real GPU multiplies instanceColor by (0,0,0) -> black nodes. InstancedMesh
    // applies instanceColor on its own (USE_INSTANCING_COLOR); vertexColors is only
    // for geometry vertex colours (which the synapse LineSegments do have).
    const nodeMat = new THREE.MeshBasicMaterial({ transparent: true });
    const mesh = new THREE.InstancedMesh(sphere, nodeMat, N);
    mesh.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(N * 3), 3);
    scene.add(mesh);

    // Dark matter: compiled skills shape the strata without being part of
    // the luminous medium (no embedding, no recall). Render them visually
    // apart — larger, violet, wrapped in a slowly turning wireframe shell.
    const isSkill = (n: Graph3DNode) => n.node_type === 'skill';
    const skillIdx: number[] = [];
    for (let i = 0; i < N; i++) if (isSkill(nodes[i])) skillIdx.push(i);
    const SKILL_COLOR = '#9085e9';
    const shellGeo = new THREE.IcosahedronGeometry(1.75, 0);
    const shellMat = new THREE.MeshBasicMaterial({
      color: SKILL_COLOR, wireframe: true, transparent: true, opacity: 0.55,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    const shells = new THREE.InstancedMesh(shellGeo, shellMat, Math.max(skillIdx.length, 1));
    shells.count = skillIdx.length;
    shells.raycast = () => {}; // decoration only — picking stays on the spheres
    scene.add(shells);
    // Heartbeat: each skill beats on its own randomized rhythm. A beat flares
    // the wireframe shell and (via the skillBeat sim force) briefly shoves
    // nearby neurons outward — dark matter announcing itself.
    const skillBeat = skillIdx.map(() => ({ next: 2 + Math.random() * 8, last: -10 }));
    function beatPulse(k: number, t: number) {
      return Math.exp(-2.5 * Math.max(t - skillBeat[k].last, 0));
    }
    function updateBeats(t: number) {
      for (const b of skillBeat) {
        if (t >= b.next) { b.last = t; b.next = t + 3 + Math.random() * 9; }
      }
    }
    const shellDummy = new THREE.Object3D();
    function syncShells(t: number) {
      for (let k = 0; k < skillIdx.length; k++) {
        const i = skillIdx[k];
        if (hidden[i]) { shellDummy.scale.setScalar(0.0001); }
        else {
          shellDummy.position.set(nodes[i].x, nodes[i].y, nodes[i].z);
          shellDummy.rotation.set(0, t * 0.25, t * 0.1);
          // scaleFor already includes the node radius; the shell geometry's
          // 1.75 base radius provides the halo margin around the sphere.
          shellDummy.scale.setScalar(scaleFor(i) * (1 + 0.6 * beatPulse(k, t)));
        }
        shellDummy.updateMatrix();
        shells.setMatrixAt(k, shellDummy.matrix);
      }
      shells.instanceMatrix.needsUpdate = true;
    }

    // The Assistant: one celestial body, the mind this graph remembers for.
    // Largest node in the universe, gold against the blue-violet medium,
    // ringed like a planet — and never force-anchored: it wanders the volume
    // on an incommensurate Lissajous path, always traversing, never parked.
    const ASSISTANT_COLOR = '#f6bd16';
    const assistantIdx = nodes.findIndex(n => n.node_type === 'assistant');
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(1.7, 24, 24),
      new THREE.MeshBasicMaterial({ color: ASSISTANT_COLOR, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false }),
    );
    // The memory trail: everywhere the wanderer has been, fading like
    // recency decay — the traversal remembers itself. (Additive blending:
    // colors ramp to black = fade to invisible.)
    const TRAIL_N = 380;
    const TRAIL_MIN_STEP = 1.5;
    const trailPts: number[] = [];
    const trailGeo = new THREE.BufferGeometry();
    trailGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(TRAIL_N * 3), 3));
    trailGeo.setAttribute('color', new THREE.BufferAttribute(new Float32Array(TRAIL_N * 3), 3));
    trailGeo.setDrawRange(0, 0);
    const trail = new THREE.Line(trailGeo, new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
    }));
    trail.frustumCulled = false;
    trail.raycast = () => {};
    // The wake is a PLUME, not a line: three additive stardust layers on the
    // same buffers — glitter core, soft glow, faint outer veil — so the trail
    // has volume and bloom without flattening the rest of the universe.
    const dustLayers = [
      { size: 3.4, opacity: 1.0 },
      { size: 11, opacity: 0.38 },
      { size: 24, opacity: 0.13 },
    ].map(cfg => {
      const pts = new THREE.Points(trailGeo, new THREE.PointsMaterial({
        size: cfg.size, opacity: cfg.opacity, vertexColors: true, transparent: true,
        blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
      }));
      pts.frustumCulled = false;
      pts.raycast = () => {};
      scene.add(pts);
      return pts;
    });
    halo.visible = trail.visible = assistantIdx >= 0;
    dustLayers.forEach(d => { d.visible = assistantIdx >= 0; });
    halo.raycast = () => {};
    scene.add(halo); scene.add(trail);
    const GOLD = new THREE.Color(ASSISTANT_COLOR);
    function syncAssistant(t: number) {
      if (assistantIdx < 0) return;
      const n = nodes[assistantIdx];
      const R = 520;
      const px = R * Math.sin(0.109 * t);
      const py = R * 0.55 * Math.sin(0.068 * t + 2.1);
      const pz = R * Math.cos(0.088 * t + 4.2);
      n.x = n.fx = px; n.y = n.fy = py; n.z = n.fz = pz;
      const s0 = scaleFor(assistantIdx);
      dummy.position.set(px, py, pz); dummy.scale.setScalar(s0); dummy.updateMatrix();
      mesh.setMatrixAt(assistantIdx, dummy.matrix);
      mesh.instanceMatrix.needsUpdate = true;
      halo.position.set(px, py, pz); halo.scale.setScalar(s0);
      // extend the memory trail when we've actually travelled
      const L = trailPts.length;
      const moved = L < 3 ||
        (px - trailPts[L - 3]) ** 2 + (py - trailPts[L - 2]) ** 2 + (pz - trailPts[L - 1]) ** 2 > TRAIL_MIN_STEP ** 2;
      if (moved) {
        trailPts.push(px, py, pz);
        while (trailPts.length > TRAIL_N * 3) trailPts.splice(0, 3);
        const count = trailPts.length / 3;
        const posAttr = trailGeo.attributes.position as THREE.BufferAttribute;
        const colAttr = trailGeo.attributes.color as THREE.BufferAttribute;
        for (let i = 0; i < count; i++) {
          posAttr.setXYZ(i, trailPts[i * 3], trailPts[i * 3 + 1], trailPts[i * 3 + 2]);
          const fade = Math.pow(i / Math.max(count - 1, 1), 2.2) * 2.4; // HDR head, wispy tail
          colAttr.setXYZ(i, GOLD.r * fade, GOLD.g * fade, GOLD.b * fade);
        }
        posAttr.needsUpdate = true; colAttr.needsUpdate = true;
        trailGeo.setDrawRange(0, count);
      }
    }

    const dummy = new THREE.Object3D();
    const baseColor = new Float32Array(N * 3); // per-node base colour (for dim/highlight)
    const hidden = new Uint8Array(N);
    const radius = new Float32Array(N);
    let nodeLight = 1.25; // neuron brightness multiplier (slider-controlled)
    // Charge magnitude actually applied to the force (slider value * scale).
    let repulsionVal = REPULSION_DEFAULT * REPULSION_SCALE;
    // Ambient-motion master switch + the animation clock it gates. Everything
    // time-driven (drift, beats, wanderer, shells, firing) reads animT, which
    // only advances while motion is on — pausing freezes the whole universe.
    let motionOn = true;
    let animT = 0;

    function recomputeRadius() {
      for (let i = 0; i < N; i++) {
        const n = nodes[i];
        const raw = sizeByRef.current === 'centrality' ? 2.4 + Math.sqrt(n.centrality) * 9
          : 2.4 + Math.sqrt(Math.min(n.invocations, 100) / 100) * 9;
        // The assistant doesn't scale off graph metrics — it IS the scale.
        radius[i] = n.node_type === 'assistant' ? 8 : isSkill(n) ? raw * 1.9 : isConcept(n) ? raw * 1.25 : raw;
      }
    }
    function recomputeColors() {
      const c = new THREE.Color();
      for (let i = 0; i < N; i++) {
        c.set(nodes[i].node_type === 'assistant' ? ASSISTANT_COLOR : isSkill(nodes[i]) ? SKILL_COLOR : nodeHexRef.current(nodes[i]));
        baseColor[i * 3] = c.r; baseColor[i * 3 + 1] = c.g; baseColor[i * 3 + 2] = c.b;
      }
    }
    recomputeRadius();
    recomputeColors();

    // Synapses: additive line segments; positions synced each tick.
    // Each edge is a quadratic bézier — bowed slightly perpendicular to the
    // chord along a stable per-edge direction — sampled into CURVE_SEGS
    // straight segments (smooth enough under bloom, cheap enough per tick).
    const CURVE_SEGS = 4;
    const CURVE_BOW = 0.14; // midpoint lift as a fraction of edge length
    const M = E.length;
    const linePos = new Float32Array(M * CURVE_SEGS * 2 * 3);
    const lineCol = new Float32Array(M * CURVE_SEGS * 2 * 3);
    const lineGeo = new THREE.BufferGeometry();
    lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
    lineGeo.setAttribute('color', new THREE.BufferAttribute(lineCol, 3));
    const lineMat = new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: 0.5,
      blending: THREE.AdditiveBlending, depthWrite: false,
    });
    const lines = new THREE.LineSegments(lineGeo, lineMat);
    scene.add(lines);
    const edgeColors = E.map(e =>
      e.edge_type === 'instantiates' ? new THREE.Color(CONCEPT_COLOR)
        : e.edge_type === 'evidence-link' ? new THREE.Color('#9085e9') // lesson → skill provenance
          : e.edge_type === 'pyramidal' ? new THREE.Color('#9fb8ff')
            : new THREE.Color('#3f5a8a'));
    // Colours are constant per edge — fill the vertex buffer once, not per tick.
    for (let e = 0; e < M; e++) {
      const c = edgeColors[e];
      for (let v = 0; v < CURVE_SEGS * 2; v++) {
        const o = (e * CURVE_SEGS * 2 + v) * 3;
        lineCol[o] = c.r; lineCol[o + 1] = c.g; lineCol[o + 2] = c.b;
      }
    }
    // Stable per-edge bow direction (random unit vector; its component
    // perpendicular to the live chord decides which way the curve bends).
    const bowVec = new Float32Array(M * 3);
    for (let e = 0; e < M; e++) {
      const v = new THREE.Vector3(Math.random() - 0.5, Math.random() - 0.5, Math.random() - 0.5).normalize();
      bowVec[e * 3] = v.x; bowVec[e * 3 + 1] = v.y; bowVec[e * 3 + 2] = v.z;
    }
    const _dir = new THREE.Vector3(), _bow = new THREE.Vector3(), _ctrl = new THREE.Vector3();
    // Quadratic-bézier control point for edge e between live endpoints s→t.
    function edgeControl(e: number, s: SimNode, t: SimNode, out: THREE.Vector3) {
      _dir.set(t.x - s.x, t.y - s.y, t.z - s.z);
      const len2 = _dir.lengthSq() || 1;
      _bow.set(bowVec[e * 3], bowVec[e * 3 + 1], bowVec[e * 3 + 2]);
      _bow.addScaledVector(_dir, -_bow.dot(_dir) / len2); // perpendicular component
      if (_bow.lengthSq() < 1e-6) _bow.set(-_dir.y, _dir.x, 0); // bow ∥ chord fallback
      if (_bow.lengthSq() < 1e-6) _bow.set(0, -_dir.z, _dir.y); // chord along z fallback
      _bow.normalize();
      out.set((s.x + t.x) / 2, (s.y + t.y) / 2, (s.z + t.z) / 2)
        .addScaledVector(_bow, Math.sqrt(len2) * CURVE_BOW);
    }

    let focusId: number | null = null;
    let visibleEdge = new Uint8Array(M).fill(1);

    // Synapse firing: very sparse — every so often a point of light traverses
    // one of the existing (visible) lines from source to target, then dies.
    const FIRE_MAX = 32;
    const firePos = new Float32Array(FIRE_MAX * 3);
    const fireCol = new Float32Array(FIRE_MAX * 3);
    const fireGeo = new THREE.BufferGeometry();
    fireGeo.setAttribute('position', new THREE.BufferAttribute(firePos, 3));
    fireGeo.setAttribute('color', new THREE.BufferAttribute(fireCol, 3));
    fireGeo.setDrawRange(0, 0);
    const firePts = new THREE.Points(fireGeo, new THREE.PointsMaterial({
      size: 6, vertexColors: true, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    }));
    firePts.frustumCulled = false;
    firePts.raycast = () => {};
    scene.add(firePts);
    const fires: { e: number; t0: number; dur: number }[] = [];
    let nextFireAt = 0;
    function spawnFire(t: number) {
      // A few random draws to land on a currently-visible edge; give up quietly
      // if the view is filtered down to almost nothing.
      for (let tries = 0; tries < 8; tries++) {
        const e = (Math.random() * M) | 0;
        const s = byId.get(E[e].source), tt = byId.get(E[e].target);
        if (!s || !tt || !visibleEdge[e] || hidden[s._i] || hidden[tt._i]) continue;
        fires.push({ e, t0: t, dur: 0.7 + Math.random() * 0.9 });
        return;
      }
    }
    function syncFires(t: number) {
      if (M > 0 && t >= nextFireAt && fires.length < FIRE_MAX) {
        spawnFire(t);
        nextFireAt = t + 0.13 + Math.random() * 0.47; // ~3 ignitions/sec
      }
      let w = 0;
      for (const fr of fires) {
        const p = (t - fr.t0) / fr.dur;
        const s = byId.get(E[fr.e].source), tt = byId.get(E[fr.e].target);
        if (p >= 1 || !s || !tt || !visibleEdge[fr.e] || hidden[s._i] || hidden[tt._i]) continue;
        fires[w] = fr;
        const o = w * 3;
        // ride the same bézier the synapse line is drawn with
        edgeControl(fr.e, s, tt, _ctrl);
        const a = (1 - p) * (1 - p), b = 2 * (1 - p) * p, cc = p * p;
        firePos[o] = a * s.x + b * _ctrl.x + cc * tt.x;
        firePos[o + 1] = a * s.y + b * _ctrl.y + cc * tt.y;
        firePos[o + 2] = a * s.z + b * _ctrl.z + cc * tt.z;
        // White-hot core over the edge colour; sin envelope = soft ignite/decay.
        const glow = Math.sin(Math.PI * p) * 2.6;
        const c = edgeColors[fr.e];
        fireCol[o] = (c.r + 0.7) * glow;
        fireCol[o + 1] = (c.g + 0.7) * glow;
        fireCol[o + 2] = (c.b + 0.7) * glow;
        w++;
      }
      fires.length = w;
      fireGeo.setDrawRange(0, w);
      (fireGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (fireGeo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
    }

    function refreshInstances() {
      const highlightId = focusId;
      for (let i = 0; i < N; i++) {
        if (hidden[i]) { dummy.scale.setScalar(0.0001); }
        else { dummy.position.set(nodes[i].x, nodes[i].y, nodes[i].z); dummy.scale.setScalar(scaleFor(i)); }
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        let r = baseColor[i * 3] * nodeLight, g = baseColor[i * 3 + 1] * nodeLight, b = baseColor[i * 3 + 2] * nodeLight;
        if (highlightId != null && nodes[i].id === highlightId) { r += 0.8; g += 0.85; b += 1.0; }
        // No clamp: values > 1 are HDR and bloom hard, so higher Neuron light truly brightens.
        mesh.setColorAt(i, tmpColor.setRGB(r, g, b));
      }
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    const tmpColor = new THREE.Color();

    function scaleFor(i: number) { return nodes[i].id === focusId ? radius[i] * 2.1 : radius[i]; }

    function syncPositions() {
      for (let i = 0; i < N; i++) {
        if (hidden[i]) { dummy.scale.setScalar(0.0001); }
        else { dummy.position.set(nodes[i].x, nodes[i].y, nodes[i].z); dummy.scale.setScalar(scaleFor(i)); }
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
      }
      mesh.instanceMatrix.needsUpdate = true;
      // synapse curves: sample the bézier into CURVE_SEGS chained segments
      for (let e = 0; e < M; e++) {
        const s = byId.get(E[e].source), t = byId.get(E[e].target);
        const base = e * CURVE_SEGS * 6;
        if (!s || !t || !visibleEdge[e] || hidden[s._i] || hidden[t._i]) {
          linePos.fill(0, base, base + CURVE_SEGS * 6);
        } else {
          edgeControl(e, s, t, _ctrl);
          let px = s.x, py = s.y, pz = s.z;
          for (let k = 1; k <= CURVE_SEGS; k++) {
            const p = k / CURVE_SEGS;
            const a = (1 - p) * (1 - p), b = 2 * (1 - p) * p, cc = p * p;
            const qx = a * s.x + b * _ctrl.x + cc * t.x;
            const qy = a * s.y + b * _ctrl.y + cc * t.y;
            const qz = a * s.z + b * _ctrl.z + cc * t.z;
            const o = base + (k - 1) * 6;
            linePos[o] = px; linePos[o + 1] = py; linePos[o + 2] = pz;
            linePos[o + 3] = qx; linePos[o + 4] = qy; linePos[o + 5] = qz;
            px = qx; py = qy; pz = qz;
          }
        }
      }
      lineGeo.attributes.position.needsUpdate = true;
    }

    // ── d3-force-3d simulation (owned directly) ──
    let sim: any = null;
    function buildSim(active: SimNode[], activeLinks: Edge[], pinnedId: number | null) {
      if (sim) sim.stop();
      const links = activeLinks.map(l => ({ source: l.source, target: l.target, weight: l.weight }));
      sim = forceSimulation(active, 3)
        .force('link', forceLink(links).id((d: SimNode) => d.id)
          .distance((l: any) => 26 + (1 - l.weight) * 70).strength((l: any) => 0.15 + l.weight * 0.5))
        .force('charge', forceManyBody().strength(pinnedId != null ? -(repulsionVal * 3) : -repulsionVal).distanceMax(1400))
        .force('x', forceX(0).strength(0.015))
        .force('y', forceY(0).strength(0.015))
        .force('z', forceZ(0).strength(0.015))
        .velocityDecay(0.34)
        .alphaDecay(0.02)
        // Never fully sleeps while motion is on (the wanderer's pull is
        // continuous); with motion off it settles and goes still.
        .alphaTarget(motionOn ? 0.025 : 0)
        .alpha(0.9);
      // The assistant exerts gravity as it traverses: nearby free nodes are
      // drawn toward it with inverse-square falloff (softened + capped), so
      // the medium visibly bends around the wanderer's path.
      if (assistantIdx >= 0) {
        sim.force('assistantGravity', (alpha: number) => {
          const a = nodes[assistantIdx];
          for (const n of active) {
            if (n === a || n.fx != null) continue;
            const dx = a.x - n.x, dy = a.y - n.y, dz = a.z - n.z;
            const d2 = dx * dx + dy * dy + dz * dz + 2500; // softening core
            const f = Math.min(3500 / d2, 0.12) * alpha; // a tide, not a singularity
            (n as any).vx += dx * f; (n as any).vy += dy * f; (n as any).vz += dz * f;
          }
        });
      }
      // Skill heartbeat: a beating skill briefly boosts repulsion around
      // itself — inverse-square shove (softened + capped) scaled by the
      // beat envelope and the current repulsion setting.
      if (skillIdx.length) {
        sim.force('skillBeat', (alpha: number) => {
          if (!motionOn) return;
          for (let k = 0; k < skillIdx.length; k++) {
            const p = beatPulse(k, animT);
            if (p < 0.04) continue;
            const s = nodes[skillIdx[k]];
            if (hidden[s._i]) continue;
            for (const n of active) {
              if (n === s || n.fx != null) continue;
              const dx = n.x - s.x, dy = n.y - s.y, dz = n.z - s.z;
              const d2 = dx * dx + dy * dy + dz * dz + 900; // softening core
              const f = Math.min((repulsionVal * 6 * p) / d2, 0.35) * alpha;
              (n as any).vx += dx * f; (n as any).vy += dy * f; (n as any).vz += dz * f;
            }
          }
        });
      }
      sim.stop(); // we tick manually in RAF
      // clear any prior pins, then pin the focus at the origin
      for (const n of nodes) { n.fx = null; n.fy = null; n.fz = null; }
      if (pinnedId != null) { const p = byId.get(pinnedId); if (p) { p.fx = 0; p.fy = 0; p.fz = 0; p.x = 0; p.y = 0; p.z = 0; } }
    }
    buildSim(nodes, E, null);

    // Raycasting (hover + click) against instanced nodes.
    const raycaster = new THREE.Raycaster();
    (raycaster.params as any).Points = { threshold: 2 };
    const pointer = new THREE.Vector2();

    function pick(clientX: number, clientY: number): number | null {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(mesh);
      for (const h of hits) {
        const inst = (h as any).instanceId;
        if (inst != null && !hidden[inst]) return inst;
      }
      return null;
    }
    function onMove(ev: PointerEvent) {
      const inst = pick(ev.clientX, ev.clientY);
      renderer.domElement.style.cursor = inst != null ? 'pointer' : 'grab';
      if (inst != null) setHover({ n: nodes[inst], sx: ev.clientX, sy: ev.clientY });
      else setHover(null);
    }
    function onClick(ev: PointerEvent) {
      const inst = pick(ev.clientX, ev.clientY);
      if (inst != null) setSelected({ ...(nodes[inst] as Graph3DNode) });
    }
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('click', onClick);

    let raf = 0;
    let lastFrame: number | null = null;
    function animate() {
      raf = requestAnimationFrame(animate);
      const now = performance.now() / 1000;
      const dt = lastFrame == null ? 0 : Math.min(now - lastFrame, 0.1);
      lastFrame = now;
      if (motionOn) {
        animT += dt;
        updateBeats(animT);
        syncFires(animT);
      }
      if (sim && sim.alpha() > 0.006) { sim.tick(); syncPositions(); }
      syncShells(animT);
      syncAssistant(animT);
      controls.update();
      composer.render();
    }
    syncPositions(); refreshInstances(); animate();

    function onResize() {
      const w = mount!.clientWidth || width, h = mount!.clientHeight || height;
      camera.aspect = w / h; camera.updateProjectionMatrix();
      renderer.setSize(w, h); composer.setSize(w, h);
    }
    window.addEventListener('resize', onResize);
    // The app's floating sub-windows resize without firing a window
    // resize event — observe the mount element so the canvas follows
    // the window chrome when the user drags its edges.
    const resizeObserver = new ResizeObserver(onResize);
    resizeObserver.observe(mount);

    // Imperative API for React control effects.
    engineRef.current = {
      api: {
        setColorMode() { recomputeColors(); refreshInstances(); },
        setSizeMode() { recomputeRadius(); refreshInstances(); },
        setBloom(on: boolean) { bloomPass.enabled = on; bloomPass.strength = on ? 0.9 : 0; },
        setMotion(on: boolean) {
          motionOn = on;
          controls.autoRotate = on;
          if (!on && idleTimer) { clearTimeout(idleTimer); idleTimer = null; }
          if (sim) {
            sim.alphaTarget(on ? 0.025 : 0);
            if (on) sim.alpha(Math.max(sim.alpha(), 0.3)); // wake a slept layout
          }
        },
        setSynapse(v: number) { for (let e = 0; e < M; e++) visibleEdge[e] = E[e].weight >= (1 - v) * 0.6 ? 1 : 0; syncPositions(); },
        setNodeLight(v: number) { nodeLight = v; refreshInstances(); },
        setSynapseLight(v: number) { lineMat.opacity = 0.02 + v * 0.88; },
        setRepulsion(v: number) {
          repulsionVal = v * REPULSION_SCALE;
          // Re-settle the CURRENT view (full or ego) with the new charge; keep camera.
          const active = focusId != null ? nodes.filter(n => !hidden[n._i]) : nodes;
          const activeLinks = focusId != null
            ? E.filter(e => !hidden[byId.get(e.source)!._i] && !hidden[byId.get(e.target)!._i])
            : E;
          buildSim(active, activeLinks, focusId);
        },
        focus(id: number) {
          focusId = id;
          // 2-hop ego neighbourhood so the "local universe" is rich, not a lone dot.
          const keep = new Set<number>([id]);
          let frontier = [id];
          for (let hop = 0; hop < 2; hop++) {
            const next: number[] = [];
            for (const cur of frontier) {
              for (const nb of (adjacency.get(cur) || [])) {
                if (!keep.has(nb)) { keep.add(nb); next.push(nb); }
              }
            }
            frontier = next;
          }
          for (let i = 0; i < N; i++) hidden[i] = keep.has(nodes[i].id) ? 0 : 1;
          const active = nodes.filter(n => keep.has(n.id));
          const activeLinks = E.filter(e => keep.has(e.source) && keep.has(e.target));
          buildSim(active, activeLinks, id);
          refreshInstances();
          controls.target.set(0, 0, 0);
          const dist = Math.max(180, 46 * Math.sqrt(active.length));
          camera.position.set(0, dist * 0.16, dist);
        },
        reset() {
          focusId = null;
          hidden.fill(0);
          buildSim(nodes, E, null);
          refreshInstances();
        },
      },
      dispose() {
        cancelAnimationFrame(raf);
        if (idleTimer) clearTimeout(idleTimer);
        window.removeEventListener('resize', onResize);
        resizeObserver.disconnect();
        renderer.domElement.removeEventListener('pointermove', onMove);
        renderer.domElement.removeEventListener('click', onClick);
        if (sim) sim.stop();
        controls.dispose();
        composer.dispose?.();
        renderer.dispose();
        sphere.dispose(); nodeMat.dispose(); lineGeo.dispose(); lineMat.dispose();
        fireGeo.dispose(); (firePts.material as THREE.Material).dispose();
        if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
        engineRef.current = null;
      },
    };

    return () => { engineRef.current?.dispose(); };
  }, [neurons, edges]);

  // ── Control effects → engine ──
  useEffect(() => { engineRef.current?.api.setColorMode(); }, [colorBy, nodeHex]);
  useEffect(() => { engineRef.current?.api.setSizeMode(); }, [sizeBy]);
  useEffect(() => { engineRef.current?.api.setBloom(bloom); }, [bloom]);
  useEffect(() => { engineRef.current?.api.setMotion(motion); }, [motion]);
  useEffect(() => { engineRef.current?.api.setSynapse(synapse); }, [synapse]);
  useEffect(() => { engineRef.current?.api.setNodeLight(nodeLight); }, [nodeLight]);
  useEffect(() => { engineRef.current?.api.setSynapseLight(synapseLight); }, [synapseLight]);
  useEffect(() => { engineRef.current?.api.setRepulsion(repulsion); }, [repulsion]);
  useEffect(() => {
    if (selected) engineRef.current?.api.focus(selected.id);
    else engineRef.current?.api.reset();
  }, [selected]);

  const synapseCount = edges.length;
  return (
    <div style={{ position: 'absolute', inset: 0, overflow: 'hidden', background: '#080a0e' }}>
      <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

      {loading && (
        <div style={overlayCenter}>
          <div style={{ color: '#9fb8ff' }}>Loading the connectome…</div>
        </div>
      )}
      {error && <div style={{ ...overlayCenter, color: '#e66767' }}>{error}</div>}

      {/* Control panel */}
      <div style={panel}>
        <div
          onClick={() => setPanelOpen(o => !o)}
          style={{ cursor: 'pointer', userSelect: 'none' }}
          title={panelOpen ? 'Collapse controls' : 'Expand controls'}
        >
          <div style={{ fontWeight: 700, color: '#e8edf7', fontSize: '0.95rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            Neuron Universe
            <span style={{ color: '#8a93a6', fontSize: '0.7rem' }}>{panelOpen ? '▾' : '▸'}</span>
          </div>
          <div style={{ color: '#8a93a6', fontSize: '0.72rem', marginBottom: panelOpen ? 10 : 0 }}>
            {neurons.length.toLocaleString()} neurons · {synapseCount.toLocaleString()} synapses
          </div>
        </div>
        {panelOpen && (<>
        <Row label="Color by">
          <select value={colorBy} onChange={e => setColorBy(e.target.value as any)} style={select}>
            <option value="region">Region</option>
            <option value="abstraction">Abstraction</option>
          </select>
        </Row>
        <Row label="Size by">
          <select value={sizeBy} onChange={e => setSizeBy(e.target.value as any)} style={select}>
            <option value="centrality">Centrality</option>
            <option value="invocations">Recalls</option>
          </select>
        </Row>
        <Row label={`Synapses ${(synapse * 100) | 0}%`}>
          <input type="range" min={0} max={1} step={0.02} value={synapse}
            onChange={e => setSynapse(+e.target.value)} style={{ width: 150 }} />
        </Row>
        <Row label={`Neuron light ${(nodeLight * 100) | 0}%`}>
          <input type="range" min={0.2} max={2.5} step={0.05} value={nodeLight}
            onChange={e => setNodeLight(+e.target.value)} style={{ width: 150 }} />
        </Row>
        <Row label={`Synapse light ${(synapseLight * 100) | 0}%`}>
          <input type="range" min={0} max={1} step={0.02} value={synapseLight}
            onChange={e => setSynapseLight(+e.target.value)} style={{ width: 150 }} />
        </Row>
        <Row label={`Repulsion ${repulsion} (${repulsion * REPULSION_SCALE})`}>
          <input type="range" min={REPULSION_MIN} max={REPULSION_MAX} step={0.5} value={repulsion}
            onChange={e => setRepulsion(+e.target.value)} style={{ width: 150 }} />
        </Row>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: '#c7d0e0', fontSize: '0.78rem', marginTop: 8, cursor: 'pointer' }}>
          <input type="checkbox" checked={bloom} onChange={e => setBloom(e.target.checked)} /> Bloom
        </label>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', color: '#c7d0e0', fontSize: '0.78rem', marginTop: 6, cursor: 'pointer' }}>
          <input type="checkbox" checked={motion} onChange={e => setMotion(e.target.checked)} /> Motion enabled
        </label>
        {selected && (
          <button onClick={() => setSelected(null)} style={backBtn}>← Back to full universe</button>
        )}
        </>)}
      </div>

      {/* Focused-neuron info */}
      {selected && (
        <div style={focusCard}>
          <div style={{ color: '#e8edf7', fontWeight: 600 }}>{selected.label || `Neuron #${selected.id}`}</div>
          <div style={{ color: '#8a93a6', fontSize: '0.74rem', marginTop: 3 }}>
            {selected.department || 'Unassigned'}
            {selected.abstraction_type && !isConcept(selected) && <> · {selected.abstraction_type}</>}
          </div>
          <div style={{ color: '#6f7a8f', fontSize: '0.72rem', marginTop: 6 }}>
            centrality {selected.centrality.toFixed(2)} · fired {selected.invocations}×
            · {(neurons.length ? '' : '')}now the centre of its universe
          </div>
          {selectedDetail && (selectedDetail.content || selectedDetail.summary) && (
            <div style={{
              color: '#aeb7c8', fontSize: '0.74rem', marginTop: 8, lineHeight: 1.45,
              maxHeight: 180, overflowY: 'auto', whiteSpace: 'pre-wrap',
              borderTop: '1px solid #2a3446', paddingTop: 8,
            }}>
              {selectedDetail.content || selectedDetail.summary}
            </div>
          )}
        </div>
      )}

      {/* Hover tooltip */}
      {hover && (
        <div style={{
          position: 'fixed', left: hover.sx + 14, top: hover.sy + 14, zIndex: 30, pointerEvents: 'none',
          background: 'rgba(14,18,26,0.92)', border: '1px solid #2a3446', borderRadius: 8,
          padding: '6px 10px', color: '#e8edf7', fontSize: '0.76rem', maxWidth: 280,
        }}>
          <div style={{ fontWeight: 600 }}>{hover.n.label || `Neuron #${hover.n.id}`}</div>
          <div style={{ color: '#8a93a6', fontSize: '0.72rem' }}>{hover.n.department || 'Unassigned'}</div>
        </div>
      )}

      {/* Region legend */}
      {colorBy === 'region' && (
        <div style={legend}>
          {Array.from(regionCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([r, c]) => (
            <span key={r} style={legendChip}>
              <span style={{ width: 9, height: 9, borderRadius: '50%', background: regionColor.get(r) || OVERFLOW_COLOR, display: 'inline-block' }} />
              {r} <span style={{ color: '#6f7a8f' }}>{c}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '6px 0', gap: 10 }}>
      <span style={{ color: '#9aa4b6', fontSize: '0.74rem' }}>{label}</span>
      {children}
    </div>
  );
}

const overlayCenter: React.CSSProperties = { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 };
const panel: React.CSSProperties = {
  position: 'absolute', top: 16, left: 16, zIndex: 20, width: 210,
  background: 'rgba(14,18,26,0.82)', border: '1px solid #232c3c', borderRadius: 12,
  padding: '14px 16px', backdropFilter: 'blur(8px)',
};
const select: React.CSSProperties = {
  background: '#141a24', color: '#e8edf7', border: '1px solid #2a3446',
  borderRadius: 6, padding: '3px 8px', fontSize: '0.76rem',
};
const backBtn: React.CSSProperties = {
  marginTop: 12, width: '100%', background: '#1e3a5f', color: '#cfe0ff',
  border: '1px solid #2f5a8f', borderRadius: 6, padding: '7px 0', cursor: 'pointer', fontSize: '0.78rem',
};
const focusCard: React.CSSProperties = {
  position: 'absolute', top: 16, right: 16, zIndex: 20, maxWidth: 300,
  background: 'rgba(14,18,26,0.86)', border: '1px solid #2f5a8f', borderRadius: 12, padding: '14px 16px',
  backdropFilter: 'blur(8px)',
};
const legend: React.CSSProperties = {
  position: 'absolute', bottom: 14, left: 16, right: 16, zIndex: 20,
  display: 'flex', flexWrap: 'wrap', gap: 8,
};
const legendChip: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 6, background: 'rgba(14,18,26,0.82)',
  border: '1px solid #232c3c', borderRadius: 20, padding: '3px 10px', color: '#c7d0e0', fontSize: '0.72rem',
};
