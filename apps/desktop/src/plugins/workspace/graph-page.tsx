/**
 * Graph Page — M12.1 knowledge visualization (ADR-002 adopt-before-build).
 *
 * Renders the workspace knowledge graph from the existing `/v1/graph` API
 * (graph_service.py — 153 nodes / 152 edges across workspace, roadmaps,
 * milestones, tasks, ADRs, journals) as a d3-force force-directed layout.
 * d3-force is already a dependency of the desktop app (M12.1 recommendation:
 * ADOPT existing substrate, build one page).
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import {
  EmptyState,
  ErrorState,
  Loader,
  type PluginContext,
  useQuery,
} from '@hermes/plugin-sdk'
import * as d3 from 'd3-force'

export interface GraphNode {
  id: string
  type: string
  title: string
  status?: string
}

export interface GraphEdge {
  source_id: string
  source_type: string
  target_id: string
  target_type: string
  relationship: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

const NODE_COLORS: Record<string, string> = {
  workspace: '#8b5cf6',
  roadmap: '#3b82f6',
  milestone: '#10b981',
  task: '#f59e0b',
  adr: '#ef4444',
  journal: '#ec4899',
}

const NODE_RADIUS: Record<string, number> = {
  workspace: 22,
  roadmap: 14,
  milestone: 10,
  task: 7,
  adr: 8,
  journal: 8,
}

export function GraphPage({ ctx }: { ctx: PluginContext }) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const [selected, setSelected] = useState<GraphNode | null>(null)

  const { data, isLoading, error } = useQuery<GraphData>({
    queryKey: ['workspace', 'graph'],
    queryFn: () => ctx.rest<GraphData>('/v1/graph'),
    refetchInterval: 30000,
  })

  const nodes = useMemo(() => data?.nodes ?? [], [data])
  const edges = useMemo(() => data?.edges ?? [], [data])

  // d3-force layout + render
  useEffect(() => {
    const svg = svgRef.current
    const wrap = wrapRef.current
    if (!svg || !wrap || nodes.length === 0) return

    const width = wrap.clientWidth
    const height = wrap.clientHeight || 480

    // Clear previous render
    while (svg.firstChild) svg.removeChild(svg.firstChild)

    const nodeMap = new Map(nodes.map((n) => [n.id, n]))

    const simulation = d3
      .forceSimulation(nodes as unknown as d3.SimulationNodeDatum[])
      .force(
        'link',
        d3
          .forceLink(
            edges.map((e) => ({
              source: e.source_id,
              target: e.target_id,
            })) as unknown as d3.SimulationLinkDatum<d3.SimulationNodeDatum>[]
          )
          .id((d: any) => d.id)
          .distance(60)
      )
      .force('charge', d3.forceManyBody().strength(-220))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collide', d3.forceCollide(24))

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g')

    const link = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    edges.forEach(() => {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line')
      line.setAttribute('stroke', '#4b5563')
      line.setAttribute('stroke-width', '1')
      line.setAttribute('stroke-opacity', '0.5')
      link.appendChild(line)
    })
    g.appendChild(link)

    const node = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    const nodeEls: SVGCircleElement[] = []
    nodes.forEach((n) => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle')
      circle.setAttribute('r', String(NODE_RADIUS[n.type] ?? 8))
      circle.setAttribute('fill', NODE_COLORS[n.type] ?? '#6b7280')
      circle.setAttribute('fill-opacity', '0.85')
      circle.setAttribute('stroke', '#fff')
      circle.setAttribute('stroke-width', '1.5')
      circle.style.cursor = 'pointer'
      circle.addEventListener('click', () => setSelected(n))
      node.appendChild(circle)
      nodeEls.push(circle)
    })
    g.appendChild(node)

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'g')
    const labelEls: SVGTextElement[] = []
    nodes.forEach((n) => {
      const text = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      text.setAttribute('fill', '#d1d5db')
      text.setAttribute('font-size', '9')
      text.setAttribute('text-anchor', 'middle')
      text.setAttribute('dy', '-12')
      text.textContent = n.title.length > 24 ? n.title.slice(0, 22) + '…' : n.title
      label.appendChild(text)
      labelEls.push(text)
    })
    g.appendChild(label)

    svg.appendChild(g)
    svg.setAttribute('width', String(width))
    svg.setAttribute('height', String(height))

    const linkEls = Array.from(link.children) as SVGLineElement[]
    simulation.on('tick', () => {
      linkEls.forEach((line, i) => {
        const l = (simulation.force('link') as any)?.links?.()?.[i]
        if (!l) return
        line.setAttribute('x1', l.source.x)
        line.setAttribute('y1', l.source.y)
        line.setAttribute('x2', l.target.x)
        line.setAttribute('y2', l.target.y)
      })
      nodeEls.forEach((circle, i) => {
        const n = nodes[i] as any
        circle.setAttribute('cx', n.x)
        circle.setAttribute('cy', n.y)
      })
      labelEls.forEach((text, i) => {
        const n = nodes[i] as any
        text.setAttribute('x', n.x)
        text.setAttribute('y', n.y)
      })
    })

    return () => {
      simulation.stop()
    }
  }, [nodes, edges])

  if (isLoading) return <Loader label="Loading knowledge graph…" />
  if (error) return <ErrorState title="Couldn't load the knowledge graph" description={String(error)} />

  if (nodes.length === 0) {
    return <EmptyState title="No graph data yet" description="The workspace graph is empty." />
  }

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Knowledge Graph</h2>
          <p className="text-sm text-gray-400">
            {nodes.length} nodes · {edges.length} edges — workspace, roadmaps, milestones,
            tasks, ADRs, journals
          </p>
        </div>
        {selected && (
          <div className="rounded-md border border-gray-700 bg-gray-900 px-3 py-2 text-xs">
            <span className="font-medium">{selected.title}</span>
            <span className="ml-2 text-gray-400">({selected.type})</span>
            {selected.status && <span className="ml-2 text-gray-500">· {selected.status}</span>}
          </div>
        )}
      </div>

      <div ref={wrapRef} className="min-h-[480px] flex-1 overflow-hidden rounded-lg border border-gray-800">
        <svg ref={svgRef} className="h-full w-full" />
      </div>

      <div className="flex flex-wrap gap-3 text-xs text-gray-400">
        {Object.entries(NODE_COLORS).map(([type, color]) => (
          <span key={type} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: color }} />
            {type}
          </span>
        ))}
      </div>
    </div>
  )
}
