/**
 * Workspace Plugin — Scope Notice (S7.2, U1C)
 *
 * Rendered by workspace pages when there is no resolved workspace scope.
 * Queries are gated on the scope store and never widened to global; this
 * notice explains why the page is empty and offers:
 *
 *   - a backfill path when a Hermes project was identified but not linked,
 *   - a bounded Retry affordance when the backend is unreachable.
 *
 * After a mapping is applied the cached scope is refreshed and the
 * Workspace queries are invalidated so the UI transitions partial → scoped.
 */

import { Button, type PluginContext, queryClient } from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import { $scopeRetryTick, applyBackfill, proposeBackfill, refreshWorkspaceScope, type WorkspaceScope } from './scope'

interface WorkspaceScopeNoticeProps {
  ctx: PluginContext
  scope: WorkspaceScope
}

export function WorkspaceScopeNotice({ ctx, scope }: WorkspaceScopeNoticeProps) {
  const [linking, setLinking] = useState(false)
  const [message, setMessage] = useState('')

  const handleLink = useCallback(async () => {
    if (!scope.projectId) { return }
    setLinking(true)
    setMessage('')

    try {
      // Inspection-first: the backend confirms the mapping is unambiguous
      // before any change.
      const proposal = await proposeBackfill(ctx, scope.projectId)

      if (proposal.status === 'ambiguous') {
        setMessage('This project is linked to multiple workspaces — resolve the mapping manually before continuing.')

        return
      }

      if (proposal.status === 'already_linked') {
        setMessage('This project is already linked to a workspace. Refreshing…')
        await refreshWorkspaceScope(ctx)
        queryClient.invalidateQueries({ queryKey: ['workspace'] })

        return
      }

      if (!proposal.workspace_id) {
        setMessage('No workspace is available to link — create a workspace first.')

        return
      }

      const applied = await applyBackfill(ctx, scope.projectId, proposal.workspace_id)
      setMessage(applied.message || 'Workspace linked to the current project.')
      // Re-home: refresh the cached scope and invalidate every Workspace
      // query so pages transition from partial → scoped with fresh data.
      await refreshWorkspaceScope(ctx)
      queryClient.invalidateQueries({ queryKey: ['workspace'] })
    } catch {
      setMessage('Could not link the workspace — check that the Hermes backend is running.')
    } finally {
      setLinking(false)
    }
  }, [ctx, scope.projectId])

  const handleRetry = useCallback(() => {
    $scopeRetryTick.set($scopeRetryTick.get() + 1)
  }, [])

  if (scope.state === 'scoped' || scope.state === 'checking') {
    return null
  }

  const copy = scope.state === 'unavailable'
    ? 'Workspace backend unreachable — data is not shown and queries never fall back to global.'
    : scope.state === 'partial' && scope.projectSlug
      ? `Project “${scope.projectSlug}” has no linked workspace yet.`
      : 'No workspace scope resolved — data is not shown (queries never fall back to global).'

  return (
    <div className="border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) px-6 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="text-xs text-(--ui-text-secondary)">{copy}</span>
          {message && (
            <span className="ml-2 text-xs text-(--ui-text-tertiary)">{message}</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {scope.state === 'unavailable' && (
            <Button disabled={scope.retrying} onClick={handleRetry} size="xs" variant="secondary">
              Retry
            </Button>
          )}
          {scope.state === 'partial' && scope.projectId && (
            <Button disabled={linking} onClick={() => void handleLink()} size="xs" variant="secondary">
              {linking ? 'Linking…' : 'Link workspace'}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
