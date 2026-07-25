/**
 * Workspace Plugin — Status Page
 *
 * Administrator dashboard rendered at ``/workspace``.  Consumes the
 * enriched ``GET /v1/health`` endpoint to display plugin, backend,
 * storage, database, and system status in a single round-trip.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { Contribute } from '@/contrib/react/contribute'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { cn } from '@/lib/utils'

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

function Card({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
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

  const lastRefreshTime = useRef<string>('')
  useEffect(() => {
    if (statusQuery.isSuccess) {
      lastRefreshTime.current = new Date().toLocaleTimeString()
    }
  }, [statusQuery.isSuccess, statusQuery.dataUpdatedAt])

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
              {lastRefreshTime.current ? `Last refresh: ${lastRefreshTime.current}` : ''}
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
        <div className="grid grid-cols-4 gap-4 px-8 pt-6">
          <TopIndicator label="Workspaces" value={String(data.workspace_count)} />
          <TopIndicator label="Repositories" value={String(data.repository_count)} />
          <TopIndicator label="Journal Entries" value={String(data.journal_count)} />
          <TopIndicator
            label="Database"
            value={data.database_connected ? 'Connected' : 'Disconnected'}
          />
        </div>

        {/* ── Card grid ──────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-4 px-8 py-6">
          {/* Plugin */}
          <Card title="Plugin">
            <StatusRow label="Name" value={data.plugin} />
            <StatusRow label="Version" value={data.plugin_version} />
            <StatusRow
              label="Status"
              value={data.status === 'ok' ? 'Running' : 'Error'}
              healthy={data.status === 'ok'}
            />
          </Card>

          {/* Backend */}
          <Card title="Backend">
            <StatusRow label="Connected" value={data.database_connected ? 'Yes' : 'No'} healthy={data.database_connected} />
            <StatusRow label="API Version" value={data.api_version} />
            <StatusRow label="Health" value={data.status === 'ok' ? 'Healthy' : data.status} healthy={data.status === 'ok'} />
          </Card>

          {/* Storage */}
          <Card title="Storage">
            <StatusRow label="Provider" value={data.storage_provider} />
            <StatusRow label="Connected" value={data.database_connected ? 'Yes' : 'No'} healthy={data.database_connected} />
            <StatusRow label="Transactions" value={data.transaction_support ? 'Supported' : 'None'} healthy={data.transaction_support} />
            <StatusRow label="Nesting" value={data.nested_transactions} healthy />
          </Card>

          {/* Database */}
          <Card title="Database">
            <StatusRow label="Schema" value={data.schema_version} />
            <StatusRow
              label="Migrations"
              value={data.migration_status}
              healthy={dbHealthy}
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
              value={lastRefreshTime.current || new Date().toLocaleTimeString()}
            />
          </Card>
        </div>
      </div>
    </div>
  )
}
