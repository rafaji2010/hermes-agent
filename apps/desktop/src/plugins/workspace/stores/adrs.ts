/**
 * ADR nanostores — reactive state for the ADR management UI.
 */

import { atom } from 'nanostores'

// ---------------------------------------------------------------------------
// Types (mirrors backend models)
// ---------------------------------------------------------------------------

export interface ADR {
  id: string
  workspace_id: string
  repository_id: string | null
  title: string
  slug: string
  status: string
  category: string
  markdown: string
  tags: string[]
  created_at: string
  updated_at: string
}

export interface ADRCreatePayload {
  workspace_id: string
  repository_id?: string | null
  title: string
  status?: string
  category?: string
  markdown?: string
  tags?: string[]
}

export interface ADRUpdatePayload {
  title?: string
  status?: string
  category?: string
  markdown?: string
  tags?: string[]
}

export type ADRStatus = 'proposed' | 'accepted' | 'rejected' | 'superseded' | 'deprecated'

// ---------------------------------------------------------------------------
// Atoms
// ---------------------------------------------------------------------------

export const $adrs = atom<ADR[]>([])
export const $selectedADRId = atom<string | null>(null)
export const $adrSearchQuery = atom<string>('')
export const $adrStatusFilter = atom<ADRStatus | ''>('')
export const $adrCategoryFilter = atom<string>('')
export const $adrTagFilter = atom<string>('')
export const $adrTags = atom<string[]>([])
export const $adrCategories = atom<string[]>([])
export const $adrEditorOpen = atom<boolean>(false)
export const $adrEditingId = atom<string | null>(null)
