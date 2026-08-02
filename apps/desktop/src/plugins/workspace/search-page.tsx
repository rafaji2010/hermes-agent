/**
 * Search Page — global search, filters, relationship panel, entity preview.
 */

import { useQuery } from '@tanstack/react-query'
import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { Contribute } from '@/contrib/react/contribute'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { Loader } from '@/components/ui/loader'
import { EmptyState } from '@/components/ui/empty-state'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

import { $searchResults, $relatedItems, type SearchResult, type RelatedEntity } from './stores/search'
import { search, getRelated } from './lib/search-api'
import { WorkspaceScopeNotice } from './scope-notice'
import { scopeReady, useWorkspaceScope } from './stores/scope'

// ---------------------------------------------------------------------------
// Type colors
// ---------------------------------------------------------------------------

const TYPE_COLORS: Record<string, string> = {
  workspace: 'bg-blue-500/10 text-blue-400',
  repository: 'bg-cyan-500/10 text-cyan-400',
  roadmap: 'bg-purple-500/10 text-purple-400',
  milestone: 'bg-yellow-500/10 text-yellow-400',
  adr: 'bg-green-500/10 text-green-400',
  journal: 'bg-pink-500/10 text-pink-400',
  task: 'bg-orange-500/10 text-orange-400',
}

const STATUS_COLORS: Record<string, string> = {
  todo: 'bg-gray-500/10 text-gray-400',
  in_progress: 'bg-blue-500/10 text-blue-500',
  blocked: 'bg-red-500/10 text-red-500',
  done: 'bg-green-500/10 text-green-500',
  proposed: 'bg-yellow-500/10 text-yellow-500',
  accepted: 'bg-green-500/10 text-green-500',
}

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function PageTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Search</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Relationship panel
// ---------------------------------------------------------------------------

interface RelationshipPanelProps {
  entity: SearchResult
  ctx: PluginContext
}

function RelationshipPanel({ entity, ctx }: RelationshipPanelProps) {
  const [items, setItems] = useState<RelatedEntity[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)

  const handleToggle = useCallback(async () => {
    if (open) { setOpen(false); return }
    setOpen(true)
    setLoading(true)
    try {
      const r = await getRelated(ctx, entity.type, entity.id)
      $relatedItems.set(r.items)
      setItems(r.items)
    } finally {
      setLoading(false)
    }
  }, [ctx, entity, open])

  return (
    <div>
      <button
        className="text-xs text-(--ui-text-tertiary) hover:text-(--ui-text-secondary) underline"
        onClick={() => void handleToggle()}
      >
        {open ? 'Hide relationships' : `Show relationships (${loading ? '...' : ''})`}
      </button>
      {open && loading && <Loader type="lemniscate-bloom" />}
      {open && !loading && (
        <div className="mt-2 space-y-1 max-h-48 overflow-auto">
          {items.length === 0 ? (
            <div className="text-xs text-(--ui-text-tertiary)">No related items found.</div>
          ) : (
            items.map(item => (
              <div key={`${item.type}:${item.id}`} className="flex items-center gap-2 text-xs py-1">
                <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-medium', TYPE_COLORS[item.type] || 'bg-gray-500/10')}>
                  {item.type}
                </span>
                <span className="text-(--ui-text-primary) truncate">{item.title}</span>
                <span className="text-(--ui-text-tertiary) text-[10px]">{item.relationship}</span>
                {item.status && (
                  <span className={cn('px-1 py-0.5 rounded text-[10px]', STATUS_COLORS[item.status] || '')}>
                    {item.status}
                  </span>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Entity result card
// ---------------------------------------------------------------------------

interface ResultCardProps {
  result: SearchResult
  ctx: PluginContext
}

function ResultCard({ result, ctx }: ResultCardProps) {
  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-4 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn('shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium', TYPE_COLORS[result.type] || 'bg-gray-500/10')}>
            {result.type}
          </span>
          <span className="text-sm font-medium text-(--ui-text-primary) truncate">{result.title}</span>
          {result.priority && (
            <span className="text-[10px] text-(--ui-text-tertiary)">{result.priority}</span>
          )}
        </div>
        {result.score > 0 && (
          <span className="text-[10px] text-(--ui-text-tertiary) shrink-0">
            {result.score.toFixed(1)}
          </span>
        )}
      </div>

      {result.description && (
        <p className="text-xs text-(--ui-text-secondary) line-clamp-2">{result.description}</p>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {result.status && (
          <span className={cn('px-1.5 py-0.5 rounded text-[10px]', STATUS_COLORS[result.status] || 'bg-gray-500/10 text-gray-400')}>
            {result.status}
          </span>
        )}
        {result.labels.map(l => (
          <Badge key={l} variant="secondary">{l}</Badge>
        ))}
      </div>

      <RelationshipPanel entity={result} ctx={ctx} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

interface SearchPageProps {
  ctx: PluginContext
}

export function SearchPage({ ctx }: SearchPageProps) {
  const scope = useWorkspaceScope(ctx)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [workspaceId, setWorkspaceId] = useState(scope.workspaceId)

  // The resolved project scope seeds the scope input; a manual entry
  // remains an explicit override for tooling/debug use.
  const effectiveWs = workspaceId || (scopeReady(scope) ? scope.workspaceId : '')

  const searchQuery = useQuery({
    queryKey: ['workspace', 'search', query, typeFilter, statusFilter, effectiveWs],
    queryFn: async () => {
      if (!query && !typeFilter && !statusFilter) return { results: [], total: 0, query: '', filters: {} }
      const r = await search(ctx, {
        q: query,
        type: typeFilter || undefined,
        status: statusFilter || undefined,
        workspace_id: effectiveWs,
        limit: 50,
      })
      $searchResults.set(r.results)
      return r
    },
    staleTime: 0,
    enabled: (!!query || !!typeFilter || !!statusFilter) && Boolean(effectiveWs),
  })

  const results = useStore($searchResults)
  const data = searchQuery.data

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace-search:titlebar">
        <PageTitlebar />
      </Contribute>

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      <div className="flex-1 overflow-auto">
        <div className="sticky top-0 z-10 border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-8 py-3 space-y-3">
          <div className="flex items-center gap-3">
            <input
              className="flex-1 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-3 py-2 text-sm text-(--ui-text-primary) outline-none focus:border-(--ui-stroke-focus)"
              placeholder='Search... (e.g. "auth" or "status:blocked type:task")'
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') searchQuery.refetch() }}
            />
          </div>
          <div className="flex items-center gap-3">
            <select
              className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary)"
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
            >
              <option value="">All Types</option>
              <option value="workspace">Workspace</option>
              <option value="repository">Repository</option>
              <option value="roadmap">Roadmap</option>
              <option value="milestone">Milestone</option>
              <option value="adr">ADR</option>
              <option value="journal">Journal</option>
              <option value="task">Task</option>
            </select>
            <select
              className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary)"
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
            >
              <option value="">Any Status</option>
              <option value="todo">To Do</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="done">Done</option>
              <option value="proposed">Proposed</option>
              <option value="accepted">Accepted</option>
            </select>
            <input
              className="rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-2 py-1 text-xs text-(--ui-text-primary) w-40 outline-none"
              placeholder="Workspace ID"
              value={workspaceId}
              onChange={e => setWorkspaceId(e.target.value)}
            />
            <Button
              disabled={searchQuery.isFetching}
              onClick={() => searchQuery.refetch()}
              size="xs"
              variant="secondary"
            >
              {searchQuery.isFetching ? 'Searching...' : 'Search'}
            </Button>
            {data && data.total > 0 && (
              <span className="text-xs text-(--ui-text-tertiary)">
                {data.total} results
              </span>
            )}
          </div>
        </div>

        <div className="px-8 py-6 space-y-4">
          {!query && !typeFilter && !statusFilter ? (
            <EmptyState description="Enter a query or select filters to search across all entities." title="Global Search" />
          ) : searchQuery.isLoading ? (
            <div className="flex justify-center py-12"><Loader type="lemniscate-bloom" /></div>
          ) : results.length === 0 ? (
            <EmptyState description="No results found. Try different search terms or filters." title="No results" />
          ) : (
            results.map(r => (
              <ResultCard key={`${r.type}:${r.id}`} result={r} ctx={ctx} />
            ))
          )}
        </div>
      </div>
    </div>
  )
}
