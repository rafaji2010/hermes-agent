/**
 * Tasks Page — Kanban board + table view, task CRUD, search, detail.
 *
 * U1C: the resolved project scope is the single source of truth for the
 * workspace; task/comment/dependency data comes from React Query, never
 * from cross-workspace module-level atoms.
 */

import {
  Badge,
  Button,
  cn,
  ConfirmDialog,
  Contribute,
  EmptyState,
  ErrorState,
  Loader,
  type PluginContext,
  useQuery,
  useQueryClient,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import { scopeReady, useWorkspaceScope } from './scope'
import { WorkspaceScopeNotice } from './scope-notice'
import {
  addComment as apiAddComment,
  createTask as apiCreateTask,
  deleteTask as apiDeleteTask,
  getTask as apiGetTask,
  updateTask as apiUpdateTask,
  fetchComments,
  fetchTasks,
  getDependencies,
  searchTasks,
} from './task-api'
import {
  $taskViewMode,
  type Task,
} from './tasks'

// ---------------------------------------------------------------------------
// Status & priority colors
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  todo: 'bg-gray-500/10 text-gray-500 border-gray-500/20',
  in_progress: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  blocked: 'bg-red-500/10 text-red-500 border-red-500/20',
  review: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  done: 'bg-green-500/10 text-green-500 border-green-500/20',
  cancelled: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
}

const PRIORITY_COLORS: Record<string, string> = {
  critical: 'bg-red-500/10 text-red-400',
  high: 'bg-orange-500/10 text-orange-400',
  medium: 'bg-blue-500/10 text-blue-400',
  low: 'bg-gray-500/10 text-gray-400',
}

const KANBAN_COLUMNS: { key: string; label: string }[] = [
  { key: 'todo', label: 'To Do' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'blocked', label: 'Blocked' },
  { key: 'review', label: 'Review' },
  { key: 'done', label: 'Done' },
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
        STATUS_COLORS[status] || STATUS_COLORS.todo,
      )}
    >
      {status.replace('_', ' ')}
    </span>
  )
}

function PriorityBadge({ priority }: { priority: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium',
        PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium,
      )}
    >
      {priority}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Task form
// ---------------------------------------------------------------------------

interface TaskFormProps {
  workspaceId: string
  editing?: Task | null
  onCancel: () => void
  onDone: () => void
  ctx: PluginContext
}

function TaskForm({ workspaceId, editing, onCancel, onDone, ctx }: TaskFormProps) {
  const [title, setTitle] = useState(editing?.title ?? '')
  const [desc, setDesc] = useState(editing?.description ?? '')
  const [status, setStatus] = useState(editing?.status ?? 'todo')
  const [priority, setPriority] = useState(editing?.priority ?? 'medium')
  const [labels, setLabels] = useState((editing?.labels ?? []).join(', '))
  const [saving, setSaving] = useState(false)

  const handleSave = useCallback(async () => {
    if (!title.trim()) {return}
    setSaving(true)

    try {
      const labelList = labels.split(',').map(l => l.trim()).filter(Boolean)

      if (editing) {
        await apiUpdateTask(ctx, editing.id, { title: title.trim(), description: desc.trim(), status, priority, labels: labelList })
      } else {
        await apiCreateTask(ctx, { workspace_id: workspaceId, title: title.trim(), description: desc.trim(), status, priority, labels: labelList })
      }

      onDone()
    } finally {
      setSaving(false)
    }
  }, [title, desc, status, priority, labels, editing, workspaceId, ctx, onDone])

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-3">
      <input
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
        onChange={e => setTitle(e.target.value)}
        placeholder="Task title"
        value={title}
      />
      <textarea
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus) resize-none"
        onChange={e => setDesc(e.target.value)}
        placeholder="Description"
        rows={2}
        value={desc}
      />
      <div className="flex gap-4">
        <div className="flex items-center gap-2">
          <span className="text-xs text-(--ui-text-tertiary)">Status:</span>
          <select
            className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-sm text-(--ui-text-primary)"
            onChange={e => setStatus(e.target.value)}
            value={status}
          >
            {['todo', 'in_progress', 'blocked', 'review', 'done', 'cancelled'].map(s => (
              <option key={s} value={s}>{s.replace('_', ' ')}</option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-(--ui-text-tertiary)">Priority:</span>
          <select
            className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-sm text-(--ui-text-primary)"
            onChange={e => setPriority(e.target.value)}
            value={priority}
          >
            {['critical', 'high', 'medium', 'low'].map(p => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
      </div>
      <input
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
        onChange={e => setLabels(e.target.value)}
        placeholder="Labels (comma-separated)"
        value={labels}
      />
      <div className="flex gap-2 justify-end">
        <Button disabled={saving} onClick={onCancel} size="sm" variant="secondary">Cancel</Button>
        <Button disabled={saving || !title.trim()} onClick={handleSave} size="sm">
          {saving ? 'Saving...' : editing ? 'Update' : 'Create'}
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Task card (kanban)
// ---------------------------------------------------------------------------

interface TaskCardProps {
  task: Task
  onSelect: (id: string) => void
  onStatusChange: (id: string, status: string) => void
}

function TaskCard({ task, onSelect, onStatusChange }: TaskCardProps) {
  return (
    <div
      className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) p-3 cursor-pointer hover:border-(--ui-stroke-focus) space-y-2"
      onClick={() => onSelect(task.id)}
    >
      <div className="flex items-start gap-2 justify-between">
        <span className="text-sm font-medium text-(--ui-text-primary) line-clamp-2 flex-1">
          {task.title}
        </span>
        <PriorityBadge priority={task.priority} />
      </div>
      {task.labels.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {task.labels.slice(0, 3).map(l => (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-(--ui-bg-quaternary) text-(--ui-text-tertiary)" key={l}>{l}</span>
          ))}
          {task.labels.length > 3 && (
            <span className="text-[10px] text-(--ui-text-tertiary)">+{task.labels.length - 3}</span>
          )}
        </div>
      )}
      {task.is_overdue && (
        <span className="text-[10px] text-red-400">Overdue</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Kanban column
// ---------------------------------------------------------------------------

interface KanbanColumnProps {
  label: string
  tasks: Task[]
  status: string
  onSelect: (id: string) => void
  onStatusChange: (id: string, status: string) => void
}

function KanbanColumn({ label, tasks, status, onSelect, onStatusChange }: KanbanColumnProps) {
  return (
    <div className="flex flex-col min-w-[240px] max-w-[280px] flex-1">
      <div className="flex items-center justify-between px-2 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary)">
          {label}
        </span>
        <span className="text-xs text-(--ui-text-tertiary)">{tasks.length}</span>
      </div>
      <div className="space-y-2 flex-1 overflow-auto px-1">
        {tasks.map(t => (
          <TaskCard
            key={t.id}
            onSelect={onSelect}
            onStatusChange={onStatusChange}
            task={t}
          />
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Task detail panel
// ---------------------------------------------------------------------------

interface TaskDetailProps {
  ctx: PluginContext
  taskId: string
  workspaceId: string
  onClose: () => void
  onRefresh: () => void
}

function TaskDetail({ ctx, taskId, workspaceId, onClose, onRefresh }: TaskDetailProps) {
  const [commentText, setCommentText] = useState('')
  const [sendingComment, setSendingComment] = useState(false)

  const taskQuery = useQuery({
    queryKey: ['workspace', 'task', workspaceId, taskId],
    queryFn: async () => {
      const r = await apiGetTask(ctx, taskId, workspaceId)

      return r.tasks[0]
    },
  })

  const commentsQuery = useQuery({
    queryKey: ['workspace', 'task', workspaceId, taskId, 'comments'],
    queryFn: () => fetchComments(ctx, taskId, workspaceId),
  })

  const depsQuery = useQuery({
    queryKey: ['workspace', 'task', workspaceId, taskId, 'deps'],
    queryFn: () => getDependencies(ctx, taskId, workspaceId),
  })

  const task = taskQuery.data
  const comments = commentsQuery.data?.comments ?? []

  const handleAddComment = useCallback(async () => {
    if (!commentText.trim()) {return}
    setSendingComment(true)

    try {
      await apiAddComment(ctx, taskId, commentText.trim())
      setCommentText('')
      onRefresh()
    } finally {
      setSendingComment(false)
    }
  }, [commentText, ctx, taskId, onRefresh])

  if (!task) {return <Loader type="lemniscate-bloom" />}

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-4 max-h-full overflow-auto">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-sm font-medium text-(--ui-text-primary)">{task.title}</h3>
          <div className="flex gap-2 mt-1">
            <StatusBadge status={task.status} />
            <PriorityBadge priority={task.priority} />
          </div>
        </div>
        <Button onClick={onClose} size="xs" variant="ghost">Close</Button>
      </div>

      {task.description && (
        <p className="text-sm text-(--ui-text-secondary)">{task.description}</p>
      )}

      {task.labels.length > 0 && (
        <div className="flex gap-1 flex-wrap">
          {task.labels.map(l => (
            <Badge key={l} variant="muted">{l}</Badge>
          ))}
        </div>
      )}

      {task.due_date && (
        <div className="text-xs text-(--ui-text-tertiary)">
          Due: {task.due_date} {task.is_overdue && <span className="text-red-400">(Overdue)</span>}
        </div>
      )}

      {task.completed_at && (
        <div className="text-xs text-(--ui-text-tertiary)">Completed: {task.completed_at}</div>
      )}

      <div className="border-t border-(--ui-stroke-tertiary) pt-3">
        <h4 className="text-xs font-medium text-(--ui-text-tertiary) mb-2">Comments</h4>
        <div className="space-y-2 mb-3">
          {comments.map(c => (
            <div className="text-xs bg-(--ui-bg-primary) rounded p-2" key={c.id}>
              <div className="text-(--ui-text-secondary)">{c.body}</div>
              <div className="text-(--ui-text-tertiary) mt-0.5">{c.created_at}</div>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            className="flex-1 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary) outline-none"
            onChange={e => setCommentText(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') {void handleAddComment()} }}
            placeholder="Add a comment..."
            value={commentText}
          />
          <Button disabled={sendingComment || !commentText.trim()} onClick={() => void handleAddComment()} size="xs">Send</Button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function PageTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Tasks</span>
    </div>
  )
}

interface TasksPageProps {
  ctx: PluginContext
}

export function TasksPage({ ctx }: TasksPageProps) {
  const queryClient = useQueryClient()
  const scope = useWorkspaceScope(ctx)
  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [detailId, setDetailId] = useState<string | null>(null)
  const [searchQ, setSearchQ] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const viewMode = useValue($taskViewMode)

  // The resolved project scope is authoritative. An unresolvable scope
  // yields '' and gates every query off.
  const ws = scopeReady(scope) ? scope.workspaceId : ''

  const tasksQuery = useQuery({
    queryKey: ['workspace', 'tasks', ws, searchQ, statusFilter],
    queryFn: async () => {
      const params: Record<string, string | undefined> = {}

      if (ws) {params.workspace_id = ws}

      if (statusFilter) {params.status = statusFilter}

      if (searchQ) {params.q = searchQ}

      return searchQ
        ? searchTasks(ctx, params)
        : fetchTasks(ctx, params)
    },
    enabled: Boolean(ws),
    staleTime: 0,
  })

  const tasks = tasksQuery.data?.tasks ?? []

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['workspace', 'tasks'] })
    queryClient.invalidateQueries({ queryKey: ['workspace', 'task'] })
  }, [queryClient])

  const handleDelete = useCallback(async (id: string) => {
    setDeleteId(null)
    await apiDeleteTask(ctx, id)
    handleRefresh()
  }, [ctx, handleRefresh])

  const handleStatusChange = useCallback(async (id: string, status: string) => {
    await apiUpdateTask(ctx, id, { status })
    handleRefresh()
  }, [ctx, handleRefresh])

  const kanbanGroups = KANBAN_COLUMNS.map(col => ({
    ...col,
    tasks: tasks.filter(t => t.status === col.key),
  }))

  if (!ws) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace-tasks:titlebar">
          <PageTitlebar />
        </Contribute>
        <WorkspaceScopeNotice ctx={ctx} scope={scope} />
        <div className="flex flex-1 items-center justify-center px-8">
          <div className="text-center max-w-sm">
            <p className="text-sm text-(--ui-text-secondary) mb-4">No workspace scope resolved — task data is not shown (queries never fall back to global).</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace-tasks:titlebar">
        <PageTitlebar />
      </Contribute>

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      <div className="flex-1 overflow-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-8 py-3">
          <span className="text-sm font-medium text-(--ui-text-primary)">Tasks</span>
          <div className="flex items-center gap-3">
            <div className="flex rounded border border-(--ui-stroke-tertiary) overflow-hidden">
              <button
                className={cn('px-2 py-1 text-xs', viewMode === 'kanban' ? 'bg-(--ui-bg-primary) text-(--ui-text-primary)' : 'text-(--ui-text-tertiary)')}
                onClick={() => $taskViewMode.set('kanban')}
              >
                Kanban
              </button>
              <button
                className={cn('px-2 py-1 text-xs border-l border-(--ui-stroke-tertiary)', viewMode === 'table' ? 'bg-(--ui-bg-primary) text-(--ui-text-primary)' : 'text-(--ui-text-tertiary)')}
                onClick={() => $taskViewMode.set('table')}
              >
                Table
              </button>
            </div>
            <select
              className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary)"
              onChange={e => setStatusFilter(e.target.value)}
              value={statusFilter}
            >
              <option value="">All Status</option>
              {KANBAN_COLUMNS.map(c => (
                <option key={c.key} value={c.key}>{c.label}</option>
              ))}
            </select>
            <input
              className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary) w-40 outline-none"
              onChange={e => setSearchQ(e.target.value)}
              placeholder="Search..."
              value={searchQ}
            />
            <Button disabled={formOpen} onClick={() => setFormOpen(true)} size="xs" variant="secondary">+ Task</Button>
            <Button disabled={tasksQuery.isFetching} onClick={handleRefresh} size="xs" variant="secondary">Refresh</Button>
          </div>
        </div>

        <div className="p-4 space-y-4">
          {formOpen && (
            <TaskForm ctx={ctx} onCancel={() => setFormOpen(false)} onDone={() => { setFormOpen(false); handleRefresh() }} workspaceId={ws} />
          )}

          {detailId && (
            <div className="mb-4">
              <TaskDetail ctx={ctx} onClose={() => { setDetailId(null); handleRefresh() }} onRefresh={() => { queryClient.invalidateQueries({ queryKey: ['workspace', 'task', ws, detailId] }); handleRefresh() }} taskId={detailId} workspaceId={ws} />
            </div>
          )}

          {tasksQuery.isLoading && !tasks.length ? (
            <div className="flex justify-center py-12"><Loader type="lemniscate-bloom" /></div>
          ) : tasksQuery.isError && !tasks.length ? (
            <ErrorState description="Failed to load tasks." title="Error">
              <Button onClick={handleRefresh} size="sm" variant="outline">Retry</Button>
            </ErrorState>
          ) : tasks.length === 0 && !formOpen ? (
            <EmptyState description="Create your first task to get started." title="No tasks" />
          ) : viewMode === 'kanban' ? (
            <div className="flex gap-4 overflow-x-auto pb-4" style={{ minHeight: '60vh' }}>
              {kanbanGroups.map(group => (
                <KanbanColumn
                  key={group.key}
                  label={group.label}
                  onSelect={setDetailId}
                  onStatusChange={handleStatusChange}
                  status={group.key}
                  tasks={group.tasks}
                />
              ))}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-(--ui-stroke-tertiary) text-xs text-(--ui-text-tertiary) uppercase">
                    <th className="text-left py-2 px-3">Title</th>
                    <th className="text-left py-2 px-3">Status</th>
                    <th className="text-left py-2 px-3">Priority</th>
                    <th className="text-left py-2 px-3">Labels</th>
                    <th className="text-right py-2 px-3">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tasks.map(t => (
                    <tr className="border-b border-(--ui-stroke-tertiary) hover:bg-(--ui-bg-secondary)" key={t.id}>
                      <td className="py-2 px-3">
                        <button className="text-(--ui-text-primary) hover:underline text-left" onClick={() => setDetailId(t.id)}>
                          {t.title}
                        </button>
                      </td>
                      <td className="py-2 px-3"><StatusBadge status={t.status} /></td>
                      <td className="py-2 px-3"><PriorityBadge priority={t.priority} /></td>
                      <td className="py-2 px-3">{t.labels.join(', ') || '—'}</td>
                      <td className="py-2 px-3 text-right">
                        <Button onClick={() => setEditingId(t.id)} size="xs" variant="ghost">Edit</Button>
                        <Button onClick={() => setDeleteId(t.id)} size="xs" variant="ghost">Delete</Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {editingId && (
        <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 bg-black/50">
          <div className="w-full max-w-lg">
            <TaskForm
              ctx={ctx}
              editing={tasks.find(t => t.id === editingId) || null}
              onCancel={() => setEditingId(null)}
              onDone={() => { setEditingId(null); handleRefresh() }}
              workspaceId={ws}
            />
          </div>
        </div>
      )}

      <ConfirmDialog
        description="Permanently delete this task and its comments."
        onClose={() => setDeleteId(null)}
        onConfirm={() => { if (deleteId) {void handleDelete(deleteId)} }}
        open={deleteId !== null}
        title="Delete Task"
      />
    </div>
  )
}
