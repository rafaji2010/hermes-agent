import type { PluginContext } from '@/contrib/plugin'
import type {
  JournalEntry,
  JournalEntryCreatePayload,
  JournalEntryUpdatePayload,
} from '../stores/journal'

interface JournalListResponse {
  entries: JournalEntry[]
}

export async function fetchJournalEntries(
  ctx: PluginContext, workspaceId: string,
  params: { repository_id?: string; tag?: string; date?: string; q?: string; limit?: number },
): Promise<JournalListResponse> {
  const qs = new URLSearchParams({ workspace_id: workspaceId })
  if (params.repository_id) qs.set('repository_id', params.repository_id)
  if (params.tag) qs.set('tag', params.tag)
  if (params.date) qs.set('date', params.date)
  if (params.q) qs.set('q', params.q)
  if (params.limit) qs.set('limit', String(params.limit))
  return ctx.rest<JournalListResponse>(`/v1/journal?${qs.toString()}`)
}

export async function createJournalEntry(
  ctx: PluginContext, payload: JournalEntryCreatePayload,
): Promise<JournalListResponse> {
  return ctx.rest<JournalListResponse>('/v1/journal', {
    method: 'POST', body: payload as unknown as Record<string, unknown>,
  })
}

export async function updateJournalEntry(
  ctx: PluginContext, entryId: string, payload: JournalEntryUpdatePayload,
): Promise<JournalListResponse> {
  return ctx.rest<JournalListResponse>(`/v1/journal/${encodeURIComponent(entryId)}`, {
    method: 'PUT', body: payload as unknown as Record<string, unknown>,
  })
}

export async function deleteJournalEntry(ctx: PluginContext, entryId: string): Promise<void> {
  await ctx.rest(`/v1/journal/${encodeURIComponent(entryId)}`, { method: 'DELETE' })
}
