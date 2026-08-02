/**
 * Analytics API — REST wrappers for analytics endpoints.
 */

import type { PluginContext } from '@/contrib/plugin'
import type { AnalyticsData, AutoInsight, TrendData } from '../stores/analytics'

export async function fetchAnalytics(ctx: PluginContext, workspaceId = ''): Promise<AnalyticsData> {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''
  return ctx.rest<AnalyticsData>(`/v1/analytics${qs}`)
}

export async function fetchTrends(ctx: PluginContext, days = 30, workspaceId = ''): Promise<TrendData> {
  const qs = new URLSearchParams({ period_days: String(days) })
  if (workspaceId) qs.set('workspace_id', workspaceId)
  return ctx.rest<TrendData>(`/v1/analytics/trends?${qs.toString()}`)
}

export async function fetchInsights(ctx: PluginContext, workspaceId = ''): Promise<{ insights: AutoInsight[] }> {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''
  return ctx.rest<{ insights: AutoInsight[] }>(`/v1/analytics/insights${qs}`)
}

export async function exportAnalytics(
  ctx: PluginContext, format: string, sections: string[] = ['all'], workspaceId = '',
): Promise<string> {
  const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''
  return ctx.rest<string>(`/v1/analytics/export${qs}`, {
    method: 'POST',
    body: JSON.stringify({ format, sections }),
  }) as any
}
