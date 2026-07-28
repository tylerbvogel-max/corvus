import type React from 'react';
import { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import {
  fetchGraph3D,
  fetchNeuron,
  fetchSemanticClusters,
  type Graph3DNode,
  type Graph3DReplayTrace,
  type SemanticCluster,
} from '../api';
import NeuronRadarControls from './NeuronRadarControls';
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

type ActivityWindow = '1d' | '7d' | '30d' | 'all';
type LensMode = 'recall' | 'heat' | 'health';
type Edge = {
  source: number;
  target: number;
  weight: number;
  edge_type: string;
  co_fire_count: number;
  activity_1d: number;
  activity_7d: number;
  activity_30d: number;
  activity_all: number;
};
type LayoutMode = 'organic' | 'zones';
type SimNode = Graph3DNode & {
  x: number; y: number; z: number;
  fx?: number | null; fy?: number | null; fz?: number | null;
  _i: number;
};
type ZoneDefinition = {
  id: number;
  label: string;
  neuronIds: number[];
  departments: string[];
  representativeLabels: string[];
  color: string;
  x: number;
  y: number;
  z: number;
  radius: number;
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
const ZONE_COLORS = [
  '#53d8fb', '#f58ad8', '#a6ed8e', '#ffd166', '#aa91ff', '#ff8f70',
  '#65b5ff', '#45dfb1', '#ed77ac', '#d9ef6f', '#8ed7d2', '#f3a95f',
  '#dc8cff', '#7cf2f0', '#ffb86b', '#8bd17c',
];
const OVERFLOW_COLOR = '#6b7280';
const CONCEPT_COLOR = '#c77dff';

function buildZones(
  neurons: Graph3DNode[],
  edges: Edge[],
  clusters: SemanticCluster[],
): ZoneDefinition[] {
  const activeIds = new Set(neurons.map(neuron => neuron.id));
  const definitions = clusters
    .map(cluster => ({
      cluster,
      neuronIds: cluster.neuron_ids.filter(id => activeIds.has(id)),
    }))
    .filter(item => item.neuronIds.length >= 3)
    .sort((a, b) => (
      b.neuronIds.length - a.neuronIds.length ||
      a.cluster.cluster_id - b.cluster.cluster_id
    ));

  // Leiden covers connected communities. If filtering leaves a node outside a
  // returned community, associate it with the zone receiving its strongest
  // cumulative synaptic weight. Truly disconnected neurons remain peripheral.
  const zoneIndexByNeuron = new Map<number, number>();
  definitions.forEach((definition, index) => {
    definition.neuronIds.forEach(id => zoneIndexByNeuron.set(id, index));
  });
  const affinity = new Map<number, Map<number, number>>();
  for (const edge of edges) {
    const sourceZone = zoneIndexByNeuron.get(edge.source);
    const targetZone = zoneIndexByNeuron.get(edge.target);
    if (sourceZone != null && targetZone == null && activeIds.has(edge.target)) {
      const scores = affinity.get(edge.target) || new Map<number, number>();
      scores.set(sourceZone, (scores.get(sourceZone) || 0) + edge.weight);
      affinity.set(edge.target, scores);
    }
    if (targetZone != null && sourceZone == null && activeIds.has(edge.source)) {
      const scores = affinity.get(edge.source) || new Map<number, number>();
      scores.set(targetZone, (scores.get(targetZone) || 0) + edge.weight);
      affinity.set(edge.source, scores);
    }
  }
  affinity.forEach((scores, neuronId) => {
    const winner = Array.from(scores).sort((a, b) => b[1] - a[1] || a[0] - b[0])[0];
    if (winner) definitions[winner[0]].neuronIds.push(neuronId);
  });

  const count = Math.max(1, definitions.length);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  return definitions.map(({ cluster, neuronIds }, index) => {
    const yUnit = 1 - 2 * ((index + 0.5) / count);
    const radial = Math.sqrt(Math.max(0, 1 - yUnit * yUnit));
    const theta = index * goldenAngle;
    const orbit = 470 + (index % 3) * 34;
    return {
      id: cluster.cluster_id,
      label: cluster.suggested_label || `Assembly ${index + 1}`,
      neuronIds,
      departments: cluster.departments,
      representativeLabels: cluster.representative_labels || [],
      color: ZONE_COLORS[index % ZONE_COLORS.length],
      x: Math.cos(theta) * radial * orbit,
      y: yUnit * orbit,
      z: Math.sin(theta) * radial * orbit,
      radius: Math.max(76, Math.min(148, 58 + Math.sqrt(neuronIds.length) * 13)),
    };
  });
}

interface NeuronUniverseProps {
  transparent?: boolean;
  controlPosition?: { left: number; top: number; width?: number };
  onControlPointerDown?: React.PointerEventHandler<HTMLDivElement>;
  controlDragMoved?: () => boolean;
  onControlHeightChange?: (height: number) => void;
}

export default function NeuronUniverse({ transparent = false, controlPosition,
  onControlPointerDown, controlDragMoved, onControlHeightChange }: NeuronUniverseProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<any>(null);

  const [neurons, setNeurons] = useState<Graph3DNode[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [replayTraces, setReplayTraces] = useState<Graph3DReplayTrace[]>([]);
  const [clusters, setClusters] = useState<SemanticCluster[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selected, setSelected] = useState<Graph3DNode | null>(null);
  const [selectedZoneId, setSelectedZoneId] = useState<number | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<any>(null);
  useEffect(() => {
    setSelectedDetail(null);
    if (selected) fetchNeuron(selected.id).then(setSelectedDetail).catch(() => {});
  }, [selected?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [hover, setHover] = useState<{ n: Graph3DNode; sx: number; sy: number } | null>(null);
  const hoverContentCache = useRef(new Map<number, string>());
  const [hoverContent, setHoverContent] = useState<{ id: number; text: string } | null>(null);
  const [assemblyHover, setAssemblyHover] = useState<{
    zone: ZoneDefinition;
    sx: number;
    sy: number;
  } | null>(null);
  const [bloom, setBloom] = useState(true);
  const [synapses, setSynapses] = useState(true);
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
  const [layoutMode, setLayoutMode] = useState<LayoutMode>('organic');
  const [panelOpen, setPanelOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchFocused, setSearchFocused] = useState(false);
  const [lensMode, setLensMode] = useState<LensMode | null>(null);
  const [activityWindow, setActivityWindow] = useState<ActivityWindow>('7d');
  const [selectedTraceId, setSelectedTraceId] = useState<number | null>(null);

  useEffect(() => {
    if (!panelRef.current || !onControlHeightChange) return;
    const report = () => onControlHeightChange(panelRef.current?.offsetHeight || 0);
    report();
    const observer = new ResizeObserver(report);
    observer.observe(panelRef.current);
    return () => observer.disconnect();
  }, [onControlHeightChange]);

  // ── Data load (hardened: coerce NaN-prone numeric fields) ──
  useEffect(() => {
    (async () => {
      try {
        setLoading(true); setError(null);
        const [d, clusterPayload] = await Promise.all([
          fetchGraph3D(0.25, 12000, 3, 48),
          fetchSemanticClusters(0.3, 3, 1).catch(() => null),
        ]);
        const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);
        setNeurons(d.neurons.map(n => ({
          ...n, centrality: num(n.centrality), invocations: num(n.invocations),
          avg_utility: num(n.avg_utility), abstraction_type: n.abstraction_type ?? null,
        })));
        setEdges(d.edges.map(e => ({
          source: e.source, target: e.target, weight: num(e.weight),
          edge_type: e.edge_type ?? 'pyramidal',
          co_fire_count: num(e.co_fire_count),
          activity_1d: num(e.activity_1d),
          activity_7d: num(e.activity_7d),
          activity_30d: num(e.activity_30d),
          activity_all: num(e.activity_all),
        })));
        setReplayTraces(d.replays?.traces || []);
        setClusters(clusterPayload?.clusters || []);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load graph');
      } finally { setLoading(false); }
    })();
  }, []);

  const zones = useMemo(
    () => buildZones(neurons, edges, clusters),
    [neurons, edges, clusters],
  );
  const zoneByNeuron = useMemo(() => {
    const index = new Map<number, ZoneDefinition>();
    zones.forEach(zone => zone.neuronIds.forEach(id => index.set(id, zone)));
    return index;
  }, [zones]);
  const neuronById = useMemo(
    () => new Map(neurons.map(neuron => [neuron.id, neuron])),
    [neurons],
  );
  const selectedZone = useMemo(
    () => zones.find(zone => zone.id === selectedZoneId) || null,
    [zones, selectedZoneId],
  );
  const selectedZoneStats = useMemo(() => {
    if (!selectedZone) return null;
    const members = new Set(selectedZone.neuronIds);
    let internal = 0;
    let outbound = 0;
    for (const edge of edges) {
      const sourceInside = members.has(edge.source);
      const targetInside = members.has(edge.target);
      if (sourceInside && targetInside) internal += 1;
      else if (sourceInside !== targetInside) outbound += 1;
    }
    return { internal, outbound };
  }, [edges, selectedZone]);
  const selectedZoneBridges = useMemo(() => {
    if (!selectedZone) return [];
    const members = new Set(selectedZone.neuronIds);
    const bridgeMap = new Map<number, { zone: ZoneDefinition; count: number; weight: number }>();
    for (const edge of edges) {
      const sourceInside = members.has(edge.source);
      const targetInside = members.has(edge.target);
      if (sourceInside === targetInside) continue;
      const outsideId = sourceInside ? edge.target : edge.source;
      const targetZone = zoneByNeuron.get(outsideId);
      if (!targetZone || targetZone.id === selectedZone.id) continue;
      const bridge = bridgeMap.get(targetZone.id) || { zone: targetZone, count: 0, weight: 0 };
      bridge.count += 1;
      bridge.weight += edge.weight;
      bridgeMap.set(targetZone.id, bridge);
    }
    return Array.from(bridgeMap.values())
      .sort((a, b) => b.count - a.count || b.weight - a.weight)
      .slice(0, 6);
  }, [edges, selectedZone, zoneByNeuron]);

  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase();
    if (query.length < 2) return [];
    const neuronMatches = neurons.flatMap(neuron => {
      const label = neuron.label.toLocaleLowerCase();
      const haystack = [
        neuron.label,
        neuron.summary || '',
        neuron.department || '',
        neuron.role_key || '',
        ...(neuron.entities || []),
      ].join(' ').toLocaleLowerCase();
      if (!haystack.includes(query)) return [];
      const score = label.startsWith(query) ? 0 : label.includes(query) ? 1 : 2;
      return [{ kind: 'neuron' as const, neuron, score }];
    });
    const zoneMatches = zones.flatMap(zone => {
      const label = zone.label.toLocaleLowerCase();
      const haystack = [
        zone.label,
        ...zone.departments,
        ...zone.representativeLabels,
      ].join(' ').toLocaleLowerCase();
      if (!haystack.includes(query)) return [];
      const score = label.startsWith(query) ? 0 : label.includes(query) ? 1 : 2;
      return [{ kind: 'zone' as const, zone, score }];
    });
    return [...zoneMatches, ...neuronMatches]
      .sort((a, b) => a.score - b.score || (
        a.kind === 'zone' ? a.zone.label : a.neuron.label
      ).localeCompare(b.kind === 'zone' ? b.zone.label : b.neuron.label))
      .slice(0, 8);
  }, [neurons, searchQuery, zones]);

  const visibleReplayTraces = useMemo(() => {
    const days = activityWindow === 'all' ? Number.POSITIVE_INFINITY : Number(activityWindow.slice(0, -1));
    const cutoff = Date.now() - days * 86_400_000;
    return replayTraces
      .filter(trace => activityWindow === 'all' || (
        trace.created_at != null && new Date(trace.created_at).getTime() >= cutoff
      ))
      .sort((a, b) => (
        new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
      ));
  }, [activityWindow, replayTraces]);
  const selectedTrace = useMemo(
    () => replayTraces.find(trace => trace.query_id === selectedTraceId) || null,
    [replayTraces, selectedTraceId],
  );
  const healthCounts = useMemo(() => {
    const counts = new Map<string, number>();
    neurons.forEach(neuron => counts.set(
      neuron.health_state,
      (counts.get(neuron.health_state) || 0) + 1,
    ));
    return counts;
  }, [neurons]);

  useEffect(() => {
    if (layoutMode === 'organic') setSelectedZoneId(null);
  }, [layoutMode]);

  useEffect(() => {
    if (selectedZoneId != null && !selectedZone) setSelectedZoneId(null);
  }, [selectedZone, selectedZoneId]);

  useEffect(() => {
    setHoverContent(null);
    if (!hover) return;
    const neuronId = hover.n.id;
    const cached = hoverContentCache.current.get(neuronId);
    if (cached != null) {
      setHoverContent({ id: neuronId, text: cached });
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      fetchNeuron(neuronId)
        .then(detail => {
          if (cancelled) return;
          const raw = (detail.content || detail.summary || 'No stored content for this neuron.').trim();
          const preview = raw.length > 700 ? `${raw.slice(0, 700).trimEnd()}…` : raw;
          hoverContentCache.current.set(neuronId, preview);
          setHoverContent({ id: neuronId, text: preview });
        })
        .catch(() => {
          if (!cancelled) setHoverContent({ id: neuronId, text: 'Content unavailable.' });
        });
    }, 120);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [hover?.n.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const isConcept = (n: Graph3DNode) => n.node_type === 'concept' && n.layer === -1;

  // ── Region palette (stable slot order by neuron count) ──
  const regionColor = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of neurons) {
      if (isConcept(n)) continue;
      const r = n.department || 'Unassigned';
      counts.set(r, (counts.get(r) || 0) + 1);
    }
    const order = Array.from(counts.entries()).sort((a, b) => b[1] - a[1]).map(([r]) => r);
    const color = new Map<string, string>();
    order.forEach((r, i) => color.set(r, i < REGION_COLORS.length ? REGION_COLORS[i] : OVERFLOW_COLOR));
    return color;
  }, [neurons]);

  const nodeHex = useMemo(() => (n: Graph3DNode): string => {
    if (layoutMode === 'zones') {
      return zoneByNeuron.get(n.id)?.color || OVERFLOW_COLOR;
    }
    if (isConcept(n)) return CONCEPT_COLOR;
    return regionColor.get(n.department || 'Unassigned') || OVERFLOW_COLOR;
  }, [layoutMode, regionColor, zoneByNeuron]);

  // Refs let the once-built engine read the CURRENT mode at call time —
  // the dropdowns were dead because the closures captured initial values.
  const nodeHexRef = useRef(nodeHex);
  nodeHexRef.current = nodeHex;

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

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: transparent, powerPreference: 'high-performance' });
    if (transparent) renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.6;
    let cameraFlight: {
      from: THREE.Vector3;
      to: THREE.Vector3;
      fromTarget: THREE.Vector3;
      toTarget: THREE.Vector3;
      startedAt: number;
      duration: number;
      onComplete?: () => void;
    } | null = null;
    function flyCamera(
      to: THREE.Vector3,
      target = new THREE.Vector3(0, 0, 0),
      duration = 850,
      onComplete?: () => void,
    ) {
      cameraFlight = {
        from: camera.position.clone(),
        to,
        fromTarget: controls.target.clone(),
        toTarget: target,
        startedAt: performance.now(),
        duration,
        onComplete,
      };
    }
    function syncCameraFlight(now: number) {
      if (!cameraFlight) return;
      const flight = cameraFlight;
      const raw = Math.min(1, (now - flight.startedAt) / flight.duration);
      const eased = raw < 0.5
        ? 4 * raw * raw * raw
        : 1 - Math.pow(-2 * raw + 2, 3) / 2;
      camera.position.lerpVectors(flight.from, flight.to, eased);
      controls.target.lerpVectors(flight.fromTarget, flight.toTarget, Math.min(1, eased + 0.08));
      if (raw >= 1) {
        cameraFlight = null;
        flight.onComplete?.();
      }
    }
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
    const zoneByNodeId = new Map<number, ZoneDefinition>();
    zones.forEach(zone => zone.neuronIds.forEach(id => zoneByNodeId.set(id, zone)));
    let zoneMode = layoutMode === 'zones';

    // Keep only edges whose BOTH endpoints are active nodes (the endpoint can
    // return edges to inactive neurons; d3 forceLink throws "node not found").
    const E = edges.filter(e => byId.has(e.source) && byId.has(e.target));

    const adjacency = new Map<number, Set<number>>();
    nodes.forEach(n => adjacency.set(n.id, new Set()));
    for (const e of E) {
      adjacency.get(e.source)?.add(e.target);
      adjacency.get(e.target)?.add(e.source);
    }

    // Associative cartography: translucent territory volumes and persistent
    // labels sit at deterministic Leiden-community anchors. They do not replace
    // or synthesize graph nodes; they are an optional map over the real graph.
    const zoneGroup = new THREE.Group();
    zoneGroup.visible = zoneMode;
    scene.add(zoneGroup);
    const zoneGeometry = new THREE.SphereGeometry(1, 18, 12);
    const zoneMaterials: THREE.Material[] = [];
    const zoneTextures: THREE.Texture[] = [];
    const zoneLabelSprites: THREE.Sprite[] = [];
    const zoneHitTargets: THREE.Mesh[] = [];
    function zoneLabelSprite(zone: ZoneDefinition) {
      const canvas = document.createElement('canvas');
      canvas.width = 640;
      canvas.height = 112;
      const ctx = canvas.getContext('2d');
      if (!ctx) return null;
      ctx.fillStyle = 'rgba(7, 12, 20, 0.88)';
      ctx.strokeStyle = zone.color;
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.roundRect(3, 3, canvas.width - 6, canvas.height - 6, 18);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = '#f4f7fc';
      ctx.font = '600 28px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const title = zone.label.length > 39 ? `${zone.label.slice(0, 38)}…` : zone.label;
      ctx.fillText(title, canvas.width / 2, 43);
      ctx.fillStyle = '#9ca8ba';
      ctx.font = '20px ui-monospace, monospace';
      ctx.fillText(`${zone.neuronIds.length} NEURONS`, canvas.width / 2, 78);
      const texture = new THREE.CanvasTexture(canvas);
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.minFilter = THREE.LinearFilter;
      zoneTextures.push(texture);
      const material = new THREE.SpriteMaterial({
        map: texture,
        transparent: true,
        depthTest: false,
        depthWrite: false,
      });
      zoneMaterials.push(material);
      const sprite = new THREE.Sprite(material);
      sprite.scale.set(205, 36, 1);
      sprite.position.set(zone.x, zone.y + zone.radius + 34, zone.z);
      sprite.renderOrder = 20;
      sprite.userData.zoneId = zone.id;
      zoneLabelSprites.push(sprite);
      return sprite;
    }
    zones.forEach(zone => {
      const fillMaterial = new THREE.MeshBasicMaterial({
        color: zone.color,
        transparent: true,
        opacity: 0.032,
        side: THREE.BackSide,
        depthWrite: false,
      });
      const wireMaterial = new THREE.MeshBasicMaterial({
        color: zone.color,
        transparent: true,
        opacity: 0.12,
        wireframe: true,
        depthWrite: false,
      });
      zoneMaterials.push(fillMaterial, wireMaterial);
      const fill = new THREE.Mesh(zoneGeometry, fillMaterial);
      const wire = new THREE.Mesh(zoneGeometry, wireMaterial);
      const hitMaterial = new THREE.MeshBasicMaterial({
        transparent: true,
        opacity: 0,
        depthWrite: false,
        colorWrite: false,
        side: THREE.DoubleSide,
      });
      zoneMaterials.push(hitMaterial);
      const hitTarget = new THREE.Mesh(zoneGeometry, hitMaterial);
      fill.position.set(zone.x, zone.y, zone.z);
      wire.position.copy(fill.position);
      hitTarget.position.copy(fill.position);
      fill.scale.setScalar(zone.radius);
      wire.scale.setScalar(zone.radius);
      hitTarget.scale.setScalar(zone.radius);
      fill.raycast = () => {};
      wire.raycast = () => {};
      hitTarget.userData.zoneId = zone.id;
      zoneHitTargets.push(hitTarget);
      zoneGroup.add(fill, wire, hitTarget);
      const label = zoneLabelSprite(zone);
      if (label) zoneGroup.add(label);
    });
    renderer.domElement.dataset.assemblyHitTargets = String(zoneHitTargets.length);

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
      if (assistantIdx < 0 || zoneMode) return;
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
    let healthMode = false;
    let heatMode = false;
    let heatWindow: ActivityWindow = '7d';
    let inspectedReplayEdges: Set<number> | null = null;
    let inspectedReplayNodes: Set<number> | null = null;
    const HEALTH_COLORS: Record<Graph3DNode['health_state'], string> = {
      contested: '#ff5c7a',
      stale: '#ff9f43',
      'low-confidence': '#ffd166',
      reinforced: '#45dfb1',
      quiet: '#53627a',
      current: '#78d3f8',
    };

    function recomputeRadius() {
      for (let i = 0; i < N; i++) {
        const n = nodes[i];
        const raw = 2.4 + Math.sqrt(Math.min(n.invocations, 100) / 100) * 9;
        // The assistant doesn't scale off graph metrics — it IS the scale.
        radius[i] = n.node_type === 'assistant' ? 8 : isSkill(n) ? raw * 1.9 : isConcept(n) ? raw * 1.25 : raw;
      }
    }
    function recomputeColors() {
      const c = new THREE.Color();
      for (let i = 0; i < N; i++) {
        c.set(
          nodes[i].node_type === 'assistant'
            ? ASSISTANT_COLOR
            : healthMode
              ? HEALTH_COLORS[nodes[i].health_state]
            : (!zoneMode && isSkill(nodes[i]))
              ? SKILL_COLOR
              : nodeHexRef.current(nodes[i]),
        );
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
    const HEAT_COLD = new THREE.Color('#18304f');
    const HEAT_WARM = new THREE.Color('#79efff');
    const HEAT_HOT = new THREE.Color('#ffd166');
    function edgeActivity(edge: Edge) {
      if (heatWindow === '1d') return edge.activity_1d;
      if (heatWindow === '7d') return edge.activity_7d;
      if (heatWindow === '30d') return edge.activity_30d;
      return edge.activity_all;
    }
    function recomputeLineColors() {
      const maximum = Math.max(1, ...E.map(edgeActivity));
      let usedEdges = 0;
      for (let e = 0; e < M; e++) {
        let c = edgeColors[e];
        let gain = 1;
        if (heatMode) {
          const activity = edgeActivity(E[e]);
          if (activity > 0) usedEdges++;
          const fraction = Math.log1p(activity) / Math.log1p(maximum);
          c = fraction < 0.58
            ? HEAT_COLD.clone().lerp(HEAT_WARM, fraction / 0.58)
            : HEAT_WARM.clone().lerp(HEAT_HOT, (fraction - 0.58) / 0.42);
          gain = activity > 0 ? 0.32 + fraction * 3.2 : 0.045;
        }
        for (let v = 0; v < CURVE_SEGS * 2; v++) {
          const o = (e * CURVE_SEGS * 2 + v) * 3;
          lineCol[o] = c.r * gain;
          lineCol[o + 1] = c.g * gain;
          lineCol[o + 2] = c.b * gain;
        }
      }
      (lineGeo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
      renderer.domElement.dataset.heatWindow = heatWindow;
      renderer.domElement.dataset.heatUsedEdges = String(heatMode ? usedEdges : 0);
    }
    // Structural edge colours are the default; Heat replaces them on demand.
    recomputeLineColors();
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
    let inspectedZoneId: number | null = null;
    let synapsesOn = true;
    let visibleEdge = new Uint8Array(M).fill(1);

    // Historical recall replay. Query rows prove which neurons co-activated;
    // the backend projects each query onto a compact forest of retained
    // synapses. Timing is organic, but the pathways are evidence-backed.
    type ReplayPulse = {
      e: number;
      t0: number;
      dur: number;
      reverse: boolean;
      kind: 'coactivation' | 'spread';
    };
    type MappedReplay = {
      queryId: number;
      segments: Array<{
        e: number;
        reverse: boolean;
        kind: 'coactivation' | 'spread';
        weight: number;
      }>;
    };
    const edgeIndex = new Map<string, number>();
    E.forEach((edge, index) => edgeIndex.set(`${edge.source}:${edge.target}`, index));
    const mappedReplays: MappedReplay[] = replayTraces.map(trace => ({
      queryId: trace.query_id,
      segments: trace.segments.flatMap(segment => {
        const forward = edgeIndex.get(`${segment.source}:${segment.target}`);
        if (forward != null) {
          return [{ e: forward, reverse: false, kind: segment.kind, weight: segment.weight }];
        }
        const reverse = edgeIndex.get(`${segment.target}:${segment.source}`);
        return reverse == null
          ? []
          : [{ e: reverse, reverse: true, kind: segment.kind, weight: segment.weight }];
      }),
    })).filter(trace => trace.segments.length > 0);
    renderer.domElement.dataset.replayTraceCount = String(mappedReplays.length);
    renderer.domElement.dataset.replayHistoricalSegments = String(
      mappedReplays.reduce((sum, trace) => sum + trace.segments.length, 0),
    );

    const REPLAY_MAX_PULSES = 48;
    const REPLAY_TAIL_POINTS = 4;
    const REPLAY_POINT_CAPACITY = REPLAY_MAX_PULSES * REPLAY_TAIL_POINTS;
    const firePos = new Float32Array(REPLAY_POINT_CAPACITY * 3);
    const fireCol = new Float32Array(REPLAY_POINT_CAPACITY * 3);
    const fireGeo = new THREE.BufferGeometry();
    fireGeo.setAttribute('position', new THREE.BufferAttribute(firePos, 3));
    fireGeo.setAttribute('color', new THREE.BufferAttribute(fireCol, 3));
    fireGeo.setDrawRange(0, 0);
    const firePts = new THREE.Points(fireGeo, new THREE.PointsMaterial({
      size: 12, vertexColors: true, transparent: true, opacity: 0.98,
      blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
    }));
    firePts.frustumCulled = false;
    firePts.raycast = () => {};
    scene.add(firePts);

    // A second line layer holds the short-lived afterimage of each historical
    // route. It shares the exact live Bézier geometry with the base synapse.
    const replayGlow = new Float32Array(M);
    const replayGlowKind = new Uint8Array(M);
    const glowPos = new Float32Array(linePos.length);
    const glowCol = new Float32Array(lineCol.length);
    const glowGeo = new THREE.BufferGeometry();
    glowGeo.setAttribute('position', new THREE.BufferAttribute(glowPos, 3));
    glowGeo.setAttribute('color', new THREE.BufferAttribute(glowCol, 3));
    const glowMat = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.9,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
    const glowLines = new THREE.LineSegments(glowGeo, glowMat);
    glowLines.frustumCulled = false;
    glowLines.raycast = () => {};
    scene.add(glowLines);
    const COACTIVATION_REPLAY = new THREE.Color('#79efff');
    const SPREAD_REPLAY = new THREE.Color('#ffd166');
    const fires: ReplayPulse[] = [];
    let nextReplayAt = 0;
    let lastReplayIndex = -1;

    function edgeCanReplay(e: number) {
      const source = byId.get(E[e].source);
      const target = byId.get(E[e].target);
      return Boolean(
        source && target && visibleEdge[e] &&
        !hidden[source._i] && !hidden[target._i],
      );
    }

    function spawnHistoricalReplay(t: number) {
      if (!synapsesOn || !mappedReplays.length || fires.length >= REPLAY_MAX_PULSES) return;
      const eligible = mappedReplays
        .map((trace, index) => ({
          trace,
          index,
          segments: trace.segments.filter(segment => edgeCanReplay(segment.e)),
        }))
        .filter(candidate => candidate.segments.length > 0);
      if (!eligible.length) return;
      // Richer remembered paths should be seen more often than one-edge
      // retrievals, without excluding the short traces entirely.
      const totalWeight = eligible.reduce(
        (sum, candidate) => sum + Math.pow(candidate.segments.length, 1.45),
        0,
      );
      let roll = Math.random() * totalWeight;
      let choice = eligible[eligible.length - 1];
      for (const candidate of eligible) {
        roll -= Math.pow(candidate.segments.length, 1.45);
        if (roll <= 0) {
          choice = candidate;
          break;
        }
      }
      if (eligible.length > 1 && choice.index === lastReplayIndex) {
        choice = eligible[(eligible.indexOf(choice) + 1 + ((Math.random() * (eligible.length - 1)) | 0)) % eligible.length];
      }
      lastReplayIndex = choice.index;
      const segments = choice.segments.slice(0, REPLAY_MAX_PULSES - fires.length);
      segments.forEach((segment, index) => {
        fires.push({
          e: segment.e,
          t0: t + index * 0.19 + Math.random() * 0.06,
          dur: 0.9 + segment.weight * 0.55,
          reverse: segment.reverse,
          kind: segment.kind,
        });
      });
      renderer.domElement.dataset.lastReplayQuery = String(choice.trace.queryId);
      nextReplayAt = t + 1.8 + segments.length * 0.16 + Math.random() * 2.4;
    }

    function syncReplayGlow() {
      let glowingEdges = 0;
      for (let e = 0; e < M; e++) {
        const base = e * CURVE_SEGS * 6;
        const intensity = replayGlow[e];
        if (intensity < 0.015 || !edgeCanReplay(e)) {
          glowPos.fill(0, base, base + CURVE_SEGS * 6);
          glowCol.fill(0, base, base + CURVE_SEGS * 6);
          continue;
        }
        glowingEdges++;
        glowPos.set(linePos.subarray(base, base + CURVE_SEGS * 6), base);
        const color = replayGlowKind[e] === 2 ? SPREAD_REPLAY : COACTIVATION_REPLAY;
        const hdr = 2.1 + intensity * 3.4;
        for (let v = 0; v < CURVE_SEGS * 2; v++) {
          const o = base + v * 3;
          glowCol[o] = color.r * hdr;
          glowCol[o + 1] = color.g * hdr;
          glowCol[o + 2] = color.b * hdr;
        }
      }
      (glowGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (glowGeo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
      renderer.domElement.dataset.replayGlowingEdges = String(glowingEdges);
    }

    function syncFires(t: number, dt: number) {
      if (!synapsesOn) {
        fires.length = 0;
        fireGeo.setDrawRange(0, 0);
        renderer.domElement.dataset.replayActivePulses = '0';
        renderer.domElement.dataset.replayGlowingEdges = '0';
        return;
      }
      for (let e = 0; e < M; e++) replayGlow[e] *= Math.exp(-dt * 2.35);
      if (M > 0 && t >= nextReplayAt) spawnHistoricalReplay(t);

      let writePulse = 0;
      let writePoint = 0;
      for (const fire of fires) {
        const raw = (t - fire.t0) / fire.dur;
        if (raw >= 1 || !edgeCanReplay(fire.e)) continue;
        fires[writePulse++] = fire;
        if (raw < 0) continue;
        const source = byId.get(E[fire.e].source)!;
        const target = byId.get(E[fire.e].target)!;
        edgeControl(fire.e, source, target, _ctrl);
        const wave = Math.sin(Math.PI * raw);
        replayGlow[fire.e] = Math.max(replayGlow[fire.e], 0.35 + wave * 1.05);
        replayGlowKind[fire.e] = fire.kind === 'spread' ? 2 : 1;
        const color = fire.kind === 'spread' ? SPREAD_REPLAY : COACTIVATION_REPLAY;

        for (let tail = 0; tail < REPLAY_TAIL_POINTS; tail++) {
          let p = raw - tail * 0.045;
          if (p < 0 || writePoint >= REPLAY_POINT_CAPACITY) continue;
          p = Math.min(1, p);
          if (fire.reverse) p = 1 - p;
          const a = (1 - p) * (1 - p);
          const b = 2 * (1 - p) * p;
          const c = p * p;
          const o = writePoint * 3;
          firePos[o] = a * source.x + b * _ctrl.x + c * target.x;
          firePos[o + 1] = a * source.y + b * _ctrl.y + c * target.y;
          firePos[o + 2] = a * source.z + b * _ctrl.z + c * target.z;
          const tailFade = (1 - tail / (REPLAY_TAIL_POINTS + 0.5));
          const hdr = (2.0 + wave * 5.5) * tailFade;
          fireCol[o] = (color.r + 0.32) * hdr;
          fireCol[o + 1] = (color.g + 0.32) * hdr;
          fireCol[o + 2] = (color.b + 0.32) * hdr;
          writePoint++;
        }
      }
      fires.length = writePulse;
      renderer.domElement.dataset.replayActivePulses = String(
        fires.filter(fire => fire.t0 <= t).length,
      );
      fireGeo.setDrawRange(0, writePoint);
      (fireGeo.attributes.position as THREE.BufferAttribute).needsUpdate = true;
      (fireGeo.attributes.color as THREE.BufferAttribute).needsUpdate = true;
      syncReplayGlow();
    }

    function refreshInstances() {
      const highlightId = focusId;
      for (let i = 0; i < N; i++) {
        if (hidden[i]) { dummy.scale.setScalar(0.0001); }
        else { dummy.position.set(nodes[i].x, nodes[i].y, nodes[i].z); dummy.scale.setScalar(scaleFor(i)); }
        dummy.updateMatrix();
        mesh.setMatrixAt(i, dummy.matrix);
        let r = baseColor[i * 3] * nodeLight, g = baseColor[i * 3 + 1] * nodeLight, b = baseColor[i * 3 + 2] * nodeLight;
        if (inspectedReplayNodes && !inspectedReplayNodes.has(nodes[i].id)) {
          r *= 0.08; g *= 0.08; b *= 0.08;
        }
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
        if (
          !s || !t || !visibleEdge[e] || hidden[s._i] || hidden[t._i] ||
          (inspectedReplayEdges != null && !inspectedReplayEdges.has(e))
        ) {
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
      const zoneOverview = zoneMode && pinnedId == null && inspectedZoneId == null;
      const links = activeLinks.map(link => ({
        source: link.source,
        target: link.target,
        weight: link.weight,
        crossZone: (
          zoneByNodeId.get(link.source)?.id !==
          zoneByNodeId.get(link.target)?.id
        ),
      }));
      sim = forceSimulation(active, 3)
        .force('link', forceLink(links).id((d: SimNode) => d.id)
          .distance((l: any) => (
            zoneOverview && l.crossZone ? 250 : 26 + (1 - l.weight) * 70
          ))
          .strength((l: any) => (
            zoneOverview && l.crossZone ? 0.018 : 0.15 + l.weight * 0.5
          )))
        .force('charge', forceManyBody().strength(
          pinnedId != null
            ? -(repulsionVal * 3)
            : zoneOverview ? -(repulsionVal * 0.34) : -repulsionVal,
        ).distanceMax(zoneOverview ? 420 : 1400))
        .force('x', forceX(0).strength(zoneOverview ? 0.004 : 0.015))
        .force('y', forceY(0).strength(zoneOverview ? 0.004 : 0.015))
        .force('z', forceZ(0).strength(zoneOverview ? 0.004 : 0.015))
        .velocityDecay(0.34)
        .alphaDecay(0.02)
        // Never fully sleeps while motion is on (the wanderer's pull is
        // continuous); with motion off every rebuilt view starts frozen.
        .alphaTarget(motionOn ? 0.025 : 0)
        .alpha(motionOn ? 0.9 : 0);
      if (zoneOverview) {
        sim.force('zoneAttraction', (alpha: number) => {
          for (const node of active) {
            if (node.fx != null) continue;
            const zone = zoneByNodeId.get(node.id);
            if (!zone) continue;
            const pull = 0.18 * alpha;
            (node as any).vx += (zone.x - node.x) * pull;
            (node as any).vy += (zone.y - node.y) * pull;
            (node as any).vz += (zone.z - node.z) * pull;
          }
        });
      }
      // The assistant exerts gravity as it traverses: nearby free nodes are
      // drawn toward it with inverse-square falloff (softened + capped), so
      // the medium visibly bends around the wanderer's path.
      if (assistantIdx >= 0 && !zoneMode) {
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
      if (skillIdx.length && !zoneMode) {
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

    type PickResult =
      | { kind: 'neuron'; index: number }
      | { kind: 'assembly'; zoneId: number };

    function pick(clientX: number, clientY: number): PickResult | null {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      if (zoneMode && focusId == null && inspectedZoneId == null) {
        const labelHit = raycaster.intersectObjects(zoneLabelSprites, false)[0];
        const zoneId = labelHit?.object.userData.zoneId;
        if (typeof zoneId === 'number') return { kind: 'assembly', zoneId };
        const volumeHit = raycaster.intersectObjects(zoneHitTargets, false)[0];
        const volumeZoneId = volumeHit?.object.userData.zoneId;
        if (typeof volumeZoneId === 'number') return { kind: 'assembly', zoneId: volumeZoneId };
      }
      const hits = raycaster.intersectObject(mesh);
      for (const h of hits) {
        const inst = (h as any).instanceId;
        if (inst != null && !hidden[inst]) return { kind: 'neuron', index: inst };
      }
      return null;
    }
    function onMove(ev: PointerEvent) {
      const hit = pick(ev.clientX, ev.clientY);
      renderer.domElement.style.cursor = hit != null ? 'pointer' : 'grab';
      if (hit?.kind === 'neuron') {
        setHover({ n: nodes[hit.index], sx: ev.clientX, sy: ev.clientY });
        setAssemblyHover(null);
      } else if (hit?.kind === 'assembly') {
        const zone = zones.find(candidate => candidate.id === hit.zoneId);
        setHover(null);
        setAssemblyHover(zone ? { zone, sx: ev.clientX, sy: ev.clientY } : null);
      } else {
        setHover(null);
        setAssemblyHover(null);
      }
    }
    function onClick(ev: PointerEvent) {
      const hit = pick(ev.clientX, ev.clientY);
      if (hit?.kind === 'neuron') {
        setSelected({ ...(nodes[hit.index] as Graph3DNode) });
      } else if (hit?.kind === 'assembly') {
        setSelected(null);
        setSelectedZoneId(hit.zoneId);
      }
    }
    renderer.domElement.addEventListener('pointermove', onMove);
    renderer.domElement.addEventListener('click', onClick);

    let raf = 0;
    let lastFrame: number | null = null;
    const probe = new THREE.Vector3();
    function setCanvasProbe(prefix: string, worldX: number, worldY: number, worldZ: number) {
      probe.set(worldX, worldY, worldZ).project(camera);
      const canvasWidth = renderer.domElement.clientWidth;
      const canvasHeight = renderer.domElement.clientHeight;
      renderer.domElement.dataset[`${prefix}X`] = String((probe.x + 1) * 0.5 * canvasWidth);
      renderer.domElement.dataset[`${prefix}Y`] = String((1 - probe.y) * 0.5 * canvasHeight);
    }
    function clearCanvasProbe(prefix: string) {
      delete renderer.domElement.dataset[`${prefix}X`];
      delete renderer.domElement.dataset[`${prefix}Y`];
      delete renderer.domElement.dataset[`${prefix}Id`];
    }
    function syncInteractionProbes() {
      if (zoneMode && focusId == null && inspectedZoneId == null && zones[0]) {
        setCanvasProbe('assemblyProbe', zones[0].x, zones[0].y, zones[0].z);
        renderer.domElement.dataset.assemblyProbeId = String(zones[0].id);
      } else {
        clearCanvasProbe('assemblyProbe');
      }

      const neuronInspectionAvailable = !zoneMode || inspectedZoneId != null;
      if (!neuronInspectionAvailable || focusId != null) {
        clearCanvasProbe('neuronProbe');
        return;
      }
      let candidate: SimNode | null = null;
      let candidateScore = Number.POSITIVE_INFINITY;
      for (const node of nodes) {
        if (hidden[node._i]) continue;
        probe.set(node.x, node.y, node.z).project(camera);
        if (Math.abs(probe.x) > 0.82 || Math.abs(probe.y) > 0.76 || probe.z < -1 || probe.z > 1) continue;
        const score = Math.abs(probe.x) + Math.abs(probe.y + 0.22);
        if (score < candidateScore) {
          candidate = node;
          candidateScore = score;
        }
      }
      if (candidate) {
        setCanvasProbe('neuronProbe', candidate.x, candidate.y, candidate.z);
        renderer.domElement.dataset.neuronProbeId = String(candidate.id);
      } else {
        clearCanvasProbe('neuronProbe');
      }
    }
    function animate() {
      raf = requestAnimationFrame(animate);
      const nowMs = performance.now();
      const now = nowMs / 1000;
      const dt = lastFrame == null ? 0 : Math.min(now - lastFrame, 0.1);
      lastFrame = now;
      if (motionOn) {
        animT += dt;
        updateBeats(animT);
        syncFires(animT, dt);
      }
      if (sim && sim.alpha() > 0.006) { sim.tick(); syncPositions(); }
      syncShells(animT);
      syncAssistant(animT);
      syncCameraFlight(nowMs);
      controls.update();
      syncInteractionProbes();
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
            if (on) {
              sim.alphaTarget(0.025);
              sim.alpha(Math.max(sim.alpha(), 0.3)); // wake a slept layout
            } else {
              sim.alphaTarget(0);
              sim.alpha(0); // Motion Off is an immediate freeze, not a slow coast.
            }
          }
        },
        setSynapses(on: boolean) {
          synapsesOn = on;
          visibleEdge.fill(on ? 1 : 0);
          renderer.domElement.dataset.synapsesEnabled = String(on);
          renderer.domElement.dataset.visibleSynapses = String(on ? M : 0);
          if (!on) {
            fires.length = 0;
            replayGlow.fill(0);
            replayGlowKind.fill(0);
            fireGeo.setDrawRange(0, 0);
            renderer.domElement.dataset.replayActivePulses = '0';
          }
          syncPositions();
          syncReplayGlow();
        },
        setEdgeLens(on: boolean, window: ActivityWindow) {
          heatMode = on;
          heatWindow = window;
          renderer.domElement.dataset.heatMode = String(on);
          recomputeLineColors();
        },
        setHealthMode(on: boolean) {
          healthMode = on;
          renderer.domElement.dataset.healthMode = String(on);
          recomputeColors();
          refreshInstances();
        },
        setRecallTrace(queryId: number | null) {
          inspectedReplayEdges = null;
          inspectedReplayNodes = null;
          replayGlow.fill(0);
          replayGlowKind.fill(0);
          renderer.domElement.dataset.inspectedReplayQuery = queryId == null ? '' : String(queryId);
          if (queryId != null) {
            const trace = mappedReplays.find(candidate => candidate.queryId === queryId);
            if (trace) {
              inspectedReplayEdges = new Set(trace.segments.map(segment => segment.e));
              inspectedReplayNodes = new Set<number>();
              trace.segments.forEach(segment => {
                const edge = E[segment.e];
                inspectedReplayNodes!.add(edge.source);
                inspectedReplayNodes!.add(edge.target);
                replayGlow[segment.e] = 1.25;
                replayGlowKind[segment.e] = segment.kind === 'spread' ? 2 : 1;
              });
            }
          }
          syncPositions();
          refreshInstances();
          syncReplayGlow();
        },
        fitView() {
          const fitNodes = nodes.filter(node => (
            !hidden[node._i] &&
            (!inspectedReplayNodes || inspectedReplayNodes.has(node.id))
          ));
          if (!fitNodes.length) return;

          const bounds = new THREE.Box3();
          const lower = new THREE.Vector3();
          const upper = new THREE.Vector3();
          for (const node of fitNodes) {
            const padding = Math.max(4, scaleFor(node._i) * (isSkill(node) ? 1.85 : 1.25));
            lower.set(node.x - padding, node.y - padding, node.z - padding);
            upper.set(node.x + padding, node.y + padding, node.z + padding);
            bounds.expandByPoint(lower);
            bounds.expandByPoint(upper);
          }
          if (zoneMode && focusId == null && inspectedZoneId == null) {
            for (const zone of zones) {
              const padding = Math.max(zone.radius, 108);
              lower.set(zone.x - padding, zone.y - padding, zone.z - padding);
              upper.set(zone.x + padding, zone.y + zone.radius + 54, zone.z + padding);
              bounds.expandByPoint(lower);
              bounds.expandByPoint(upper);
            }
          }

          const sphereBounds = bounds.getBoundingSphere(new THREE.Sphere());
          const target = sphereBounds.center;
          const radiusToFit = Math.max(45, sphereBounds.radius);
          const verticalFov = THREE.MathUtils.degToRad(camera.fov);
          const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * camera.aspect);
          const limitingFov = Math.min(verticalFov, horizontalFov);
          const distance = (radiusToFit / Math.sin(limitingFov / 2)) * 1.12;
          const direction = camera.position.clone().sub(controls.target);
          if (direction.lengthSq() < 0.0001) direction.set(0, 0.14, 1);
          direction.normalize();
          const destination = target.clone().addScaledVector(direction, distance);
          const scope = inspectedReplayNodes
            ? 'recall'
            : focusId != null
              ? 'focus'
              : inspectedZoneId != null
                ? 'assembly'
                : zoneMode ? 'assemblies' : 'universe';
          const fitSequence = Number(renderer.domElement.dataset.fitSequence || 0) + 1;
          renderer.domElement.dataset.fitSequence = String(fitSequence);
          renderer.domElement.dataset.fitScope = scope;
          renderer.domElement.dataset.fitVisibleNodes = String(fitNodes.length);
          renderer.domElement.dataset.fitRadius = radiusToFit.toFixed(2);
          renderer.domElement.dataset.fitDistance = distance.toFixed(2);
          renderer.domElement.dataset.fitFlightComplete = 'false';
          camera.far = Math.max(8000, distance + radiusToFit * 4);
          camera.updateProjectionMatrix();
          flyCamera(destination, target, 850, () => {
            let projectedInside = 0;
            const projected = new THREE.Vector3();
            for (const node of fitNodes) {
              projected.set(node.x, node.y, node.z).project(camera);
              if (
                projected.z >= -1 && projected.z <= 1 &&
                Math.abs(projected.x) <= 0.94 &&
                Math.abs(projected.y) <= 0.94
              ) projectedInside += 1;
            }
            renderer.domElement.dataset.fitProjectedInside = String(projectedInside);
            renderer.domElement.dataset.fitFlightComplete = 'true';
          });
        },
        setNodeLight(v: number) { nodeLight = v; refreshInstances(); },
        setSynapseLight(v: number) { lineMat.opacity = 0.02 + v * 0.88; },
        setRepulsion(v: number) {
          repulsionVal = v * REPULSION_SCALE;
          // Re-settle the CURRENT view (full or ego) with the new charge; keep camera.
          const filtered = focusId != null || inspectedZoneId != null;
          const active = filtered ? nodes.filter(n => !hidden[n._i]) : nodes;
          const activeLinks = filtered
            ? E.filter(e => !hidden[byId.get(e.source)!._i] && !hidden[byId.get(e.target)!._i])
            : E;
          buildSim(active, activeLinks, focusId);
        },
        setLayoutMode(mode: LayoutMode) {
          zoneMode = mode === 'zones';
          if (!zoneMode && inspectedZoneId != null) {
            inspectedZoneId = null;
            hidden.fill(0);
          }
          zoneGroup.visible = zoneMode && focusId == null && inspectedZoneId == null;
          recomputeColors();
          const filtered = focusId != null || inspectedZoneId != null;
          const active = filtered ? nodes.filter(n => !hidden[n._i]) : nodes;
          const activeLinks = filtered
            ? E.filter(e => !hidden[byId.get(e.source)!._i] && !hidden[byId.get(e.target)!._i])
            : E;
          buildSim(active, activeLinks, focusId);
          refreshInstances();
        },
        focus(id: number) {
          focusId = id;
          inspectedZoneId = null;
          zoneGroup.visible = false;
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
          flyCamera(new THREE.Vector3(0, dist * 0.16, dist));
        },
        focusZone(zoneId: number) {
          const zone = zones.find(candidate => candidate.id === zoneId);
          if (!zone) return;
          focusId = null;
          inspectedZoneId = zoneId;
          zoneGroup.visible = false;
          const keep = new Set(zone.neuronIds);
          for (let i = 0; i < N; i++) hidden[i] = keep.has(nodes[i].id) ? 0 : 1;
          const active = nodes.filter(node => keep.has(node.id));
          const activeLinks = E.filter(edge => keep.has(edge.source) && keep.has(edge.target));
          if (!active.length) return;
          const centre = active.reduce(
            (sum, node) => ({
              x: sum.x + node.x,
              y: sum.y + node.y,
              z: sum.z + node.z,
            }),
            { x: 0, y: 0, z: 0 },
          );
          centre.x /= active.length;
          centre.y /= active.length;
          centre.z /= active.length;
          for (const node of active) {
            node.x -= centre.x;
            node.y -= centre.y;
            node.z -= centre.z;
            (node as any).vx = 0;
            (node as any).vy = 0;
            (node as any).vz = 0;
          }
          buildSim(active, activeLinks, null);
          refreshInstances();
          controls.target.set(0, 0, 0);
          const dist = Math.max(220, 52 * Math.sqrt(active.length));
          flyCamera(new THREE.Vector3(0, dist * 0.14, dist));
        },
        reset() {
          focusId = null;
          inspectedZoneId = null;
          hidden.fill(0);
          zoneGroup.visible = zoneMode;
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
        zoneGeometry.dispose();
        zoneMaterials.forEach(material => material.dispose());
        zoneTextures.forEach(texture => texture.dispose());
        fireGeo.dispose(); (firePts.material as THREE.Material).dispose();
        glowGeo.dispose(); glowMat.dispose();
        if (renderer.domElement.parentElement === mount) mount.removeChild(renderer.domElement);
        engineRef.current = null;
      },
    };

    return () => { engineRef.current?.dispose(); };
  }, [neurons, edges, transparent, zones, replayTraces]);

  // ── Control effects → engine ──
  useEffect(() => { engineRef.current?.api.setColorMode(); }, [nodeHex]);
  useEffect(() => { engineRef.current?.api.setBloom(bloom); }, [bloom]);
  useEffect(() => { engineRef.current?.api.setMotion(motion); }, [motion]);
  useEffect(() => { engineRef.current?.api.setSynapses(synapses); }, [synapses]);
  useEffect(() => { engineRef.current?.api.setNodeLight(nodeLight); }, [nodeLight]);
  useEffect(() => { engineRef.current?.api.setSynapseLight(synapseLight); }, [synapseLight]);
  useEffect(() => { engineRef.current?.api.setRepulsion(repulsion); }, [repulsion]);
  useEffect(() => { engineRef.current?.api.setLayoutMode(layoutMode); }, [layoutMode]);
  useEffect(() => {
    engineRef.current?.api.setEdgeLens(lensMode === 'heat', activityWindow);
  }, [activityWindow, lensMode]);
  useEffect(() => {
    engineRef.current?.api.setHealthMode(lensMode === 'health');
  }, [lensMode]);
  useEffect(() => {
    engineRef.current?.api.setRecallTrace(
      lensMode === 'recall' ? selectedTraceId : null,
    );
  }, [lensMode, selectedTraceId]);
  useEffect(() => {
    if (selected) engineRef.current?.api.focus(selected.id);
    else if (selectedZone) engineRef.current?.api.focusZone(selectedZone.id);
    else engineRef.current?.api.reset();
  }, [selected, selectedZone]);
  useEffect(() => {
    if (
      selectedTraceId != null &&
      !visibleReplayTraces.some(trace => trace.query_id === selectedTraceId)
    ) {
      setSelectedTraceId(null);
    }
  }, [selectedTraceId, visibleReplayTraces]);

  const synapseCount = edges.length;
  const zoneCoverage = zoneByNeuron.size;
  const controlPanelStyle = transparent
    ? {
      ...desktopPanel,
      ...controlPosition,
      width: Math.max(264, controlPosition?.width || 0),
    }
    : panel;
  const openLens = (mode: LensMode) => {
    setLensMode(current => current === mode ? null : mode);
    if (mode !== 'recall') setSelectedTraceId(null);
  };
  const inspectNeuron = (neuron: Graph3DNode) => {
    setSelectedTraceId(null);
    setSelectedZoneId(null);
    setSelected(neuron);
    setSearchQuery('');
    setSearchFocused(false);
  };
  const inspectZone = (zone: ZoneDefinition) => {
    setSelectedTraceId(null);
    setSelected(null);
    setLayoutMode('zones');
    setSelectedZoneId(zone.id);
    setSearchQuery('');
    setSearchFocused(false);
  };
  const inspectTrace = (trace: Graph3DReplayTrace) => {
    setSelected(null);
    setSelectedZoneId(null);
    setSelectedTraceId(trace.query_id);
    setMotion(false);
  };
  const inspectSearchResult = (result: (typeof searchResults)[number]) => {
    if (result.kind === 'zone') inspectZone(result.zone);
    else inspectNeuron(result.neuron);
  };
  const heatStats = useMemo(() => {
    const key = `activity_${activityWindow}` as const;
    const values = edges.map(edge => edge[key]);
    return {
      used: values.filter(value => value > 0).length,
      peak: Math.max(0, ...values),
    };
  }, [activityWindow, edges]);
  return (
    <div className={transparent ? 'neuron-universe neuron-universe--transparent' : 'neuron-universe'}
      style={{ position: 'absolute', inset: 0, overflow: 'hidden', background: transparent ? 'transparent' : '#080a0e' }}>
      {transparent && layoutMode === 'zones' && (
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            inset: 0,
            background: 'radial-gradient(circle at 50% 45%, rgba(9,18,31,0.48), rgba(3,7,13,0.74))',
            backdropFilter: 'blur(0.8px)',
            pointerEvents: 'none',
          }}
        />
      )}
      <div ref={mountRef} style={{ position: 'absolute', inset: 0 }} />

      {loading && (
        <div style={overlayCenter}>
          <div style={{ color: '#9fb8ff' }}>Loading the connectome…</div>
        </div>
      )}
      {error && <div style={{ ...overlayCenter, color: '#e66767' }}>{error}</div>}

      {/* Control panel */}
      <div ref={panelRef} data-testid={transparent ? 'desktop-neuron-controls' : undefined}
        data-wake-obstacle={transparent ? true : undefined}
        style={controlPanelStyle}>
        <div
          onPointerDown={transparent ? onControlPointerDown : undefined}
          onClick={() => { if (!controlDragMoved?.()) setPanelOpen(o => !o); }}
          style={{ cursor: transparent ? 'grab' : 'pointer', userSelect: 'none', touchAction: 'none' }}
          title={transparent
            ? `Drag with navigation · click to ${panelOpen ? 'collapse' : 'expand'} controls`
            : (panelOpen ? 'Collapse controls' : 'Expand controls')}
        >
          <div style={{ fontWeight: 700, color: '#e8edf7', fontSize: '0.95rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            Neuron Universe
            <span style={{ color: '#8a93a6', fontSize: '0.7rem' }}>{panelOpen ? '▾' : '▸'}</span>
          </div>
          <div style={{ color: '#8a93a6', fontSize: '0.72rem', marginBottom: panelOpen ? 10 : 0 }}>
            {layoutMode === 'zones'
              ? selectedZone
                ? `${selectedZone.neuronIds.length} neurons · ${selectedZoneStats?.internal || 0} internal synapses`
                : `${zones.length} named assemblies · ${zoneCoverage}/${neurons.length} mapped`
              : `${neurons.length.toLocaleString()} neurons · ${synapseCount.toLocaleString()} synapses`}
          </div>
        </div>
        {panelOpen && (<>
        <div className="neuron-search" data-testid="neuron-search">
          <span aria-hidden="true">⌕</span>
          <input
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
            onFocus={() => setSearchFocused(true)}
            onBlur={() => window.setTimeout(() => setSearchFocused(false), 120)}
            onKeyDown={event => {
              if (event.key === 'Enter' && searchResults[0]) inspectSearchResult(searchResults[0]);
              if (event.key === 'Escape') {
                setSearchQuery('');
                setSearchFocused(false);
                event.currentTarget.blur();
              }
            }}
            placeholder="Search memories or assemblies"
            aria-label="Search and fly through the neuron universe"
            data-testid="neuron-search-input"
          />
          {searchQuery && (
            <button
              type="button"
              aria-label="Clear search"
              onClick={() => setSearchQuery('')}
            >
              ×
            </button>
          )}
          {searchFocused && searchQuery.trim().length >= 2 && (
            <div className="neuron-search-results" data-testid="neuron-search-results">
              {searchResults.length ? searchResults.map(result => {
                const isZoneResult = result.kind === 'zone';
                const label = isZoneResult ? result.zone.label : result.neuron.label;
                const meta = isZoneResult
                  ? `Assembly · ${result.zone.neuronIds.length} neurons`
                  : `${result.neuron.department || 'Unassigned'} · ${result.neuron.health_state}`;
                return (
                  <button
                    type="button"
                    key={`${result.kind}-${isZoneResult ? result.zone.id : result.neuron.id}`}
                    onMouseDown={event => event.preventDefault()}
                    onClick={() => inspectSearchResult(result)}
                  >
                    <span>{label}</span>
                    <small>{meta}</small>
                  </button>
                );
              }) : (
                <div className="neuron-search-empty">No matching territory</div>
              )}
            </div>
          )}
        </div>

        <div className="neuron-lens-tabs" data-testid="neuron-lens-tabs">
          {(['recall', 'heat', 'health'] as LensMode[]).map(mode => (
            <button
              type="button"
              key={mode}
              className={lensMode === mode ? 'active' : ''}
              aria-pressed={lensMode === mode}
              data-testid={`lens-${mode}`}
              onClick={() => openLens(mode)}
            >
              {mode === 'recall' ? 'Recall' : mode === 'heat' ? 'Pathways' : 'Health'}
            </button>
          ))}
        </div>

        {lensMode && (
          <div className="neuron-lens-drawer" data-testid="neuron-lens-drawer">
            {(lensMode === 'recall' || lensMode === 'heat') && (
              <div className="neuron-time-window" aria-label="Activity time window">
                {(['1d', '7d', '30d', 'all'] as ActivityWindow[]).map(window => (
                  <button
                    type="button"
                    key={window}
                    className={activityWindow === window ? 'active' : ''}
                    aria-pressed={activityWindow === window}
                    data-testid={`time-${window}`}
                    onClick={() => setActivityWindow(window)}
                  >
                    {window === 'all' ? 'All' : window}
                  </button>
                ))}
              </div>
            )}

            {lensMode === 'recall' && (
              selectedTrace ? (
                <div className="neuron-trace-detail" data-testid="recall-trace-detail">
                  <div className="neuron-lens-heading">
                    <span>Query #{selectedTrace.query_id}</span>
                    <button type="button" onClick={() => setSelectedTraceId(null)}>×</button>
                  </div>
                  <time>{selectedTrace.created_at ? new Date(selectedTrace.created_at).toLocaleString() : 'Unknown time'}</time>
                  <p>{selectedTrace.query_preview || 'Historical retrieval'}</p>
                  <div className="neuron-trace-firings">
                    {selectedTrace.firings.slice(0, 12).map(firing => {
                      const neuron = neuronById.get(firing.neuron_id);
                      if (!neuron) return null;
                      return (
                        <button
                          type="button"
                          key={firing.neuron_id}
                          onClick={() => inspectNeuron(neuron)}
                          title={`score ${firing.score.toFixed(3)}${firing.spread_boost > 0 ? ` · spread +${firing.spread_boost.toFixed(3)}` : ''}`}
                        >
                          <b>{firing.rank}</b>
                          <span>{neuron.label}</span>
                          {firing.spread_boost > 0 && <i>spread</i>}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : (
                <div className="neuron-trace-list" data-testid="recall-trace-list">
                  <div className="neuron-lens-summary">
                    {visibleReplayTraces.length} sampled retrievals · select one to freeze
                  </div>
                  {visibleReplayTraces.slice(0, 6).map(trace => (
                    <button
                      type="button"
                      key={trace.query_id}
                      onClick={() => inspectTrace(trace)}
                    >
                      <span>{trace.query_preview || `Query #${trace.query_id}`}</span>
                      <small>
                        {trace.created_at ? new Date(trace.created_at).toLocaleDateString() : 'undated'}
                        {' · '}{trace.neuron_count} fired
                        {trace.spread_derived ? ' · spread' : ''}
                      </small>
                    </button>
                  ))}
                </div>
              )
            )}

            {lensMode === 'heat' && (
              <div className="neuron-heat-summary" data-testid="pathway-heat-summary">
                <div><strong>{heatStats.used.toLocaleString()}</strong> used synapses</div>
                <div><strong>{heatStats.peak.toLocaleString()}</strong> peak traversals</div>
                <p>Cold blue is structural potential. Cyan through gold is remembered traffic.</p>
              </div>
            )}

            {lensMode === 'health' && (
              <div className="neuron-health-legend" data-testid="memory-health-summary">
                {([
                  ['contested', 'Contested', '#ff5c7a'],
                  ['stale', 'Stale review', '#ff9f43'],
                  ['low-confidence', 'Low confidence', '#ffd166'],
                  ['reinforced', 'Reinforced', '#45dfb1'],
                  ['quiet', 'Quiet', '#53627a'],
                  ['current', 'Current', '#78d3f8'],
                ] as const).map(([key, label, color]) => (
                  <div key={key}>
                    <span style={{ background: color }} />
                    <b>{label}</b>
                    <strong>{healthCounts.get(key) || 0}</strong>
                  </div>
                ))}
                <p>Health reflects open integrity findings and observed recall—not an invented confidence score.</p>
              </div>
            )}
          </div>
        )}

        <NeuronRadarControls
          axes={[
            {
              key: 'neuron-light',
              label: 'Neuron light',
              min: 0.2,
              max: 2.5,
              step: 0.05,
              value: nodeLight,
              format: value => `${Math.round(value * 100)}%`,
              onChange: setNodeLight,
            },
            {
              key: 'synapse-light',
              label: 'Synapse light',
              min: 0,
              max: 1,
              step: 0.02,
              value: synapseLight,
              format: value => `${Math.round(value * 100)}%`,
              onChange: setSynapseLight,
            },
            {
              key: 'repulsion',
              label: 'Repulsion',
              min: REPULSION_MIN,
              max: REPULSION_MAX,
              step: 0.5,
              value: repulsion,
              format: value => `${value} · ${value * REPULSION_SCALE}`,
              onChange: setRepulsion,
            },
          ]}
          layoutMode={layoutMode}
          zonesAvailable={zones.length > 0}
          bloom={bloom}
          motion={motion}
          synapses={synapses}
          recallActive={lensMode === 'recall'}
          replayTraceCount={replayTraces.length}
          onBloomChange={setBloom}
          onMotionChange={setMotion}
          onSynapsesChange={setSynapses}
          onRecallOpen={() => openLens('recall')}
          onFitView={() => engineRef.current?.api.fitView()}
          onLayoutModeChange={mode => {
            setSelected(null);
            if (mode === 'organic') setSelectedZoneId(null);
            setLayoutMode(mode);
          }}
        />
        {selected && (
          <button
            data-testid="neuron-focus-back"
            onClick={() => setSelected(null)}
            style={backBtn}
          >
            ← Back to {selectedZone ? 'assembly' : 'full universe'}
          </button>
        )}
        </>)}
      </div>

      {/* Focused-neuron info */}
      {selected && (
        <div
          data-testid="neuron-focus-detail"
          style={transparent ? desktopFocusCard : focusCard}
        >
          <div style={{ color: '#e8edf7', fontWeight: 600 }}>{selected.label || `Neuron #${selected.id}`}</div>
          <div style={{ color: '#8a93a6', fontSize: '0.74rem', marginTop: 3 }}>
            {selected.department || 'Unassigned'}
            {selected.abstraction_type && !isConcept(selected) && <> · {selected.abstraction_type}</>}
            {layoutMode === 'zones' && zoneByNeuron.get(selected.id) && (
              <> · {zoneByNeuron.get(selected.id)!.label}</>
            )}
          </div>
          <div style={{ color: '#6f7a8f', fontSize: '0.72rem', marginTop: 6 }}>
            centrality {selected.centrality.toFixed(2)} · fired {selected.invocations}×
            · {(neurons.length ? '' : '')}now the centre of its universe
          </div>
          <div className={`neuron-health-chip ${selected.health_state}`}>
            {selected.health_state.replace('-', ' ')} · {selected.health_reason}
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

      {/* Focused associative assembly — the territory itself is the inspection unit. */}
      {selectedZone && !selected && selectedZoneStats && (
        <div
          data-testid="assembly-inspection"
          style={transparent ? desktopFocusCard : focusCard}
        >
          <div style={{ color: selectedZone.color, fontSize: '0.66rem', fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Associative assembly
          </div>
          <div style={{ color: '#e8edf7', fontWeight: 650, marginTop: 4 }}>
            {selectedZone.label}
          </div>
          <div style={{ color: '#9ca8ba', fontSize: '0.74rem', marginTop: 5 }}>
            {selectedZone.neuronIds.length} neurons · {selectedZoneStats.internal} internal synapses
            · {selectedZoneStats.outbound} outbound bridges
          </div>
          {selectedZone.representativeLabels.length > 0 && (
            <div style={{ color: '#aeb7c8', fontSize: '0.73rem', lineHeight: 1.42, marginTop: 9 }}>
              {selectedZone.representativeLabels.slice(0, 4).join(' · ')}
            </div>
          )}
          {selectedZoneBridges.length > 0 && (
            <div className="neuron-bridge-list" data-testid="assembly-bridges">
              <span>Strongest neighboring engrams</span>
              {selectedZoneBridges.map(bridge => (
                <button
                  type="button"
                  key={bridge.zone.id}
                  onClick={() => inspectZone(bridge.zone)}
                  data-testid={`assembly-bridge-${bridge.zone.id}`}
                >
                  <i style={{ background: bridge.zone.color }} />
                  <b>{bridge.zone.label}</b>
                  <small>{bridge.count} bridges →</small>
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            data-testid="assembly-back"
            onClick={() => setSelectedZoneId(null)}
            style={{ ...backBtn, marginTop: 11 }}
          >
            ← All assemblies
          </button>
        </div>
      )}

      {/* Hover tooltip */}
      {hover && (
        <div data-testid="neuron-hover-detail" style={{
          position: 'fixed', left: hover.sx + 14, top: hover.sy + 14, zIndex: 30, pointerEvents: 'none',
          background: 'rgba(14,18,26,0.92)', border: '1px solid #2a3446', borderRadius: 8,
          padding: '7px 10px', color: '#e8edf7', fontSize: '0.76rem', maxWidth: 360,
        }}>
          <div style={{ fontWeight: 600 }}>{hover.n.label || `Neuron #${hover.n.id}`}</div>
          <div style={{ color: '#8a93a6', fontSize: '0.72rem' }}>
            {layoutMode === 'zones'
              ? zoneByNeuron.get(hover.n.id)?.label || 'Peripheral · not yet assembled'
              : hover.n.department || 'Unassigned'}
          </div>
          {(
            <div
              data-testid="neuron-hover-content"
              style={{
                color: '#b8c2d2',
                fontSize: '0.72rem',
                lineHeight: 1.45,
                marginTop: 7,
                paddingTop: 7,
                borderTop: '1px solid #2a3446',
                maxHeight: 190,
                overflow: 'hidden',
                whiteSpace: 'pre-wrap',
              }}
            >
              {hoverContent?.id === hover.n.id ? hoverContent.text : 'Loading memory…'}
            </div>
          )}
        </div>
      )}
      {assemblyHover && (
        <div style={{
          position: 'fixed', left: assemblyHover.sx + 14, top: assemblyHover.sy + 14,
          zIndex: 30, pointerEvents: 'none', background: 'rgba(14,18,26,0.94)',
          border: `1px solid ${assemblyHover.zone.color}`, borderRadius: 8,
          padding: '7px 10px', color: '#e8edf7', fontSize: '0.76rem', maxWidth: 280,
        }}>
          <div style={{ fontWeight: 600 }}>{assemblyHover.zone.label}</div>
          <div style={{ color: '#9ca8ba', fontSize: '0.72rem' }}>
            {assemblyHover.zone.neuronIds.length} neurons · click to inspect
          </div>
        </div>
      )}

    </div>
  );
}

const overlayCenter: React.CSSProperties = { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10 };
const panel: React.CSSProperties = {
  position: 'absolute', top: 16, left: 16, zIndex: 40, width: 264, boxSizing: 'border-box',
  background: 'rgba(14,18,26,0.82)', border: '1px solid #232c3c', borderRadius: 12,
  padding: '14px 16px', backdropFilter: 'blur(8px)',
};
const desktopPanel: React.CSSProperties = { ...panel, left: 18, right: 'auto', top: 78 };
const backBtn: React.CSSProperties = {
  marginTop: 12, width: '100%', background: '#1e3a5f', color: '#cfe0ff',
  border: '1px solid #2f5a8f', borderRadius: 6, padding: '7px 0', cursor: 'pointer', fontSize: '0.78rem',
};
const focusCard: React.CSSProperties = {
  position: 'absolute', top: 16, right: 16, zIndex: 20, maxWidth: 300,
  background: 'rgba(14,18,26,0.86)', border: '1px solid #2f5a8f', borderRadius: 12, padding: '14px 16px',
  backdropFilter: 'blur(8px)',
};
const desktopFocusCard: React.CSSProperties = { ...focusCard, top: 150 };
