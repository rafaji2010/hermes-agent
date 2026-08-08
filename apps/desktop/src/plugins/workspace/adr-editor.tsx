/**
 * ADR Editor — create or edit an Architecture Decision Record.
 */

import {
  Button,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  Input,
  type PluginContext,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Separator,
  Textarea,
} from '@hermes/plugin-sdk'
import { useCallback, useState } from 'react'

import { createADR, updateADR, updateADRFile } from './adr-api'
import type { ADR } from './adrs'
import { isCanonicalADR } from './adrs'

interface ADREditorProps {
  adr: ADR | null
  ctx: PluginContext
  onClose: () => void
  onSaved: () => void
  workspaceId: string
}

export function ADREditor({ adr, ctx, onClose, onSaved, workspaceId }: ADREditorProps) {
  const [title, setTitle] = useState(adr?.title ?? '')
  const [status, setStatus] = useState(adr?.status ?? 'proposed')
  const [category, setCategory] = useState(adr?.category ?? '')
  const [markdown, setMarkdown] = useState(adr?.markdown ?? '')
  const [tagsInput, setTagsInput] = useState((adr?.tags ?? []).join(', '))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isEditing = adr !== null
  const canonical = isEditing && isCanonicalADR(adr!)

  const handleSave = useCallback(async () => {
    if (!title.trim()) {
      setError('Title is required.')

      return
    }

    setSaving(true)
    setError(null)

    try {
      const tags = tagsInput
        .split(',')
        .map(t => t.trim().toLowerCase())
        .filter(Boolean)

      if (isEditing && canonical) {
        // S7.3A: canonical ADRs are edited through the FILE — the markdown
        // body (including its frontmatter) is written atomically to the
        // canonical file, then the projection refreshes.  The form's
        // title/status/category fields apply to the file's frontmatter.
        await updateADRFile(ctx, adr!.id, markdown, workspaceId)
      } else if (isEditing) {
        await updateADR(ctx, adr!.id, {
          title: title.trim(),
          status,
          category: category.trim(),
          markdown,
          tags,
        })
      } else {
        await createADR(ctx, {
          workspace_id: workspaceId,
          title: title.trim(),
          status,
          category: category.trim(),
          markdown,
          tags,
        })
      }

      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save ADR.')
    } finally {
      setSaving(false)
    }
  }, [title, status, category, markdown, tagsInput, isEditing, canonical, adr, ctx, workspaceId, onSaved])

  return (
    <Dialog onOpenChange={onClose} open>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-auto">
        <DialogHeader>
          <DialogTitle>{isEditing ? 'Edit ADR' : 'New ADR'}</DialogTitle>
        </DialogHeader>

        {canonical && (
          <div className="rounded-md border border-blue-500/20 bg-blue-500/10 px-3 py-2 text-xs text-blue-400">
            Canonical ADR — the markdown body (including its frontmatter) is
            saved to the repository file. Title/status/category edits apply
            to the file's frontmatter.
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">
              Title *
            </label>
            <Input
              autoFocus
              onChange={e => setTitle(e.target.value)}
              placeholder="Short descriptive title"
              value={title}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">
                Status
              </label>
              <Select onValueChange={setStatus} value={status}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="proposed">Proposed</SelectItem>
                  <SelectItem value="accepted">Accepted</SelectItem>
                  <SelectItem value="rejected">Rejected</SelectItem>
                  <SelectItem value="superseded">Superseded</SelectItem>
                  <SelectItem value="deprecated">Deprecated</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">
                Category
              </label>
              <Input
                onChange={e => setCategory(e.target.value)}
                placeholder="e.g. Architecture"
                value={category}
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">
              Tags
            </label>
            <Input
              onChange={e => setTagsInput(e.target.value)}
              placeholder="architecture, backend, security (comma-separated)"
              value={tagsInput}
            />
          </div>

          <Separator />

          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">
              Markdown Body
            </label>
            <Textarea
              className="min-h-[200px] font-mono text-sm"
              onChange={e => setMarkdown(e.target.value)}
              placeholder="Write the ADR content in Markdown..."
              value={markdown}
            />
          </div>

          {error && (
            <div className="rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-500">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-2">
            <Button disabled={saving} onClick={onClose} size="sm" variant="ghost">
              Cancel
            </Button>
            <Button disabled={saving || !title.trim()} onClick={handleSave} size="sm" variant="default">
              {saving ? 'Saving...' : isEditing ? 'Save' : 'Create'}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
