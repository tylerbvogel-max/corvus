export type Point = { x: number; y: number };
export type Zone = {
  id: string; name: string; boundary: string; purpose: string; details: string[];
  evidence_ok: boolean; evidence_results: { evidence: string; exists: boolean; ok?: boolean }[];
};
export type Flow = { id: string; from: string; to: string; label: string; transport: string; why: string; evidence: string[]; loops: string[] };
export type Tile = { zone: Zone; index: number; x: number; y: number; scale: number; corners: Point[] };

const X = 116;
const Y = 58;
export const project = (u: number, v: number): Point => ({ x: 320 + (u - v) * X, y: 150 + (u + v) * Y });
export const unproject = (point: Point) => ({ u: ((point.x - 320) / X + (point.y - 150) / Y) / 2, v: ((point.y - 150) / Y - (point.x - 320) / X) / 2 });

export function sourceFootprints(zones: Zone[]) {
  const references = new Map(zones.map(zone => [zone.id, new Set(zone.evidence_results
    .filter(item => item.exists && /\.(py|tsx?|jsx?|mjs)(::|$)/.test(item.evidence))
    .map(item => item.evidence.split('::')[0]))]));
  const owners = new Map<string, Set<string>>();
  references.forEach((paths, id) => paths.forEach(path => {
    const ids = owners.get(path) ?? new Set<string>(); ids.add(id); owners.set(path, ids);
  }));
  const shared = [...owners].filter(([, ids]) => ids.size > 1).map(([path]) => path).sort();
  const exclusive = new Map([...references].map(([id, paths]) => [id, [...paths].filter(path => owners.get(path)?.size === 1).sort()]));
  return { exclusive, shared };
}

export function layoutZones(zones: Zone[], sized = true) {
  const footprint = sourceFootprints(zones);
  const maximum = Math.max(1, ...[...footprint.exclusive.values()].map(paths => paths.length));
  const internal = zones.filter(zone => ['backend', 'cache'].includes(zone.boundary));
  const external = zones.filter(zone => !internal.includes(zone));
  const tiles: Tile[] = zones.map((zone, index) => {
    const inside = internal.indexOf(zone);
    const outside = external.indexOf(zone);
    const position = inside >= 0 ? project(2 + (inside % 2) * 2.2, Math.floor(inside / 2) * 2.2)
      : outside === 0 ? project(-1.4, .2) : project(.4, 4.4 + (outside - 1) * 2.2);
    const scale = sized ? Math.max(.45, Math.sqrt((footprint.exclusive.get(zone.id)?.length ?? 0) / maximum)) : 1;
    const { x, y } = position;
    // Include every visible face, not just the top diamond.
    const corners = [[0, -58], [116, 0], [116, 20], [0, 78], [-116, 20], [-116, 0]]
      .map(([dx, dy]) => ({ x: x + dx * scale, y: y + dy * scale }));
    return { zone, index, x, y, scale, corners };
  });
  const points = tiles.filter(tile => internal.includes(tile.zone)).flatMap(tile => tile.corners).map(unproject);
  // Bound in the SAME plane as the slabs, then project. Padding is in plane units.
  const perimeter = points.length ? (() => {
    const u0 = Math.min(...points.map(p => p.u)) - .35, u1 = Math.max(...points.map(p => p.u)) + .35;
    const v0 = Math.min(...points.map(p => p.v)) - .35, v1 = Math.max(...points.map(p => p.v)) + .35;
    return [project(u0, v0), project(u1, v0), project(u1, v1), project(u0, v1)];
  })() : [];
  const all = [...tiles.flatMap(tile => tile.corners), ...perimeter];
  const left = Math.min(0, ...all.map(p => p.x)) - 120;
  const top = Math.min(0, ...all.map(p => p.y)) - 90;
  const width = Math.max(1, ...all.map(p => p.x)) - left + 120;
  const height = Math.max(1, ...all.map(p => p.y)) - top + 140;
  return { tiles, perimeter, footprint, viewBox: `${left} ${top} ${width} ${height}`, left, top, width, height };
}

export function reviewedFlows(zones: Zone[]): Flow[] {
  const ids = new Set(zones.map(zone => zone.id));
  const uniqueBoundary = (boundary: string) => { const found = zones.filter(zone => zone.boundary === boundary); return found.length === 1 ? found[0].id : ''; };
  const db = uniqueBoundary('datastore'), cache = uniqueBoundary('cache');
  const flows: Flow[] = [
    { id: 'capture', from: 'harness-boundary', to: 'ingest-governance', label: 'Captured episodes', transport: 'JSONL / scheduled ingestion', why: 'Harness evidence is captured to files; bounded distillation consumes eligible input. This is not a direct synchronous harness-to-distiller call.', evidence: ['harness/claude-code/episode_hook.py', 'backend/app/services/distiller.py'], loops: ['capture'] },
    { id: 'request', from: 'harness-boundary', to: 'recall-engine', label: 'Recall request', transport: 'HTTP / MCP client', why: 'Harness clients request relevant memory through the backend boundary.', evidence: ['harness/claude-code/mind_mcp_server.py', 'backend/app/routers/recall.py'], loops: ['recall'] },
    { id: 'delivery', from: 'recall-engine', to: 'harness-boundary', label: 'Assembled context', transport: 'Response / injection', why: 'Retrieved evidence returns to the harness for context delivery, not permission to mutate memory.', evidence: ['harness/claude-code/memory_inject_hook.py', 'backend/app/routers/recall.py'], loops: ['recall'] },
    { id: 'write', from: 'ingest-governance', to: db, label: 'Governed writes', transport: 'Database transaction', why: 'Evidence and authority gates route durable writes or review proposals through governed persistence.', evidence: ['backend/app/services/lesson_store.py', 'backend/app/services/write_gate.py', 'backend/app/services/action_bus.py'], loops: ['capture'] },
    { id: 'read', from: db, to: 'recall-engine', label: 'Canonical reads', transport: 'SQL / retrieval', why: 'Recall reads canonical graph state. The arrow denotes data movement, not which side initiates SQL.', evidence: ['backend/app/services/recall_lanes.py'], loops: ['recall'] },
    { id: 'rebuild', from: db, to: cache, label: 'Derived indexes', transport: 'Revision-checked rebuild', why: 'Process-local indexes derive from canonical state; they do not replace PostgreSQL authority.', evidence: ['backend/app/services/semantic_prefilter.py', 'backend/app/services/adjacency_cache.py'], loops: ['recall'] },
    { id: 'lookup', from: cache, to: 'recall-engine', label: 'Candidate lookup', transport: 'Process-local read', why: 'Recall consumes derived matrices and adjacency data within the backend process.', evidence: ['backend/app/services/semantic_prefilter.py', 'backend/app/services/adjacency_cache.py'], loops: ['recall'] },
    { id: 'maintenance-read', from: db, to: 'maintenance-projection', label: 'Inspect memory', transport: 'Canonical-state read', why: 'Maintenance examines existing memories and their evidence before proposing or applying governed changes.', evidence: ['backend/app/services/mind_janitors.py'], loops: ['maintenance'] },
    { id: 'maintenance-write', from: 'maintenance-projection', to: db, label: 'Governed maintenance', transport: 'Governed persistence', why: 'Maintenance changes remain subject to their existing writer and review boundaries; this is not unrestricted database authority.', evidence: ['backend/app/services/mind_janitors.py', 'backend/app/services/reconsolidation/review.py'], loops: ['maintenance'] },
    { id: 'projection', from: 'maintenance-projection', to: 'harness-boundary', label: 'Skills / policy', transport: 'Filesystem projection', why: 'Trusted memory is projected into harness-readable artifacts. The files are derivatives, not another canonical memory store.', evidence: ['backend/app/services/skill_compiler.py'], loops: ['maintenance'] },
  ];
  return flows.filter(flow => ids.has(flow.from) && ids.has(flow.to));
}

export function flowCurve(from: Tile, to: Tile) {
  const dx = to.x - from.x, dy = to.y - from.y;
  const clip = (tile: Tile) => 1.13 / (Math.abs(dx) / (116 * tile.scale) + Math.abs(dy) / (58 * tile.scale));
  const a = clip(from), b = clip(to);
  const start = { x: from.x + dx * a, y: from.y + dy * a };
  const end = { x: to.x - dx * b, y: to.y - dy * b };
  const length = Math.hypot(dx, dy) || 1;
  // Reciprocal directions bend onto opposite sides rather than overwrite.
  const control = { x: (start.x + end.x) / 2 - dy / length * 55, y: (start.y + end.y) / 2 + dx / length * 55 };
  return { path: `M${start.x},${start.y} Q${control.x},${control.y} ${end.x},${end.y}`,
    label: { x: .25 * start.x + .5 * control.x + .25 * end.x, y: .25 * start.y + .5 * control.y + .25 * end.y } };
}
