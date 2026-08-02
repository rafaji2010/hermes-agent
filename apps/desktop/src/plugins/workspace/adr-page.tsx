/**
 * ADR Page — browse, search, filter, create, edit, delete ADRs.
 *
 * U1C: the resolved project scope is the single source of truth for the
 * workspace (`scope.ts`); there is no manual override input and no prop
 * seeding that could outlive a scope change.
 */

import {
  Button,
  cn,
  ConfirmDialog,
  Contribute,
  EmptyState,
  ErrorState,
  Loader,
  type PluginContext,
  SearchField,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  useQuery,
  useQueryClient,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import {
  deleteADR as apiDelete,
  fetchADRs,
  reconcileADRs,
} from './adr-api'
import { ADRDetail } from './adr-detail'
import { ADREditor } from './adr-editor'
import {
  $adrCategoryFilter,
  $adrEditingId,
  $adrEditorOpen,
  $adrSearchQuery,
  $adrStatusFilter,
  $adrTagFilter,
  type ADR,
  adrReconcileLabel,
  type ADRReconcileSummary,
  adrReconcileSummaryMessage,
  adrReconcileTone,
  type ADRStatus,
} from './adrs'
import { scopeReady, useWorkspaceScope } from './scope'
import { WorkspaceScopeNotice } from './scope-notice'

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
        <div className="flex shrink-0 items-center gap-1">
          {/* S7.3A — reconciliation state badge */}
          <span
            className={cn(
              'inline-flex items-center rounded-full border px-1.5 py-px text-[10px] font-medium',
              adrReconcileTone(adr.reconcile_state),
            )}
            title={adr.last_error || adr.reconcile_state}
          >
            {adrReconcileLabel(adr.reconcile_state)}
          </span>
          <StatusBadge status={adr.status} />
        </div>
      </div>
      {adr.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {adr.tags.slice(0, 5).map(t => (
            <span
              className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)"
              key={t}
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
}: {
  tags: string[]
  categories: string[]
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SearchField
        containerClassName="w-56"
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
}

export function ADRPage({ ctx }: ADRPageProps) {
  const queryClient = useQueryClient()
  const scope = useWorkspaceScope(ctx)
  // The resolved project scope is authoritative. An unresolvable scope
  // yields '' and gates every query off.
  const ws = scopeReady(scope) ? scope.workspaceId : ''
  const searchQuery = useValue($adrSearchQuery)
  const statusFilter = useValue($adrStatusFilter)
  const categoryFilter = useValue($adrCategoryFilter)
  const tagFilter = useValue($adrTagFilter)
  const editorOpen = useValue($adrEditorOpen)
  const editingId = useValue($adrEditingId)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const [reconciling, setReconciling] = useState(false)
  const [reconcileMsg, setReconcileMsg] = useState('')

  // Fetch ADRs — gated on the scope so an unresolvable scope never
  // triggers an unscoped (global) query.
  const adrsQuery = useQuery({
    queryKey: ['workspace', 'adrs', ws, searchQuery, statusFilter, categoryFilter, tagFilter],
    queryFn: () => fetchADRs(ctx, ws, {
      q: searchQuery || undefined,
      status: statusFilter || undefined,
      category: categoryFilter || undefined,
      tag: tagFilter || undefined,
    }),
    enabled: Boolean(ws),
    staleTime: 10_000,
  })

  const adrs = adrsQuery.data?.adrs ?? []
  const tags = [...new Set(adrs.flatMap(a => a.tags))].sort()
  const categories = [...new Set(adrs.map(a => a.category).filter(Boolean))].sort()

  // Selected ADR
  const selectedADR = adrs.find(a => a.id === selectedId) ?? null

  const handleReconcile = useCallback(async () => {
    if (!ws) {return}
    setReconciling(true)
    setReconcileMsg('')

    try {
      // Inspection-first: preview, then apply.
      await reconcileADRs(ctx, ws, true)
      const summary: ADRReconcileSummary = await reconcileADRs(ctx, ws, false)
      setReconcileMsg(`Reconciled: ${adrReconcileSummaryMessage(summary)}`)
      queryClient.invalidateQueries({ queryKey: ['workspace', 'adrs'] })
    } catch (err) {
      setReconcileMsg(err instanceof Error ? err.message : 'Reconciliation failed.')
    } finally {
      setReconciling(false)
    }
  }, [ctx, ws, queryClient])

  const handleDelete = useCallback(async () => {
    if (!deleteConfirmId) {return}

    try {
      await apiDelete(ctx, deleteConfirmId)
      setDeleteConfirmId(null)

      if (selectedId === deleteConfirmId) {setSelectedId(null)}
      queryClient.invalidateQueries({ queryKey: ['workspace', 'adrs'] })
    } catch (err) {
      setReconcileMsg(err instanceof Error ? err.message : 'Delete failed.')
      setDeleteConfirmId(null)
    }
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

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      {/* Header bar */}
      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-6 py-2.5">
        <FilterBar categories={categories} tags={tags} />
        <div className="flex items-center gap-2">
          {reconcileMsg && (
            <span className="max-w-[240px] truncate text-[11px] text-(--ui-text-tertiary)">
              {reconcileMsg}
            </span>
          )}
          <Button
            disabled={reconciling || !ws}
            onClick={() => void handleReconcile()}
            size="xs"
            variant="secondary"
          >
            {reconciling ? 'Reconciling…' : 'Reconcile'}
          </Button>
          <Button
            onClick={() => { $adrEditingId.set(null); $adrEditorOpen.set(true) }}
            size="xs"
            variant="default"
          >
            New ADR
          </Button>
        </div>
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
            <ErrorState description="Could not fetch ADRs." title="Failed to load" />
          ) : adrs.length === 0 ? (
            <EmptyState description="Create your first Architecture Decision Record." title="No ADRs" />
          ) : (
            <div className="space-y-2">
              {adrs.map(adr => (
                <ADRRow
                  adr={adr}
                  key={adr.id}
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
              ctx={ctx}
              onChanged={() => queryClient.invalidateQueries({ queryKey: ['workspace', 'adrs'] })}
              onDelete={() => setDeleteConfirmId(selectedADR.id)}
              onEdit={() => {
                $adrEditingId.set(selectedADR.id)
                $adrEditorOpen.set(true)
              }}
              workspaceId={ws}
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
          workspaceId={ws}
        />
      )}

      {/* Delete confirmation */}
      {deleteConfirmId && (
        <ConfirmDialog
          description="This ADR and its content will be permanently deleted."
          onClose={() => setDeleteConfirmId(null)}
          onConfirm={handleDelete}
          open={true}
          title="Delete ADR?"
        />
      )}
    </div>
  )
}
