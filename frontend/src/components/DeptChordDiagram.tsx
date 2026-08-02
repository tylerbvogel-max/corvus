import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { fetchDeptChord } from '../api/knowledge_graph';
import type { DeptChordEntry } from '../types'
import { DEPT_COLORS } from '../constants'
import { escapeHtml } from '../utils'

const LAYER_NAMES = ['Department', 'Role', 'Task', 'System', 'Decision', 'Output'];

interface Connection {
  name: string;
  department: string;
  weight: number;
  edgeCount: number;
}

interface Selection {
  name: string;
  department: string;
  connections: Connection[];
}

export default function DeptChordDiagram() {
  const containerRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [data, setData] = useState<DeptChordEntry[] | null>(null);
  const [error, setError] = useState('');
  const [layer, setLayer] = useState(1);
  const [minWeight, setMinWeight] = useState(0.15);
  const [loading, setLoading] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);

  // Shared refs so the D3 click handler can access current data
  const drawDataRef = useRef<{
    names: string[];
    nameDept: Map<string, string>;
    matrix: number[][];
    countMatrix: number[][];
    chords: d3.Chords;
  } | null>(null);

  useEffect(() => {
    setError('');
    setLoading(true);
    setSelection(null);
    fetchDeptChord(layer, minWeight)
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [layer, minWeight]);

  useEffect(() => {
    if (!data || data.length === 0 || !containerRef.current || !svgRef.current) {
      if (svgRef.current) d3.select(svgRef.current).selectAll('*').remove();
      drawDataRef.current = null;
      return;
    }

    function handleArcClick(idx: number) {
      const dd = drawDataRef.current;
      if (!dd) return;
      const { names, nameDept, matrix, countMatrix, chords } = dd;

      // Find all connected indices
      const connected = new Set<number>();
      for (const c of chords) {
        if (c.source.index === idx) connected.add(c.target.index);
        if (c.target.index === idx) connected.add(c.source.index);
      }

      const connections: Connection[] = [];
      for (const ci of connected) {
        if (ci === idx) continue;
        const w = matrix[idx][ci] || matrix[ci][idx] || 0;
        const ec = countMatrix[idx][ci] || countMatrix[ci][idx] || 0;
        if (w > 0 || ec > 0) {
          connections.push({
            name: names[ci],
            department: nameDept.get(names[ci]) ?? '',
            weight: w,
            edgeCount: ec,
          });
        }
      }
      // Also include self-connections if they exist
      if (matrix[idx][idx] > 0) {
        connections.push({
          name: names[idx] + ' (self)',
          department: nameDept.get(names[idx]) ?? '',
          weight: matrix[idx][idx],
          edgeCount: countMatrix[idx][idx],
        });
      }

      connections.sort((a, b) => b.weight - a.weight);

      setSelection({
        name: names[idx],
        department: nameDept.get(names[idx]) ?? '',
        connections,
      });
    }

    function draw() {
      const el = containerRef.current!;
      const svg = d3.select(svgRef.current!);
      svg.selectAll('*').remove();

      const w = el.clientWidth;
      const h = el.clientHeight > 100 ? el.clientHeight : w;
      const size = Math.min(w, h);

      if (size < 50) return;

      // Build department lookup first so we can sort by department
      const nameDept = new Map<string, string>();
      for (const d of data!) {
        if (d.source_department) nameDept.set(d.source_dept, d.source_department);
        if (d.target_department) nameDept.set(d.target_dept, d.target_department);
      }

      // Sort names grouped by department, then alphabetically within each department
      const names = [...new Set(data!.flatMap(d => [d.source_dept, d.target_dept]))]
        .sort((a, b) => {
          const deptA = nameDept.get(a) ?? '';
          const deptB = nameDept.get(b) ?? '';
          if (deptA !== deptB) return deptA.localeCompare(deptB);
          return a.localeCompare(b);
        });
      const n = names.length;
      const nameIndex = new Map(names.map((r, i) => [r, i]));

      const labelMargin = n > 30 ? 90 : 120;
      const outerRadius = (size / 2) - labelMargin;
      const innerRadius = outerRadius - 20;

      if (outerRadius < 30) return;

      svg.attr('viewBox', `0 0 ${size} ${size}`)
        .style('width', '100%')
        .style('height', '100%')
        .style('max-width', `${size}px`)
        .style('max-height', `${size}px`)
        .style('display', 'block')
        .style('margin', '0 auto')
        .style('overflow', 'visible');

      const g = svg.append('g')
        .attr('transform', `translate(${size / 2},${size / 2})`);

      const matrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
      const countMatrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));

      for (const d of data!) {
        const si = nameIndex.get(d.source_dept)!;
        const ti = nameIndex.get(d.target_dept)!;
        matrix[si][ti] += d.total_weight;
        countMatrix[si][ti] += d.edge_count;
        if (si !== ti) {
          matrix[ti][si] += d.total_weight;
          countMatrix[ti][si] += d.edge_count;
        }
      }

      const chord = d3.chord().padAngle(n > 30 ? 0.01 : 0.03).sortGroups(null).sortSubgroups(d3.descending);
      const chords = chord(matrix);

      // Store for click handler
      drawDataRef.current = { names, nameDept, matrix, countMatrix, chords };

      const arc = d3.arc<d3.ChordGroup>()
        .innerRadius(innerRadius)
        .outerRadius(outerRadius);

      const ribbon = d3.ribbon<d3.Chord, d3.ChordSubgroup>()
        .radius(innerRadius);

      const color = (i: number) => {
        const dept = nameDept.get(names[i]);
        return dept ? (DEPT_COLORS[dept] ?? '#c8d0dc') : '#c8d0dc';
      };

      // Tooltip
      const tooltip = d3.select(el)
        .selectAll<HTMLDivElement, unknown>('.chord-tooltip')
        .data([null])
        .join('div')
        .attr('class', 'chord-tooltip')
        .style('position', 'absolute')
        .style('pointer-events', 'none')
        .style('background', 'var(--bg-card)')
        .style('border', '1px solid var(--border)')
        .style('border-radius', '6px')
        .style('padding', '6px 10px')
        .style('font-size', '0.75rem')
        .style('color', 'var(--text)')
        .style('opacity', 0)
        .style('z-index', '10');

      // Arcs
      const arcs = g.append('g')
        .selectAll('path')
        .data(chords.groups)
        .join('path')
        .attr('d', arc as any)
        .attr('fill', d => color(d.index))
        .attr('stroke', 'var(--bg)')
        .style('cursor', 'pointer');

      // Arc labels (hidden by default, shown on hover) — upright, word-wrapped
      const labelRadius = outerRadius + 8;
      const fontSize = n > 30 ? 10 : 12;
      const maxCharsPerLine = n > 30 ? 16 : 20;

      function wrapName(name: string): string[] {
        if (name.length <= maxCharsPerLine) return [name];
        const words = name.split(/[\s/—–-]+/);
        const lines: string[] = [];
        let cur = '';
        for (const word of words) {
          if (cur && (cur + ' ' + word).length > maxCharsPerLine) {
            lines.push(cur);
            cur = word;
          } else {
            cur = cur ? cur + ' ' + word : word;
          }
        }
        if (cur) lines.push(cur);
        return lines.length > 0 ? lines : [name];
      }

      const labels = g.append('g')
        .selectAll<SVGGElement, d3.ChordGroup>('g')
        .data(chords.groups)
        .join('g')
        .each(function (d) {
          (d as any).angle = (d.startAngle + d.endAngle) / 2;
          const angle = (d as any).angle as number;
          const x = Math.cos(angle - Math.PI / 2) * labelRadius;
          const y = Math.sin(angle - Math.PI / 2) * labelRadius;
          const anchor = angle > Math.PI ? 'end' : 'start';
          const lines = wrapName(names[d.index]);
          const lineHeight = fontSize * 1.25;
          const yOffset = -((lines.length - 1) * lineHeight) / 2;

          const text = d3.select(this).append('text')
            .attr('x', x)
            .attr('y', y)
            .attr('text-anchor', anchor)
            .attr('fill', 'var(--text)')
            .attr('font-size', `${fontSize}px`);

          lines.forEach((line, i) => {
            text.append('tspan')
              .attr('x', x)
              .attr('dy', i === 0 ? `${yOffset}px` : `${lineHeight}px`)
              .text(line);
          });
        })
        .style('opacity', 0)
        .style('pointer-events', 'none');

      // Ribbons
      const ribbons = g.append('g')
        .selectAll('path')
        .data(chords)
        .join('path')
        .attr('d', ribbon as any)
        .attr('fill', d => d3.color(color(d.source.index))!.copy({ opacity: 0.4 }).formatRgb())
        .attr('stroke', 'none');

      function connectedIndices(idx: number): Set<number> {
        const s = new Set<number>([idx]);
        for (const c of chords) {
          if (c.source.index === idx) s.add(c.target.index);
          if (c.target.index === idx) s.add(c.source.index);
        }
        return s;
      }

      // Hover arc
      arcs
        .on('mouseenter', (event, d) => {
          const connected = connectedIndices(d.index);
          ribbons.style('opacity', c =>
            c.source.index === d.index || c.target.index === d.index ? 1 : 0.1
          );
          labels.style('opacity', l => connected.has(l.index) ? 1 : 0);
          const name = names[d.index];
          const dept = nameDept.get(name) ?? '';
          tooltip.html(`<strong>${escapeHtml(name)}</strong>${dept ? `<br><span style="opacity:0.6">${escapeHtml(dept)}</span>` : ''}<br><span style="opacity:0.5">Click for details</span>`)
            .style('opacity', 1)
            .style('left', `${event.offsetX + 12}px`)
            .style('top', `${event.offsetY - 10}px`);
        })
        .on('mousemove', (event) => {
          tooltip.style('left', `${event.offsetX + 12}px`).style('top', `${event.offsetY - 10}px`);
        })
        .on('mouseleave', () => {
          ribbons.style('opacity', 1);
          labels.style('opacity', 0);
          tooltip.style('opacity', 0);
        })
        .on('click', (_event, d) => {
          handleArcClick(d.index);
        });

      // Hover ribbon
      ribbons
        .on('mouseenter', (event, d) => {
          const si = d.source.index;
          const ti = d.target.index;
          const w = matrix[si][ti].toFixed(2);
          const ec = countMatrix[si][ti];
          tooltip.html(`${escapeHtml(names[si])} ↔ ${escapeHtml(names[ti])}<br>${w} weight (${ec} edges)`)
            .style('opacity', 1)
            .style('left', `${event.offsetX + 12}px`)
            .style('top', `${event.offsetY - 10}px`);
        })
        .on('mousemove', (event) => {
          tooltip.style('left', `${event.offsetX + 12}px`).style('top', `${event.offsetY - 10}px`);
        })
        .on('mouseleave', () => tooltip.style('opacity', 0));
    }

    draw();

    const ro = new ResizeObserver(draw);
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, [data]);

  if (error) return <div className="error-msg">{error}</div>;

  const deptColor = (dept: string) => DEPT_COLORS[dept] ?? '#c8d0dc';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', width: '100%', height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '0 4px 8px', flexShrink: 0 }}>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Layer:</label>
        <select
          value={layer}
          onChange={e => setLayer(Number(e.target.value))}
          style={{
            background: 'var(--bg-input)', color: 'var(--text)', border: '1px solid var(--border)',
            borderRadius: 4, padding: '4px 8px', fontSize: '0.8rem',
          }}
        >
          <option value={-1}>Concepts</option>
          {[1, 2, 3, 4, 5].map(l => (
            <option key={l} value={l}>L{l} — {LAYER_NAMES[l]}</option>
          ))}
        </select>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginLeft: 12 }}>Threshold:</label>
        <input
          type="range"
          min={0}
          max={0.5}
          step={0.01}
          value={minWeight}
          onChange={e => setMinWeight(Number(e.target.value))}
          style={{ width: 100, accentColor: 'var(--accent, #60a5fa)' }}
        />
        <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontFamily: 'monospace', minWidth: 32 }}>
          {minWeight.toFixed(2)}
        </span>
        {loading && <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Loading...</span>}
        {data && !loading && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-dim)' }}>
            {[...new Set(data.flatMap(d => [d.source_dept, d.target_dept]))].length} groups
          </span>
        )}
        {selection && (
          <button
            onClick={() => setSelection(null)}
            style={{
              marginLeft: 'auto', background: 'transparent', border: '1px solid var(--border)',
              color: 'var(--text-dim)', borderRadius: 4, padding: '2px 8px', fontSize: '0.75rem', cursor: 'pointer',
            }}
          >
            Clear selection
          </button>
        )}
      </div>

      <div style={{ display: 'flex', flex: '1 1 auto', minHeight: 0, gap: 0 }}>
        <div ref={containerRef} style={{
          position: 'relative',
          flex: selection ? '0 0 55%' : '1 1 auto',
          minHeight: 300,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          transition: 'flex 0.2s',
        }}>
          {loading && <div className="loading">Loading...</div>}
          {!loading && data && data.length === 0 && <div style={{ color: 'var(--text-dim)', padding: 16 }}>No co-firing edges at L{layer}</div>}
          <svg ref={svgRef} style={{ display: 'block' }} />
        </div>

        {selection && (
          <div style={{
            flex: '0 0 45%', overflow: 'auto', padding: '0 16px',
            borderLeft: '1px solid var(--border)',
          }}>
            <div style={{ marginBottom: 16 }}>
              <h3 style={{ margin: '0 0 4px', fontSize: '1rem' }}>
                <span style={{
                  display: 'inline-block', width: 10, height: 10, borderRadius: 2,
                  background: deptColor(selection.department), marginRight: 8, verticalAlign: 'middle',
                }} />
                {selection.name}
              </h3>
              {selection.department && (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>{selection.department}</div>
              )}
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: 4 }}>
                {selection.connections.length} connection{selection.connections.length !== 1 ? 's' : ''} at L{layer} ({LAYER_NAMES[layer]})
              </div>
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ textAlign: 'left', padding: '6px 8px', color: 'var(--text-dim)', fontWeight: 600 }}>Connected Node</th>
                  <th style={{ textAlign: 'left', padding: '6px 8px', color: 'var(--text-dim)', fontWeight: 600 }}>Department</th>
                  <th style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--text-dim)', fontWeight: 600 }}>Weight</th>
                  <th style={{ textAlign: 'right', padding: '6px 8px', color: 'var(--text-dim)', fontWeight: 600 }}>Edges</th>
                </tr>
              </thead>
              <tbody>
                {selection.connections.map((c, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '6px 8px' }}>
                      <span style={{
                        display: 'inline-block', width: 8, height: 8, borderRadius: 2,
                        background: deptColor(c.department), marginRight: 6, verticalAlign: 'middle',
                      }} />
                      {c.name}
                    </td>
                    <td style={{ padding: '6px 8px', color: 'var(--text-dim)', fontSize: '0.75rem' }}>{c.department || '—'}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{c.weight.toFixed(2)}</td>
                    <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{c.edgeCount}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr style={{ borderTop: '2px solid var(--border)' }}>
                  <td style={{ padding: '6px 8px', fontWeight: 700 }} colSpan={2}>Total</td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace', fontWeight: 700 }}>
                    {selection.connections.reduce((s, c) => s + c.weight, 0).toFixed(2)}
                  </td>
                  <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace', fontWeight: 700 }}>
                    {selection.connections.reduce((s, c) => s + c.edgeCount, 0)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
