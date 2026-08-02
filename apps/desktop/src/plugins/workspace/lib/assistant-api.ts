/**
 * Assistant API.
 */

import type { PluginContext } from '@/contrib/plugin'
import type { ChatResponseData, Suggestion } from '../stores/assistant'

export async function chat(
  ctx: PluginContext,
  question: string,
  conversationId: string,
  workspaceId: string,
): Promise<ChatResponseData> {
  return ctx.rest<ChatResponseData>('/v1/assistant/chat', {
    method: 'POST',
    body: JSON.stringify({
      question,
      conversation_id: conversationId,
      workspace_id: workspaceId,
    }),
  })
}

export async function getContext(
  ctx: PluginContext,
  question: string,
  workspaceId: string,
): Promise<any> {
  return ctx.rest(`/v1/assistant/context?question=${encodeURIComponent(question)}&workspace_id=${encodeURIComponent(workspaceId)}`)
}

export async function getSuggestions(
  ctx: PluginContext,
  workspaceId: string,
): Promise<{ suggestions: Suggestion[] }> {
  return ctx.rest<{ suggestions: Suggestion[] }>(
    `/v1/assistant/suggestions?workspace_id=${encodeURIComponent(workspaceId)}`,
  )
}
