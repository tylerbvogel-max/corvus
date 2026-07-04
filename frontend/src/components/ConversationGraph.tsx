import { useEffect, useRef, useState, useMemo } from 'react';
import * as d3 from 'd3';
import { DEPT_COLORS } from '../constants';
import type { NeuronScoreResponse } from '../types';

const LAYER_LABELS = ['Dept', 'Role', 'Task', 'System', 'Decision', 'Output'];
const SECTION_GAP = 40;
const SIBLING_GAP = 26;
const DEPTH_GAP = 42;
const MIN_R = 4;
const MAX_R = 12;
const NEURON_R = 6;

const DEPT_ABBREV: Record<string, string> = {
  'Engineering': 'Eng',
  'Manufacturing & Operations': 'Mfg',
  'Executive Leadership': 'Exec',
  'Contracts & Compliance': 'C&C',
  'Business Development': 'BD',
  'Administrative & Support': 'Admin',
  'Program Management': 'PM',
  'Finance': 'Fin',
  'Regulatory': 'Reg',
  'Concepts': 'Con',
};

interface Message {
  role: 'user' | 'assistant';
  text: string;
  model?: string;
  tokens?: { input: number; output: number };
  cost?: number;
  neurons_activated?: number;
  neuron_scores?: NeuronScoreResponse[];
}

interface ConversationGraphProps {
  messages: Message[];
  onNavigateToNeuron?: (id: number) => void;
}

interface PromptSection {
  index: number;
  userText: string;
  neurons: NeuronScoreResponse[];
}

interface TreeNodeData {
  key: string;
  neuron_id?: number;
  label: string;
  department: string;
  layer: number;
  combined: number;
  spread_boost: number;
  isPrompt?: boolean;
  isAncestor?: boolean;
  isRefired?: boolean;
  summary?: string | null;
  burst?: number;
  impact?: number;
  precision?: number;
  novelty?: number;
  recency?: number;
  relevance?: number;
  parent_id?: number | null;
  children?: TreeNodeData[];
}

interface PositionedNode {
  key: string;
  x: number;
  y: number;
  data: TreeNodeData;
}

interface TreeEdge {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  neuronToNeuron: boolean;
}

interface RefiredLink {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  department: string;
}

type AncestorInfo = {
  id: number;
  label: string;
  department: string | null;
  layer: number;
  parent_id: number | null;
};

/** Vertical S-curve Bezier for diagonal tree edges */
function bezierDiag(x1: number, y1: number, x2: number, y2: number): string {
  const my = (y1 + y2) / 2;
  return `M ${x1} ${y1} C ${x1} ${my}, ${x2} ${my}, ${x2} ${y2}`;
}

const PROMPT_MAX_W = 200;
const PROMPT_LINE_H = 16;
const PROMPT_PAD = 10;
const PROMPT_GAP = 30;
const DUAL_COL_THRESHOLD = 8;
const LANE_SEP = 22; // perpendicular separation between dual-column lanes

/** Prompt rect dimensions — width + height for word-wrapped text */
function promptRectDims(label: string): { w: number; h: number } {
  const w = Math.min(PROMPT_MAX_W, Math.max(100, label.length * 6.5 + 16));
  const charsPerLine = Math.floor((w - 12) / 6.5);
  const lines = Math.ceil(label.length / charsPerLine);
  const h = lines * PROMPT_LINE_H + PROMPT_PAD * 2;
  return { w, h };
}

export default function ConversationGraph({ messages, onNavigateToNeuron }: ConversationGraphProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const ancestorCache = useRef<Map<number, AncestorInfo>>(new Map());
  const [ancestorData, setAncestorData] = useState<Map<number, AncestorInfo>>(new Map());
  const [hoverNode, setHoverNode] = useState<PositionedNode | null>(null);
  const [containerWidth, setContainerWidth] = useState(400);

  // Measure actual container width so SVG fills the panel
  useEffect(() => {
    if (!scrollRef.current) return;
    const ro = new ResizeObserver(entries => {
      const w = entries[0]?.contentRect.width;
      if (w > 0) setContainerWidth(w);
    });
    ro.observe(scrollRef.current);
    return () => ro.disconnect();
  }, []);

  // Extract prompt sections
  const sections = useMemo<PromptSection[]>(() => {
    const result: PromptSection[] = [];
    for (let i = 0; i < messages.length; i++) {
      const msg = messages[i];
      if (msg.role === 'assistant' && msg.neuron_scores && msg.neuron_scores.length > 0) {
        let userText = 'Query';
        for (let j = i - 1; j >= 0; j--) {
          if (messages[j].role === 'user') {
            userText = messages[j].text;
            break;
          }
        }
        result.push({ index: result.length, userText, neurons: msg.neuron_scores });
      }
    }
    return result;
  }, [messages]);

  // Detect refired neurons
  const refiredMap = useMemo(() => {
    const seen = new Map<number, { firstSectionIndex: number; firstNodeKey: string }>();
    const refired = new Map<string, { firstNodeKey: string; department: string }>();
    for (const section of sections) {
      for (const n of section.neurons) {
        const nodeKey = `s${section.index}-n${n.neuron_id}`;
        const existing = seen.get(n.neuron_id);
        if (existing) {
          refired.set(nodeKey, { firstNodeKey: existing.firstNodeKey, department: n.department || 'Unknown' });
        } else {
          seen.set(n.neuron_id, { firstSectionIndex: section.index, firstNodeKey: nodeKey });
        }
      }
    }
    return refired;
  }, [sections]);

  // Fetch ancestors
  useEffect(() => {
    if (sections.length === 0) return;
    const allNeuronIds = new Set<number>();
    for (const s of sections) for (const n of s.neurons) allNeuronIds.add(n.neuron_id);
    const missingParents = new Set<number>();
    for (const s of sections) {
      for (const n of s.neurons) {
        if (n.parent_id != null && !allNeuronIds.has(n.parent_id) && !ancestorCache.current.has(n.parent_id)) {
          missingParents.add(n.parent_id);
        }
      }
    }
    if (missingParents.size === 0) {
      setAncestorData(new Map(ancestorCache.current));
      return;
    }
    const fetchAncestors = async () => {
      const toFetch = [...missingParents];
      for (let depth = 0; depth < 6 && toFetch.length > 0; depth++) {
        const batch = toFetch.splice(0, toFetch.length);
        const results = await Promise.all(
          batch.map(id => fetch(`/neurons/${id}`).then(r => r.ok ? r.json() : null).catch(() => null))
        );
        for (const r of results) {
          if (!r) continue;
          if (!ancestorCache.current.has(r.id)) {
            ancestorCache.current.set(r.id, {
              id: r.id, label: r.label, department: r.department, layer: r.layer, parent_id: r.parent_id,
            });
          }
          if (r.parent_id != null && !allNeuronIds.has(r.parent_id) && !ancestorCache.current.has(r.parent_id)) {
            toFetch.push(r.parent_id);
          }
        }
      }
      setAncestorData(new Map(ancestorCache.current));
    };
    fetchAncestors();
  }, [sections]);

  // Build per-section trees → diagonal zigzag layout
  const { sectionLayouts, totalHeight, refiredLinks, spine } = useMemo(() => {
    if (sections.length === 0) return { sectionLayouts: [], totalHeight: 0, refiredLinks: [], spine: [] };

    const PADDING = 30;
    const leftX = PADDING;
    const rightX = containerWidth - PADDING;

    const allNeuronIds = new Set<number>();
    for (const s of sections) for (const n of s.neurons) allNeuronIds.add(n.neuron_id);

    // --- Pass 1: build d3 trees, collect dimensions ---
    interface TreeInfo {
      root: d3.HierarchyPointNode<TreeNodeData>;
      section: PromptSection;
      minBreadth: number;
      maxBreadth: number;
      maxDepth: number;
    }
    const treeInfos: TreeInfo[] = [];

    for (const section of sections) {
      const neuronMap = new Map<number, NeuronScoreResponse>();
      for (const n of section.neurons) neuronMap.set(n.neuron_id, n);

      const rootData: TreeNodeData = {
        key: `s${section.index}-prompt`,
        label: section.userText.length > 60 ? section.userText.slice(0, 57) + '...' : section.userText,
        department: '',
        layer: -1,
        combined: 0,
        spread_boost: 0,
        isPrompt: true,
        children: [],
      };

      const childrenByParent = new Map<string, TreeNodeData[]>();

      for (const n of section.neurons) {
        const nodeKey = `s${section.index}-n${n.neuron_id}`;
        const isRefired = refiredMap.has(nodeKey);
        const nodeData: TreeNodeData = {
          key: nodeKey,
          neuron_id: n.neuron_id,
          label: n.label || `#${n.neuron_id}`,
          department: n.department || 'Unknown',
          layer: n.layer,
          combined: n.combined,
          spread_boost: n.spread_boost,
          isRefired,
          summary: n.summary,
          burst: n.burst,
          impact: n.impact,
          precision: n.precision,
          novelty: n.novelty,
          recency: n.recency,
          relevance: n.relevance,
          parent_id: n.parent_id,
          children: [],
        };

        let parentKey = `s${section.index}-prompt`;
        if (n.parent_id != null) {
          if (neuronMap.has(n.parent_id)) {
            parentKey = `s${section.index}-n${n.parent_id}`;
          } else if (ancestorData.has(n.parent_id)) {
            let currentParentId: number | null = n.parent_id;
            let bridgeParentKey = `s${section.index}-prompt`;
            const bridgeChain: AncestorInfo[] = [];
            while (currentParentId != null && ancestorData.has(currentParentId)) {
              bridgeChain.unshift(ancestorData.get(currentParentId)!);
              currentParentId = ancestorData.get(currentParentId)!.parent_id;
            }
            for (const anc of bridgeChain) {
              const ancKey = `s${section.index}-a${anc.id}`;
              if (!childrenByParent.has(ancKey + '-EXISTS')) {
                const ancNode: TreeNodeData = {
                  key: ancKey,
                  neuron_id: anc.id,
                  label: anc.label || `#${anc.id}`,
                  department: anc.department || 'Unknown',
                  layer: anc.layer,
                  combined: 0,
                  spread_boost: 0,
                  isAncestor: true,
                  parent_id: anc.parent_id,
                  children: [],
                };
                const siblings = childrenByParent.get(bridgeParentKey) || [];
                if (!siblings.find(s => s.key === ancKey)) {
                  siblings.push(ancNode);
                  childrenByParent.set(bridgeParentKey, siblings);
                }
                childrenByParent.set(ancKey + '-EXISTS', []);
              }
              bridgeParentKey = ancKey;
            }
            parentKey = bridgeParentKey;
          }
        }

        const siblings = childrenByParent.get(parentKey) || [];
        siblings.push(nodeData);
        childrenByParent.set(parentKey, siblings);
      }

      const attachChildren = (node: TreeNodeData): void => {
        const children = childrenByParent.get(node.key) || [];
        node.children = children;
        for (const child of children) attachChildren(child);
      };
      attachChildren(rootData);

      const hierarchy = d3.hierarchy(rootData, d => d.children && d.children.length > 0 ? d.children : undefined);
      const treeLayout = d3.tree<TreeNodeData>().nodeSize([SIBLING_GAP, DEPTH_GAP]);
      const root = treeLayout(hierarchy);

      let minB = Infinity, maxB = -Infinity, maxD = 0;
      root.each(d => {
        if ((d.x ?? 0) < minB) minB = d.x ?? 0;
        if ((d.x ?? 0) > maxB) maxB = d.x ?? 0;
        if ((d.y ?? 0) > maxD) maxD = d.y ?? 0;
      });

      treeInfos.push({ root, section, minBreadth: minB, maxBreadth: maxB, maxDepth: maxD });
    }

    // --- Pass 2: diagonal position nodes ---
    const layouts: { nodes: PositionedNode[]; edges: TreeEdge[]; height: number; promptNode: PositionedNode; isLeft: boolean; promptRectH: number }[] = [];
    const nodePositions = new Map<string, { x: number; y: number }>();
    let currentY = 0;

    for (const ti of treeInfos) {
      const isLeft = ti.section.index % 2 === 0;
      const maxD = ti.maxDepth || 1;

      const nodes: PositionedNode[] = [];
      const edges: TreeEdge[] = [];

      // Constrain sibling spread so nodes don't overflow container edges
      const availableSpread = (rightX - leftX) * 0.3;
      const halfBreadth = Math.max(Math.abs(ti.minBreadth), Math.abs(ti.maxBreadth));
      const spreadScale = halfBreadth > 0 ? Math.min(1, availableSpread / halfBreadth) : 1;

      // Fixed prompt center offset estimate for edge-alignment
      const PROMPT_CENTER_OFFSET = 45;
      const promptX = isLeft ? PADDING + PROMPT_CENTER_OFFSET : containerWidth - PADDING - PROMPT_CENTER_OFFSET;

      // Compute prompt rect height for this section
      const promptLabel = ti.root.data.label;
      const { h: promptRectH } = promptRectDims(promptLabel);

      // Dual-column: count non-prompt nodes and assign lanes by subtree
      let nonPromptCount = 0;
      ti.root.each(d => { if (!d.data.isPrompt) nonPromptCount++; });
      const dualColumn = nonPromptCount > DUAL_COL_THRESHOLD;

      // Assign each subtree (direct child of prompt) to a lane
      const laneMap = new Map<string, number>();
      if (dualColumn) {
        const directChildren = ti.root.children || [];
        directChildren.forEach((child, i) => {
          const lane = i % 2;
          child.each(desc => { laneMap.set(desc.data.key, lane); });
        });
      }

      ti.root.each(d => {
        const siblingOffset = (d.x ?? 0) * spreadScale;
        const depth = d.y ?? 0;
        const depthT = maxD > 0 ? depth / maxD : 0;

        let x: number, y: number;
        if (d.data.isPrompt) {
          x = promptX;
          y = currentY;
        } else {
          // X position: diagonal spread toward opposite side
          let rawX: number;
          if (isLeft) {
            rawX = leftX + depthT * (rightX - leftX) + siblingOffset;
          } else {
            rawX = rightX - depthT * (rightX - leftX) + siblingOffset;
          }
          x = Math.max(leftX + 4, Math.min(rightX - 4, rawX));

          // Y: 45° means vertical delta = horizontal delta from prompt
          const xDelta = Math.abs(x - promptX);
          y = currentY + PROMPT_GAP + xDelta + Math.abs(siblingOffset) * 0.3;

          // Dual-column: offset lane 1 perpendicular to the 45° diagonal
          // For 45°, perpendicular shift ≈ (-sep*0.7, +sep*0.7)
          if (dualColumn) {
            const lane = laneMap.get(d.data.key) ?? 0;
            if (lane === 1) {
              const xShift = isLeft ? -LANE_SEP * 0.7 : LANE_SEP * 0.7;
              x = Math.max(leftX + 4, Math.min(rightX - 4, x + xShift));
              y += LANE_SEP * 0.7;
            }
          }
        }

        const node: PositionedNode = { key: d.data.key, x, y, data: d.data };
        nodes.push(node);
        nodePositions.set(d.data.key, { x, y });
      });

      ti.root.links().forEach(link => {
        const sNode = nodePositions.get(link.source.data.key);
        const tNode = nodePositions.get(link.target.data.key);
        if (!sNode || !tNode) return;

        const srcIsNeuron = !link.source.data.isPrompt && !link.source.data.isAncestor;
        const tgtIsNeuron = !link.target.data.isPrompt && !link.target.data.isAncestor;
        edges.push({ x1: sNode.x, y1: sNode.y, x2: tNode.x, y2: tNode.y, neuronToNeuron: srcIsNeuron && tgtIsNeuron });
      });

      // Compute actual section height from positioned nodes
      let maxNodeY = currentY;
      for (const n of nodes) {
        if (n.y > maxNodeY) maxNodeY = n.y;
      }
      const sectionHeight = maxNodeY - currentY + 20;

      const promptNode = nodes.find(n => n.data.isPrompt)!;
      layouts.push({ nodes, edges, height: sectionHeight, promptNode, isLeft, promptRectH });
      currentY += sectionHeight + SECTION_GAP;
    }

    const totalH = currentY - (sections.length > 0 ? SECTION_GAP : 0) + 40;

    // Refired links
    const rLinks: RefiredLink[] = [];
    for (const [nodeKey, { firstNodeKey, department }] of refiredMap) {
      const from = nodePositions.get(nodeKey);
      const to = nodePositions.get(firstNodeKey);
      if (from && to) {
        rLinks.push({ fromX: from.x, fromY: from.y, toX: to.x, toY: to.y, department });
      }
    }

    // Prompt-to-prompt spine (zigzag Bezier)
    const spineSegments: { x1: number; y1: number; x2: number; y2: number }[] = [];
    for (let i = 1; i < layouts.length; i++) {
      const prev = layouts[i - 1];
      const next = layouts[i];
      if (prev.promptNode && next.promptNode) {
        spineSegments.push({
          x1: prev.promptNode.x, y1: prev.promptNode.y + prev.promptRectH / 2 + 2,
          x2: next.promptNode.x, y2: next.promptNode.y - next.promptRectH / 2 - 2,
        });
      }
    }

    return {
      sectionLayouts: layouts,
      totalHeight: totalH,
      refiredLinks: rLinks,
      spine: spineSegments,
    };
  }, [sections, ancestorData, refiredMap, containerWidth]);

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages.length]);

  // Active departments for legend (filter Unknown)
  const activeDepartments = useMemo(() => {
    const depts = new Set<string>();
    for (const layout of sectionLayouts) {
      for (const node of layout.nodes) {
        if (!node.data.isPrompt && node.data.department && node.data.department !== 'Unknown') {
          depts.add(node.data.department);
        }
      }
    }
    return [...depts].sort();
  }, [sectionLayouts]);

  if (sections.length === 0) return null;

  // Score range for radius + opacity scaling
  let minScore = Infinity, maxScore = -Infinity;
  for (const layout of sectionLayouts) {
    for (const node of layout.nodes) {
      if (!node.data.isPrompt && !node.data.isAncestor) {
        if (node.data.combined < minScore) minScore = node.data.combined;
        if (node.data.combined > maxScore) maxScore = node.data.combined;
      }
    }
  }
  const scoreRange = maxScore - minScore;
  const neuronRadius = (combined: number) => {
    if (scoreRange <= 0) return NEURON_R;
    const t = (combined - minScore) / scoreRange;
    return MIN_R + t * (MAX_R - MIN_R);
  };
  const neuronOpacity = (combined: number) => {
    if (scoreRange <= 0) return 0.7;
    const t = (combined - minScore) / scoreRange;
    return 0.4 + t * 0.6;
  };

  const SVG_PAD_TOP = 30;

  // Unique department IDs for gradients
  const gradientDepts = new Set<string>();
  for (const layout of sectionLayouts) {
    for (const node of layout.nodes) {
      if (!node.data.isPrompt && !node.data.isAncestor && node.data.department) {
        gradientDepts.add(node.data.department);
      }
    }
  }

  const lastSectionIndex = sections.length - 1;

  return (
    <div className="conversation-graph" ref={scrollRef}>
      {/* Department legend */}
      {activeDepartments.length > 0 && (
        <div className="cg-legend">
          {activeDepartments.map(dept => (
            <span key={dept} className="cg-legend-item">
              <span className="cg-legend-dot" style={{ background: DEPT_COLORS[dept] || '#4a5568' }} />
              {DEPT_ABBREV[dept] || dept.slice(0, 3)}
            </span>
          ))}
        </div>
      )}

      <svg width={containerWidth} height={totalHeight + 60 + SVG_PAD_TOP} style={{ display: 'block' }}>
        <defs>
          {[...gradientDepts].map(dept => {
            const color = DEPT_COLORS[dept] || '#4a5568';
            return (
              <radialGradient key={`grad-${dept}`} id={`grad-${dept.replace(/[^a-zA-Z]/g, '')}`}>
                <stop offset="0%" stopColor={color} stopOpacity={1} />
                <stop offset="100%" stopColor={color} stopOpacity={0.3} />
              </radialGradient>
            );
          })}
        </defs>
        <g transform={`translate(0, ${SVG_PAD_TOP})`}>

          {/* Layer 1: Prompt-to-prompt zigzag spine */}
          {spine.map((seg, i) => (
            <path
              key={`spine-${i}`}
              d={`M ${seg.x1} ${seg.y1} C ${seg.x1} ${(seg.y1 + seg.y2) / 2}, ${seg.x2} ${(seg.y1 + seg.y2) / 2}, ${seg.x2} ${seg.y2}`}
              fill="none"
              stroke="var(--accent-dim, rgba(59, 130, 246, 0.3))"
              strokeOpacity={0.3}
              strokeWidth={1.5}
              strokeDasharray="4,3"
            />
          ))}

          {/* Layer 2: Hierarchy edges (horizontal Bezier curves) */}
          {sectionLayouts.map((layout, si) => (
            <g key={`edges-${si}`} className={`cg-section cg-section-${si}`}>
              {layout.edges.map((e, ei) => (
                <path
                  key={`edge-${si}-${ei}`}
                  d={bezierDiag(e.x1, e.y1, e.x2, e.y2)}
                  fill="none"
                  stroke="var(--accent-dim, rgba(59, 130, 246, 0.3))"
                  strokeOpacity={e.neuronToNeuron ? 0.6 : 0.3}
                  strokeWidth={e.neuronToNeuron ? 1.5 : 1}
                  strokeDasharray={e.neuronToNeuron ? undefined : '4,3'}
                />
              ))}
            </g>
          ))}

          {/* Layer 3: Refired back-links */}
          {refiredLinks.map((rl, i) => {
            const arcOffset = 30 + (i % 3) * 12;
            const midY = (rl.fromY + rl.toY) / 2;
            const controlX = Math.max(rl.fromX, rl.toX) + arcOffset;
            const color = DEPT_COLORS[rl.department] || '#4a5568';
            return (
              <path
                key={`refire-${i}`}
                d={`M ${rl.fromX} ${rl.fromY} Q ${controlX} ${midY} ${rl.toX} ${rl.toY}`}
                fill="none"
                stroke={color}
                strokeWidth={1}
                strokeOpacity={0.35}
              />
            );
          })}

          {/* Layer 4: Nodes + labels */}
          {sectionLayouts.map((layout, si) => (
            <g key={`nodes-${si}`} className={`cg-section cg-section-${si}`}>
              {layout.nodes.map(node => {
                const d = node.data;

                if (d.isPrompt) {
                  const { w: rw, h: rh } = promptRectDims(d.label);
                  const isLatest = si === lastSectionIndex;
                  const rectX = layout.isLeft ? 30 : containerWidth - 30 - rw;
                  return (
                    <g key={node.key} className={isLatest ? 'cg-prompt-latest' : undefined}>
                      <rect
                        x={rectX}
                        y={node.y - rh / 2}
                        width={rw}
                        height={rh}
                        rx={8}
                        fill="var(--accent-glow, rgba(59, 130, 246, 0.12))"
                        stroke="var(--accent, rgba(59, 130, 246, 0.6))"
                        strokeWidth={1.5}
                        style={{ cursor: 'default' }}
                        onMouseEnter={() => setHoverNode(node)}
                        onMouseLeave={() => setHoverNode(null)}
                      />
                      <foreignObject x={rectX + 6} y={node.y - rh / 2 + 4} width={rw - 12} height={rh - 8}>
                        <div style={{
                          color: 'var(--accent-light, #93c5fd)',
                          fontSize: 11, fontWeight: 700, lineHeight: '16px',
                          overflow: 'hidden', wordWrap: 'break-word',
                          display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical' as const,
                        }}>
                          {d.label}
                        </div>
                      </foreignObject>
                    </g>
                  );
                }

                const color = DEPT_COLORS[d.department] || '#4a5568';
                const gradId = `grad-${d.department.replace(/[^a-zA-Z]/g, '')}`;
                const r = d.isAncestor ? NEURON_R : neuronRadius(d.combined);
                const opacity = d.isAncestor ? 0.3 : neuronOpacity(d.combined);

                // Label: truncated neuron name
                const labelText = d.label.length > 14 ? d.label.slice(0, 12) + '..' : d.label;
                // Position label on the tree-growth side
                const labelOnRight = layout.isLeft;
                const labelX = labelOnRight ? node.x + r + 4 : node.x - r - 4;
                const labelAnchor = labelOnRight ? 'start' : 'end';

                return (
                  <g key={node.key} className="cg-neuron">
                    {d.spread_boost > 0 && (
                      <circle
                        cx={node.x} cy={node.y} r={r + 2.5}
                        fill="none"
                        stroke="#e8a735"
                        strokeWidth={1.5}
                        opacity={opacity}
                      />
                    )}
                    {d.isRefired && (
                      <circle
                        cx={node.x} cy={node.y} r={r + 1.5}
                        fill="none"
                        stroke="rgba(59, 130, 246, 0.5)"
                        strokeWidth={1}
                        strokeDasharray="3,2"
                        opacity={opacity}
                      />
                    )}
                    <circle
                      cx={node.x} cy={node.y} r={r}
                      fill={d.isAncestor ? color : `url(#${gradId})`}
                      opacity={opacity}
                      style={{ cursor: onNavigateToNeuron ? 'pointer' : 'default' }}
                      onClick={() => d.neuron_id && onNavigateToNeuron?.(d.neuron_id)}
                      onMouseEnter={() => setHoverNode(node)}
                      onMouseLeave={() => setHoverNode(null)}
                    />
                    {/* Tiny node label */}
                    {!d.isAncestor && (
                      <text
                        x={labelX}
                        y={node.y + 3}
                        textAnchor={labelAnchor}
                        fill="var(--text, #d4cfc6)"
                        fillOpacity={0.9}
                        fontSize={9}
                        pointerEvents="none"
                      >
                        {labelText}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          ))}
        </g>
      </svg>

      {/* Hover popup */}
      {hoverNode && !hoverNode.data.isPrompt && (
        <div style={{
          position: 'sticky',
          bottom: 0,
          left: 0,
          right: 0,
          background: 'var(--bg-card, rgba(15, 23, 42, 0.92))',
          backdropFilter: 'blur(8px)',
          borderTop: '1px solid var(--accent-dim, rgba(59, 130, 246, 0.2))',
          padding: '10px 16px',
          fontSize: '0.78rem',
          color: 'var(--text, #e2e8f0)',
          zIndex: 20,
          pointerEvents: 'none',
          display: 'flex',
          gap: 24,
          alignItems: 'flex-start',
          flexWrap: 'wrap',
        }}>
          <div style={{ minWidth: 160 }}>
            <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 2 }}>
              {hoverNode.data.label}
            </div>
            <div style={{ color: DEPT_COLORS[hoverNode.data.department] || '#4a5568', fontSize: '0.72rem' }}>
              {hoverNode.data.department} · {LAYER_LABELS[hoverNode.data.layer] ?? `L${hoverNode.data.layer}`}
            </div>
            {hoverNode.data.summary && (
              <div style={{ color: 'var(--text-dim, #94a3b8)', fontSize: '0.7rem', marginTop: 4, maxWidth: 300 }}>
                {hoverNode.data.summary}
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Combined</span> <strong>{hoverNode.data.combined.toFixed(3)}</strong></div>
            {hoverNode.data.burst != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Burst</span> {hoverNode.data.burst.toFixed(3)}</div>}
            {hoverNode.data.impact != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Impact</span> {hoverNode.data.impact.toFixed(3)}</div>}
            {hoverNode.data.precision != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Precision</span> {hoverNode.data.precision.toFixed(3)}</div>}
            {hoverNode.data.novelty != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Novelty</span> {hoverNode.data.novelty.toFixed(3)}</div>}
            {hoverNode.data.recency != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Recency</span> {hoverNode.data.recency.toFixed(3)}</div>}
            {hoverNode.data.relevance != null && <div><span style={{ color: 'var(--text-dim, #64748b)' }}>Relevance</span> {hoverNode.data.relevance.toFixed(3)}</div>}
          </div>
          {hoverNode.data.spread_boost > 0 && (
            <div style={{ color: '#e8a735' }}>
              Spread +{hoverNode.data.spread_boost.toFixed(3)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
