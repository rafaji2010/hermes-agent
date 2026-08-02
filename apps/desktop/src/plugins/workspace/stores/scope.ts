/**
 * Workspace Plugin — Project Scope Store (S7.2)
 *
 * Bridges the Hermes Project authority (shared `projects.ts` atoms +
 * backend `projects.db`) to the Workspace plugin's workspace-scoped
 * REST surface.
 *
 * Authority model: the BACKEND resolves the scope (`POST /v1/scope/resolve`,
 * which walks session cwd → git root → workspace mapping).  The renderer
 * caches that resolution here and never guesses.  An unresolvable scope
 * yields `unresolved` — pages must NOT query globally (the backend
 * rejects unscoped requests with 403; this store surfaces that state
 * honestly instead of letting a page silently widen).
 *
 * View state (which project you've entered in the sidebar) lives in the
 * shared `$projectScope` atom; the workspace id itself is workspace.db
 * state and only the backend knows it.  Re-resolve whenever the active
 * session or project scope changes.
 */

import type { PluginContext } from '@hermes/plugin-sdk'
import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { useEffect } from 'react'

import { $projectScope, ALL_PROJECTS } from '@/store/projects'
import { $activeSessionId } from '@/store/session'

export type WorkspaceScopeState = 'checking' | 'scoped' | 'partial' | 'unresolved'

export interface WorkspaceScope {
  state: WorkspaceScopeState
  workspaceId: string
  projectId: null | string
  projectSlug: null | string
  matchSource: string
}

export interface ResolvedProjectScopePayload {
  workspace_id: string
  workspace_name: string
  project_id: null | string
  project_slug: null | string
  state: string
  match_source: string
  matched_path: string
}

export interface ScopeBackfillPayload {
  status: string
  workspace_id: string
  project_id: string
  candidates: string[]
  message: string
}

export const EMPTY_SCOPE: WorkspaceScope = {
  state: 'unresolved',
  workspaceId: '',
  projectId: null,
  projectSlug: null,
  matchSource: 'none',
}

export const $workspaceScope = atom<WorkspaceScope>({
  state: 'checking',
  workspaceId: '',
  projectId: null,
  projectSlug: null,
  matchSource: 'none',
})

// ── Pure mapping (testable, no I/O) ────────────────────────────────────────

export function workspaceScopeFromResolution(res: ResolvedProjectScopePayload): WorkspaceScope {
  const workspaceId = (res.workspace_id || '').trim()

  if (workspaceId) {
    return {
      state: 'scoped',
      workspaceId,
      projectId: res.project_id || null,
      projectSlug: res.project_slug || null,
      matchSource: res.match_source || 'mapping',
    }
  }

  if (res.project_id) {
    return {
      state: 'partial',
      workspaceId: '',
      projectId: res.project_id,
      projectSlug: res.project_slug || null,
      matchSource: res.match_source || 'none',
    }
  }

  return EMPTY_SCOPE
}

// The query params a page should send. NEVER returns a global scope —
// an unscoped store yields NO workspace param, and the page gates its
// queries on the resulting truthiness (see `scopeReady`).
export function scopeQueryParams(scope: WorkspaceScope): Record<string, string> {
  if (scope.state === 'scoped' && scope.workspaceId) {
    return { workspace_id: scope.workspaceId }
  }

  return {}
}

// True when a page may issue workspace-scoped queries.
export function scopeReady(scope: WorkspaceScope): boolean {
  return scope.state === 'scoped' && Boolean(scope.workspaceId)
}

// ── Resolution (backend is authoritative) ─────────────────────────────────

let resolveGeneration = 0

export async function refreshWorkspaceScope(ctx: PluginContext): Promise<WorkspaceScope> {
  const generation = ++resolveGeneration
  const sessionId = $activeSessionId.get()
  const projectScope = $projectScope.get()

  $workspaceScope.set({ ...EMPTY_SCOPE, state: 'checking' })

  // Outside any project scope there is nothing to resolve to — short
  // circuit to unresolved without a round trip.
  if (!sessionId || projectScope === ALL_PROJECTS || !projectScope) {
    const scope = { ...EMPTY_SCOPE }
    $workspaceScope.set(scope)

    return scope
  }

  try {
    const res = await ctx.rest<ResolvedProjectScopePayload>('/v1/scope/resolve', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        workspace_id: '',
        cwd: '',
      }),
    })

    if (generation !== resolveGeneration) {
      return $workspaceScope.get()
    }

    const scope = workspaceScopeFromResolution(res)
    $workspaceScope.set(scope)

    return scope
  } catch {
    if (generation !== resolveGeneration) {
      return $workspaceScope.get()
    }

    $workspaceScope.set({ ...EMPTY_SCOPE })

    return { ...EMPTY_SCOPE }
  }
}

// ── Backfill / linking (inspection-first, never auto) ─────────────────────

// Dry-run proposal: what would linking the current project to a workspace do?
export async function proposeBackfill(
  ctx: PluginContext,
  projectId: string,
  workspaceId = ''
): Promise<ScopeBackfillPayload> {
  return ctx.rest<ScopeBackfillPayload>('/v1/scope/backfill', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      workspace_id: workspaceId,
      dry_run: true,
    }),
  })
}

// Apply a backfill link (explicit user confirmation required).
export async function applyBackfill(
  ctx: PluginContext,
  projectId: string,
  workspaceId: string
): Promise<ScopeBackfillPayload> {
  return ctx.rest<ScopeBackfillPayload>('/v1/scope/backfill', {
    method: 'POST',
    body: JSON.stringify({
      project_id: projectId,
      workspace_id: workspaceId,
      dry_run: false,
    }),
  })
}

// Explicit link of a known workspace to the current project.
export async function linkWorkspaceToProject(
  ctx: PluginContext,
  workspaceId: string,
  projectId: string
): Promise<void> {
  await ctx.rest(`/v1/workspaces/${encodeURIComponent(workspaceId)}/project`, {
    method: 'PUT',
    body: JSON.stringify({ project_id: projectId }),
  })
}

// ── React hook ─────────────────────────────────────────────────────────────

// One narrow job: keep the cached scope fresh for the session/project the
// user is actually in, and return it. Pages read `workspaceId` from this.
export function useWorkspaceScope(ctx: PluginContext): WorkspaceScope {
  const scope = useStore($workspaceScope)
  const sessionId = useStore($activeSessionId)
  const projectScope = useStore($projectScope)

  useEffect(() => {
    void refreshWorkspaceScope(ctx)
  }, [ctx, sessionId, projectScope])

  return scope
}
