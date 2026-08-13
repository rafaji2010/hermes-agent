/**
 * Whiteboard Plugin — Page Shell (U1C)
 *
 * Mounts the whiteboard route's full-page surface in the workspace pane:
 * a titlebar contribution (mirroring the Workspace plugin's titlebar) and the
 * lazy-loaded tldraw canvas.
 *
 * tldraw is a heavyweight dependency (~4-5 MB), so the canvas lives behind a
 * `React.lazy` boundary — the package is parsed and evaluated only when the
 * user actually opens the whiteboard, never on cold start (same rationale as
 * the shiki/mermaid lazy boundaries).
 */

import { Contribute, Loader, type PluginContext, TITLEBAR_AREAS } from '@hermes/plugin-sdk'
import { lazy, Suspense } from 'react'

const TldrawCanvas = lazy(async () => ({ default: (await import('./tldraw-canvas')).TldrawCanvas }))

function WhiteboardTitlebar() {
  return (
    <div className="flex items-center gap-2 px-1">
      <span className="text-sm font-medium text-(--ui-text-primary)">Whiteboard</span>
      <span className="rounded bg-(--ui-bg-quaternary) px-1.5 py-px text-[10px] font-medium uppercase tracking-wide text-(--ui-text-tertiary)">
        Offline
      </span>
    </div>
  )
}

interface WhiteboardPageProps {
  ctx: PluginContext
}

export function WhiteboardPage({ ctx }: WhiteboardPageProps) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <Contribute area={TITLEBAR_AREAS.center} id="whiteboard:titlebar">
        <WhiteboardTitlebar />
      </Contribute>
      <div className="relative min-h-0 flex-1">
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center gap-3 text-sm text-(--ui-text-tertiary)">
              <Loader type="lemniscate-bloom" />
              <span>Loading whiteboard…</span>
            </div>
          }
        >
          <TldrawCanvas ctx={ctx} />
        </Suspense>
      </div>
    </div>
  )
}
