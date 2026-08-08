/**
 * Assistant API — REST wrappers for the workspace assistant.
 *
 * U1C: mutation bodies are plain objects — `ctx.rest()` serializes them.
 */

import type { PluginContext } from '@hermes/plugin-sdk'

import type { ChatResponseData, Suggestion } from './assistant'

export async function chat(
  ctx: PluginContext,
  question: string,
  conversationId: string,
  workspaceId: string,
): Promise<ChatResponseData> {
  return ctx.rest<ChatResponseData>('/v1/assistant/chat', {
    method: 'POST',
    body: {
      question,
      conversation_id: conversationId,
      workspace_id: workspaceId,
    },
  })
}

export async function getContext(
  ctx: PluginContext,
  question: string,
  workspaceId: string,
): Promise<unknown> {
  return ctx.rest(
    `/v1/assistant/context?question=${encodeURIComponent(question)}&workspace_id=${encodeURIComponent(workspaceId)}`,
  )
}

export async function getSuggestions(
  ctx: PluginContext,
  workspaceId: string,
): Promise<{ suggestions: Suggestion[] }> {
  return ctx.rest<{ suggestions: Suggestion[] }>(
    `/v1/assistant/suggestions?workspace_id=${encodeURIComponent(workspaceId)}`,
  )
}
