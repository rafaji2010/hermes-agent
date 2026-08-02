/**
 * Workspace Plugin — Desktop Renderer Entry Point
 *
 * Discovered by ``discoverBundledPlugins()`` via the Vite glob
 * ``src/plugins/*\/plugin.{ts,tsx}``.  Default-exports a `HermesPlugin`
 * that registers:
 *
 * - One contributed route at ``/workspace`` (the current upstream route
 *   contract defines one-segment contributed paths — all Workspace
 *   surfaces live behind this root via internal navigation).
 * - A sidebar nav entry labelled "Workspace".
 *
 * The route renders ``<WorkspaceShell />`` whose overview tab calls
 * ``ctx.rest("/v1/health")`` to confirm the Python backend plugin is
 * reachable.
 *
 * U1C: imports are SDK-only (+ react) — no application internals, so the
 * plugin is a valid consumer of the current authoring boundary.
 */

import type { HermesPlugin } from '@hermes/plugin-sdk'

import { WorkspaceShell } from './workspace-shell'

const plugin: HermesPlugin = {
  id: 'workspace',
  name: 'Workspace',
  defaultEnabled: true,

  register(ctx) {
    ctx.register({
      id: 'dashboard',
      area: 'routes',
      title: 'Workspace',
      data: { path: '/workspace' },
      render: () => <WorkspaceShell ctx={ctx} />,
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
