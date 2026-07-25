import { atom } from 'nanostores'

export interface JournalEntry {
  id: string
  workspace_id: string
  repository_id: string | null
  title: string
  summary: string
  markdown: string
  entry_date: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface JournalEntryCreatePayload {
  workspace_id: string
  repository_id?: string | null
  title: string
  summary?: string
  markdown?: string
  entry_date?: string
  tags?: string[]
}

export interface JournalEntryUpdatePayload {
  title?: string
  summary?: string
  markdown?: string
  entry_date?: string
  repository_id?: string | null
  tags?: string[]
}

export const $journalEntries = atom<JournalEntry[]>([])
export const $selectedJournalId = atom<string | null>(null)
export const $journalSearchQuery = atom<string>('')
export const $journalTagFilter = atom<string>('')
export const $journalDateFilter = atom<string>('')
export const $journalEditorOpen = atom<boolean>(false)
export const $journalEditingId = atom<string | null>(null)
