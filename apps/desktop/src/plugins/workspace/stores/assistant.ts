/**
 * Assistant nanostores.
 */

import { atom } from 'nanostores'

export interface ReferencedEntity {
  id: string
  type: string
  title: string
  status: string
  relevance: string
}

export interface ChatMessage {
  role: string
  content: string
  referenced_entities: ReferencedEntity[]
}

export interface ChatResponseData {
  conversation_id: string
  answer: string
  referenced_entities: ReferencedEntity[]
  analytics_support: string
  related_items: string[]
  confidence: number
  explanation: string
}

export interface Suggestion {
  type: string
  title: string
  description: string
  entity_id: string
  entity_type: string
  priority: string
}

export const $messages = atom<ChatMessage[]>([])
export const $conversationId = atom<string>('')
export const $suggestions = atom<Suggestion[]>([])
export const $isThinking = atom<boolean>(false)
export const $referencedEntities = atom<ReferencedEntity[]>([])
