/**
 * Analytics Page — dashboards, trends, insights, export.
 *
 * U1C: analytics data comes from React Query, keyed by the resolved
 * workspace scope; export uses the existing backend endpoint through
 * `ctx.rest` and a renderer-side download flow (no raw `window.open`).
 */

import {
  Button,
  cn,
  Contribute,
  EmptyState,
  ErrorState,
  Loader,
  type PluginContext,
  useQuery,
  useValue,
} from '@hermes/plugin-sdk'
import { useCallback } from 'react'
import type { ReactNode } from 'react'

import { $trendPeriod } from './analytics'
import { exportAnalytics, fetchAnalytics, fetchInsights, fetchTrends } from './analytics-api'
import { scopeReady, useWorkspaceScope } from './scope'
import { WorkspaceScopeNotice } from './scope-notice'

// ---------------------------------------------------------------------------
// Titlebar
// ---------------------------------------------------------------------------

function PageTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Analytics</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared components
// ---------------------------------------------------------------------------

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) p-5">
      <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-(--ui-text-tertiary)">
        {title}
      </h3>
      <div className="space-y-2">{children}</div>
    </div>
  )
}

function StatRow({ label, value, unit = '' }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="flex items-center justify-between py-1">
      <span className="text-sm text-(--ui-text-secondary)">{label}</span>
      <span className="text-sm font-medium text-(--ui-text-primary)">
        {value}{unit && <span className="text-xs ml-0.5 text-(--ui-text-tertiary)">{unit}</span>}
      </span>
    </div>
  )
}

function KPI({ label, value, color = '' }: { label: string; value: string | number; color?: string }) {
  return (
    <div className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-primary) px-4 py-3 text-center">
      <div className={cn('text-2xl font-bold', color || 'text-(--ui-text-primary)')}>
        {value}
      </div>
      <div className="text-[11px] text-(--ui-text-tertiary) mt-0.5">{label}</div>
    </div>
  )
}

function InsightBadge({ type, title, description }: { type: string; title: string; description: string }) {
  const colors: Record<string, string> = {
    danger: 'bg-red-500/10 border-red-500/20 text-red-400',
    warning: 'bg-yellow-500/10 border-yellow-500/20 text-yellow-400',
    info: 'bg-blue-500/10 border-blue-500/20 text-blue-400',
    success: 'bg-green-500/10 border-green-500/20 text-green-400',
  }

  return (
    <div className={cn('rounded border p-3', colors[type] || 'bg-gray-500/10 border-gray-500/20')}>
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs mt-0.5 opacity-80">{description}</div>
    </div>
  )
}

function MiniBar({
  label,
  value,
  max = 100,
  color = 'bg-green-500',
}: { label?: string; value: number; max?: number; color?: string }) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))

  return (
    <div className="flex items-center gap-2">
      {label && <span className="w-24 shrink-0 truncate text-xs text-(--ui-text-tertiary)">{label}</span>}
      <div className="h-2 flex-1 rounded-full bg-(--ui-bg-quaternary)">
        <div className={cn('h-2 rounded-full transition-all', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-(--ui-text-tertiary) w-10 text-right">{value}</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

interface AnalyticsPageProps {
  ctx: PluginContext
}

export function AnalyticsPage({ ctx }: AnalyticsPageProps) {
  const trendPeriod = useValue($trendPeriod)
  const scope = useWorkspaceScope(ctx)
  // The resolved project scope is authoritative. An unresolvable scope
  // yields '' and gates every query off.
  const ws = scopeReady(scope) ? scope.workspaceId : ''

  const analyticsQ = useQuery({
    queryKey: ['workspace', 'analytics', ws],
    queryFn: () => fetchAnalytics(ctx, ws),
    enabled: Boolean(ws),
    staleTime: 15000,
  })

  const trendsQ = useQuery({
    queryKey: ['workspace', 'trends', trendPeriod, ws],
    queryFn: () => fetchTrends(ctx, trendPeriod, ws),
    staleTime: 15000,
    enabled: !!analyticsQ.data,
  })

  const insightsQ = useQuery({
    queryKey: ['workspace', 'insights', ws],
    queryFn: () => fetchInsights(ctx, ws),
    staleTime: 15000,
    enabled: !!analyticsQ.data,
  })

  const data = analyticsQ.data
  const trends = trendsQ.data
  const insights = insightsQ.data?.insights ?? []

  const handleExport = useCallback(async (format: string) => {
    if (!ws) {return}

    try {
      // The backend export endpoint is workspace-scoped and POST-only; the
      // returned text is downloaded renderer-side.
      const text = await exportAnalytics(ctx, format, ['all'], ws)
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `workspace-analytics.${format}`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // Export failures are surfaced silently — the button stays usable.
    }
  }, [ctx, ws])

  if (analyticsQ.isError && !data) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace-analytics:titlebar"><PageTitlebar /></Contribute>
        <WorkspaceScopeNotice ctx={ctx} scope={scope} />
        <div className="flex flex-1 items-center justify-center">
          <ErrorState description="Failed to load analytics." title="Error">
            <Button onClick={() => void analyticsQ.refetch()} size="sm" variant="outline">Retry</Button>
          </ErrorState>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex h-full flex-col">
        <Contribute area="titleBar.center" id="workspace-analytics:titlebar"><PageTitlebar /></Contribute>
        <WorkspaceScopeNotice ctx={ctx} scope={scope} />
        {ws ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader type="lemniscate-bloom" />
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center text-sm text-(--ui-text-tertiary)">
            No workspace scope resolved — analytics are not shown.
          </div>
        )}
      </div>
    )
  }

  const maxTrendVal = trends ? Math.max(
    ...trends.task_completion.map(p => p.value),
    ...trends.milestone_completion.map(p => p.value),
    ...trends.journal_activity.map(p => p.value),
    1,
  ) : 1

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace-analytics:titlebar"><PageTitlebar /></Contribute>

      <div className="flex-1 overflow-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-(--ui-stroke-tertiary) bg-(--ui-chat-surface-background) px-8 py-3">
          <span className="text-sm font-medium text-(--ui-text-primary)">Engineering Analytics</span>
          <div className="flex items-center gap-3">
            <Button onClick={() => void analyticsQ.refetch()} size="xs" variant="secondary">Refresh</Button>
          </div>
        </div>

        <div className="px-8 py-6 space-y-6">
          {/* ── Overview KPIs ─────────────────────────────── */}
          <div className="grid grid-cols-4 gap-4">
            <KPI label="Total Tasks" value={data.tasks.total} />
            <KPI color="text-blue-500" label="Open Tasks" value={data.tasks.open} />
            <KPI color="text-red-500" label="Blocked" value={data.tasks.blocked} />
            <KPI color="text-orange-500" label="Overdue" value={data.tasks.overdue} />
          </div>

          <div className="grid grid-cols-2 gap-6">
            {/* ── Roadmaps ──────────────────────────────────── */}
            <Section title="Roadmaps">
              <div className="grid grid-cols-3 gap-3 mb-3">
                <KPI label="Total" value={data.roadmaps.total} />
                <KPI color="text-blue-500" label="Active" value={data.roadmaps.active} />
                <KPI color="text-green-500" label="Completed" value={data.roadmaps.completed} />
              </div>
              <StatRow label="Avg Progress" value={`${data.roadmaps.avg_progress}%`} />
              <StatRow label="Milestones" value={data.roadmaps.total_milestones} />
              <MiniBar color="bg-green-500" label="Completed" value={data.roadmaps.milestones_completed} />
              <MiniBar color="bg-blue-500" label="In Progress" value={data.roadmaps.milestones_in_progress} />
              <MiniBar color="bg-red-500" label="Blocked" value={data.roadmaps.milestones_blocked} />
            </Section>

            {/* ── Tasks ────────────────────────────────────── */}
            <Section title="Tasks">
              <div className="grid grid-cols-3 gap-3 mb-3">
                <KPI label="Total" value={data.tasks.total} />
                <KPI color="text-green-500" label="Completed" value={data.tasks.completed} />
                <KPI color="text-orange-500" label="Overdue" value={data.tasks.overdue} />
              </div>
              <div className="space-y-1">
                {Object.entries(data.tasks.by_status).map(([k, v]) => (
                  <MiniBar key={k} label={k} max={data.tasks.total} value={v} />
                ))}
              </div>
            </Section>

            {/* ── Repositories ─────────────────────────────── */}
            <Section title="Repositories">
              <StatRow label="Total" value={data.repositories.total} />
              <StatRow label="Active" value={data.repositories.active} />
              <StatRow label="Most Active" value={data.repositories.most_active || '—'} />
              <StatRow label="Tasks on Most Active" value={data.repositories.most_active_task_count || '—'} />
            </Section>

            {/* ── ADRs ─────────────────────────────────────── */}
            <Section title="ADRs">
              <StatRow label="Total" value={data.adrs.total} />
              <StatRow label="Recently Added" value={`${data.adrs.recently_added} (30d)`} />
              {Object.entries(data.adrs.by_status).map(([k, v]) => (
                <MiniBar key={k} label={k} max={data.adrs.total} value={v} />
              ))}
            </Section>

            {/* ── Journal ──────────────────────────────────── */}
            <Section title="Journal">
              <StatRow label="This Week" value={data.journal.entries_this_week} />
              <StatRow label="This Month" value={data.journal.entries_this_month} />
              <StatRow label="Writing Streak" value={`${data.journal.writing_streak_days} days`} />
            </Section>

            {/* ── Knowledge Graph ──────────────────────────── */}
            <Section title="Knowledge Graph">
              <StatRow label="Entities" value={data.graph_entities} />
              <StatRow label="Edges" value={data.graph_edges} />
              <StatRow label="Orphans" value={data.graph_orphans} />
            </Section>
          </div>

          {/* ── Trends ─────────────────────────────────────── */}
          <Section title="Activity Trends">
            <div className="flex gap-3 mb-4">
              {[7, 30, 90].map(d => (
                <Button
                  key={d}
                  onClick={() => $trendPeriod.set(d)}
                  size="xs"
                  variant={trendPeriod === d ? 'default' : 'secondary'}
                >
                  {d}d
                </Button>
              ))}
            </div>
            {trends && (
              <div className="space-y-4">
                {trends.task_completion.some(p => p.value > 0) && (
                  <div>
                    <div className="text-xs text-(--ui-text-tertiary) mb-1">Task Completions</div>
                    <div className="flex gap-0.5 h-8 items-end">
                      {trends.task_completion.map((p, i) => (
                        <div className="flex-1 bg-(--ui-bg-quaternary) relative rounded-t" key={i} title={`${p.date}: ${p.value}`}>
                          <div
                            className="absolute bottom-0 left-0 right-0 bg-green-500/60"
                            style={{ height: `${Math.min(100, (p.value / maxTrendVal) * 100)}%` }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {trends.journal_activity.some(p => p.value > 0) && (
                  <div>
                    <div className="text-xs text-(--ui-text-tertiary) mb-1">Journal Activity</div>
                    <div className="flex gap-0.5 h-8 items-end">
                      {trends.journal_activity.map((p, i) => (
                        <div className="flex-1 bg-(--ui-bg-quaternary) relative rounded-t" key={i} title={`${p.date}: ${p.value}`}>
                          <div
                            className="absolute bottom-0 left-0 right-0 bg-blue-500/60"
                            style={{ height: `${Math.min(100, (p.value / maxTrendVal) * 100)}%` }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Section>

          {/* ── Insights ───────────────────────────────────── */}
          <Section title="Engineering Insights">
            {insights.length === 0 ? (
              <EmptyState description="No insights detected yet. Add more data to surface patterns." title="All clear" />
            ) : (
              <div className="space-y-2">
                {insights.map((ins, i) => (
                  <InsightBadge description={ins.description} key={i} title={ins.title} type={ins.type} />
                ))}
              </div>
            )}
          </Section>

          {/* ── Export ─────────────────────────────────────── */}
          <Section title="Export">
            <div className="flex gap-3">
              <Button onClick={() => void handleExport('markdown')} size="xs" variant="secondary">Markdown</Button>
              <Button onClick={() => void handleExport('json')} size="xs" variant="secondary">JSON</Button>
              <Button onClick={() => void handleExport('csv')} size="xs" variant="secondary">CSV</Button>
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
