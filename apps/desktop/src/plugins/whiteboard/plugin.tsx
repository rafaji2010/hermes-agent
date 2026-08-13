/**
 * Whiteboard Plugin — Desktop Renderer Entry Point
 *
 * Discovered by ``discoverBundledPlugins()`` via the Vite glob
 * ``src/plugins/*\/plugin.{ts,tsx}``. Default-exports a `HermesPlugin`
 * that registers:
 *
 * - One contributed route at ``/whiteboard`` (the contributed-path route
 *   contract — one segment, rendered full-page in the workspace pane).
 * - A sidebar nav entry labelled "Whiteboard".
 *
 * The route renders ``<WhiteboardPage />``: a fully local/offline tldraw
 * canvas whose drawings persist to localStorage (via the plugin-scoped
 * ``ctx.storage``) and reload on reopen. No network is involved anywhere in
 * the feature.
 *
 * Registration mirrors the Workspace plugin: one `routes` contribution +
 * one `sidebar.nav` contribution.
 */

import {
  type HermesPlugin,
  type RouteContribution,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  type SidebarNavContribution
} from '@hermes/plugin-sdk'

import { WhiteboardPage } from './whiteboard'

const plugin: HermesPlugin = {
  id: 'whiteboard',
  name: 'Whiteboard',
  description: 'Offline tldraw canvas — draw, sketch, and diagram locally, no network.',
  defaultEnabled: true,

  register(ctx) {
    ctx.register({
      id: 'page',
      area: ROUTES_AREA,
      title: 'Whiteboard',
      data: { path: '/whiteboard' } satisfies RouteContribution,
      render: () => <WhiteboardPage ctx={ctx} />
    })

    ctx.register({
      id: 'nav',
      area: SIDEBAR_NAV_AREA,
      data: { codicon: 'pencil', label: 'Whiteboard', path: '/whiteboard' } satisfies SidebarNavContribution
    })
  }
}

export default plugin
