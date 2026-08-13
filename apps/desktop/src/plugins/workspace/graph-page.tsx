/**
 * Graph Page — projects → milestones → tasks, drawn Excalidraw-style.
 *
 * A deliberately simple, dependency-free SVG diagram. No tldraw editor, no
 * d3-force, no physics, no asset files — just a deterministic three-column
 * layout (projects | milestones | tasks) with hand-drawn boxes and arrows in
 * Excalidraw's palette. This replaces the earlier d3-force and tldraw-editor
 * attempts, both of which were fragile for a read-only overview.
 *
 * Scope: like every workspace surface, this page resolves the project scope
 * via `useWorkspaceScope` and passes `workspace_id` on the `/v1/graph` query
 * — the backend refuses unscoped requests (403 SCOPE_UNRESOLVED).
 */

import type { PluginContext } from '@hermes/plugin-sdk'
import { EmptyState, ErrorState, Loader, useQuery } from '@hermes/plugin-sdk'
import { useMemo } from 'react'

import { scopeReady, useWorkspaceScope } from './scope'
import { WorkspaceScopeNotice } from './scope-notice'

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

type Column = 'project' | 'milestone' | 'task'

function classify(type: string): Column {
  if (type === 'workspace' || type === 'roadmap') return 'project'
  if (type === 'milestone') return 'milestone'
  return 'task'
}

// Excalidraw pastel palette (fill + hand-drawn stroke).
const COLUMN_STYLE: Record<Column, { fill: string; stroke: string; label: string }> = {
  project: { fill: '#a5d8ff', stroke: '#1e6eb5', label: 'Projects' },
  milestone: { fill: '#b2f2bb', stroke: '#2b8a3e', label: 'Milestones' },
  task: { fill: '#ffd8a8', stroke: '#e8590c', label: 'Tasks' }
}

const COLUMN_ORDER: Column[] = ['project', 'milestone', 'task']

const BOX_W = 240
const BOX_H = 64
const COL_GAP = 80
const ROW_GAP = 28
const PAD_X = 40
const PAD_Y = 40

interface Placed {
  node: GraphNode
  col: Column
  x: number
  y: number
}

function layout(nodes: GraphNode[]) {
  const buckets: Record<Column, GraphNode[]> = { project: [], milestone: [], task: [] }
  for (const n of nodes) buckets[classify(n.type)].push(n)

  const placed: Placed[] = []
  const byId = new Map<string, Placed>()

  let colX = PAD_X
  let maxHeight = 0
  for (const col of COLUMN_ORDER) {
    let y = PAD_Y
    for (const n of buckets[col]) {
      const p: Placed = { node: n, col, x: colX, y }
      placed.push(p)
      byId.set(n.id, p)
      y += BOX_H + ROW_GAP
    }
    maxHeight = Math.max(maxHeight, y)
    colX += BOX_W + COL_GAP
  }

  return { placed, byId, width: colX + PAD_X, height: maxHeight + PAD_Y }
}

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

export function GraphPage({ ctx }: { ctx: PluginContext }) {
  const scope = useWorkspaceScope(ctx)
  const ws = scopeReady(scope) ? scope.workspaceId : ''

  const { data, isLoading, error } = useQuery<GraphData>({
    queryKey: ['workspace', 'graph', ws],
    queryFn: () => ctx.rest<GraphData>(`/v1/graph?workspace_id=${encodeURIComponent(ws)}`),
    enabled: Boolean(ws),
    refetchInterval: 30000
  })

  const nodes = data?.nodes ?? []
  const edges = data?.edges ?? []

  const { placed, byId, width, height } = useMemo(() => layout(nodes), [nodes])

  if (!ws) {
    return <WorkspaceScopeNotice ctx={ctx} scope={scope} />
  }

  if (isLoading) return <Loader label="Loading knowledge graph…" />
  if (error) return <ErrorState title="Couldn't load the knowledge graph" description={String(error)} />
  if (nodes.length === 0) {
    return <EmptyState title="No graph data yet" description="The workspace graph is empty." />
  }

  // Edges whose endpoints both exist.
  const drawnEdges = edges.filter(e => byId.has(e.source_id) && byId.has(e.target_id))

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Knowledge Graph</h2>
          <p className="text-sm text-gray-400">
            {nodes.length} nodes · {edges.length} edges — projects, milestones, tasks
          </p>
        </div>
      </div>

      <div className="min-h-[480px] flex-1 overflow-auto rounded-lg border border-gray-800 bg-white">
        <svg
          width={width}
          height={Math.max(height, 480)}
          viewBox={`0 0 ${width} ${Math.max(height, 480)}`}
          className="block"
          role="img"
          aria-label="Workspace knowledge graph"
        >
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="#868e96" />
            </marker>
          </defs>
          {/* Edges: subtle curved arrows from source right-edge to target left-edge. */}
          {drawnEdges.map(e => {
            const a = byId.get(e.source_id)!
            const b = byId.get(e.target_id)!
            const x1 = a.x + BOX_W
            const y1 = a.y + BOX_H / 2
            const x2 = b.x
            const y2 = b.y + BOX_H / 2
            const mx = (x1 + x2) / 2
            return (
              <path
                key={`${e.source_id}->${e.target_id}`}
                d={`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`}
                fill="none"
                stroke="#868e96"
                strokeWidth={1.6}
                markerEnd="url(#arrow)"
              />
            )
          })}

          {/* Nodes: hand-drawn boxes with pastel fill + bound label. */}
          {placed.map(p => {
            const st = COLUMN_STYLE[p.col]
            return (
              <g key={p.node.id}>
                <rect
                  x={p.x}
                  y={p.y}
                  width={BOX_W}
                  height={BOX_H}
                  rx={8}
                  fill={st.fill}
                  stroke={st.stroke}
                  strokeWidth={2}
                />
                <text
                  x={p.x + BOX_W / 2}
                  y={p.y + BOX_H / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize={13}
                  fontFamily="'Virgil', 'Segoe Print', 'Comic Sans MS', cursive"
                  fill="#1e1e1e"
                >
                  {truncate(p.node.title, 30)}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs text-gray-400">
        {COLUMN_ORDER.map(col => (
          <span key={col} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-3 w-3 rounded-sm"
              style={{ background: COLUMN_STYLE[col].fill, border: `1px solid ${COLUMN_STYLE[col].stroke}` }}
            />
            {COLUMN_STYLE[col].label}
          </span>
        ))}
      </div>
    </div>
  )
}
