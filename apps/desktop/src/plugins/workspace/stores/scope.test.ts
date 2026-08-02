import { describe, expect, it } from 'vitest'

import {
  EMPTY_SCOPE,
  scopeQueryParams,
  scopeReady,
  workspaceScopeFromResolution
} from './scope'

describe('workspaceScopeFromResolution', () => {
  it('maps a mapped workspace to scoped with its workspace id', () => {
    const scope = workspaceScopeFromResolution({
      workspace_id: 'abc123',
      workspace_name: 'main',
      project_id: 'p_1234',
      project_slug: 'hermes-agent',
      state: 'mapped',
      match_source: 'mapping',
      matched_path: ''
    })

    expect(scope.state).toBe('scoped')
    expect(scope.workspaceId).toBe('abc123')
    expect(scope.projectId).toBe('p_1234')
    expect(scope.projectSlug).toBe('hermes-agent')
  })

  it('treats partial with a workspace as scoped (workspace is the query scope)', () => {
    const scope = workspaceScopeFromResolution({
      workspace_id: 'ws_9',
      workspace_name: 'tmp',
      project_id: 'p_9',
      project_slug: null,
      state: 'partial',
      match_source: 'session_cwd',
      matched_path: '/tmp'
    })

    expect(scope.state).toBe('scoped')
    expect(scope.workspaceId).toBe('ws_9')
  })

  it('maps partial without a workspace to partial (linkable, not queryable)', () => {
    const scope = workspaceScopeFromResolution({
      workspace_id: '',
      workspace_name: '',
      project_id: 'p_9',
      project_slug: 'proj',
      state: 'partial',
      match_source: 'session_cwd',
      matched_path: '/proj'
    })

    expect(scope.state).toBe('partial')
    expect(scope.workspaceId).toBe('')
    expect(scope.projectId).toBe('p_9')
  })

  it('maps unresolved to the empty scope', () => {
    const scope = workspaceScopeFromResolution({
      workspace_id: '',
      workspace_name: '',
      project_id: null,
      project_slug: null,
      state: 'unresolved',
      match_source: 'none',
      matched_path: ''
    })

    expect(scope).toEqual(EMPTY_SCOPE)
  })
})

describe('scopeQueryParams', () => {
  it('returns the workspace param when scoped', () => {
    expect(
      scopeQueryParams({ ...EMPTY_SCOPE, state: 'scoped', workspaceId: 'w1' })
    ).toEqual({ workspace_id: 'w1' })
  })

  it('never returns a global scope for an unscoped store', () => {
    expect(scopeQueryParams(EMPTY_SCOPE)).toEqual({})
    expect(
      scopeQueryParams({ ...EMPTY_SCOPE, state: 'partial', projectId: 'p_1' })
    ).toEqual({})
    expect(
      scopeQueryParams({ ...EMPTY_SCOPE, state: 'checking' })
    ).toEqual({})
  })

  it('ignores a scoped store with an empty workspace id', () => {
    expect(scopeQueryParams({ ...EMPTY_SCOPE, state: 'scoped', workspaceId: '' })).toEqual({})
  })
})

describe('scopeReady', () => {
  it('is true only for a scoped store with a workspace id', () => {
    expect(scopeReady({ ...EMPTY_SCOPE, state: 'scoped', workspaceId: 'w1' })).toBe(true)
    expect(scopeReady(EMPTY_SCOPE)).toBe(false)
    expect(scopeReady({ ...EMPTY_SCOPE, state: 'checking' })).toBe(false)
    expect(scopeReady({ ...EMPTY_SCOPE, state: 'scoped', workspaceId: '' })).toBe(false)
  })
})
