/**
 * Task API — REST wrappers for task + comment + dependency endpoints.
 *
 * U1C: `ctx.rest()` serializes object bodies itself — every mutation passes
 * a plain object, never a pre-serialized JSON string.  Get-by-id helpers
 * carry the effective workspace scope for the backend membership check.
 */

import type { PluginContext } from '@hermes/plugin-sdk'

import type {
  TaskCommentListResponse,
  TaskCreatePayload,
  TaskDependencyListResponse,
  TaskListResponse,
  TaskUpdatePayload,
} from './tasks'

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const p = new URLSearchParams()

  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {p.set(k, String(v))}
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
  return ctx.rest<TaskListResponse>(`/v1/tasks?${qs(params)}`)
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
  return ctx.rest<TaskListResponse>(`/v1/tasks/search?${qs(params)}`)
}

export async function getTask(
  ctx: PluginContext,
  id: string,
  workspaceId = '',
): Promise<TaskListResponse> {
  const scope = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<TaskListResponse>(`/v1/tasks/${encodeURIComponent(id)}${scope}`)
}

export async function createTask(
  ctx: PluginContext,
  payload: TaskCreatePayload,
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>('/v1/tasks', { method: 'POST', body: payload })
}

export async function updateTask(
  ctx: PluginContext,
  id: string,
  payload: TaskUpdatePayload,
): Promise<TaskListResponse> {
  return ctx.rest<TaskListResponse>(`/v1/tasks/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: payload,
  })
}

export async function deleteTask(ctx: PluginContext, id: string): Promise<{ ok: boolean }> {
  return ctx.rest<{ ok: boolean }>(`/v1/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function fetchComments(
  ctx: PluginContext,
  taskId: string,
  workspaceId = '',
): Promise<TaskCommentListResponse> {
  const scope = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<TaskCommentListResponse>(
    `/v1/tasks/${encodeURIComponent(taskId)}/comments${scope}`,
  )
}

export async function addComment(
  ctx: PluginContext,
  taskId: string,
  body: string,
): Promise<TaskCommentListResponse> {
  return ctx.rest<TaskCommentListResponse>(`/v1/tasks/${encodeURIComponent(taskId)}/comments`, {
    method: 'POST',
    body: { body },
  })
}

export async function getDependencies(
  ctx: PluginContext,
  taskId: string,
  workspaceId = '',
): Promise<TaskDependencyListResponse> {
  const scope = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<TaskDependencyListResponse>(
    `/v1/tasks/${encodeURIComponent(taskId)}/dependencies${scope}`,
  )
}

export async function setDependencies(
  ctx: PluginContext,
  taskId: string,
  dependsOnIds: string[],
): Promise<TaskDependencyListResponse> {
  return ctx.rest<TaskDependencyListResponse>(
    `/v1/tasks/${encodeURIComponent(taskId)}/dependencies`,
    { method: 'PUT', body: { depends_on_ids: dependsOnIds } },
  )
}
