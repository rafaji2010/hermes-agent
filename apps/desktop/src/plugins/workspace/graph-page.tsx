/**
 * Graph Page — projects → milestones → tasks, drawn Excalidraw-style as a TREE.
 *
 * Mirrors the user's whiteboard sample: a project (roadmap) box sits at the
 * root; its milestones hang below it as children; tasks (or milestone
 * sub-items) hang below each milestone as grandchildren. Deterministic,
 * dependency-free SVG — no tldraw editor, no d3-force, no physics.
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

// Excalidraw pastel palette (fill + hand-drawn stroke) per level.
const LEVEL_STYLE: Record<string, { fill: string; stroke: string; label: string }> = {
  project: { fill: '#a5d8ff', stroke: '#1e6eb5', label: 'Projects' },
  milestone: { fill: '#b2f2bb', stroke: '#2b8a3e', label: 'Milestones' },
  task: { fill: '#ffd8a8', stroke: '#e8590c', label: 'Tasks' }
}

const BOX_W = 240
const BOX_H = 64
const COL_GAP = 80
const ROW_GAP = 28
const PAD_X = 40
const PAD_Y = 40

interface Placed {
  node: GraphNode
  level: 'project' | 'milestone' | 'task'
  x: number
  y: number
}

function classify(type: string): 'project' | 'milestone' | 'task' {
  if (type === 'workspace' || type === 'roadmap') return 'project'
  if (type === 'milestone') return 'milestone'
  return 'task'
}

/**
 * Tree layout: workspace is hidden; roadmaps (projects) are roots; their
 * milestones are children (via reversed `milestone_of` edges); tasks are
 * grandchildren (via reversed `belongs_to` edges, or attached to the project
 * when a milestone link is absent). Columns: projects | milestones | tasks.
 */
function layout(nodes: GraphNode[], edges: GraphEdge[]) {
  const nodeById = new Map(nodes.map(n => [n.id, n]))
  // Reverse edges into child lists.
  const children = new Map<string, { id: string; rel: string }[]>()
  for (const e of edges) {
    const list = children.get(e.target_id) ?? []
    list.push({ id: e.source_id, rel: e.relationship })
    children.set(e.target_id, list)
  }

  // Roots: roadmaps (projects). Exclude the workspace node itself.
  const roots = nodes.filter(n => n.type === 'roadmap' || n.type === 'workspace')

  const placed: Placed[] = []
  const placedByLevel: Record<string, Placed[]> = { project: [], milestone: [], task: [] }
  const seen = new Set<string>()
  const byId = new Map<string, Placed>()

  // Column X positions.
  const colX: Record<string, number> = {
    project: PAD_X,
    milestone: PAD_X + BOX_W + COL_GAP,
    task: PAD_X + 2 * (BOX_W + COL_GAP)
  }
  const maxY: Record<string, number> = { project: PAD_Y, milestone: PAD_Y, task: PAD_Y }

  // Assign a node to its level column at the next free Y.
  const place = (n: GraphNode, level: 'project' | 'milestone' | 'task'): Placed => {
    const p: Placed = { node: n, level, x: colX[level], y: maxY[level] }
    placed.push(p)
    placedByLevel[level].push(p)
    byId.set(n.id, p)
    maxY[level] += BOX_H + ROW_GAP
    seen.add(n.id)
    return p
  }

  // Recursive: place node at `level`, then its children at level+1.
  const walk = (n: GraphNode, level: 'project' | 'milestone' | 'task') => {
    if (seen.has(n.id)) return
    const p = place(n, level)
    const kids = children.get(n.id) ?? []
    const nextLevel =
      level === 'project' ? 'milestone' : level === 'milestone' ? 'task' : 'task'
    for (const kid of kids) {
      const kn = nodeById.get(kid.id)
      if (kn && !seen.has(kn.id)) walk(kn, nextLevel)
    }
    return p
  }

  for (const r of roots) {
    if (r.type === 'workspace') continue // hide the workspace wrapper
    walk(r, 'project')
  }

  // Orphan milestones/tasks (no roadmap edge) — attach to a fallback column.
  for (const n of nodes) {
    if (seen.has(n.id)) continue
    walk(n, classify(n.type))
  }

  const width = colX.task + BOX_W + PAD_X
  const height = Math.max(maxY.project, maxY.milestone, maxY.task) + PAD_Y
  return { placed, byId, width, height }
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

  const { placed, byId, width, height } = useMemo(() => layout(nodes, edges), [nodes, edges])

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
          {/* Edges: curved arrows — for tree edges, connect parent bottom to child top. */}
          {drawnEdges.map(e => {
            const a = byId.get(e.source_id)!
            const b = byId.get(e.target_id)!
            // If both are placed, draw from parent bottom-center to child top-center
            // when a is a parent level (project/milestone); else right-edge to left-edge.
            const parentOf = a.level === 'project' || a.level === 'milestone'
            const x1 = parentOf ? a.x + BOX_W / 2 : a.x + BOX_W
            const y1 = parentOf ? a.y + BOX_H : a.y + BOX_H / 2
            const x2 = parentOf ? b.x + BOX_W / 2 : b.x
            const y2 = parentOf ? b.y : b.y + BOX_H / 2
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
            const st = LEVEL_STYLE[p.level]
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
        {(['project', 'milestone', 'task'] as const).map(level => (
          <span key={level} className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-3 w-3 rounded-sm"
              style={{ background: LEVEL_STYLE[level].fill, border: `1px solid ${LEVEL_STYLE[level].stroke}` }}
            />
            {LEVEL_STYLE[level].label}
          </span>
        ))}
      </div>
    </div>
  )
}
