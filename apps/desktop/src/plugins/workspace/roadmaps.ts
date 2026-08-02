/**
 * Roadmap nanostores — types for the Roadmaps UI.
 */

// ---------------------------------------------------------------------------
// Types (mirrors backend models)
// ---------------------------------------------------------------------------

export interface RoadmapMilestone {
  id: string
  roadmap_id: string
  title: string
  description: string
  status: string
  sort_order: number
  target_date: string
  created_at: string
  updated_at: string
}

export interface Roadmap {
  id: string
  workspace_id: string
  name: string
  description: string
  milestones: RoadmapMilestone[]
  progress: number
  milestone_count: number
  completed_count: number
  created_at: string
  updated_at: string
}

export interface RoadmapCreatePayload {
  workspace_id: string
  name: string
  description?: string
}

export interface RoadmapUpdatePayload {
  name?: string
  description?: string
}

export interface MilestoneCreatePayload {
  title: string
  description?: string
  status?: string
  target_date?: string
}

export interface MilestoneUpdatePayload {
  title?: string
  description?: string
  status?: string
  target_date?: string
  sort_order?: number
}

export interface RoadmapListResponse {
  roadmaps: Roadmap[]
}

export interface MilestoneListResponse {
  milestones: RoadmapMilestone[]
}

// No atoms here: request-shaped data (roadmaps, milestones) lives in
// React Query, keyed by workspace/roadmap — see roadmaps-page.tsx.
