/**
 * Roadmap API — REST wrappers for roadmap + milestone endpoints.
 */

import type { PluginContext } from '@/contrib/plugin'
import type {
  MilestoneCreatePayload,
  MilestoneListResponse,
  MilestoneUpdatePayload,
  RoadmapCreatePayload,
  RoadmapListResponse,
  RoadmapUpdatePayload,
} from '../stores/roadmaps'

export async function fetchRoadmaps(
  ctx: PluginContext,
  workspaceId: string,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>(
    `/v1/roadmaps?workspace_id=${encodeURIComponent(workspaceId)}`,
  )
}

export async function getRoadmap(
  ctx: PluginContext,
  roadmapId: string,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>(`/v1/roadmaps/${encodeURIComponent(roadmapId)}`)
}

export async function createRoadmap(
  ctx: PluginContext,
  payload: RoadmapCreatePayload,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>('/v1/roadmaps', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateRoadmap(
  ctx: PluginContext,
  roadmapId: string,
  payload: RoadmapUpdatePayload,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>(`/v1/roadmaps/${encodeURIComponent(roadmapId)}`, {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export async function deleteRoadmap(
  ctx: PluginContext,
  roadmapId: string,
): Promise<{ ok: boolean }> {
  return ctx.rest<{ ok: boolean }>(`/v1/roadmaps/${encodeURIComponent(roadmapId)}`, {
    method: 'DELETE',
  })
}

// ---------------------------------------------------------------------------
// Milestones
// ---------------------------------------------------------------------------

export async function fetchMilestones(
  ctx: PluginContext,
  roadmapId: string,
): Promise<MilestoneListResponse> {
  return ctx.rest<MilestoneListResponse>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones`,
  )
}

export async function createMilestone(
  ctx: PluginContext,
  roadmapId: string,
  payload: MilestoneCreatePayload,
): Promise<MilestoneListResponse> {
  return ctx.rest<MilestoneListResponse>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones`,
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export async function updateMilestone(
  ctx: PluginContext,
  roadmapId: string,
  milestoneId: string,
  payload: MilestoneUpdatePayload,
): Promise<MilestoneListResponse> {
  return ctx.rest<MilestoneListResponse>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones/${encodeURIComponent(milestoneId)}`,
    {
      method: 'PUT',
      body: JSON.stringify(payload),
    },
  )
}

export async function deleteMilestone(
  ctx: PluginContext,
  roadmapId: string,
  milestoneId: string,
): Promise<{ ok: boolean }> {
  return ctx.rest<{ ok: boolean }>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones/${encodeURIComponent(milestoneId)}`,
    { method: 'DELETE' },
  )
}

export async function reorderMilestones(
  ctx: PluginContext,
  roadmapId: string,
  ids: string[],
): Promise<MilestoneListResponse> {
  return ctx.rest<MilestoneListResponse>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones/reorder`,
    {
      method: 'PUT',
      body: JSON.stringify({ ids }),
    },
  )
}
