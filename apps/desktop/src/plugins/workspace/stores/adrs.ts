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
  // S7.3A — canonical reconciliation projection fields (backend mirrors).
  canonical_path: string
  content_hash: string
  reconcile_state: string
  source: string
  last_indexed: string
  last_error: string
}

export type ADRReconcileState =
  | 'synced'
  | 'file_new'
  | 'file_changed'
  | 'db_legacy'
  | 'missing_file'
  | 'conflict'
  | 'invalid'

export interface ADRReconcileStatus {
  id: string
  workspace_id: string
  title: string
  slug: string
  status: string
  reconcile_state: string
  source: string
  canonical_path: string
  canonical_id: string
  content_hash: string
  last_indexed: string
  last_error: string
  file_exists: boolean
}

export interface ADRReconcileSummary {
  workspace_id: string
  project_id: string
  scanned_files: number
  indexed: number
  synced: number
  file_changed: number
  db_legacy: number
  missing_file: number
  conflict: number
  invalid: number
  invalid_paths: string[]
  dry_run: boolean
}

export interface ADRMaterializeResult {
  id: string
  status: string
  target_path: string
  message: string
}

// ── Pure reconcile helpers (testable, no I/O) ──────────────────────────────

export function isCanonicalADR(adr: Pick<ADR, 'source'>): boolean {
  return adr.source === 'git_file'
}

export function isLegacyADR(adr: Pick<ADR, 'source'>): boolean {
  return adr.source === 'workspace_db'
}

const RECONCILE_TONES: Record<string, string> = {
  synced: 'bg-green-500/10 text-green-500 border-green-500/20',
  file_new: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  file_changed: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  db_legacy: 'bg-gray-500/10 text-gray-400 border-gray-500/20',
  missing_file: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  conflict: 'bg-red-500/10 text-red-500 border-red-500/20',
  invalid: 'bg-red-500/10 text-red-500 border-red-500/20',
}

export function adrReconcileTone(state: string): string {
  return RECONCILE_TONES[state] ?? RECONCILE_TONES.db_legacy
}

export function adrReconcileLabel(state: string): string {
  const labels: Record<string, string> = {
    synced: 'synced',
    file_new: 'new file',
    file_changed: 'changed',
    db_legacy: 'legacy',
    missing_file: 'missing file',
    conflict: 'conflict',
    invalid: 'invalid',
  }
  return labels[state] ?? state
}

export function adrReconcileSummaryMessage(summary: ADRReconcileSummary | null): string {
  if (!summary) return ''
  const parts: string[] = []
  if (summary.indexed > 0) parts.push(`${summary.indexed} indexed`)
  if (summary.synced > 0) parts.push(`${summary.synced} synced`)
  if (summary.file_changed > 0) parts.push(`${summary.file_changed} refreshed`)
  if (summary.db_legacy > 0) parts.push(`${summary.db_legacy} legacy`)
  if (summary.conflict > 0) parts.push(`${summary.conflict} conflict`)
  if (summary.invalid > 0) parts.push(`${summary.invalid} invalid`)
  return parts.join(', ') || 'no changes'
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
