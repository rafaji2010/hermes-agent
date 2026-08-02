/**
 * Search API — global search + graph endpoints.
 */

import type { PluginContext } from '@/contrib/plugin'
import type { RelatedItemsResponse, SearchResult } from '../stores/search'

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
    if (v !== undefined && v !== '') p.set(k, v)
  }
  return ctx.rest<SearchResponse>(`/v1/search?${p.toString()}`)
}

export async function getRelated(
  ctx: PluginContext,
  entityType: string,
  entityId: string,
): Promise<RelatedItemsResponse> {
  return ctx.rest<RelatedItemsResponse>(
    `/v1/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/related`,
  )
}

export async function getGraph(ctx: PluginContext, workspaceId = ''): Promise<{ nodes: any[]; edges: any[] }> {
  return ctx.rest(`/v1/graph?workspace_id=${encodeURIComponent(workspaceId)}`)
}

export async function shortestPath(
  ctx: PluginContext,
  sourceType: string, sourceId: string,
  targetType: string, targetId: string,
): Promise<{ path: any[]; edges: any[]; distance: number }> {
  return ctx.rest(`/v1/graph/shortest-path?source_type=${sourceType}&source_id=${sourceId}&target_type=${targetType}&target_id=${targetId}`)
}
