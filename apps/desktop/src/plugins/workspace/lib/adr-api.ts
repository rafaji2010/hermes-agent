/**
 * ADR API — REST wrappers for all ADR endpoints.
 *
 * Calls ``ctx.rest()`` which routes through the plugin REST bridge to
 * ``/api/plugins/workspace/v1/adrs*``.
 */

import type { PluginContext } from '@/contrib/plugin'
import type {
  ADR,
  ADRCreatePayload,
  ADRUpdatePayload,
  ADRReconcileStatus,
  ADRReconcileSummary,
  ADRMaterializeResult,
} from '../stores/adrs'

export interface ADRListResponse {
  adrs: ADR[]
}

export async function fetchADRs(
  ctx: PluginContext,
  workspaceId: string,
  params: {
    status?: string
    category?: string
    tag?: string
    q?: string
  },
): Promise<ADRListResponse> {
  const qs = new URLSearchParams({ workspace_id: workspaceId })
  if (params.status) qs.set('status', params.status)
  if (params.category) qs.set('category', params.category)
  if (params.tag) qs.set('tag', params.tag)
  if (params.q) qs.set('q', params.q)
  return ctx.rest<ADRListResponse>(`/v1/adrs?${qs.toString()}`)
}

export async function getADR(
  ctx: PluginContext,
  adrId: string,
): Promise<ADRListResponse> {
  return ctx.rest<ADRListResponse>(`/v1/adrs/${encodeURIComponent(adrId)}`)
}

export async function createADR(
  ctx: PluginContext,
  payload: ADRCreatePayload,
): Promise<ADRListResponse> {
  return ctx.rest<ADRListResponse>('/v1/adrs', {
    method: 'POST',
    body: payload as unknown as Record<string, unknown>,
  })
}

export async function updateADR(
  ctx: PluginContext,
  adrId: string,
  payload: ADRUpdatePayload,
): Promise<ADRListResponse> {
  return ctx.rest<ADRListResponse>(`/v1/adrs/${encodeURIComponent(adrId)}`, {
    method: 'PUT',
    body: payload as unknown as Record<string, unknown>,
  })
}

export async function deleteADR(
  ctx: PluginContext,
  adrId: string,
): Promise<void> {
  await ctx.rest(`/v1/adrs/${encodeURIComponent(adrId)}`, { method: 'DELETE' })
}

// ---------------------------------------------------------------------------
// S7.3A — canonical ADR reconciliation
// ---------------------------------------------------------------------------

export async function reconcileADRs(
  ctx: PluginContext,
  workspaceId: string,
  dryRun = false,
): Promise<ADRReconcileSummary> {
  return ctx.rest<ADRReconcileSummary>('/v1/adrs/reconcile', {
    method: 'POST',
    body: { workspace_id: workspaceId, dry_run: dryRun },
  })
}

export async function fetchReconcileStatus(
  ctx: PluginContext,
  workspaceId: string,
): Promise<ADRReconcileStatus[]> {
  const resp = await ctx.rest<{ statuses: ADRReconcileStatus[] }>(
    `/v1/adrs/reconcile/status?workspace_id=${encodeURIComponent(workspaceId)}`,
  )
  return resp.statuses
}

export async function materializeADR(
  ctx: PluginContext,
  adrId: string,
  dryRun: boolean,
  workspaceId: string,
): Promise<ADRMaterializeResult> {
  return ctx.rest<ADRMaterializeResult>(
    `/v1/adrs/${encodeURIComponent(adrId)}/materialize?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      method: 'POST',
      body: { dry_run: dryRun },
    },
  )
}

export async function updateADRFile(
  ctx: PluginContext,
  adrId: string,
  markdown: string,
  workspaceId: string,
): Promise<ADRMaterializeResult> {
  return ctx.rest<ADRMaterializeResult>(
    `/v1/adrs/${encodeURIComponent(adrId)}/file?workspace_id=${encodeURIComponent(workspaceId)}`,
    {
      method: 'PUT',
      body: { markdown, dry_run: false },
    },
  )
}

export async function fetchADRTags(
  ctx: PluginContext,
  workspaceId: string,
): Promise<{ tags: string[] }> {
  const resp = await fetchADRs(ctx, workspaceId, {})
  const tags = [...new Set(resp.adrs.flatMap(a => a.tags))].sort()
  return { tags }
}

export async function fetchADRCategories(
  ctx: PluginContext,
  workspaceId: string,
): Promise<{ categories: string[] }> {
  const resp = await fetchADRs(ctx, workspaceId, {})
  const categories = [...new Set(resp.adrs.map(a => a.category).filter(Boolean))].sort()
  return { categories }
}
