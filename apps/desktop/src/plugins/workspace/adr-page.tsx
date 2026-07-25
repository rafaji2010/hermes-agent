/**
 * ADR Page — browse, search, filter, create, edit, delete ADRs.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useState } from 'react'

import { Contribute } from '@/contrib/react/contribute'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import {
  $adrs,
  $adrSearchQuery,
  $adrStatusFilter,
  $adrCategoryFilter,
  $adrTagFilter,
  $adrTags,
  $adrCategories,
  $adrEditorOpen,
  $adrEditingId,
  type ADR,
  type ADRStatus,
} from './stores/adrs'
import {
  fetchADRs,
  createADR as apiCreate,
  updateADR as apiUpdate,
  deleteADR as apiDelete,
} from './lib/adr-api'
import { ADREditor } from './adr-editor'
import { ADRDetail } from './adr-detail'

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function ADRTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">ADRs</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, string> = {
  proposed: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  accepted: 'bg-green-500/10 text-green-500 border-green-500/20',
  rejected: 'bg-red-500/10 text-red-500 border-red-500/20',
  superseded: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  deprecated: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
        STATUS_COLORS[status] || STATUS_COLORS.proposed,
      )}
    >
      {status}
    </span>
  )
}

// ---------------------------------------------------------------------------
// ADR row
// ---------------------------------------------------------------------------

function ADRRow({
  adr,
  onSelect,
  selected,
}: {
  adr: ADR
  onSelect: (id: string) => void
  selected: boolean
}) {
  return (
    <button
      className={cn(
        'w-full rounded-lg border px-4 py-3 text-left transition-colors',
        selected
          ? 'border-(--ui-accent) bg-(--ui-accent)/5'
          : 'border-(--ui-stroke-tertiary) hover:border-(--ui-stroke-secondary)',
      )}
      onClick={() => onSelect(adr.id)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-(--ui-text-primary) truncate">
            {adr.title}
          </div>
          <div className="mt-0.5 text-xs text-(--ui-text-tertiary) truncate">
            {adr.slug}
          </div>
        </div>
        <StatusBadge status={adr.status} />
      </div>
      {adr.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {adr.tags.slice(0, 5).map(t => (
            <span
              key={t}
              className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </button>
  )
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

function FilterBar({
  tags,
  categories,
  workspaceId,
}: {
  tags: string[]
  categories: string[]
  workspaceId: string
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SearchField
        className="w-56"
        onChange={v => $adrSearchQuery.set(v)}
        placeholder="Search ADRs..."
        value={$adrSearchQuery.get()}
      />
      <Select
        onValueChange={v => $adrStatusFilter.set(v as ADRStatus | '')}
        value={$adrStatusFilter.get()}
      >
        <SelectTrigger className="h-7 w-[130px] text-xs">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">All statuses</SelectItem>
          <SelectItem value="proposed">Proposed</SelectItem>
          <SelectItem value="accepted">Accepted</SelectItem>
          <SelectItem value="rejected">Rejected</SelectItem>
          <SelectItem value="superseded">Superseded</SelectItem>
          <SelectItem value="deprecated">Deprecated</SelectItem>
        </SelectContent>
      </Select>
      {categories.length > 0 && (
        <Select
          onValueChange={v => $adrCategoryFilter.set(v)}
          value={$adrCategoryFilter.get()}
        >
          <SelectTrigger className="h-7 w-[140px] text-xs">
            <SelectValue placeholder="All categories" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All categories</SelectItem>
            {categories.map(c => (
              <SelectItem key={c} value={c}>{c}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {tags.length > 0 && (
        <Select
          onValueChange={v => $adrTagFilter.set(v)}
          value={$adrTagFilter.get()}
        >
          <SelectTrigger className="h-7 w-[140px] text-xs">
            <SelectValue placeholder="All tags" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="">All tags</SelectItem>
            {tags.map(t => (
              <SelectItem key={t} value={t}>{t}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

interface ADRPageProps {
  ctx: PluginContext
  workspaceId: string
}

export function ADRPage({ ctx, workspaceId }: ADRPageProps) {
  const queryClient = useQueryClient()
  const searchQuery = useStore($adrSearchQuery)
  const statusFilter = useStore($adrStatusFilter)
  const categoryFilter = useStore($adrCategoryFilter)
  const tagFilter = useStore($adrTagFilter)
  const editorOpen = useStore($adrEditorOpen)
  const editingId = useStore($adrEditingId)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)

  // Fetch ADRs
  const adrsQuery = useQuery({
    queryKey: ['workspace', 'adrs', workspaceId, searchQuery, statusFilter, categoryFilter, tagFilter],
    queryFn: () => fetchADRs(ctx, workspaceId, {
      q: searchQuery || undefined,
      status: statusFilter || undefined,
      category: categoryFilter || undefined,
      tag: tagFilter || undefined,
    }),
    staleTime: 10_000,
  })

  const adrs = adrsQuery.data?.adrs ?? []
  const tags = [...new Set(adrs.flatMap(a => a.tags))].sort()
  const categories = [...new Set(adrs.map(a => a.category).filter(Boolean))].sort()

  // Selected ADR
  const selectedADR = adrs.find(a => a.id === selectedId) ?? null

  const handleDelete = useCallback(async () => {
    if (!deleteConfirmId) return
    await apiDelete(ctx, deleteConfirmId)
    setDeleteConfirmId(null)
    if (selectedId === deleteConfirmId) setSelectedId(null)
    queryClient.invalidateQueries({ queryKey: ['workspace', 'adrs'] })
  }, [ctx, deleteConfirmId, queryClient, selectedId])

  const handleSaved = useCallback(() => {
    $adrEditorOpen.set(false)
    $adrEditingId.set(null)
    queryClient.invalidateQueries({ queryKey: ['workspace', 'adrs'] })
  }, [queryClient])

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace:adr:titlebar">
        <ADRTitlebar />
      </Contribute>

      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-6 py-2.5">
        <FilterBar tags={tags} categories={categories} workspaceId={workspaceId} />
        <Button
          onClick={() => { $adrEditingId.set(null); $adrEditorOpen.set(true) }}
          size="xs"
          variant="default"
        >
          New ADR
        </Button>
      </div>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        {/* List */}
        <div className="w-80 shrink-0 overflow-auto border-r border-(--ui-stroke-tertiary) p-3">
          {adrsQuery.isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader type="lemniscate-bloom" />
            </div>
          ) : adrsQuery.isError ? (
            <ErrorState title="Failed to load" description="Could not fetch ADRs." />
          ) : adrs.length === 0 ? (
            <EmptyState title="No ADRs" description="Create your first Architecture Decision Record." />
          ) : (
            <div className="space-y-2">
              {adrs.map(adr => (
                <ADRRow
                  key={adr.id}
                  adr={adr}
                  onSelect={setSelectedId}
                  selected={selectedId === adr.id}
                />
              ))}
            </div>
          )}
        </div>

        {/* Detail */}
        <div className="flex-1 overflow-auto">
          {selectedADR ? (
            <ADRDetail
              adr={selectedADR}
              onDelete={() => setDeleteConfirmId(selectedADR.id)}
              onEdit={() => {
                $adrEditingId.set(selectedADR.id)
                $adrEditorOpen.set(true)
              }}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-(--ui-text-tertiary)">
              Select an ADR to view details
            </div>
          )}
        </div>
      </div>

      {/* Editor dialog */}
      {editorOpen && (
        <ADREditor
          adr={editingId ? adrs.find(a => a.id === editingId) ?? null : null}
          ctx={ctx}
          onClose={() => { $adrEditorOpen.set(false); $adrEditingId.set(null) }}
          onSaved={handleSaved}
          workspaceId={workspaceId}
        />
      )}

      {/* Delete confirmation */}
      {deleteConfirmId && (
        <ConfirmDialog
          description="This ADR and its content will be permanently deleted."
          onCancel={() => setDeleteConfirmId(null)}
          onConfirm={handleDelete}
          open={true}
          title="Delete ADR?"
        />
      )}
    </div>
  )
}
