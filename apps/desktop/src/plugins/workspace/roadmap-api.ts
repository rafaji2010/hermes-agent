/**
 * Roadmap API — REST wrappers for roadmap + milestone endpoints.
 *
 * U1C: every mutation passes a plain object body — `ctx.rest()` serializes
 * it itself.  Get-by-id helpers carry the effective workspace scope.
 */

import type { PluginContext } from '@hermes/plugin-sdk'

import type {
  MilestoneCreatePayload,
  MilestoneListResponse,
  MilestoneUpdatePayload,
  RoadmapCreatePayload,
  RoadmapListResponse,
  RoadmapUpdatePayload,
} from './roadmaps'

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
  workspaceId = '',
): Promise<RoadmapListResponse> {
  const scope = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<RoadmapListResponse>(`/v1/roadmaps/${encodeURIComponent(roadmapId)}${scope}`)
}

export async function createRoadmap(
  ctx: PluginContext,
  payload: RoadmapCreatePayload,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>('/v1/roadmaps', {
    method: 'POST',
    body: payload,
  })
}

export async function updateRoadmap(
  ctx: PluginContext,
  roadmapId: string,
  payload: RoadmapUpdatePayload,
): Promise<RoadmapListResponse> {
  return ctx.rest<RoadmapListResponse>(`/v1/roadmaps/${encodeURIComponent(roadmapId)}`, {
    method: 'PUT',
    body: payload,
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
  workspaceId = '',
): Promise<MilestoneListResponse> {
  const scope = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''

  return ctx.rest<MilestoneListResponse>(
    `/v1/roadmaps/${encodeURIComponent(roadmapId)}/milestones${scope}`,
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
      body: payload,
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
      body: payload,
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
      body: { ids },
    },
  )
}
