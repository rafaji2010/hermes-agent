/**
 * Task nanostores — reactive state for task management UI.
 */

import { atom } from 'nanostores'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Task {
  id: string
  title: string
  description: string
  status: string
  priority: string
  workspace_id: string | null
  repository_id: string | null
  roadmap_id: string | null
  milestone_id: string | null
  adr_id: string | null
  journal_id: string | null
  labels: string[]
  estimate_hours: number | null
  actual_hours: number | null
  due_date: string
  completed_at: string | null
  dependency_ids: string[]
  depends_on_ids: string[]
  comment_count: number
  is_overdue: boolean
  created_at: string
  updated_at: string
}

export interface TaskComment {
  id: string
  task_id: string
  author: string
  body: string
  created_at: string
}

export interface TaskCreatePayload {
  title: string
  description?: string
  status?: string
  priority?: string
  workspace_id?: string | null
  repository_id?: string | null
  roadmap_id?: string | null
  milestone_id?: string | null
  adr_id?: string | null
  journal_id?: string | null
  labels?: string[]
  estimate_hours?: number | null
  actual_hours?: number | null
  due_date?: string
  dependency_ids?: string[]
}

export interface TaskUpdatePayload {
  title?: string
  description?: string
  status?: string
  priority?: string
  labels?: string[]
  estimate_hours?: number | null
  actual_hours?: number | null
  due_date?: string
  dependency_ids?: string[]
}

export interface TaskListResponse {
  tasks: Task[]
}

export interface TaskCommentListResponse {
  comments: TaskComment[]
}

export interface TaskDependencyListResponse {
  dependencies: Task[]
  depends_on: Task[]
}

// ---------------------------------------------------------------------------
// Stores
// ---------------------------------------------------------------------------

export const $tasks = atom<Task[]>([])
export const $selectedTaskId = atom<string | null>(null)
export const $taskComments = atom<TaskComment[]>([])
export const $taskDeps = atom<TaskDependencyListResponse>({ dependencies: [], depends_on: [] })
export const $tasksLoading = atom<boolean>(false)
export const $taskViewMode = atom<'kanban' | 'table'>('kanban')
