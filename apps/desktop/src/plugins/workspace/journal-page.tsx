import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useStore } from '@nanostores/react'
import { useCallback, useState } from 'react'

import { Contribute } from '@/contrib/react/contribute'
import type { PluginContext } from '@/contrib/plugin'
import { Button } from '@/components/ui/button'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { SearchField } from '@/components/ui/search-field'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

import {
  $journalEntries, $journalSearchQuery, $journalTagFilter, $journalDateFilter,
  $journalEditorOpen, $journalEditingId,
  type JournalEntry,
} from './stores/journal'
import {
  fetchJournalEntries, createJournalEntry as apiCreate,
  updateJournalEntry as apiUpdate, deleteJournalEntry as apiDelete,
} from './lib/journal-api'
import { JournalEditor } from './journal-editor'
import { WorkspaceScopeNotice } from './scope-notice'
import { scopeReady, useWorkspaceScope } from './stores/scope'

function JournalTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Workspace</span>
      <span className="text-[10px] text-(--ui-text-tertiary)">/</span>
      <span className="text-sm text-(--ui-text-secondary)">Journal</span>
    </div>
  )
}

function JournalRow({ entry, selected, onSelect }: {
  entry: JournalEntry; selected: boolean; onSelect: (id: string) => void;
}) {
  return (
    <button
      className={cn(
        'w-full rounded-lg border px-4 py-3 text-left transition-colors',
        selected ? 'border-(--ui-accent) bg-(--ui-accent)/5' : 'border-(--ui-stroke-tertiary) hover:border-(--ui-stroke-secondary)',
      )}
      onClick={() => onSelect(entry.id)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-(--ui-text-primary) truncate">{entry.title}</div>
          <div className="mt-0.5 text-xs text-(--ui-text-tertiary) line-clamp-2">{entry.summary || '(no summary)'}</div>
        </div>
        <span className="shrink-0 text-[11px] text-(--ui-text-tertiary)">{entry.entry_date}</span>
      </div>
      {entry.tags.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {entry.tags.slice(0, 5).map(t => (
            <span key={t} className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)">{t}</span>
          ))}
        </div>
      )}
    </button>
  )
}

interface JournalPageProps { ctx: PluginContext; workspaceId: string }

export function JournalPage({ ctx, workspaceId }: JournalPageProps) {
  const queryClient = useQueryClient()
  const scope = useWorkspaceScope(ctx)
  const ws = scopeReady(scope) ? scope.workspaceId : workspaceId
  const searchQuery = useStore($journalSearchQuery)
  const tagFilter = useStore($journalTagFilter)
  const dateFilter = useStore($journalDateFilter)
  const editorOpen = useStore($journalEditorOpen)
  const editingId = useStore($journalEditingId)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteId, setDeleteId] = useState<string | null>(null)

  const q = useQuery({
    queryKey: ['workspace', 'journal', ws, searchQuery, tagFilter, dateFilter],
    queryFn: () => fetchJournalEntries(ctx, ws, { q: searchQuery || undefined, tag: tagFilter || undefined, date: dateFilter || undefined }),
    enabled: Boolean(ws),
    staleTime: 10_000,
  })
  const entries = q.data?.entries ?? []
  const selected = entries.find(e => e.id === selectedId) ?? null
  const tags = [...new Set(entries.flatMap(e => e.tags))].sort()

  const handleDelete = useCallback(async () => {
    if (!deleteId) return
    await apiDelete(ctx, deleteId)
    setDeleteId(null)
    if (selectedId === deleteId) setSelectedId(null)
    queryClient.invalidateQueries({ queryKey: ['workspace', 'journal'] })
  }, [ctx, deleteId, queryClient, selectedId])

  const handleSaved = useCallback(() => {
    $journalEditorOpen.set(false); $journalEditingId.set(null)
    queryClient.invalidateQueries({ queryKey: ['workspace', 'journal'] })
  }, [queryClient])

  return (
    <div className="flex h-full flex-col">
      <Contribute area="titleBar.center" id="workspace:journal:titlebar"><JournalTitlebar /></Contribute>

      <WorkspaceScopeNotice ctx={ctx} scope={scope} />

      <div className="flex items-center justify-between border-b border-(--ui-stroke-tertiary) px-6 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <SearchField className="w-56" onChange={v => $journalSearchQuery.set(v)} placeholder="Search entries..." value={$journalSearchQuery.get()} />
          <Input className="h-7 w-[140px] text-xs" onChange={e => $journalDateFilter.set(e.target.value)} placeholder="YYYY-MM-DD" type="date" value={dateFilter} />
          {tags.length > 0 && (
            <Select onValueChange={v => $journalTagFilter.set(v)} value={tagFilter}>
              <SelectTrigger className="h-7 w-[140px] text-xs"><SelectValue placeholder="All tags" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="">All tags</SelectItem>
                {tags.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
              </SelectContent>
            </Select>
          )}
        </div>
        <Button onClick={() => { $journalEditingId.set(null); $journalEditorOpen.set(true) }} size="xs" variant="default">New Entry</Button>
      </div>

      <div className="flex flex-1 overflow-hidden">
        <div className="w-80 shrink-0 overflow-auto border-r border-(--ui-stroke-tertiary) p-3">
          {q.isLoading ? <div className="flex justify-center py-12"><Loader type="lemniscate-bloom" /></div>
          : q.isError ? <ErrorState title="Failed" description="Could not load entries." />
          : entries.length === 0 ? <EmptyState title="No entries" description="Create your first journal entry." />
          : <div className="space-y-2">{entries.map(e => <JournalRow key={e.id} entry={e} selected={selectedId === e.id} onSelect={setSelectedId} />)}</div>}
        </div>

        <div className="flex-1 overflow-auto">
          {selected ? (
            <div className="mx-auto max-w-3xl px-6 py-6">
              <div className="mb-4 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-base font-semibold text-(--ui-text-primary)">{selected.title}</h2>
                  <div className="mt-1 flex items-center gap-2 text-xs text-(--ui-text-tertiary)">
                    <span>{selected.entry_date}</span>
                    {selected.repository_id && <><span>·</span><Codicon name="repo" /><span>repo</span></>}
                  </div>
                </div>
                <div className="flex gap-1">
                  <Button aria-label="Edit" onClick={() => { $journalEditingId.set(selected.id); $journalEditorOpen.set(true) }} size="icon-sm" variant="ghost"><Codicon name="edit" /></Button>
                  <Button aria-label="Delete" onClick={() => setDeleteId(selected.id)} size="icon-sm" variant="ghost"><Codicon name="trash" /></Button>
                </div>
              </div>
              {selected.summary && <p className="mb-4 text-sm text-(--ui-text-secondary) italic">{selected.summary}</p>}
              {selected.tags.length > 0 && <div className="mb-4 flex flex-wrap gap-1">{selected.tags.map(t => <span key={t} className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] text-(--ui-text-tertiary)">{t}</span>)}</div>}
              <pre className="whitespace-pre-wrap font-mono text-sm text-(--ui-text-secondary)">{selected.markdown || '(No content)'}</pre>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-(--ui-text-tertiary)">Select an entry to view details</div>
          )}
        </div>
      </div>

      {editorOpen && (
        <JournalEditor
          entry={editingId ? entries.find(e => e.id === editingId) ?? null : null}
          ctx={ctx} workspaceId={ws}
          onClose={() => { $journalEditorOpen.set(false); $journalEditingId.set(null) }}
          onSaved={handleSaved}
        />
      )}
      {deleteId && <ConfirmDialog open title="Delete entry?" description="This entry will be permanently deleted." onCancel={() => setDeleteId(null)} onConfirm={handleDelete} />}
    </div>
  )
}
