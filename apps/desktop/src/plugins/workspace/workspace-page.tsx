/**
 * Workspace Plugin — Status Page
 *
 * Administrator dashboard rendered at the Workspace overview tab.  Consumes
 * the enriched ``GET /v1/health`` endpoint to display plugin, backend,
 * storage, database, and system status in a single round-trip.
 */

import {
  Button,
  cn,
  Contribute,
  ErrorState,
  Loader,
  type PluginContext,
  useQuery,
  useQueryClient,
} from '@hermes/plugin-sdk'
import { type ReactNode, useCallback, useState } from 'react'

// ---------------------------------------------------------------------------
// Status response shape  (matches backend/models.py StatusResponse)
// ---------------------------------------------------------------------------

interface StatusResponse {
  status: string
  plugin: string
  plugin_version: string
  api_version: string
  storage_provider: string
  database_connected: boolean
  database_path: string
  transaction_support: boolean
  nested_transactions: string
  schema_version: string
  migration_status: string
  workspace_count: number
  repository_count: number
  journal_count: number
  roadmap_count: number
  milestone_count: number
  completed_milestone_count: number
  task_count: number
  open_task_count: number
  blocked_task_count: number
  overdue_task_count: number
  graph_entity_count: number
  graph_edge_count: number
  graph_orphan_count: number
  hermes_home: string
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

function StatusDot({ healthy }: { healthy: boolean }) {
  return (
    <span
      className={cn(
        'inline-block size-2 shrink-0 rounded-full',
        healthy ? 'bg-green-500' : 'bg-red-500',
      )}
    />
  )
}

function StatusRow({
  label,
  value,
  healthy,
}: {
  label: string
  value: string
  healthy?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-sm text-(--ui-text-secondary)">{label}</span>
      <span className="inline-flex items-center gap-1.5 text-sm font-medium text-(--ui-text-primary)">
        {healthy !== undefined && <StatusDot healthy={healthy} />}
        {value}
      </span>
    </div>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-5">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary)">
        {title}
      </h3>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function TopIndicator({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-5 py-3">
      <div className="text-xs text-(--ui-text-tertiary)">{label}</div>
      <div className="mt-0.5 text-base font-semibold text-(--ui-text-primary)">
        {value}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function WorkspaceTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">
        Workspace
      </span>
      <span className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-(--ui-text-tertiary)">
        Status
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

interface WorkspacePageProps {
  ctx: PluginContext
}

export function WorkspacePage({ ctx }: WorkspacePageProps) {
  const queryClient = useQueryClient()
  const [refreshKey, setRefreshKey] = useState(0)

  const statusQuery = useQuery<StatusResponse>({
    queryKey: ['workspace', 'status', refreshKey],
    queryFn: () => ctx.rest<StatusResponse>('/v1/health'),
    retry: 1,
    staleTime: 0,
  })

  const data = statusQuery.data

  // Derived from the query's own timestamps — no mirrored refs.
  const lastRefresh = statusQuery.dataUpdatedAt > 0
    ? new Date(statusQuery.dataUpdatedAt).toLocaleTimeString()
    : ''

  const handleRefresh = useCallback(() => {
    setRefreshKey(k => k + 1)
    queryClient.invalidateQueries({ queryKey: ['workspace', 'status'] })
  }, [queryClient])

  // -- error state -----------------------------------------------------------

  if (statusQuery.isError && !data) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace:titlebar">
          <WorkspaceTitlebar />
        </Contribute>
        <div className="flex flex-1 items-center justify-center px-8">
          <ErrorState
            description={
              statusQuery.error instanceof Error
                ? statusQuery.error.message
                : 'The Workspace backend plugin is not reachable.'
            }
            title="Backend Unavailable"
          >
            <Button onClick={handleRefresh} size="sm" variant="outline">
              Retry
            </Button>
          </ErrorState>
        </div>
      </div>
    )
  }

  // -- loading state ---------------------------------------------------------

  if (!data) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace:titlebar">
          <WorkspaceTitlebar />
        </Contribute>
        <div className="flex flex-1 items-center justify-center gap-3 text-sm text-(--ui-text-tertiary)">
          <Loader type="lemniscate-bloom" />
          <span>Loading status...</span>
        </div>
      </div>
    )
  }

  // -- dashboard -------------------------------------------------------------

  const backendHealthy = data.status === 'ok' && data.database_connected
  const dbHealthy = data.database_connected && data.migration_status === 'Up to date'

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace:titlebar">
        <WorkspaceTitlebar />
      </Contribute>

      <div className="flex-1 overflow-auto">
        {/* ── Header bar ─────────────────────────────────────── */}
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-8 py-3">
          <div className="flex items-center gap-3">
            <StatusDot healthy={backendHealthy} />
            <span className="text-sm font-medium text-(--ui-text-primary)">
              {backendHealthy ? 'System Healthy' : 'System Degraded'}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-(--ui-text-tertiary)">
              {lastRefresh ? `Last refresh: ${lastRefresh}` : ''}
            </span>
            <Button
              disabled={statusQuery.isFetching}
              onClick={handleRefresh}
              size="xs"
              variant="secondary"
            >
              {statusQuery.isFetching ? 'Refreshing...' : 'Refresh'}
            </Button>
          </div>
        </div>

        {/* ── Top-line metrics ───────────────────────────────── */}
        <div className="space-y-4 px-8 pt-6">
          <div className="grid grid-cols-5 gap-4">
            <TopIndicator label="Workspaces" value={String(data.workspace_count)} />
            <TopIndicator label="Repositories" value={String(data.repository_count)} />
            <TopIndicator label="Journal" value={String(data.journal_count)} />
            <TopIndicator label="Roadmaps" value={String(data.roadmap_count)} />
            <TopIndicator label="Milestones" value={String(data.milestone_count)} />
          </div>
          <div className="grid grid-cols-5 gap-4">
            <TopIndicator label="Tasks" value={String(data.task_count)} />
            <TopIndicator label="Open Tasks" value={String(data.open_task_count)} />
            <TopIndicator label="Blocked" value={String(data.blocked_task_count)} />
            <TopIndicator label="Overdue" value={String(data.overdue_task_count)} />
            <TopIndicator
              label="Database"
              value={data.database_connected ? 'Connected' : 'Disconnected'}
            />
          </div>
          <div className="grid grid-cols-4 gap-4">
            <TopIndicator label="Graph Entities" value={String(data.graph_entity_count)} />
            <TopIndicator label="Graph Edges" value={String(data.graph_edge_count)} />
            <TopIndicator label="Orphans" value={String(data.graph_orphan_count)} />
            <TopIndicator label="Completed" value={String(data.completed_milestone_count)} />
          </div>
        </div>

        {/* ── Card grid ──────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-4 px-8 py-6">
          {/* Plugin */}
          <Card title="Plugin">
            <StatusRow label="Name" value={data.plugin} />
            <StatusRow label="Version" value={data.plugin_version} />
            <StatusRow
              healthy={data.status === 'ok'}
              label="Status"
              value={data.status === 'ok' ? 'Running' : 'Error'}
            />
          </Card>

          {/* Backend */}
          <Card title="Backend">
            <StatusRow healthy={data.database_connected} label="Connected" value={data.database_connected ? 'Yes' : 'No'} />
            <StatusRow label="API Version" value={data.api_version} />
            <StatusRow healthy={data.status === 'ok'} label="Health" value={data.status === 'ok' ? 'Healthy' : data.status} />
          </Card>

          {/* Storage */}
          <Card title="Storage">
            <StatusRow label="Provider" value={data.storage_provider} />
            <StatusRow healthy={data.database_connected} label="Connected" value={data.database_connected ? 'Yes' : 'No'} />
            <StatusRow healthy={data.transaction_support} label="Transactions" value={data.transaction_support ? 'Supported' : 'None'} />
            <StatusRow healthy label="Nesting" value={data.nested_transactions} />
          </Card>

          {/* Database */}
          <Card title="Database">
            <StatusRow label="Schema" value={data.schema_version} />
            <StatusRow
              healthy={dbHealthy}
              label="Migrations"
              value={data.migration_status}
            />
            <StatusRow label="Workspaces" value={String(data.workspace_count)} />
            <StatusRow label="Repositories" value={String(data.repository_count)} />
          </Card>
        </div>

        {/* ── System info (full width) ───────────────────────── */}
        <div className="px-8 pb-8">
          <Card title="System">
            <StatusRow label="Hermes Home" value={data.hermes_home || '—'} />
            <StatusRow label="Database Path" value={data.database_path || '—'} />
            <StatusRow label="Plugin Version" value={data.plugin_version} />
            <StatusRow
              label="Last Refresh"
              value={lastRefresh || new Date().toLocaleTimeString()}
            />
          </Card>
        </div>
      </div>
    </div>
  )
}
