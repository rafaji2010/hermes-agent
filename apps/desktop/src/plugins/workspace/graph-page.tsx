/**
 * Graph Page — knowledge graph as a tldraw diagram.
 *
 * Replaces the previous d3-force force-directed SVG layout with a tldraw
 * canvas (the same offline substrate as the Whiteboard plugin). The graph is
 * rendered as box nodes connected by arrows — projects (workspace/roadmap)
 * on the left, milestones in the middle, tasks (and other leaf entities) on
 * the right — a deterministic layered layout, not a physics simulation.
 *
 * tldraw is already a dependency (bundled by the M12.5 whiteboard plugin);
 * this page reuses the same local/offline asset wiring so nothing reaches
 * the network.
 */

import '@tldraw/tldraw/tldraw.css'

import type { PluginContext } from '@hermes/plugin-sdk'
import { EmptyState, ErrorState, Loader, useQuery } from '@hermes/plugin-sdk'
import {
  createShapeId,
  createTLStore,
  iconTypes,
  Tldraw,
  toRichText,
  type Editor,
  type TLDefaultColorStyle,
  type TLCreateShapePartial,
  type TLEditorAssetUrls,
  type TLShapeId,
  type TLStore
} from '@tldraw/tldraw'
import { useCallback, useMemo, useRef, useState } from 'react'

import iconSpriteUrl from '../whiteboard/assets/0_merged.svg?url'
import monoBoldUrl from '../whiteboard/assets/fonts/IBMPlexMono-Bold.woff2?url'
import monoBoldItalicUrl from '../whiteboard/assets/fonts/IBMPlexMono-BoldItalic.woff2?url'
import monoUrl from '../whiteboard/assets/fonts/IBMPlexMono-Medium.woff2?url'
import monoItalicUrl from '../whiteboard/assets/fonts/IBMPlexMono-MediumItalic.woff2?url'
import sansBoldUrl from '../whiteboard/assets/fonts/IBMPlexSans-Bold.woff2?url'
import sansBoldItalicUrl from '../whiteboard/assets/fonts/IBMPlexSans-BoldItalic.woff2?url'
import sansUrl from '../whiteboard/assets/fonts/IBMPlexSans-Medium.woff2?url'
import sansItalicUrl from '../whiteboard/assets/fonts/IBMPlexSans-MediumItalic.woff2?url'
import serifBoldUrl from '../whiteboard/assets/fonts/IBMPlexSerif-Bold.woff2?url'
import serifBoldItalicUrl from '../whiteboard/assets/fonts/IBMPlexSerif-BoldItalic.woff2?url'
import serifUrl from '../whiteboard/assets/fonts/IBMPlexSerif-Medium.woff2?url'
import serifItalicUrl from '../whiteboard/assets/fonts/IBMPlexSerif-MediumItalic.woff2?url'
import drawBoldUrl from '../whiteboard/assets/fonts/Shantell_Sans-Informal_Bold.woff2?url'
import drawBoldItalicUrl from '../whiteboard/assets/fonts/Shantell_Sans-Informal_Bold_Italic.woff2?url'
import drawUrl from '../whiteboard/assets/fonts/Shantell_Sans-Informal_Regular.woff2?url'
import drawItalicUrl from '../whiteboard/assets/fonts/Shantell_Sans-Informal_Regular_Italic.woff2?url'
import { useIsDark } from '../whiteboard/use-is-dark'

const OFFLINE_FONTS: TLEditorAssetUrls['fonts'] = {
  tldraw_mono: monoUrl,
  tldraw_mono_italic: monoItalicUrl,
  tldraw_mono_bold: monoBoldUrl,
  tldraw_mono_italic_bold: monoBoldItalicUrl,
  tldraw_serif: serifUrl,
  tldraw_serif_italic: serifItalicUrl,
  tldraw_serif_bold: serifBoldUrl,
  tldraw_serif_italic_bold: serifBoldItalicUrl,
  tldraw_sans: sansUrl,
  tldraw_sans_italic: sansItalicUrl,
  tldraw_sans_bold: sansBoldUrl,
  tldraw_sans_italic_bold: sansBoldItalicUrl,
  tldraw_draw: drawUrl,
  tldraw_draw_italic: drawItalicUrl,
  tldraw_draw_bold: drawBoldUrl,
  tldraw_draw_italic_bold: drawBoldItalicUrl
}

const OFFLINE_ICONS: Record<string, string> = Object.fromEntries(
  iconTypes.map(name => [name, `${iconSpriteUrl}#${name}`])
)

const ASSET_URLS = { fonts: OFFLINE_FONTS, icons: OFFLINE_ICONS }

// ── Data model (matches backend GraphNode / GraphEdge) ──────────────────────

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

// ── Layout ──────────────────────────────────────────────────────────────────
// Deterministic layered layout: projects (left) → milestones (middle) →
// tasks / ADRs / journals (right). Columns are stacked vertically with a
// fixed gap; edges are drawn as tldraw arrows between the box centres.

const COLUMN_X = { project: 0, milestone: 440, task: 880 }
const COLUMN_ORDER = ['project', 'milestone', 'task'] as const

function classify(type: string): 'project' | 'milestone' | 'task' {
  if (type === 'workspace' || type === 'roadmap') return 'project'
  if (type === 'milestone') return 'milestone'
  return 'task'
}

const NODE_COLORS: Record<string, TLDefaultColorStyle> = {
  project: 'violet',
  milestone: 'light-green',
  task: 'orange'
}

const BOX_WIDTH = 360
const BOX_HEIGHT = 60
const VERTICAL_GAP = 28

function computePositions(
  nodes: GraphNode[]
): Map<string, { x: number; y: number; col: 'project' | 'milestone' | 'task' }> {
  const buckets: Record<'project' | 'milestone' | 'task', GraphNode[]> = {
    project: [],
    milestone: [],
    task: []
  }
  for (const n of nodes) buckets[classify(n.type)].push(n)

  const positions = new Map<string, { x: number; y: number; col: 'project' | 'milestone' | 'task' }>()
  for (const col of COLUMN_ORDER) {
    let y = 0
    for (const n of buckets[col]) {
      positions.set(n.id, {
        x: COLUMN_X[col] + BOX_WIDTH / 2,
        y: y + BOX_HEIGHT / 2,
        col
      })
      y += BOX_HEIGHT + VERTICAL_GAP
    }
  }
  return positions
}

// ── Shape insertion (on editor mount) ───────────────────────────────────────

function buildGraph(editor: Editor, data: GraphData) {
  const positions = computePositions(data.nodes)

  const shapes: TLCreateShapePartial[] = []

  for (const n of data.nodes) {
    const p = positions.get(n.id)
    if (!p) continue
    const color = NODE_COLORS[p.col]
    const label = n.title.length > 42 ? `${n.title.slice(0, 40)}…` : n.title
    shapes.push({
      id: createShapeId(`node-${n.id}`),
      type: 'geo',
      x: p.x - BOX_WIDTH / 2,
      y: p.y - BOX_HEIGHT / 2,
      props: {
        geo: 'rectangle',
        w: BOX_WIDTH,
        h: BOX_HEIGHT,
        color,
        fill: 'solid',
        size: 'm'
      }
    })
    // Label as a separate text shape centred on the box.
    shapes.push({
      id: createShapeId(`label-${n.id}`),
      type: 'text',
      x: p.x - BOX_WIDTH / 2 + 8,
      y: p.y - BOX_HEIGHT / 2 + 8,
      props: {
        richText: toRichText(label),
        color: 'black',
        size: 's',
        w: BOX_WIDTH - 16,
        autoSize: true
      }
    })
  }

  for (const e of data.edges) {
    const a = positions.get(e.source_id)
    const b = positions.get(e.target_id)
    if (!a || !b) continue
    shapes.push({
      id: createShapeId(`edge-${e.source_id}-${e.target_id}`),
      type: 'arrow',
      x: a.x,
      y: a.y,
      props: {
        color: 'grey',
        size: 's',
        start: { x: a.x, y: a.y },
        end: { x: b.x, y: b.y }
      }
    })
  }

  editor.createShapes(shapes)
  editor.zoomToFit({ animation: { duration: 0 } })
}

// ── Page ────────────────────────────────────────────────────────────────────

export function GraphPage({ ctx }: { ctx: PluginContext }) {
  const isDark = useIsDark()
  const { data, isLoading, error } = useQuery<GraphData>({
    queryKey: ['workspace', 'graph'],
    queryFn: () => ctx.rest<GraphData>('/v1/graph'),
    refetchInterval: 30000
  })

  const nodes = data?.nodes ?? []
  const edges = data?.edges ?? []

  const store = useMemo<TLStore>(() => createTLStore(), [])
  const [builtFor, setBuiltFor] = useState<string>('')

  // (Re)build the graph when the data changes (e.g. 30s polling picks up a
  // new milestone/task). We track the shape ids we inserted so a rebuild can
  // clear them first.
  const shapeIdsRef = useRef<TLShapeId[]>([])

  const onMount = useCallback(
    (editor: Editor) => {
      const key = `${nodes.length}:${edges.length}`
      if (builtFor !== key && nodes.length > 0) {
        // Clear any previously-inserted shapes before rebuilding.
        if (shapeIdsRef.current.length > 0) {
          editor.deleteShapes(shapeIdsRef.current)
        }
        const before = new Set(
          Array.from(editor.getCurrentPageShapes()).map(s => s.id)
        )
        buildGraph(editor, { nodes, edges })
        const after = new Set(
          Array.from(editor.getCurrentPageShapes()).map(s => s.id)
        )
        shapeIdsRef.current = Array.from(after).filter(id => !before.has(id))
        setBuiltFor(key)
      }
    },
    [builtFor, nodes, edges]
  )

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
            {nodes.length} nodes · {edges.length} edges — projects, milestones, tasks
          </p>
        </div>
      </div>

      <div className="min-h-[480px] flex-1 overflow-hidden rounded-lg border border-gray-800">
        <Tldraw
          assetUrls={ASSET_URLS}
          colorScheme={isDark ? 'dark' : 'light'}
          store={store}
          onMount={onMount}
        />
      </div>
    </div>
  )
}
