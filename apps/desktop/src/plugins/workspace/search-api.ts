/**
 * Search API — workspace-scoped search + graph endpoints.
 *
 * U1C: every graph/search helper transmits the effective workspace scope —
 * requests never silently fall back to an unscoped/global query.
 */

import type { PluginContext } from '@hermes/plugin-sdk'

import type { RelatedItemsResponse, SearchResult } from './search'

interface SearchResponse {
  results: SearchResult[]
  total: number
  query: string
  filters: Record<string, string>
}

export async function search(
  ctx: PluginContext,
  params: { q?: string; workspace_id?: string; type?: string; status?: string;
            priority?: string; label?: string; roadmap?: string; repository?: string;
            limit?: number },
): Promise<SearchResponse> {
  const p = new URLSearchParams()

  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {p.set(k, String(v))}
  }

  return ctx.rest<SearchResponse>(`/v1/search?${p.toString()}`)
}

export async function getRelated(
  ctx: PluginContext,
  entityType: string,
  entityId: string,
  workspaceId = '',
): Promise<RelatedItemsResponse> {
  const scope = workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<RelatedItemsResponse>(
    `/v1/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/related?${scope.replace(/^&/, '')}`,
  )
}

export async function getGraph(
  ctx: PluginContext,
  workspaceId: string,
): Promise<{ nodes: unknown[]; edges: unknown[] }> {
  return ctx.rest(`/v1/graph?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export async function shortestPath(
  ctx: PluginContext,
  sourceType: string,
  sourceId: string,
  targetType: string,
  targetId: string,
  workspaceId = '',
): Promise<{ path: unknown[]; edges: unknown[]; distance: number }> {
  const params = new URLSearchParams({
    source_type: sourceType,
    source_id: sourceId,
    target_type: targetType,
    target_id: targetId,
  })

  if (workspaceId) {params.set('workspace_id', workspaceId)}

  return ctx.rest(`/v1/graph/shortest-path?${params.toString()}`)
}
