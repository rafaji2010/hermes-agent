/**
 * Board API — execution-board wrapper (ADR-010 execution layer, M11.4).
 *
 * The renderer's plugin REST namespace is scoped to /api/plugins/workspace
 * by construction; the backend proxies the kanban plugin's board at
 * /v1/execution-board so this pane stays inside the SDK boundary.
 */

import type { PluginContext } from '@hermes/plugin-sdk'

export interface KanbanTask {
  id: string
  title: string
  status: string
  assignee?: string | null
  priority?: string | null
  deadline?: string | null
  created_at?: string
  updated_at?: string
}

export interface KanbanBoardColumn {
  name: string
  tasks: KanbanTask[]
}

export interface KanbanBoard {
  columns: KanbanBoardColumn[]
  tenants: unknown[]
  assignees: unknown[]
  latest_event_id: number
  now: number
}

export async function fetchExecutionBoard(ctx: PluginContext): Promise<KanbanBoard> {
  return ctx.rest<KanbanBoard>('/v1/execution-board')
}
