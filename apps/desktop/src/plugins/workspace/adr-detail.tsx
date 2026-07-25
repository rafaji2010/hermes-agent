/**
 * ADR Detail — read-only view of a single ADR with markdown body.
 */

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

import type { ADR } from './stores/adrs'

const STATUS_COLORS: Record<string, string> = {
  proposed: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
  accepted: 'bg-green-500/10 text-green-500 border-green-500/20',
  rejected: 'bg-red-500/10 text-red-500 border-red-500/20',
  superseded: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  deprecated: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
}

export function ADRDetail({
  adr,
  onDelete,
  onEdit,
}: {
  adr: ADR
  onDelete: () => void
  onEdit: () => void
}) {
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

      {/* Tags */}
      {adr.tags.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-1">
          {adr.tags.map(t => (
            <span
              key={t}
              className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)"
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
