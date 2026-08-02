/**
 * Roadmaps Page — browse, create, edit, delete roadmaps and milestones.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { Contribute } from '@/contrib/react/contribute'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import {
  $roadmaps,
  $selectedRoadmapId,
  $milestones,
  type Roadmap,
  type RoadmapMilestone,
} from './stores/roadmaps'
import {
  fetchRoadmaps,
  getRoadmap,
  createRoadmap as apiCreateRoadmap,
  updateRoadmap as apiUpdateRoadmap,
  deleteRoadmap as apiDeleteRoadmap,
  fetchMilestones,
  createMilestone as apiCreateMilestone,
  updateMilestone as apiUpdateMilestone,
  deleteMilestone as apiDeleteMilestone,
  reorderMilestones,
} from './lib/roadmap-api'
import { WorkspaceScopeNotice } from './scope-notice'
import { scopeReady, useWorkspaceScope } from './stores/scope'

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function PageTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Roadmaps</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  planned: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  in_progress: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  blocked: 'bg-red-500/10 text-red-500 border-red-500/20',
  completed: 'bg-green-500/10 text-green-500 border-green-500/20',
}

function MilestoneBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
        STATUS_COLORS[status] || STATUS_COLORS.planned,
      )}
    >
      {status.replace('_', ' ')}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

function ProgressBar({ progress }: { progress: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 rounded-full bg-(--ui-bg-quaternary)">
        <div
          className="h-2 rounded-full bg-green-500 transition-all"
          style={{ width: `${progress}%` }}
        />
      </div>
      <span className="text-xs text-(--ui-text-tertiary)">{progress}%</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Forms (inline create/edit)
// ---------------------------------------------------------------------------

interface RoadmapFormProps {
  workspaceId: string
  editing?: Roadmap | null
  onCancel: () => void
  onDone: () => void
  ctx: PluginContext
}

function RoadmapForm({ workspaceId, editing, onCancel, onDone, ctx }: RoadmapFormProps) {
  const [name, setName] = useState(editing?.name ?? '')
  const [description, setDescription] = useState(editing?.description ?? '')
  const [saving, setSaving] = useState(false)

  const handleSave = useCallback(async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      if (editing) {
        await apiUpdateRoadmap(ctx, editing.id, { name: name.trim(), description: description.trim() })
      } else {
        await apiCreateRoadmap(ctx, { workspace_id: workspaceId, name: name.trim(), description: description.trim() })
      }
      onDone()
    } catch {
      // handled by query retry
    } finally {
      setSaving(false)
    }
  }, [name, description, editing, workspaceId, ctx, onDone])

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-3">
      <input
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
        placeholder="Roadmap name"
        value={name}
        onChange={e => setName(e.target.value)}
      />
      <textarea
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus) resize-none"
        placeholder="Description (optional)"
        rows={2}
        value={description}
        onChange={e => setDescription(e.target.value)}
      />
      <div className="flex gap-2 justify-end">
        <Button disabled={saving} onClick={onCancel} size="sm" variant="secondary">
          Cancel
        </Button>
        <Button disabled={saving || !name.trim()} onClick={handleSave} size="sm">
          {saving ? 'Saving...' : editing ? 'Update' : 'Create'}
        </Button>
      </div>
    </div>
  )
}

interface MilestoneFormProps {
  roadmapId: string
  editing?: RoadmapMilestone | null
  onCancel: () => void
  onDone: () => void
  ctx: PluginContext
}

function MilestoneForm({ roadmapId, editing, onCancel, onDone, ctx }: MilestoneFormProps) {
  const [title, setTitle] = useState(editing?.title ?? '')
  const [description, setDescription] = useState(editing?.description ?? '')
  const [status, setStatus] = useState(editing?.status ?? 'planned')
  const [saving, setSaving] = useState(false)

  const handleSave = useCallback(async () => {
    if (!title.trim()) return
    setSaving(true)
    try {
      if (editing) {
        await apiUpdateMilestone(ctx, roadmapId, editing.id, {
          title: title.trim(),
          description: description.trim(),
          status,
        })
      } else {
        await apiCreateMilestone(ctx, roadmapId, {
          title: title.trim(),
          description: description.trim(),
          status,
        })
      }
      onDone()
    } catch {
      // handled by query retry
    } finally {
      setSaving(false)
    }
  }, [title, description, status, editing, roadmapId, ctx, onDone])

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-3">
      <input
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
        placeholder="Milestone title"
        value={title}
        onChange={e => setTitle(e.target.value)}
      />
      <textarea
        className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus) resize-none"
        placeholder="Description (optional)"
        rows={2}
        value={description}
        onChange={e => setDescription(e.target.value)}
      />
      <div className="flex gap-2 items-center">
        <span className="text-xs text-(--ui-text-tertiary)">Status:</span>
        <select
          className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-sm text-(--ui-text-primary)"
          value={status}
          onChange={e => setStatus(e.target.value)}
        >
          <option value="planned">Planned</option>
          <option value="in_progress">In Progress</option>
          <option value="blocked">Blocked</option>
          <option value="completed">Completed</option>
        </select>
      </div>
      <div className="flex gap-2 justify-end">
        <Button disabled={saving} onClick={onCancel} size="sm" variant="secondary">
          Cancel
        </Button>
        <Button disabled={saving || !title.trim()} onClick={handleSave} size="sm">
          {saving ? 'Saving...' : editing ? 'Update' : 'Create'}
        </Button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Milestone list
// ---------------------------------------------------------------------------

interface MilestoneListProps {
  roadmapId: string
  onRefresh: () => void
  ctx: PluginContext
}

function MilestoneListView({ roadmapId, onRefresh, ctx }: MilestoneListProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const msQuery = useQuery({
    queryKey: ['workspace', 'roadmap', roadmapId, 'milestones'],
    queryFn: async () => {
      const r = await fetchMilestones(ctx, roadmapId)
      $milestones.set(r.milestones)
      return r.milestones
    },
    staleTime: 0,
  })

  const milestones = useStore($milestones)

  const handleDelete = useCallback(async (id: string) => {
    setDeleteId(null)
    await apiDeleteMilestone(ctx, roadmapId, id)
    onRefresh()
  }, [ctx, roadmapId, onRefresh])

  const handleMoveUp = useCallback(async (idx: number) => {
    if (idx <= 0) return
    const ids = milestones.map(m => m.id)
    ;[ids[idx], ids[idx - 1]] = [ids[idx - 1], ids[idx]]
    await reorderMilestones(ctx, roadmapId, ids)
    onRefresh()
  }, [ctx, roadmapId, milestones, onRefresh])

  const handleMoveDown = useCallback(async (idx: number) => {
    if (idx >= milestones.length - 1) return
    const ids = milestones.map(m => m.id)
    ;[ids[idx], ids[idx + 1]] = [ids[idx + 1], ids[idx]]
    await reorderMilestones(ctx, roadmapId, ids)
    onRefresh()
  }, [ctx, roadmapId, milestones, onRefresh])

  if (msQuery.isLoading) {
    return <Loader type="lemniscate-bloom" />
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-(--ui-text-primary)">Milestones</h4>
        <Button
          disabled={adding}
          onClick={() => setAdding(true)}
          size="xs"
          variant="secondary"
        >
          + Add
        </Button>
      </div>

      {adding && (
        <MilestoneForm
          ctx={ctx}
          editing={null}
          roadmapId={roadmapId}
          onCancel={() => setAdding(false)}
          onDone={() => { setAdding(false); onRefresh() }}
        />
      )}

      {milestones.length === 0 && !adding ? (
        <EmptyState description="No milestones yet. Add one to get started." title="Empty" />
      ) : (
        <div className="space-y-2">
          {milestones.map((m, idx) => (
            <div key={m.id}>
              {editingId === m.id ? (
                <MilestoneForm
                  ctx={ctx}
                  editing={m}
                  roadmapId={roadmapId}
                  onCancel={() => setEditingId(null)}
                  onDone={() => { setEditingId(null); onRefresh() }}
                />
              ) : (
                <div className="flex items-center gap-2 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) p-3">
                  <div className="flex flex-col gap-0.5">
                    <button
                      className="text-(--ui-text-tertiary) hover:text-(--ui-text-primary) text-xs leading-none disabled:opacity-30"
                      disabled={idx === 0}
                      onClick={() => handleMoveUp(idx)}
                    >
                      ▲
                    </button>
                    <button
                      className="text-(--ui-text-tertiary) hover:text-(--ui-text-primary) text-xs leading-none disabled:opacity-30"
                      disabled={idx === milestones.length - 1}
                      onClick={() => handleMoveDown(idx)}
                    >
                      ▼
                    </button>
                  </div>
                  <MilestoneBadge status={m.status} />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-(--ui-text-primary) truncate">
                      {m.title}
                    </div>
                    {m.description && (
                      <div className="text-xs text-(--ui-text-tertiary) truncate">
                        {m.description}
                      </div>
                    )}
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <Button onClick={() => setEditingId(m.id)} size="xs" variant="ghost">
                      Edit
                    </Button>
                    <Button onClick={() => setDeleteId(m.id)} size="xs" variant="ghost">
                      Delete
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        description="This action cannot be undone."
        onCancel={() => setDeleteId(null)}
        onConfirm={() => { if (deleteId) void handleDelete(deleteId) }}
        open={deleteId !== null}
        title="Delete Milestone"
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Roadmap card
// ---------------------------------------------------------------------------

interface RoadmapCardProps {
  roadmap: Roadmap
  onRefresh: () => void
  ctx: PluginContext
}

function RoadmapCard({ roadmap, onRefresh, ctx }: RoadmapCardProps) {
  const [editing, setEditing] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState(false)

  const handleDelete = useCallback(async () => {
    setDeleteConfirm(false)
    await apiDeleteRoadmap(ctx, roadmap.id)
    onRefresh()
  }, [ctx, roadmap.id, onRefresh])

  if (editing) {
    return (
      <RoadmapForm
        ctx={ctx}
        editing={roadmap}
        workspaceId={roadmap.workspace_id}
        onCancel={() => setEditing(false)}
        onDone={() => { setEditing(false); onRefresh() }}
      />
    )
  }

  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-3">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-medium text-(--ui-text-primary) truncate">
            {roadmap.name}
          </h3>
          {roadmap.description && (
            <p className="text-xs text-(--ui-text-tertiary) mt-0.5 line-clamp-2">
              {roadmap.description}
            </p>
          )}
        </div>
        <div className="flex gap-1 shrink-0 ml-2">
          <Button onClick={() => setEditing(true)} size="xs" variant="ghost">
            Edit
          </Button>
          <Button onClick={() => setDeleteConfirm(true)} size="xs" variant="ghost">
            Delete
          </Button>
        </div>
      </div>

      <div className="flex items-center gap-4 text-xs text-(--ui-text-tertiary)">
        <span>{roadmap.milestone_count} milestones</span>
        <span>{roadmap.completed_count} completed</span>
      </div>
      <ProgressBar progress={roadmap.progress} />

      <MilestoneListView
        ctx={ctx}
        roadmapId={roadmap.id}
        onRefresh={onRefresh}
      />

      <ConfirmDialog
        description="This will also delete all milestones in this roadmap."
        onCancel={() => setDeleteConfirm(false)}
        onConfirm={() => void handleDelete()}
        open={deleteConfirm}
        title="Delete Roadmap"
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

interface RoadmapsPageProps {
  ctx: PluginContext
  workspaceId?: string
}

export function RoadmapsPage({ ctx, workspaceId = '' }: RoadmapsPageProps) {
  const queryClient = useQueryClient()
  const scope = useWorkspaceScope(ctx)
  const [formOpen, setFormOpen] = useState(false)
  const [wsId, setWsId] = useState(workspaceId || scope.workspaceId)

  const effectiveWs = wsId || (scopeReady(scope) ? scope.workspaceId : '')

  const roadmapsQuery = useQuery({
    queryKey: ['workspace', 'roadmaps', 'list', effectiveWs],
    queryFn: async () => {
      const r = await fetchRoadmaps(ctx, effectiveWs)
      $roadmaps.set(r.roadmaps)
      return r.roadmaps
    },
    staleTime: 0,
    enabled: !!effectiveWs,
  })

  const roadmaps = useStore($roadmaps)
  const selectedRoadmapId = useStore($selectedRoadmapId)

  const handleRefresh = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['workspace', 'roadmaps'] })
    queryClient.invalidateQueries({ queryKey: ['workspace', 'roadmap'] })
  }, [queryClient])

  if (!effectiveWs) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace-roadmaps:titlebar">
          <PageTitlebar />
        </Contribute>
        <WorkspaceScopeNotice ctx={ctx} scope={scope} />
        <div className="flex flex-1 items-center justify-center px-8">
          <div className="text-center max-w-sm">
            <p className="text-sm text-(--ui-text-secondary) mb-4">
              Select a workspace to view roadmaps.
            </p>
            <input
              className="w-full rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
              placeholder="Workspace ID"
              value={wsId}
              onChange={e => setWsId(e.target.value)}
            />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace-roadmaps:titlebar">
        <PageTitlebar />
      </Contribute>

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      <div className="flex-1 overflow-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-8 py-3">
          <span className="text-sm font-medium text-(--ui-text-primary)">
            Roadmaps
          </span>
          <div className="flex items-center gap-3">
            <Button
              disabled={formOpen}
              onClick={() => setFormOpen(true)}
              size="xs"
              variant="secondary"
            >
              + New Roadmap
            </Button>
            <Button
              disabled={roadmapsQuery.isFetching}
              onClick={handleRefresh}
              size="xs"
              variant="secondary"
            >
              Refresh
            </Button>
          </div>
        </div>

        <div className="px-8 py-6 space-y-4">
          {formOpen && (
            <RoadmapForm
              ctx={ctx}
              workspaceId={effectiveWs}
              onCancel={() => setFormOpen(false)}
              onDone={() => { setFormOpen(false); handleRefresh() }}
            />
          )}

          {roadmapsQuery.isLoading && !roadmaps.length ? (
            <div className="flex justify-center py-12">
              <Loader type="lemniscate-bloom" />
            </div>
          ) : roadmapsQuery.isError && !roadmaps.length ? (
            <ErrorState
              description="Failed to load roadmaps. The backend may be unavailable."
              title="Error"
            >
              <Button onClick={handleRefresh} size="sm" variant="outline">
                Retry
              </Button>
            </ErrorState>
          ) : roadmaps.length === 0 ? (
            <EmptyState description="Create your first roadmap to begin tracking milestones." title="No roadmaps" />
          ) : (
            roadmaps.map(r => (
              <RoadmapCard
                key={r.id}
                ctx={ctx}
                roadmap={r}
                onRefresh={handleRefresh}
              />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
