/**
 * Workspace Plugin — Desktop Renderer Entry Point
 *
 * Discovered by ``discoverBundledPlugins()`` via the Vite glob
 * ``src/plugins/*\/plugin.{ts,tsx}``.  Default-exports a `HermesPlugin`
 * that registers:
 *
 * - A contributed route at ``/workspace``
 * - A sidebar nav entry labelled "Workspace"
 *
 * The route renders ``<WorkspacePage />`` which calls ``ctx.rest("/health")``
 * to confirm the Python backend plugin is reachable.
 */

import type { HermesPlugin } from '@/contrib/plugin'

import { WorkspacePage } from './workspace-page'
import { ADRPage } from './adr-page'
import { JournalPage } from './journal-page'

const plugin: HermesPlugin = {
  id: 'workspace',
  name: 'Workspace',
  defaultEnabled: true,

  register(ctx) {
    console.log('[Workspace Plugin] Loaded successfully')

    ctx.register({
      id: 'dashboard',
      area: 'routes',
      title: 'Workspace',
      data: { path: '/workspace' },
      render: () => <WorkspacePage ctx={ctx} />,
    })

    ctx.register({
      id: 'adrs',
      area: 'routes',
      title: 'Workspace ADRs',
      data: { path: '/workspace/adrs' },
      render: () => <ADRPage ctx={ctx} workspaceId="" />,
    })

    ctx.register({
      id: 'journal',
      area: 'routes',
      title: 'Workspace Journal',
      data: { path: '/workspace/journal' },
      render: () => <JournalPage ctx={ctx} workspaceId="" />,
    })

    ctx.register({
      id: 'nav',
      area: 'sidebar.nav',
      data: {
        codicon: 'organization',
        label: 'Workspace',
        path: '/workspace',
      },
    })
  },
}

export default plugin
