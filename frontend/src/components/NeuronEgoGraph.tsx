import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { fetchNeuronEdges } from '../api/knowledge_graph';
import type { EgoGraphResponse } from '../types'
import { DEPT_COLORS } from '../constants'

interface Props {
  neuronId: number;
  onSelectNeuron?: (id: number) => void;
}

interface SimNode extends d3.SimulationNodeDatum {
  id: number;
  label: string;
  department: string | null;
  layer: number;
  isCenter: boolean;
  hop: number;
  weight?: number;
  co_fire_count?: number;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  weight: number;
  co_fire_count: number;
}

export default function NeuronEgoGraph({ neuronId, onSelectNeuron }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [data, setData] = useState<EgoGraphResponse | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setError('');
    fetchNeuronEdges(neuronId)
      .then(setData)
      .catch(e => setError(e.message));
  }, [neuronId]);

  useEffect(() => {
    if (!data || !svgRef.current || data.neighbors.length === 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = svgRef.current.clientWidth || 600;
    const height = 500;
    const cx = width / 2;
    const cy = height / 2;

    svg.attr('viewBox', `0 0 ${width} ${height}`);

    // SVG filter for node glow
    const defs = svg.append('defs');
    const glowFilter = defs.append('filter').attr('id', 'node-glow');
    glowFilter.append('feGaussianBlur').attr('stdDeviation', '2').attr('result', 'blur');
    glowFilter.append('feMerge')
      .selectAll('feMergeNode')
      .data(['blur', 'SourceGraphic'])
      .join('feMergeNode')
      .attr('in', d => d);

    // Subtle glow for wires
    const wireGlow = defs.append('filter').attr('id', 'wire-glow');
    wireGlow.append('feGaussianBlur').attr('stdDeviation', '1.5').attr('result', 'blur');
    wireGlow.append('feMerge')
      .selectAll('feMergeNode')
      .data(['blur', 'SourceGraphic'])
      .join('feMergeNode')
      .attr('in', d => d);

    const nodes: SimNode[] = [
      { id: data.center.id, label: data.center.label, department: data.center.department, layer: data.center.layer, isCenter: true, hop: 0, fx: cx, fy: cy },
      ...data.neighbors.map(n => ({
        id: n.id, label: n.label, department: n.department, layer: n.layer,
        isCenter: false, hop: n.hop, weight: n.weight, co_fire_count: n.co_fire_count,
      })),
    ];

    const nodeById = new Map(nodes.map(n => [n.id, n]));

    // Filter edges: only keep edges where at least one endpoint is center or hop-1
    // This prevents the dense hop2-to-hop2 wire mess
    const allLinks: SimLink[] = data.edges
      ? data.edges.map(e => ({ source: e.source, target: e.target, weight: e.weight, co_fire_count: e.co_fire_count }))
      : data.neighbors.map(n => ({ source: data.center.id, target: n.id, weight: n.weight, co_fire_count: n.co_fire_count }));

    const links = allLinks.filter(l => {
      const sNode = nodeById.get(typeof l.source === 'object' ? (l.source as SimNode).id : l.source as number);
      const tNode = nodeById.get(typeof l.target === 'object' ? (l.target as SimNode).id : l.target as number);
      return (sNode?.isCenter || sNode?.hop === 1) || (tNode?.isCenter || tNode?.hop === 1);
    });

    // Layout — strong repulsion and wide radii to avoid clustering
    const sim = d3.forceSimulation(nodes)
      .force('link', d3.forceLink<SimNode, SimLink>(links).id(d => d.id).distance(d => {
        const s = nodeById.get((d.source as SimNode).id);
        const t = nodeById.get((d.target as SimNode).id);
        if (s?.isCenter || t?.isCenter) return 160;
        return 120;
      }).strength(0.25))
      .force('radial', d3.forceRadial<SimNode>(d => d.isCenter ? 0 : d.hop === 1 ? 150 : 280, cx, cy).strength(0.5))
      .force('collide', d3.forceCollide<SimNode>(d => d.hop >= 2 ? 20 : 30))
      .force('charge', d3.forceManyBody().strength(-150))
      .stop();

    for (let i = 0; i < 200; i++) sim.tick();

    const maxWeight = d3.max(links, l => l.weight) || 1;
    const weightScale = d3.scaleLinear().domain([0, maxWeight]).range([0.5, 2.5]);
    const opacityScale = d3.scaleLinear().domain([0, maxWeight]).range([0.15, 0.6]);

    // Tooltip
    const tooltip = d3.select(svgRef.current.parentElement!)
      .selectAll<HTMLDivElement, unknown>('.ego-tooltip')
      .data([null])
      .join('div')
      .attr('class', 'ego-tooltip')
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

    function linkTouchesCenter(d: SimLink): boolean {
      const sId = typeof d.source === 'object' ? (d.source as SimNode).id : d.source;
      const tId = typeof d.target === 'object' ? (d.target as SimNode).id : d.target;
      return sId === data!.center.id || tId === data!.center.id;
    }

    // --- Curved wire edges ---
    const edgeG = svg.append('g');

    // Generate curved path between two points with organic curvature
    function wirePath(d: SimLink): string {
      const s = nodeById.get((d.source as SimNode).id);
      const t = nodeById.get((d.target as SimNode).id);
      const x1 = s?.x ?? cx, y1 = s?.y ?? cy;
      const x2 = t?.x ?? cx, y2 = t?.y ?? cy;

      const dx = x2 - x1, dy = y2 - y1;
      const dist = Math.sqrt(dx * dx + dy * dy);
      // Perpendicular offset for curvature — varies by link index for organic feel
      const linkIdx = links.indexOf(d);
      const curvature = dist * 0.15 * (linkIdx % 2 === 0 ? 1 : -1);
      // Midpoint offset perpendicular to the line
      const mx = (x1 + x2) / 2 + (-dy / dist) * curvature;
      const my = (y1 + y2) / 2 + (dx / dist) * curvature;

      return `M ${x1},${y1} Q ${mx},${my} ${x2},${y2}`;
    }

    // Glow layer (thicker, more transparent — creates wire glow)
    edgeG.selectAll('path.wire-glow')
      .data(links)
      .join('path')
      .attr('class', 'wire-glow')
      .attr('d', wirePath)
      .attr('fill', 'none')
      .attr('stroke', d => {
        const s = nodeById.get((d.source as SimNode).id);
        const t = nodeById.get((d.target as SimNode).id);
        const dept = s?.isCenter ? t?.department : s?.department;
        return DEPT_COLORS[dept ?? ''] ?? '#c8d0dc';
      })
      .attr('stroke-width', d => linkTouchesCenter(d) ? weightScale(d.weight) + 2 : weightScale(d.weight) + 0.5)
      .attr('stroke-opacity', d => linkTouchesCenter(d) ? opacityScale(d.weight) * 0.2 : opacityScale(d.weight) * 0.06)
      .attr('filter', d => linkTouchesCenter(d) ? 'url(#wire-glow)' : null);

    // Core wire layer
    edgeG.selectAll('path.wire-core')
      .data(links)
      .join('path')
      .attr('class', 'wire-core')
      .attr('d', wirePath)
      .attr('fill', 'none')
      .attr('stroke', d => {
        const s = nodeById.get((d.source as SimNode).id);
        const t = nodeById.get((d.target as SimNode).id);
        const dept = s?.isCenter ? t?.department : s?.department;
        return DEPT_COLORS[dept ?? ''] ?? '#c8d0dc';
      })
      .attr('stroke-width', d => linkTouchesCenter(d) ? weightScale(d.weight) : Math.max(0.3, weightScale(d.weight) * 0.4))
      .attr('stroke-opacity', d => linkTouchesCenter(d) ? opacityScale(d.weight) + 0.15 : opacityScale(d.weight) * 0.35)
      .attr('stroke-dasharray', d => linkTouchesCenter(d) ? 'none' : '2,5');

    // --- Nodes ---
    const nodeG = svg.append('g')
      .selectAll<SVGGElement, SimNode>('g')
      .data(nodes)
      .join('g')
      .attr('transform', d => `translate(${d.x},${d.y})`)
      .style('cursor', d => d.isCenter ? 'default' : 'pointer')
      .on('click', (_e, d) => {
        if (!d.isCenter && onSelectNeuron) onSelectNeuron(d.id);
      })
      .on('mouseenter', (event, d) => {
        const parts = [d.label];
        if (d.department) parts.push(d.department);
        if (!d.isCenter) {
          parts.push(`Hop: ${d.hop}`);
          if (d.weight != null) parts.push(`Weight: ${d.weight.toFixed(3)}`);
          if (d.co_fire_count != null) parts.push(`Co-fires: ${d.co_fire_count}`);
        }
        tooltip.html(parts.join('<br>'))
          .style('opacity', 1)
          .style('left', `${event.offsetX + 12}px`)
          .style('top', `${event.offsetY - 10}px`);
      })
      .on('mouseleave', () => tooltip.style('opacity', 0));

    // Glow behind nodes (subtle halo)
    nodeG.append('circle')
      .attr('r', d => d.isCenter ? 18 : d.hop === 1 ? 11 : 7)
      .attr('fill', d => {
        const color = DEPT_COLORS[d.department ?? ''] ?? '#c8d0dc';
        return color + '20';
      })
      .attr('filter', 'url(#node-glow)');

    // Main node circle
    nodeG.append('circle')
      .attr('r', d => d.isCenter ? 12 : d.hop === 1 ? 7 : 4.5)
      .attr('fill', d => {
        const color = DEPT_COLORS[d.department ?? ''] ?? '#c8d0dc';
        return d.hop >= 2 ? color + 'bb' : color;
      })
      .attr('stroke', d => {
        if (d.isCenter) return '#fff';
        const color = DEPT_COLORS[d.department ?? ''] ?? '#c8d0dc';
        return color + '60';
      })
      .attr('stroke-width', d => d.isCenter ? 2 : 1);

    // Center label
    nodeG.filter(d => d.isCenter)
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', 28)
      .attr('fill', 'var(--text)')
      .attr('font-size', '0.7rem')
      .text(d => d.label.length > 30 ? d.label.slice(0, 27) + '...' : d.label);

    return () => { tooltip.remove(); };
  }, [data, onSelectNeuron]);

  if (error) return <div className="error-msg">{error}</div>;
  if (!data || data.neighbors.length === 0) return null;

  const hop1Count = data.neighbors.filter(n => n.hop === 1).length;
  const hop2Count = data.neighbors.filter(n => n.hop >= 2).length;

  return (
    <div className="detail-section" style={{ position: 'relative' }}>
      <h3>Co-Firing Neighbors</h3>
      <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginBottom: 4, display: 'flex', gap: 14 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#c8d0dc', boxShadow: '0 0 4px #c8d0dc' }} />
          Direct ({hop1Count})
        </span>
        {hop2Count > 0 && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#c8d0dcbb', boxShadow: '0 0 3px #c8d0dc' }} />
            Multi-hop ({hop2Count})
          </span>
        )}
      </div>
      <svg ref={svgRef} width="100%" height="500" style={{ background: 'rgba(0,0,0,0.15)', borderRadius: 8 }} />
    </div>
  );
}
