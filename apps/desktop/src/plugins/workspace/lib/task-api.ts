/**
 * Task API — REST wrappers for task + comment + dependency endpoints.
 */

import type { PluginContext } from '@/contrib/plugin'
import type {
  TaskCreatePayload,
  TaskDependencyListResponse,
  TaskListResponse,
  TaskUpdatePayload,
  TaskCommentListResponse,
} from '../stores/tasks'

function qs(params: Record<string, string | undefined>): string {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') p.set(k, v)
  }
  return p.toString()
}

export async function fetchTasks(
  ctx: PluginContext,
  params: {
    workspace_id?: string
    status?: string
    priority?: string
    label?: string
    repository_id?: string
    roadmap_id?: string
    milestone_id?: string
    adr_id?: string
    journal_id?: string
    q?: string
    overdue?: boolean
    limit?: number
  },
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>(`/v1/tasks?${qs(params as Record<string,string|undefined>)}`)
}

export async function searchTasks(
  ctx: PluginContext,
  params: {
    workspace_id?: string
    status?: string
    priority?: string
    label?: string
    repository_id?: string
    roadmap_id?: string
    milestone_id?: string
    adr_id?: string
    journal_id?: string
    q?: string
    overdue?: boolean
  },
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>(`/v1/tasks/search?${qs(params as Record<string,string|undefined>)}`)
}

export async function getTask(ctx: PluginContext, id: string): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>(`/v1/tasks/${encodeURIComponent(id)}`)
}

export async function createTask(
  ctx: PluginContext,
  payload: TaskCreatePayload,
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>('/v1/tasks', { method: 'POST', body: JSON.stringify(payload) })
}

export async function updateTask(
  ctx: PluginContext,
  id: string,
  payload: TaskUpdatePayload,
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>(`/v1/tasks/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteTask(ctx: PluginContext, id: string): Promise<{ ok: boolean }> {
  return ctx.rest<{ ok: boolean }>(`/v1/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function fetchComments(
  ctx: PluginContext,
  taskId: string,
): Promise<TaskCommentListResponse> {
  return ctx.rest<TaskCommentListResponse>(`/v1/tasks/${encodeURIComponent(taskId)}/comments`)
}

export async function addComment(
  ctx: PluginContext,
  taskId: string,
  body: string,
): Promise<TaskCommentListResponse> {
  return ctx.rest<TaskCommentListResponse>(`/v1/tasks/${encodeURIComponent(taskId)}/comments`, {
    method: 'POST',
    body: JSON.stringify({ body }),
  })
}

export async function getDependencies(
  ctx: PluginContext,
  taskId: string,
): Promise<TaskDependencyListResponse> {
  return ctx.rest<TaskDependencyListResponse>(
    `/v1/tasks/${encodeURIComponent(taskId)}/dependencies`,
  )
}

export async function setDependencies(
  ctx: PluginContext,
  taskId: string,
  depends_on_ids: string[],
): Promise<TaskDependencyListResponse> {
  return ctx.rest<TaskDependencyListResponse>(
    `/v1/tasks/${encodeURIComponent(taskId)}/dependencies`,
    { method: 'PUT', body: JSON.stringify({ depends_on_ids }) },
  )
}
