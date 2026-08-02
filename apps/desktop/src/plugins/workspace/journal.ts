import { atom } from '@hermes/plugin-sdk'

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

// Atoms — pure UI state only. Request-shaped data lives in React Query.
export const $journalSearchQuery = atom<string>('')
export const $journalTagFilter = atom<string>('')
export const $journalDateFilter = atom<string>('')
export const $journalEditorOpen = atom<boolean>(false)
export const $journalEditingId = atom<string | null>(null)
