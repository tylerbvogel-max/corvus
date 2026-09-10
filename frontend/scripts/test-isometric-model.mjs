import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { layoutZones, sourceFootprints, reviewedFlows, flowCurve, project, unproject } from '../src/components/isometricModel.ts';
const root = new URL('../../', import.meta.url);
const model = JSON.parse(readFileSync(new URL('architecture/conformance.json', root), 'utf8'));
const zones = model.memory_engine.zones;
const internal = zone => ['backend', 'cache'].includes(zone.boundary);

function contains(polygon, point) {
  const crosses = polygon.map((a, i) => {
    const b = polygon[(i + 1) % polygon.length];
    return (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x);
  });
  return crosses.every(n => n >= -1e-7) || crosses.every(n => n <= 1e-7);
}

test('project and inverse use one isometric coordinate plane', () => {
  for (const [u, v] of [[0, 0], [-4, 3], [7.25, -2.5]]) {
    const actual = unproject(project(u, v));
    assert.ok(Math.abs(actual.u - u) < 1e-10);
    assert.ok(Math.abs(actual.v - v) < 1e-10);
  }
});

for (const sized of [false, true]) {
  test(`perimeter contains every backend/cache face and excludes outside centers; sized=${sized}`, () => {
    const scene = layoutZones(zones, sized);
    for (const tile of scene.tiles) {
      if (internal(tile.zone)) {
        for (const point of tile.corners) assert.ok(contains(scene.perimeter, point), tile.zone.id);
      } else assert.equal(contains(scene.perimeter, tile), false, tile.zone.id);
    }
    for (const point of scene.perimeter) {
      assert.ok(point.x >= scene.left && point.x <= scene.left + scene.width);
      assert.ok(point.y >= scene.top && point.y <= scene.top + scene.height);
    }
    if (!sized) assert.ok(scene.tiles.every(tile => tile.scale === 1));
    else assert.ok(new Set(scene.tiles.map(tile => tile.scale)).size > 1);
  });
}

test('shared references are counted once in their own pool, never as exclusive zone footprint', () => {
  const make = (id, paths) => ({ id, name: id, boundary: 'backend', purpose: '', details: [], evidence_ok: true,
    evidence_results: paths.map(evidence => ({ evidence, exists: true })) });
  const result = sourceFootprints([
    make('a', ['shared.py::one', 'shared.py::two', 'a.py', 'a.py', 'README.md']),
    make('b', ['shared.py', 'b.tsx']),
  ]);
  assert.deepEqual(result.shared, ['shared.py']);
  assert.deepEqual(result.exclusive.get('a'), ['a.py']);
  assert.deepEqual(result.exclusive.get('b'), ['b.tsx']);
});

test('empty and missing evidence is not fabricated as measured volume', () => {
  assert.equal(layoutZones([]).perimeter.length, 0);
  assert.ok(!layoutZones([]).viewBox.includes('Infinity'));
  const one = { ...zones[0], evidence_results: [{ evidence: 'missing.py', exists: false }] };
  assert.deepEqual(sourceFootprints([one]).exclusive.get(one.id), []);
});

test('perimeter expands with extra backend zones rather than using fixed drawing corners', () => {
  const extra = Array.from({ length: 8 }, (_, i) => ({ ...zones.find(internal), id: `extra-${i}` }));
  const scene = layoutZones([...zones, ...extra]);
  for (const tile of scene.tiles.filter(tile => internal(tile.zone))) {
    assert.ok(tile.corners.every(point => contains(scene.perimeter, point)));
  }
});

test('flows connect present zones, carry valid source pointers, and curve without invalid coordinates', () => {
  const scene = layoutZones(zones);
  const flows = reviewedFlows(zones);
  assert.ok(flows.length >= 8);
  for (const flow of flows) {
    const from = scene.tiles.find(tile => tile.zone.id === flow.from), to = scene.tiles.find(tile => tile.zone.id === flow.to);
    assert.ok(from && to && from !== to);
    const curve = flowCurve(from, to);
    assert.ok(!/NaN|Infinity/.test(curve.path));
    for (const path of flow.evidence) assert.ok(existsSync(new URL(path, root)), path);
  }
  assert.deepEqual(reviewedFlows([]), []);
});
