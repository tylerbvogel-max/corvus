import { useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchGraph3D,
  fetchNeuron,
  fetchSemanticClusters,
  type Graph3DEdge,
  type Graph3DNode,
  type SemanticCluster,
} from '../api';
import type { NeuronDetail } from '../types';
import CarlosLabFrame, { LAB_CYAN, LAB_MAGENTA } from './CarlosLabFrame';

type NodeKind = 'root' | 'domain' | 'cluster' | 'neuron';
type EdgeKind = 'hierarchy' | 'local' | 'bridge';

interface VisualNode {
  id: string;
  neuronId: number | null;
  kind: NodeKind;
  label: string;
  domain: string | null;
  layer: number;
  invocations: number;
  utility: number;
  x: number;
  y: number;
  z: number;
  ux: number;
  uy: number;
  uz: number;
  size: number;
  color: string;
}

interface VisualEdge {
  a: string;
  b: string;
  kind: EdgeKind;
  weight: number;
}

interface BuiltGraph {
  nodes: VisualNode[];
  edges: VisualEdge[];
  adjacency: Map<string, Set<string>>;
  nodeById: Map<string, VisualNode>;
  bridges: number;
  local: number;
}

const COLORS = [
  '#ff4fd8', '#45e6ff', '#a8ff60', '#ffcc4d', '#b388ff',
  '#ff7a59', '#60a5fa', '#34d399', '#f472b6', '#facc15',
];
const LAYER_NAMES = ['Region', 'Role', 'Task', 'System', 'Decision', 'Output'];

function seededDirection(base: VisualNode, seed: number, spread: number) {
  const random = (salt: number) => {
    const value = Math.sin(seed + salt) * 43758.5453;
    return value - Math.floor(value);
  };
  let x = random(1) - 0.5;
  let y = random(2) - 0.5;
  let z = random(3) - 0.5;
  const dot = x * base.ux + y * base.uy + z * base.uz;
  x -= dot * base.ux;
  y -= dot * base.uy;
  z -= dot * base.uz;
  const magnitude = Math.hypot(x, y, z) || 1;
  x /= magnitude;
  y /= magnitude;
  z /= magnitude;
  const angle = spread * (0.35 + random(4) * 0.65);
  return {
    ux: base.ux * Math.cos(angle) + x * Math.sin(angle),
    uy: base.uy * Math.cos(angle) + y * Math.sin(angle),
    uz: base.uz * Math.cos(angle) + z * Math.sin(angle),
  };
}

function buildGraph(neurons: Graph3DNode[], sourceEdges: Graph3DEdge[]): BuiltGraph {
  const raw = neurons.filter(node => node.layer >= 0);
  const domainCounts = new Map<string, number>();
  for (const node of raw) {
    const domain = node.department || 'Unassigned';
    domainCounts.set(domain, (domainCounts.get(domain) || 0) + 1);
  }
  const domains = Array.from(domainCounts)
    .sort((a, b) => b[1] - a[1])
    .map(([name]) => name);
  const colorByDomain = new Map(
    domains.map((domain, index) => [domain, COLORS[index % COLORS.length]]),
  );

  const nodes: VisualNode[] = [];
  const edges: VisualEdge[] = [];
  const nodeById = new Map<string, VisualNode>();
  const add = (node: VisualNode) => {
    nodes.push(node);
    nodeById.set(node.id, node);
  };

  add({
    id: 'root', neuronId: null, kind: 'root', label: 'Corvus',
    domain: null, layer: -1, invocations: 0, utility: 1,
    x: 0, y: 0, z: 0, ux: 0, uy: 1, uz: 0,
    size: 1.8, color: LAB_MAGENTA,
  });

  domains.forEach((domain, index) => {
    const phi = Math.acos(1 - 2 * ((index + 0.5) / Math.max(1, domains.length)));
    const theta = Math.PI * (1 + Math.sqrt(5)) * index;
    const ux = Math.sin(phi) * Math.cos(theta);
    const uy = Math.cos(phi);
    const uz = Math.sin(phi) * Math.sin(theta);
    const count = domainCounts.get(domain) || 1;
    add({
      id: `domain:${domain}`, neuronId: null, kind: 'domain', label: domain,
      domain, layer: 0, invocations: 0, utility: 1,
      x: ux * 205, y: uy * 205, z: uz * 205, ux, uy, uz,
      size: Math.min(2, 0.8 + Math.sqrt(count) / 12),
      color: colorByDomain.get(domain)!,
    });
    edges.push({ a: 'root', b: `domain:${domain}`, kind: 'hierarchy', weight: 1 });
  });

  const grouped = new Map<string, Graph3DNode[]>();
  for (const node of raw) {
    const domain = node.department || 'Unassigned';
    const key = `${domain}::${node.layer}`;
    grouped.set(key, [...(grouped.get(key) || []), node]);
  }
  Array.from(grouped.entries()).forEach(([key, members], index) => {
    const [domain, layerText] = key.split('::');
    const layer = Number(layerText);
    const parent = nodeById.get(`domain:${domain}`);
    if (!parent) return;
    const direction = seededDirection(parent, index * 17 + layer, 0.42);
    add({
      id: `cluster:${key}`, neuronId: null, kind: 'cluster',
      label: `${LAYER_NAMES[layer] || `L${layer}`} · ${domain}`,
      domain, layer, invocations: 0, utility: 0.8,
      x: direction.ux * 355, y: direction.uy * 355, z: direction.uz * 355,
      ...direction,
      size: Math.min(1.5, 0.55 + Math.sqrt(members.length) / 8),
      color: colorByDomain.get(domain)!,
    });
    edges.push({
      a: `domain:${domain}`, b: `cluster:${key}`,
      kind: 'hierarchy', weight: 1,
    });
  });

  raw.forEach((node, index) => {
    const domain = node.department || 'Unassigned';
    const parent = nodeById.get(`cluster:${domain}::${node.layer}`);
    if (!parent) return;
    const direction = seededDirection(parent, node.id * 31 + index, 0.82);
    add({
      id: `neuron:${node.id}`, neuronId: node.id, kind: 'neuron',
      label: node.label, domain, layer: node.layer,
      invocations: node.invocations, utility: node.avg_utility,
      x: direction.ux * 560, y: direction.uy * 560, z: direction.uz * 560,
      ...direction,
      size: 0.55 + Math.min(0.75, Math.log10(node.invocations + 1) * 0.45),
      color: colorByDomain.get(domain)!,
    });
    edges.push({
      a: `cluster:${domain}::${node.layer}`, b: `neuron:${node.id}`,
      kind: 'hierarchy', weight: 1,
    });
  });

  const rawById = new Map(raw.map(node => [node.id, node]));
  const ranked = [...sourceEdges].sort((a, b) => b.weight - a.weight);
  let bridges = 0;
  let local = 0;
  for (const edge of ranked) {
    const source = rawById.get(edge.source);
    const target = rawById.get(edge.target);
    if (!source || !target) continue;
    const crossDomain = (
      (source.department || 'Unassigned') !==
      (target.department || 'Unassigned')
    );
    const kind: EdgeKind = crossDomain || edge.edge_type === 'pyramidal'
      ? 'bridge'
      : 'local';
    if (kind === 'bridge' && bridges >= 70) continue;
    if (kind === 'local' && local >= 110) continue;
    edges.push({
      a: `neuron:${source.id}`, b: `neuron:${target.id}`,
      kind, weight: edge.weight,
    });
    if (kind === 'bridge') bridges += 1;
    else local += 1;
  }

  const adjacency = new Map<string, Set<string>>();
  for (const edge of edges) {
    if (!adjacency.has(edge.a)) adjacency.set(edge.a, new Set());
    if (!adjacency.has(edge.b)) adjacency.set(edge.b, new Set());
    adjacency.get(edge.a)!.add(edge.b);
    adjacency.get(edge.b)!.add(edge.a);
  }
  return { nodes, edges, adjacency, nodeById, bridges, local };
}

function neighborhood(start: string, adjacency: Map<string, Set<string>>) {
  const seen = new Set([start]);
  let frontier = [start];
  for (let depth = 0; depth < 2; depth += 1) {
    const next: string[] = [];
    for (const current of frontier) {
      adjacency.get(current)?.forEach(id => {
        if (!seen.has(id)) {
          seen.add(id);
          next.push(id);
        }
      });
    }
    frontier = next;
  }
  return seen;
}

export default function NexusLabPage() {
  const [graphData, setGraphData] = useState<{ neurons: Graph3DNode[]; edges: Graph3DEdge[] } | null>(null);
  const [clusters, setClusters] = useState<SemanticCluster[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showBridges, setShowBridges] = useState(false);
  const [showLocal, setShowLocal] = useState(false);
  const [showCommunities, setShowCommunities] = useState(false);
  const [autoOrbit, setAutoOrbit] = useState(true);
  const [hover, setHover] = useState<{ label: string; detail: string; x: number; y: number } | null>(null);
  const [detail, setDetail] = useState<NeuronDetail | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef({ yaw: 0.5, pitch: -0.24, distance: 1500, focal: 1000 });
  const togglesRef = useRef({ showBridges, showLocal, showCommunities, autoOrbit });
  togglesRef.current = { showBridges, showLocal, showCommunities, autoOrbit };

  useEffect(() => {
    Promise.all([
      fetchGraph3D(0.15, 900, 4),
      fetchSemanticClusters(0.3, 3),
    ]).then(([graph, communityPayload]) => {
      setGraphData(graph);
      setClusters(communityPayload.clusters);
    }).catch(exc => setError(exc instanceof Error ? exc.message : String(exc)));
  }, []);

  const built = useMemo(
    () => graphData ? buildGraph(graphData.neurons, graphData.edges) : null,
    [graphData],
  );

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !built) return;
    const canvas = document.createElement('canvas');
    canvas.className = 'nexus-lab__canvas';
    container.appendChild(canvas);
    const context = canvas.getContext('2d');
    if (!context) return;
    const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
    let width = 0;
    let height = 0;
    let animation = 0;
    let dragging = false;
    let lastX = 0;
    let lastY = 0;
    let hoverId: string | null = null;

    const resize = () => {
      width = container.clientWidth;
      height = Math.max(620, container.clientHeight);
      canvas.width = width * pixelRatio;
      canvas.height = height * pixelRatio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    const project = (node: VisualNode) => {
      const camera = cameraRef.current;
      const phase = node.x * 0.013 + node.y * 0.017 + node.z * 0.011;
      const activity = node.kind === 'neuron'
        ? 0.5 + Math.min(5, Math.log10(node.invocations + 1) + node.utility * 2)
        : node.kind === 'domain' ? 3 : 1.5;
      const time = Date.now() * 0.00035;
      const x = node.x + Math.sin(time + phase) * activity;
      const y = node.y + Math.cos(time * 0.8 + phase) * activity;
      const z = node.z + Math.sin(time * 0.6 + phase) * activity;
      const cy = Math.cos(camera.yaw);
      const sy = Math.sin(camera.yaw);
      const x1 = x * cy - z * sy;
      const z1 = x * sy + z * cy;
      const cp = Math.cos(camera.pitch);
      const sp = Math.sin(camera.pitch);
      const y1 = y * cp - z1 * sp;
      const z2 = y * sp + z1 * cp;
      const viewZ = Math.max(1, z2 + camera.distance);
      const scale = camera.focal / viewZ;
      return {
        x: width / 2 + x1 * scale,
        y: height / 2 - y1 * scale,
        z: viewZ,
        scale,
      };
    };

    const radius = (node: VisualNode, scale: number) => {
      const base = node.kind === 'root' ? 13
        : node.kind === 'domain' ? 9
          : node.kind === 'cluster' ? 5.5 : 3;
      return Math.max(0.8, base * node.size * scale * 0.88);
    };

    const draw = () => {
      const gradient = context.createRadialGradient(
        width * 0.5, height * 0.45, 40,
        width * 0.5, height * 0.5, Math.max(width, height) * 0.8,
      );
      gradient.addColorStop(0, '#17102a');
      gradient.addColorStop(0.42, '#0a1425');
      gradient.addColorStop(1, '#05070d');
      context.fillStyle = gradient;
      context.fillRect(0, 0, width, height);

      const focus = hoverId ? neighborhood(hoverId, built.adjacency) : null;
      const projected = built.nodes
        .map(node => ({ node, point: project(node) }))
        .sort((a, b) => b.point.z - a.point.z);
      const pointById = new Map(projected.map(item => [item.node.id, item.point]));

      context.globalCompositeOperation = 'lighter';
      for (const edge of built.edges) {
        if (edge.kind === 'bridge' && !togglesRef.current.showBridges) continue;
        if (edge.kind === 'local' && !togglesRef.current.showLocal) continue;
        const a = pointById.get(edge.a);
        const b = pointById.get(edge.b);
        if (!a || !b) continue;
        const inFocus = !!focus?.has(edge.a) && !!focus?.has(edge.b);
        context.strokeStyle = edge.kind === 'bridge'
          ? inFocus ? LAB_CYAN : 'rgba(69,230,255,0.22)'
          : edge.kind === 'local'
            ? inFocus ? LAB_MAGENTA : 'rgba(255,79,216,0.12)'
            : inFocus ? 'rgba(255,255,255,0.38)' : 'rgba(150,170,210,0.08)';
        context.lineWidth = inFocus ? 1.2 : edge.kind === 'hierarchy' ? 0.45 : 0.65;
        context.beginPath();
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        context.stroke();
      }

      if (togglesRef.current.showCommunities) {
        for (const cluster of clusters) {
          const members = cluster.neuron_ids
            .map(id => pointById.get(`neuron:${id}`))
            .filter((point): point is NonNullable<typeof point> => !!point);
          if (members.length < 2) continue;
          const x = members.reduce((sum, point) => sum + point.x, 0) / members.length;
          const y = members.reduce((sum, point) => sum + point.y, 0) / members.length;
          context.strokeStyle = 'rgba(168,255,96,0.22)';
          context.lineWidth = 0.55;
          members.forEach(point => {
            context.beginPath();
            context.moveTo(x, y);
            context.lineTo(point.x, point.y);
            context.stroke();
          });
          context.fillStyle = '#a8ff60';
          context.beginPath();
          context.arc(x, y, 3, 0, Math.PI * 2);
          context.fill();
          context.globalCompositeOperation = 'source-over';
          context.font = '10px system-ui';
          context.textAlign = 'center';
          context.fillStyle = 'rgba(220,255,190,0.88)';
          context.fillText(cluster.suggested_label.slice(0, 34), x, y - 9);
          context.globalCompositeOperation = 'lighter';
        }
      }

      for (const { node, point } of projected) {
        const inFocus = focus ? focus.has(node.id) : true;
        const nodeRadius = radius(node, point.scale);
        if (node.kind !== 'neuron' || inFocus) {
          const halo = context.createRadialGradient(
            point.x, point.y, 0, point.x, point.y, nodeRadius * 4,
          );
          halo.addColorStop(0, `${node.color}42`);
          halo.addColorStop(1, `${node.color}00`);
          context.fillStyle = halo;
          context.beginPath();
          context.arc(point.x, point.y, nodeRadius * 4, 0, Math.PI * 2);
          context.fill();
        }
        context.fillStyle = inFocus ? node.color : `${node.color}28`;
        context.beginPath();
        context.arc(point.x, point.y, nodeRadius, 0, Math.PI * 2);
        context.fill();
        if (node.kind === 'root' || node.kind === 'domain' || node.id === hoverId) {
          context.globalCompositeOperation = 'source-over';
          context.fillStyle = inFocus ? '#f2f7ff' : 'rgba(242,247,255,0.28)';
          context.font = `${node.kind === 'root' ? 13 : 11}px system-ui`;
          context.textAlign = 'center';
          context.fillText(node.label.slice(0, 30), point.x, point.y - nodeRadius - 6);
          context.globalCompositeOperation = 'lighter';
        }
      }
      context.globalCompositeOperation = 'source-over';

      if (togglesRef.current.autoOrbit) {
        cameraRef.current.yaw += 0.0014;
        cameraRef.current.pitch = -0.24 + Math.sin(Date.now() * 0.00035) * 0.07;
      }
      animation = requestAnimationFrame(draw);
    };

    const hitTest = (x: number, y: number) => {
      let found: VisualNode | null = null;
      let distance = Number.POSITIVE_INFINITY;
      for (const node of built.nodes) {
        const point = project(node);
        const delta = Math.hypot(point.x - x, point.y - y);
        if (delta <= Math.max(7, radius(node, point.scale) + 3) && delta < distance) {
          found = node;
          distance = delta;
        }
      }
      return found;
    };

    const localPoint = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };
    const onDown = (event: MouseEvent) => {
      dragging = true;
      lastX = event.clientX;
      lastY = event.clientY;
      setAutoOrbit(false);
      canvas.style.cursor = 'grabbing';
    };
    const onUp = () => {
      dragging = false;
      canvas.style.cursor = 'grab';
    };
    const onMove = (event: MouseEvent) => {
      if (dragging) {
        cameraRef.current.yaw += (event.clientX - lastX) * 0.005;
        cameraRef.current.pitch = Math.max(
          -Math.PI / 2,
          Math.min(Math.PI / 2, cameraRef.current.pitch + (event.clientY - lastY) * 0.005),
        );
        lastX = event.clientX;
        lastY = event.clientY;
        return;
      }
      const point = localPoint(event);
      const node = hitTest(point.x, point.y);
      hoverId = node?.id || null;
      setHover(node ? {
        label: node.label,
        detail: node.kind === 'neuron'
          ? `Neuron · ${node.domain} · L${node.layer} · used ${node.invocations}×`
          : `${node.kind} · ${node.domain || 'central corpus'}`,
        x: point.x,
        y: point.y,
      } : null);
    };
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      cameraRef.current.distance = Math.max(
        450,
        Math.min(3200, cameraRef.current.distance + event.deltaY * 1.2),
      );
    };
    const onClick = (event: MouseEvent) => {
      const point = localPoint(event);
      const node = hitTest(point.x, point.y);
      if (node?.neuronId != null) {
        void fetchNeuron(node.neuronId).then(setDetail);
      }
    };

    canvas.addEventListener('mousedown', onDown);
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('click', onClick);
    window.addEventListener('mouseup', onUp);
    draw();

    return () => {
      cancelAnimationFrame(animation);
      observer.disconnect();
      canvas.removeEventListener('mousedown', onDown);
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('click', onClick);
      window.removeEventListener('mouseup', onUp);
      canvas.remove();
    };
  }, [built, clusters]);

  return (
    <CarlosLabFrame
      title="Nexus Graph"
      subtitle="A deterministic projected-shell lens over the current active Corvus graph."
    >
      <div className="carlos-lab__toolbar nexus-lab__toolbar">
        <span>Drag to rotate · scroll to zoom · hover to trace two hops · click for detail</span>
        <button
          className={showBridges ? 'active' : ''}
          onClick={() => setShowBridges(value => !value)}
        >
          Bridges {built ? `(${built.bridges})` : ''}
        </button>
        <button
          className={showLocal ? 'active' : ''}
          onClick={() => setShowLocal(value => !value)}
        >
          Local {built ? `(${built.local})` : ''}
        </button>
        <button
          className={showCommunities ? 'active community' : ''}
          onClick={() => setShowCommunities(value => !value)}
        >
          Leiden {clusters.length ? `(${clusters.length})` : ''}
        </button>
        <button
          className={autoOrbit ? 'active' : ''}
          onClick={() => setAutoOrbit(value => !value)}
        >
          {autoOrbit ? 'Pause orbit' : 'Resume orbit'}
        </button>
        <button onClick={() => {
          cameraRef.current = { yaw: 0.5, pitch: -0.24, distance: 1500, focal: 1000 };
          setAutoOrbit(true);
        }}>
          Reset
        </button>
      </div>

      {error && <div className="carlos-lab__error">{error}</div>}
      <div className="nexus-lab" ref={containerRef} data-testid="nexus-lab-canvas">
        {!built && !error && <div className="nexus-lab__loading">Assembling the nexus…</div>}
        {hover && (
          <div className="nexus-lab__tooltip" style={{ left: hover.x + 14, top: hover.y + 5 }}>
            <strong>{hover.label}</strong>
            <span>{hover.detail}</span>
          </div>
        )}
      </div>
      {built && (
        <footer className="nexus-lab__stats">
          <span>{built.nodes.length} projected nodes</span>
          <span>{built.edges.length} available edges</span>
          <span>{built.bridges} cross-region bridges</span>
          <span>{clusters.length} deterministic Leiden communities</span>
        </footer>
      )}

      {detail && (
        <div className="nexus-detail" role="dialog" aria-modal="true" onClick={() => setDetail(null)}>
          <article onClick={event => event.stopPropagation()}>
            <button onClick={() => setDetail(null)}>Close ×</button>
            <small>{detail.node_type} · {detail.department || 'general'} · #{detail.id}</small>
            <h3>{detail.label}</h3>
            {detail.summary && <p className="nexus-detail__summary">{detail.summary}</p>}
            {detail.content && <pre>{detail.content}</pre>}
            <footer>used {detail.invocations}× · utility {detail.avg_utility.toFixed(2)}</footer>
          </article>
        </div>
      )}
    </CarlosLabFrame>
  );
}
