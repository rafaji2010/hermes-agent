import { useCallback, useState } from 'react'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Separator } from '@/components/ui/separator'
import type { JournalEntry } from './stores/journal'
import { createJournalEntry, updateJournalEntry } from './lib/journal-api'

interface Props {
  entry: JournalEntry | null; ctx: PluginContext; workspaceId: string
  onClose: () => void; onSaved: () => void
}

export function JournalEditor({ entry, ctx, workspaceId, onClose, onSaved }: Props) {
  const [title, setTitle] = useState(entry?.title ?? '')
  const [summary, setSummary] = useState(entry?.summary ?? '')
  const [markdown, setMarkdown] = useState(entry?.markdown ?? '')
  const [entryDate, setEntryDate] = useState(entry?.entry_date ?? new Date().toISOString().slice(0, 10))
  const [tagsInput, setTagsInput] = useState((entry?.tags ?? []).join(', '))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isEditing = entry !== null

  const handleSave = useCallback(async () => {
    if (!title.trim()) { setError('Title is required.'); return }
    setSaving(true); setError(null)
    try {
      const tags = tagsInput.split(',').map(t => t.trim().toLowerCase()).filter(Boolean)
      if (isEditing) {
        await updateJournalEntry(ctx, entry!.id, { title: title.trim(), summary: summary.trim(), markdown, entry_date: entryDate, tags })
      } else {
        await createJournalEntry(ctx, { workspace_id: workspaceId, title: title.trim(), summary: summary.trim(), markdown, entry_date: entryDate, tags })
      }
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save.')
    } finally { setSaving(false) }
  }, [title, summary, markdown, entryDate, tagsInput, isEditing])

  return (
    <Dialog onOpenChange={onClose} open>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-auto">
        <DialogHeader><DialogTitle>{isEditing ? 'Edit Entry' : 'New Journal Entry'}</DialogTitle></DialogHeader>
        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">Title *</label>
            <Input autoFocus onChange={e => setTitle(e.target.value)} placeholder="What did you work on?" value={title} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">Date</label>
              <Input onChange={e => setEntryDate(e.target.value)} type="date" value={entryDate} />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">Tags</label>
              <Input onChange={e => setTagsInput(e.target.value)} placeholder="comma-separated" value={tagsInput} />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">Summary</label>
            <Input onChange={e => setSummary(e.target.value)} placeholder="One-line summary" value={summary} />
          </div>
          <Separator />
          <div>
            <label className="mb-1 block text-xs font-medium text-(--ui-text-secondary)">Markdown Body</label>
            <Textarea className="min-h-[200px] font-mono text-sm" onChange={e => setMarkdown(e.target.value)} placeholder="Detailed notes in Markdown..." value={markdown} />
          </div>
          {error && <div className="rounded-md border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-500">{error}</div>}
          <div className="flex justify-end gap-2">
            <Button disabled={saving} onClick={onClose} size="sm" variant="ghost">Cancel</Button>
            <Button disabled={saving || !title.trim()} onClick={handleSave} size="sm" variant="default">{saving ? 'Saving...' : isEditing ? 'Save' : 'Create'}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
