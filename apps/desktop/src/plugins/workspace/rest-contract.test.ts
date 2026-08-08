/**
 * REST transport contract tests (U1C).
 *
 * `ctx.rest()` serializes object bodies itself — a pre-serialized JSON
 * string would be double-encoded on the wire and rejected by the backend.
 * These tests pin every Workspace mutation wrapper to the current
 * transport contract with a fake `ctx.rest` that records calls.
 */

import type { PluginContext } from '@hermes/plugin-sdk'
import { describe, expect, it, vi } from 'vitest'

import { exportAnalytics } from './analytics-api'
import { chat } from './assistant-api'
import { createMilestone, createRoadmap, reorderMilestones } from './roadmap-api'
import { applyBackfill, proposeBackfill } from './scope'
import { addComment, createTask, setDependencies } from './task-api'

function fakeCtx() {
  const rest = vi.fn().mockResolvedValue({})
  const ctx = { rest } as unknown as PluginContext

  return { ctx, rest }
}

describe('Workspace REST bodies (U1C transport contract)', () => {
  it('createTask sends a plain object body, not a JSON string', async () => {
    const { ctx, rest } = fakeCtx()
    const payload = { title: 't', workspace_id: 'w1' }

    await createTask(ctx, payload)

    expect(rest).toHaveBeenCalledWith('/v1/tasks', {
      method: 'POST',
      body: payload,
    })
    expect(typeof rest.mock.calls[0][1]?.body).toBe('object')
  })

  it('addComment sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()

    await addComment(ctx, 'task-1', 'hello')

    expect(rest).toHaveBeenCalledWith('/v1/tasks/task-1/comments', {
      method: 'POST',
      body: { body: 'hello' },
    })
  })

  it('setDependencies sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()

    await setDependencies(ctx, 'task-1', ['task-2'])

    expect(rest).toHaveBeenCalledWith('/v1/tasks/task-1/dependencies', {
      method: 'PUT',
      body: { depends_on_ids: ['task-2'] },
    })
  })

  it('createRoadmap sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()
    const payload = { workspace_id: 'w1', name: 'r' }

    await createRoadmap(ctx, payload)

    expect(rest).toHaveBeenCalledWith('/v1/roadmaps', {
      method: 'POST',
      body: payload,
    })
  })

  it('createMilestone sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()
    const payload = { title: 'm' }

    await createMilestone(ctx, 'roadmap-1', payload)

    expect(rest).toHaveBeenCalledWith('/v1/roadmaps/roadmap-1/milestones', {
      method: 'POST',
      body: payload,
    })
  })

  it('reorderMilestones sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()

    await reorderMilestones(ctx, 'roadmap-1', ['a', 'b'])

    expect(rest).toHaveBeenCalledWith('/v1/roadmaps/roadmap-1/milestones/reorder', {
      method: 'PUT',
      body: { ids: ['a', 'b'] },
    })
  })

  it('assistant chat sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()

    await chat(ctx, 'question', 'conv-1', 'w1')

    expect(rest).toHaveBeenCalledWith('/v1/assistant/chat', {
      method: 'POST',
      body: { question: 'question', conversation_id: 'conv-1', workspace_id: 'w1' },
    })
  })

  it('analytics export sends a plain object body', async () => {
    const { ctx, rest } = fakeCtx()

    await exportAnalytics(ctx, 'markdown', ['all'], 'w1')

    expect(rest).toHaveBeenCalledWith('/v1/analytics/export?workspace_id=w1', {
      method: 'POST',
      body: { format: 'markdown', sections: ['all'] },
    })
  })

  it('scope backfill proposal sends a plain object body with dry_run true', async () => {
    const { ctx, rest } = fakeCtx()

    await proposeBackfill(ctx, 'p_1')

    expect(rest).toHaveBeenCalledWith('/v1/scope/backfill', {
      method: 'POST',
      body: { project_id: 'p_1', workspace_id: '', dry_run: true },
    })
  })

  it('scope backfill apply sends a plain object body with dry_run false', async () => {
    const { ctx, rest } = fakeCtx()

    await applyBackfill(ctx, 'p_1', 'w1')

    expect(rest).toHaveBeenCalledWith('/v1/scope/backfill', {
      method: 'POST',
      body: { project_id: 'p_1', workspace_id: 'w1', dry_run: false },
    })
  })
})
