import { useEffect, useRef, useState } from 'react'
import * as d3 from 'd3'
import { fetchTree, fetchNeuron } from '../api'
import type { TreeNode } from '../types'
import { DEPT_COLORS } from '../constants'
import { escapeHtml } from '../utils'

interface HierNode {
  name: string
  id: number
  layer: number
  node_type: string
  department: string | null
  role_key: string | null
  invocations: number
  avg_utility: number
  children?: HierNode[]
  value?: number
}

function treeToHier(nodes: TreeNode[]): HierNode {
  const convert = (n: TreeNode): HierNode => {
    const kids = n.children ?? []
    return {
      name: n.label,
      id: n.id,
      layer: n.layer,
      node_type: n.node_type,
      department: n.department,
      role_key: n.role_key,
      invocations: n.invocations,
      avg_utility: n.avg_utility,
      children: kids.length > 0 ? kids.map(convert) : undefined,
      value: kids.length === 0 ? 1 : undefined,
    }
  }
  return {
    name: 'Corvus',
    id: 0,
    layer: -1,
    node_type: 'root',
    department: null,
    role_key: null,
    invocations: 0,
    avg_utility: 0,
    children: nodes.map(convert),
  }
}

// DEPT_COLORS imported from constants.ts

type TreemapNode = d3.HierarchyRectangularNode<HierNode>

function getColor(d: TreemapNode): string {
  const dept = d.data.department
  const base = dept ? (DEPT_COLORS[dept] || '#c8d0dc') : '#c8d0dc'
  const col = d3.color(base)
  if (col) {
    const hsl = d3.hsl(col)
    // Deeper layers get lighter
    hsl.l = Math.min(0.8, hsl.l + d.depth * 0.07)
    hsl.s = Math.max(0.25, hsl.s - d.depth * 0.04)
    return hsl.formatHex()
  }
  return base
}

export default function CirclePacking() {
  const svgRef = useRef<SVGSVGElement>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<any>(null)

  useEffect(() => {
    let cancelled = false

    fetchTree()
      .then(tree => {
        if (cancelled || !svgRef.current) return
        setLoading(false)

        const root = treeToHier(tree)
        const container = svgRef.current.parentElement!
        const width = container.clientWidth
        const height = container.clientHeight || 700

        const hierarchy = d3.hierarchy<HierNode>(root)
          .sum(d => d.value ?? 0)
          .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))

        const treemap = d3.treemap<HierNode>()
          .size([width, height])
          .paddingTop(22)
          .paddingRight(2)
          .paddingBottom(2)
          .paddingLeft(2)
          .paddingInner(1)
          .round(true)

        treemap(hierarchy)

        const svg = d3.select(svgRef.current)
        svg.selectAll('*').remove()
        svg
          .attr('width', width)
          .attr('height', height)
          .attr('viewBox', `0 0 ${width} ${height}`)
          .style('background', '#0f1729')

        // Tooltip
        const tooltip = d3.select('body').selectAll<HTMLDivElement, null>('.treemap-tooltip').data([null])
        const tooltipMerged = tooltip.enter().append('div').attr('class', 'treemap-tooltip').merge(tooltip)
        tooltipMerged
          .style('position', 'absolute')
          .style('display', 'none')
          .style('background', '#1a2744')
          .style('border', '1px solid #334155')
          .style('border-radius', '8px')
          .style('padding', '10px 14px')
          .style('font-size', '0.8rem')
          .style('color', '#ffffff')
          .style('pointer-events', 'none')
          .style('z-index', '1000')
          .style('max-width', '320px')
          .style('line-height', '1.5')

        // Breadcrumb
        const breadcrumb = d3.select('#treemap-breadcrumb')
        breadcrumb.html('<span style="color:#60a5fa">Corvus</span>')

        function render(focus: TreemapNode) {

          // Re-layout from focus
          const focusHier = d3.hierarchy<HierNode>(focus.data)
            .sum(d => d.value ?? 0)
            .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))

          const tm = d3.treemap<HierNode>()
            .size([width, height])
            .paddingTop(24)
            .paddingRight(2)
            .paddingBottom(2)
            .paddingLeft(2)
            .paddingInner(1)
            .round(true)

          tm(focusHier)

          // We show: the focus node's children (groups) and their children (leaves)
          // Show up to 2 levels deep from focus
          // After treemap() call, nodes have x0/y0/x1/y1 properties
          const nodes = (focusHier.descendants() as TreemapNode[]).filter(d => d.depth <= 2 && d.depth > 0)

          svg.selectAll('*').remove()
          const g = svg.append('g')

          // Groups (depth 1 = direct children of focus)
          const groups = nodes.filter(d => d.depth === 1)
          const leaves = nodes.filter(d => d.depth === 2)

          // Draw group rects
          g.selectAll<SVGRectElement, TreemapNode>('rect.group')
            .data(groups)
            .join('rect')
            .attr('class', 'group')
            .attr('x', d => d.x0)
            .attr('y', d => d.y0)
            .attr('width', d => Math.max(0, d.x1 - d.x0))
            .attr('height', d => Math.max(0, d.y1 - d.y0))
            .attr('fill', d => {
              const c = getColor(d)
              const col = d3.color(c)
              if (col) { col.opacity = 0.15; return col.formatRgb() }
              return c
            })
            .attr('stroke', d => getColor(d))
            .attr('stroke-width', 1.5)
            .attr('stroke-opacity', 0.5)
            .attr('rx', 3)
            .style('cursor', d => d.children ? 'pointer' : 'default')
            .on('click', (_, d) => {
              if (d.children && d.children.length > 0) {
                // Find matching node in the original hierarchy
                const match = findNode(hierarchy as TreemapNode, d.data.id)
                if (match) {
                  render(match)
                  updateBreadcrumb(match)
                }
              }
            })

          // Group header labels
          g.selectAll<SVGTextElement, TreemapNode>('text.group-label')
            .data(groups)
            .join('text')
            .attr('class', 'group-label')
            .attr('x', d => d.x0 + 4)
            .attr('y', d => d.y0 + 15)
            .attr('fill', d => getColor(d))
            .attr('font-size', '11px')
            .attr('font-weight', '600')
            .attr('pointer-events', 'none')
            .text(d => {
              const w = d.x1 - d.x0
              const maxChars = Math.floor(w / 7)
              if (maxChars < 3) return ''
              return d.data.name.length > maxChars ? d.data.name.slice(0, maxChars - 1) + '\u2026' : d.data.name
            })
            .append('title')
            .text(d => d.data.name)

          // Leaf rects
          g.selectAll<SVGRectElement, TreemapNode>('rect.leaf')
            .data(leaves)
            .join('rect')
            .attr('class', 'leaf')
            .attr('x', d => d.x0)
            .attr('y', d => d.y0)
            .attr('width', d => Math.max(0, d.x1 - d.x0))
            .attr('height', d => Math.max(0, d.y1 - d.y0))
            .attr('fill', d => getColor(d))
            .attr('fill-opacity', 0.65)
            .attr('stroke', '#0f1729')
            .attr('stroke-width', 0.5)
            .attr('rx', 2)
            .style('cursor', d => d.children ? 'pointer' : 'default')
            .on('click', (_, d) => {
              if (d.children && d.children.length > 0) {
                const match = findNode(hierarchy as TreemapNode, d.data.id)
                if (match) {
                  render(match)
                  updateBreadcrumb(match)
                }
              }
              else {
                fetchNeuron(d.data.id).then(setDetail).catch(() => {})
              }
            })
            .on('mouseover', function (_, d) {
              d3.select(this).attr('fill-opacity', 0.9)
              const desc = d.descendants().length - 1
              tooltipMerged
                .style('display', 'block')
                .html(
                  `<strong>${escapeHtml(d.data.name)}</strong><br/>` +
                  `L${d.data.layer} ${escapeHtml(d.data.node_type)}<br/>` +
                  (d.data.department ? `${escapeHtml(d.data.department)}<br/>` : '') +
                  (desc > 0 ? `${desc} descendants<br/>` : '') +
                  `Invocations: ${d.data.invocations}<br/>` +
                  `Utility: ${d.data.avg_utility.toFixed(2)}`
                )
            })
            .on('mousemove', (event) => {
              tooltipMerged
                .style('left', (event.pageX + 12) + 'px')
                .style('top', (event.pageY - 10) + 'px')
            })
            .on('mouseout', function () {
              d3.select(this).attr('fill-opacity', 0.65)
              tooltipMerged.style('display', 'none')
            })

          // Leaf labels
          g.selectAll<SVGTextElement, TreemapNode>('text.leaf-label')
            .data(leaves)
            .join('text')
            .attr('class', 'leaf-label')
            .attr('x', d => d.x0 + 4)
            .attr('y', d => d.y0 + ((d.y1 - d.y0) / 2) + 4)
            .attr('fill', '#ffffff')
            .attr('font-size', '10px')
            .attr('pointer-events', 'none')
            .text(d => {
              const w = d.x1 - d.x0
              const h = d.y1 - d.y0
              if (w < 30 || h < 16) return ''
              const maxChars = Math.floor(w / 6.5)
              if (maxChars < 3) return ''
              return d.data.name.length > maxChars ? d.data.name.slice(0, maxChars - 1) + '\u2026' : d.data.name
            })

          // Also add tooltip to groups
          g.selectAll<SVGRectElement, TreemapNode>('rect.group')
            .on('mouseover', function (_, d) {
              d3.select(this).attr('stroke-opacity', 1)
              const desc = countDescendants(d)
              tooltipMerged
                .style('display', 'block')
                .html(
                  `<strong>${escapeHtml(d.data.name)}</strong><br/>` +
                  `L${d.data.layer} ${escapeHtml(d.data.node_type)}<br/>` +
                  (d.data.department ? `${escapeHtml(d.data.department)}<br/>` : '') +
                  `${desc} descendants<br/>` +
                  `Invocations: ${d.data.invocations}`
                )
            })
            .on('mousemove', (event) => {
              tooltipMerged
                .style('left', (event.pageX + 12) + 'px')
                .style('top', (event.pageY - 10) + 'px')
            })
            .on('mouseout', function () {
              d3.select(this).attr('stroke-opacity', 0.5)
              tooltipMerged.style('display', 'none')
            })
        }

        function countDescendants(d: d3.HierarchyNode<HierNode>): number {
          return d.descendants().length - 1
        }

        function findNode(node: TreemapNode, id: number): TreemapNode | null {
          if (node.data.id === id) return node
          if (node.children) {
            for (const child of node.children) {
              const found = findNode(child as TreemapNode, id)
              if (found) return found
            }
          }
          return null
        }

        function updateBreadcrumb(node: TreemapNode) {
          const ancestors = node.ancestors().reverse()
          breadcrumb.html(
            ancestors.map((a, i) =>
              i === ancestors.length - 1
                ? `<span style="color:#60a5fa">${escapeHtml(a.data.name)}</span>`
                : `<span style="cursor:pointer;color:#c8d0dc" class="bc-link" data-id="${a.data.id}">${escapeHtml(a.data.name)}</span>`
            ).join(' <span style="color:#334155">/</span> ')
          )
          breadcrumb.selectAll('.bc-link').on('click', function () {
            const id = +(d3.select(this).attr('data-id') ?? 0)
            const match = findNode(hierarchy as TreemapNode, id)
            if (match) {
              render(match)
              updateBreadcrumb(match)
            }
          })
        }

        // Initial render at root
        render(hierarchy as TreemapNode)
      })
      .catch(e => { setError(e.message); setLoading(false) })

    return () => {
      cancelled = true
      d3.select('.treemap-tooltip').remove()
    }
  }, [])

  if (error) return <div className="error-msg">{error}</div>

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        id="treemap-breadcrumb"
        style={{
          padding: '8px 16px',
          fontSize: '0.85rem',
          fontFamily: 'monospace',
          background: '#0d1321',
          borderBottom: '1px solid #1e2d4a',
          minHeight: '32px',
        }}
      />
      <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
        {loading && <div className="loading" style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>Loading...</div>}
        <svg ref={svgRef} style={{ width: '100%', height: '100%', display: 'block' }} />
        {detail && (
          <div style={{
            position: 'absolute', right: 12, bottom: 12, width: 340, maxHeight: 260,
            overflowY: 'auto', background: 'rgba(13,19,33,0.95)', border: '1px solid #2a3446',
            borderRadius: 10, padding: '10px 12px', zIndex: 10,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <div style={{ color: '#e8edf7', fontWeight: 600, fontSize: '0.85rem' }}>{detail.label}</div>
              <a style={{ cursor: 'pointer', color: '#8a93a6' }} onClick={() => setDetail(null)}>✕</a>
            </div>
            <div style={{ color: '#8a93a6', fontSize: '0.72rem', marginTop: 2 }}>
              {detail.department} · {detail.node_type} · fired {detail.invocations ?? 0}×
            </div>
            <div style={{ color: '#aeb7c8', fontSize: '0.75rem', marginTop: 8, whiteSpace: 'pre-wrap', lineHeight: 1.45 }}>
              {detail.content || detail.summary || '(no content)'}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
