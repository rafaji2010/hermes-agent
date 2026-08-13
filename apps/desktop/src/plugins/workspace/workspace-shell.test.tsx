/**
 * Workspace Shell navigation tests (U1C).
 *
 * The plugin registers a single `/workspace` route; all Workspace surfaces
 * are reachable through internal navigation. These tests pin the tab bar
 * surface set and the tab-switching behavior.
 */

import type { PluginContext } from '@hermes/plugin-sdk'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { $workspaceScope } from './scope'
import { $workspaceTab, WORKSPACE_TABS, WorkspaceShell } from './workspace-shell'

function renderShell(ctx: PluginContext) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <WorkspaceShell ctx={ctx} />
    </QueryClientProvider>,
  )
}

function fakeCtx(): PluginContext {
  return {
    rest: async (path: string) => {
      if (path.startsWith('/v1/graph')) {
        return {
          nodes: [{ id: 'w1', type: 'workspace', title: 'Test Workspace', status: '' }],
          edges: [],
        }
      }
      if (path === '/v1/health') {
        return {
          status: 'ok',
          plugin: 'workspace',
          plugin_version: '0.1.0',
          api_version: 'v1',
          storage_provider: 'sqlite',
          database_connected: true,
          database_path: '',
          transaction_support: true,
          nested_transactions: 'savepoint',
          schema_version: '7',
          migration_status: 'Up to date',
          workspace_count: 0,
          repository_count: 0,
          journal_count: 0,
          roadmap_count: 0,
          milestone_count: 0,
          completed_milestone_count: 0,
          task_count: 0,
          open_task_count: 0,
          blocked_task_count: 0,
          overdue_task_count: 0,
          graph_entity_count: 0,
          graph_edge_count: 0,
          graph_orphan_count: 0,
          hermes_home: '',
        }
      }

      throw new Error(`unexpected rest call: ${path}`)
    },
  } as unknown as PluginContext
}

describe('WORKSPACE_TABS', () => {
  it('exposes every Workspace surface through internal navigation', () => {
    const labels = WORKSPACE_TABS.map(t => t.label)

    expect(labels).toEqual([
      'Overview',
      'ADRs',
      'Journal',
      'Roadmaps',
      'Graph',
      'Search',
      'Analytics',
      'Assistant',
    ])
  })

  it('has unique tab ids', () => {
    const ids = WORKSPACE_TABS.map(t => t.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})

describe('WorkspaceShell', () => {
  it('renders the tab bar with all surfaces', () => {
    $workspaceTab.set('overview')
    renderShell(fakeCtx())

    expect(screen.getByText('Overview')).toBeTruthy()
    expect(screen.getByText('ADRs')).toBeTruthy()
    expect(screen.getByText('Journal')).toBeTruthy()
    expect(screen.getByText('Roadmaps')).toBeTruthy()
    expect(screen.getByText('Graph')).toBeTruthy()
    expect(screen.getByText('Search')).toBeTruthy()
    expect(screen.getByText('Analytics')).toBeTruthy()
    expect(screen.getByText('Assistant')).toBeTruthy()
  })

  it('starts on the overview surface', async () => {
    $workspaceTab.set('overview')
    renderShell(fakeCtx())

    expect(await screen.findByText('System Healthy')).toBeTruthy()
  })

  it('switches to the graph surface through the internal tab atom', async () => {
    $workspaceScope.set({
      state: 'scoped',
      workspaceId: 'ws-graph-test',
      projectId: null,
      projectSlug: null,
      matchSource: 'mapping',
      cwd: '/tmp',
      profile: 'default',
      error: '',
      retrying: false,
    })
    $workspaceTab.set('graph')
    renderShell(fakeCtx())

    // Graph page renders its header once the tab is active (async loader first).
    expect(await screen.findByText('Knowledge Graph')).toBeTruthy()
  })

  it('switches to the ADR surface', () => {
    $workspaceTab.set('adrs')
    renderShell(fakeCtx())

    expect(screen.getByText('New ADR')).toBeTruthy()
  })
})
