/**
 * Workspace Plugin — Project Scope Store (S7.2, U1C)
 *
 * Bridges the Hermes Project authority (backend `projects.db` + the
 * ProjectScopeResolver) to the Workspace plugin's workspace-scoped REST
 * surface.
 *
 * Authority model: the BACKEND resolves the scope (`POST /v1/scope/resolve`,
 * which walks session cwd → git root → workspace mapping).  The renderer
 * caches that resolution here and never guesses.  An unresolvable scope
 * yields `unresolved` — pages must NOT query globally (the backend rejects
 * unscoped requests with 403; this store surfaces that state honestly
 * instead of letting a page silently widen).
 *
 * U1C — SDK boundary: this store no longer imports application-internal
 * project/session atoms.  It consumes only the sanctioned `host.state`
 * surfaces:
 *
 *   - `host.state.cwd`      — the active workspace cwd ('' when detached)
 *   - `host.state.profile`  — the profile the live gateway is routed to
 *
 * IDENTITY RULE: `host.state.activeSessionId` is a VOLATILE runtime identity
 * and is deliberately NOT sent as `session_id` — `SessionDB.get_session()`
 * keys on the durable stored id, so the runtime id would resolve to
 * `unresolved` (or worse, mask a valid cwd).  For U1C the frontend resolves
 * scope from sanctioned CWD + profile only; the backend session-metadata
 * leg remains available to callers that possess a durable stored id.
 *
 * View state (which project you've entered in the sidebar) is app state the
 * backend cannot see; the effective working CWD is the sanctioned projection
 * of it that reaches this store.  Re-resolve whenever the current CWD,
 * profile, or runtime session changes.
 */

import { atom, host, type PluginContext, useValue } from '@hermes/plugin-sdk'
import { useEffect } from 'react'

export type WorkspaceScopeState =
  | 'checking'
  | 'scoped'
  | 'partial'
  | 'unresolved'
  | 'unavailable'

export interface WorkspaceScope {
  state: WorkspaceScopeState
  workspaceId: string
  projectId: null | string
  projectSlug: null | string
  matchSource: string
  /** Sanctioned cwd the resolution was based on ('' when detached). */
  cwd: string
  /** Profile the resolution ran against. */
  profile: string
  /** Transport/backend error message when `state === 'unavailable'`. */
  error: string
  /** True while an automatic retry is in flight. */
  retrying: boolean
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
  cwd: '',
  profile: '',
  error: '',
  retrying: false,
}

export const $workspaceScope = atom<WorkspaceScope>({
  ...EMPTY_SCOPE,
  state: 'checking',
})

/** Bumped by the notice's Retry affordance to re-run resolution. */
export const $scopeRetryTick = atom(0)

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
      cwd: '',
      profile: '',
      error: '',
      retrying: false,
    }
  }

  if (res.project_id) {
    return {
      state: 'partial',
      workspaceId: '',
      projectId: res.project_id,
      projectSlug: res.project_slug || null,
      matchSource: res.match_source || 'none',
      cwd: '',
      profile: '',
      error: '',
      retrying: false,
    }
  }

  return { ...EMPTY_SCOPE }
}

/** Pure factory for the transport-failure state (backend unreachable). */
export function workspaceScopeUnavailable(message: string): WorkspaceScope {
  return { ...EMPTY_SCOPE, state: 'unavailable', error: message }
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
  // Sanctioned CWD + profile only. The runtime session id is a volatile
  // identity and is NEVER sent as `session_id` (see module docstring).
  const cwd = host.state.cwd.get()
  const profile = host.state.profile.get()

  $workspaceScope.set({ ...EMPTY_SCOPE, state: 'checking', cwd, profile })

  // Outside any working directory there is nothing to resolve to — short
  // circuit to unresolved without a round trip. Never widens to global.
  if (!cwd) {
    const scope: WorkspaceScope = { ...EMPTY_SCOPE, cwd, profile }
    $workspaceScope.set(scope)

    return scope
  }

  try {
    const res = await ctx.rest<ResolvedProjectScopePayload>('/v1/scope/resolve', {
      method: 'POST',
      body: {
        session_id: '',
        workspace_id: '',
        cwd,
      },
    })

    if (generation !== resolveGeneration) {
      return $workspaceScope.get()
    }

    const scope: WorkspaceScope = {
      ...workspaceScopeFromResolution(res),
      cwd,
      profile,
    }

    $workspaceScope.set(scope)

    return scope
  } catch (err) {
    if (generation !== resolveGeneration) {
      return $workspaceScope.get()
    }

    const scope: WorkspaceScope = {
      ...workspaceScopeUnavailable(
        err instanceof Error ? err.message : 'Workspace backend unavailable.',
      ),
      cwd,
      profile,
    }

    $workspaceScope.set(scope)

    return scope
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
    body: {
      project_id: projectId,
      workspace_id: workspaceId,
      dry_run: true,
    },
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
    body: {
      project_id: projectId,
      workspace_id: workspaceId,
      dry_run: false,
    },
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
    body: { project_id: projectId },
  })
}

// ── React hook ─────────────────────────────────────────────────────────────

// One narrow job: keep the cached scope fresh for the CWD/profile/session the
// user is actually in, and return it. Pages read `workspaceId` from this.
// Re-resolves on every sanctioned re-home signal: cwd, profile, runtime
// session, and the notice's explicit retry tick.
export function useWorkspaceScope(ctx: PluginContext): WorkspaceScope {
  const scope = useValue($workspaceScope)
  const cwd = useValue(host.state.cwd)
  const profile = useValue(host.state.profile)
  const activeSessionId = useValue(host.state.activeSessionId)
  const retryTick = useValue($scopeRetryTick)

  useEffect(() => {
    void refreshWorkspaceScope(ctx)
  }, [ctx, cwd, profile, activeSessionId, retryTick])

  return scope
}
