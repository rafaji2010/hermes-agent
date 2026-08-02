/**
 * ADR Detail — read-only view of a single ADR with markdown body.
 */

import {
  Button,
  cn,
  Codicon,
  type PluginContext,
} from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import { materializeADR } from './adr-api'
import {
  type ADR,
  adrReconcileLabel,
  adrReconcileTone,
  isLegacyADR,
} from './adrs'

const STATUS_COLORS: Record<string, string> = {
  proposed: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  accepted: 'bg-green-500/10 text-green-500 border-green-500/20',
  rejected: 'bg-red-500/10 text-red-500 border-red-500/20',
  superseded: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  deprecated: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
}

export function ADRDetail({
  adr,
  ctx,
  workspaceId,
  onDelete,
  onEdit,
  onChanged,
}: {
  adr: ADR
  ctx: PluginContext
  workspaceId: string
  onDelete: () => void
  onEdit: () => void
  onChanged: () => void
}) {
  const [materializing, setMaterializing] = useState(false)
  const [materializeMsg, setMaterializeMsg] = useState('')
  const legacy = isLegacyADR(adr)

  const handleMaterialize = useCallback(async () => {
    setMaterializing(true)
    setMaterializeMsg('')

    try {
      // Inspection-first: preview what would be written.
      const preview = await materializeADR(ctx, adr.id, true, workspaceId)

      if (preview.status === 'preview') {
        setMaterializeMsg(`Preview: would write ${preview.target_path}.`)
      }

      // Explicit apply.
      const result = await materializeADR(ctx, adr.id, false, workspaceId)

      if (result.status === 'materialized') {
        setMaterializeMsg(`Materialized to ${result.target_path}.`)
        onChanged()
      } else {
        setMaterializeMsg(result.message || `Materialization: ${result.status}`)
      }
    } catch (err) {
      setMaterializeMsg(err instanceof Error ? err.message : 'Materialization failed.')
    } finally {
      setMaterializing(false)
    }
  }, [ctx, adr.id, workspaceId, onChanged])

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      {/* Header */}
      <div className="mb-6 flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h2 className="text-base font-semibold text-(--ui-text-primary)">
            {adr.title}
          </h2>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-(--ui-text-tertiary)">
            <span>{adr.slug}</span>
            {adr.category && (
              <>
                <span>·</span>
                <span>{adr.category}</span>
              </>
            )}
            {/* S7.3A — reconciliation state */}
            <span
              className={cn(
                'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
                adrReconcileTone(adr.reconcile_state),
              )}
              title={adr.last_error || adr.reconcile_state}
            >
              {adrReconcileLabel(adr.reconcile_state)}
            </span>
            {adr.source === 'git_file' && adr.canonical_path && (
              <span className="font-mono text-[10px] text-(--ui-text-tertiary)">
                {adr.canonical_path}
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className={cn(
              'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
              STATUS_COLORS[adr.status] || '',
            )}
          >
            {adr.status}
          </span>
          <Button aria-label="Edit ADR" onClick={onEdit} size="icon-sm" variant="ghost">
            <Codicon name="edit" />
          </Button>
          <Button aria-label="Delete ADR" onClick={onDelete} size="icon-sm" variant="ghost">
            <Codicon name="trash" />
          </Button>
        </div>
      </div>

      {/* S7.3A — legacy ADR materialization */}
      {legacy && (
        <div className="mb-4 rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-bg-quaternary) px-3 py-2 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="text-(--ui-text-secondary)">
              Legacy DB-only ADR — the canonical file does not exist yet.
            </span>
            <Button
              disabled={materializing}
              onClick={() => void handleMaterialize()}
              size="xs"
              variant="secondary"
            >
              {materializing ? 'Materializing…' : 'Materialize to file'}
            </Button>
          </div>
          {materializeMsg && (
            <div className="mt-1 text-(--ui-text-tertiary)">{materializeMsg}</div>
          )}
        </div>
      )}

      {/* Tags */}
      {adr.tags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-1">
          {adr.tags.map(t => (
            <span
              className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)"
              key={t}
            >
              {t}
            </span>
          ))}
        </div>
      )}

      {/* Meta */}
      <div className="mb-6 grid grid-cols-2 gap-2 rounded-lg border border-(--ui-stroke-tertiary) p-3 text-xs">
        <div>
          <span className="text-(--ui-text-tertiary)">Status</span>
          <div className="font-medium text-(--ui-text-primary)">{adr.status}</div>
        </div>
        <div>
          <span className="text-(--ui-text-tertiary)">Category</span>
          <div className="font-medium text-(--ui-text-primary)">{adr.category || '—'}</div>
        </div>
        <div>
          <span className="text-(--ui-text-tertiary)">Created</span>
          <div className="font-medium text-(--ui-text-primary)">{adr.created_at}</div>
        </div>
        <div>
          <span className="text-(--ui-text-tertiary)">Updated</span>
          <div className="font-medium text-(--ui-text-primary)">{adr.updated_at}</div>
        </div>
      </div>

      {/* Markdown body */}
      <div className="prose prose-sm max-w-none dark:prose-invert">
        <pre className="whitespace-pre-wrap font-mono text-sm text-(--ui-text-secondary)">
          {adr.markdown || '(No content)'}
        </pre>
      </div>
    </div>
  )
}
