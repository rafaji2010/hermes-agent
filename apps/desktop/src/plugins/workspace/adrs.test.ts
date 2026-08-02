import { describe, expect, it } from 'vitest'

import {
  adrReconcileLabel,
  adrReconcileSummaryMessage,
  adrReconcileTone,
  isCanonicalADR,
  isLegacyADR
} from './adrs'

describe('isCanonicalADR / isLegacyADR', () => {
  it('classifies by source', () => {
    expect(isCanonicalADR({ source: 'git_file' })).toBe(true)
    expect(isCanonicalADR({ source: 'workspace_db' })).toBe(false)
    expect(isLegacyADR({ source: 'workspace_db' })).toBe(true)
    expect(isLegacyADR({ source: 'git_file' })).toBe(false)
  })
})

describe('adrReconcileLabel', () => {
  it('maps every backend state to a short label', () => {
    expect(adrReconcileLabel('synced')).toBe('synced')
    expect(adrReconcileLabel('file_new')).toBe('new file')
    expect(adrReconcileLabel('file_changed')).toBe('changed')
    expect(adrReconcileLabel('db_legacy')).toBe('legacy')
    expect(adrReconcileLabel('missing_file')).toBe('missing file')
    expect(adrReconcileLabel('conflict')).toBe('conflict')
    expect(adrReconcileLabel('invalid')).toBe('invalid')
  })

  it('falls back to the raw state for unknown values', () => {
    expect(adrReconcileLabel('mystery')).toBe('mystery')
  })
})

describe('adrReconcileTone', () => {
  it('has a stable tone for every state (never undefined)', () => {
    for (const state of ['synced', 'file_new', 'file_changed', 'db_legacy', 'missing_file', 'conflict', 'invalid']) {
      expect(adrReconcileTone(state)).toBeTruthy()
    }
  })

  it('treats conflict and invalid as danger tones', () => {
    expect(adrReconcileTone('conflict')).toContain('red')
    expect(adrReconcileTone('invalid')).toContain('red')
  })
})

describe('adrReconcileSummaryMessage', () => {
  it('builds a human summary from counts', () => {
    const msg = adrReconcileSummaryMessage({
      workspace_id: 'w',
      project_id: '',
      scanned_files: 3,
      indexed: 2,
      synced: 1,
      file_changed: 0,
      db_legacy: 1,
      missing_file: 0,
      conflict: 1,
      invalid: 0,
      invalid_paths: [],
      dry_run: false
    })

    expect(msg).toContain('2 indexed')
    expect(msg).toContain('1 synced')
    expect(msg).toContain('1 legacy')
    expect(msg).toContain('1 conflict')
  })

  it('reports no changes for an empty summary', () => {
    expect(adrReconcileSummaryMessage(null)).toBe('')
    expect(
      adrReconcileSummaryMessage({
        workspace_id: 'w',
        project_id: '',
        scanned_files: 0,
        indexed: 0,
        synced: 0,
        file_changed: 0,
        db_legacy: 0,
        missing_file: 0,
        conflict: 0,
        invalid: 0,
        invalid_paths: [],
        dry_run: false
      })
    ).toBe('no changes')
  })
})
