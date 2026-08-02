/**
 * Search nanostores — reactive state for global search UI.
 */

import { atom } from 'nanostores'

export interface SearchResult {
  id: string
  type: string
  title: string
  description: string
  status: string
  priority: string
  labels: string[]
  workspace_id: string | null
  workspace_name: string
  created_at: string
  score: number
}

export interface RelatedEntity {
  id: string
  type: string
  title: string
  status: string
  relationship: string
}

export interface RelatedItemsResponse {
  entity_type: string
  entity_id: string
  items: RelatedEntity[]
}

export interface GraphNode {
  id: string
  type: string
  title: string
  status: string
}

export interface GraphEdge {
  source_id: string
  source_type: string
  target_id: string
  target_type: string
  relationship: string
}

export const $searchResults = atom<SearchResult[]>([])
export const $searchQuery = atom<string>('')
export const $searchFilters = atom<string>('')
export const $relatedItems = atom<RelatedEntity[]>([])
export const $selectedEntity = atom<SearchResult | null>(null)
