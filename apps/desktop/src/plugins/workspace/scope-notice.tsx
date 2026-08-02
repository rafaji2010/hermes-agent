/**
 * Workspace Plugin — Scope Notice (S7.2)
 *
 * Rendered by workspace pages when there is no resolved workspace scope.
 * Queries are gated on the scope store and never widened to global; this
 * notice explains why the page is empty and offers the backfill path when
 * a Hermes project was identified but not yet linked.
 */

import { Button, type PluginContext } from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import { applyBackfill, proposeBackfill, type WorkspaceScope } from './stores/scope'

interface WorkspaceScopeNoticeProps {
  ctx: PluginContext
  scope: WorkspaceScope
}

export function WorkspaceScopeNotice({ ctx, scope }: WorkspaceScopeNoticeProps) {
  const [linking, setLinking] = useState(false)
  const [message, setMessage] = useState('')

  const handleLink = useCallback(async () => {
    if (!scope.projectId) {return}
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

        return
      }

      if (!proposal.workspace_id) {
        setMessage('No workspace is available to link — create a workspace first.')

        return
      }

      const applied = await applyBackfill(ctx, scope.projectId, proposal.workspace_id)
      setMessage(applied.message || 'Workspace linked to the current project.')
    } catch {
      setMessage('Could not link the workspace — check that the Hermes backend is running.')
    } finally {
      setLinking(false)
    }
  }, [ctx, scope.projectId])

  if (scope.state === 'scoped' || scope.state === 'checking') {
    return null
  }

  return (
    <div className="border-b border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) px-6 py-2">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <span className="text-xs text-(--ui-text-secondary)">
            {scope.state === 'partial' && scope.projectSlug
              ? `Project “${scope.projectSlug}” has no linked workspace yet.`
              : 'No workspace scope resolved — data is not shown (queries never fall back to global).'}
          </span>
          {message && (
            <span className="ml-2 text-xs text-(--ui-text-tertiary)">{message}</span>
          )}
        </div>
        {scope.state === 'partial' && scope.projectId && (
          <Button disabled={linking} onClick={() => void handleLink()} size="xs" variant="secondary">
            {linking ? 'Linking…' : 'Link workspace'}
          </Button>
        )}
      </div>
    </div>
  )
}
