/**
 * Search nanostores — types for workspace-scoped search UI.
 */

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

// No atoms here: request-shaped data (results, related items, graph) lives
// in React Query — see search-page.tsx.
