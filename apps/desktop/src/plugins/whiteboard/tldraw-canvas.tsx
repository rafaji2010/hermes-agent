/**
 * Whiteboard Plugin — tldraw Canvas (offline, local-first)
 *
 * A controlled tldraw editor rendered in the workspace pane:
 *
 * - **Local-only store.** A `TLStore` is created per mount (tldraw never
 *   reaches the network for the canvas itself — no sync, no multiplayer).
 * - **Local persistence.** A debounced snapshot listener writes the document
 *   to the plugin-scoped `ctx.storage` (localStorage, namespaced under
 *   `hermes.plugin.whiteboard.`), and the saved snapshot is loaded before the
 *   editor mounts. Drawings survive restarts with no backend involved.
 * - **No CDN at runtime.** tldraw's default asset URLs point at
 *   `cdn.tldraw.com` (fonts, icon sprite). For a genuinely offline feature the
 *   16 font faces and the merged icon sprite are bundled as package assets and
 *   wired through the `assetUrls` override, so nothing is fetched from the
 *   network. English UI strings ship in the package itself.
 */

import '@tldraw/tldraw/tldraw.css'

import type { PluginContext } from '@hermes/plugin-sdk'
import {
  createTLStore,
  iconTypes,
  Tldraw,
  type TLEditorAssetUrls,
  type TLStore,
  type TLStoreSnapshot
} from '@tldraw/tldraw'
import { useEffect, useMemo } from 'react'

import iconSpriteUrl from './assets/0_merged.svg?url'
// ── Offline assets ───────────────────────────────────────────────────────────
// Imported via vite `?url` so the files ship inside the renderer bundle (dev
// server serves them; the built app emits them under dist/assets with the
// relative base). Bundled from the tldraw 5.2.5 CDN release so font/style
// hashes and the icon sprite line up with the library version we ship.
import monoBoldUrl from './assets/fonts/IBMPlexMono-Bold.woff2?url'
import monoBoldItalicUrl from './assets/fonts/IBMPlexMono-BoldItalic.woff2?url'
import monoUrl from './assets/fonts/IBMPlexMono-Medium.woff2?url'
import monoItalicUrl from './assets/fonts/IBMPlexMono-MediumItalic.woff2?url'
import sansBoldUrl from './assets/fonts/IBMPlexSans-Bold.woff2?url'
import sansBoldItalicUrl from './assets/fonts/IBMPlexSans-BoldItalic.woff2?url'
import sansUrl from './assets/fonts/IBMPlexSans-Medium.woff2?url'
import sansItalicUrl from './assets/fonts/IBMPlexSans-MediumItalic.woff2?url'
import serifBoldUrl from './assets/fonts/IBMPlexSerif-Bold.woff2?url'
import serifBoldItalicUrl from './assets/fonts/IBMPlexSerif-BoldItalic.woff2?url'
import serifUrl from './assets/fonts/IBMPlexSerif-Medium.woff2?url'
import serifItalicUrl from './assets/fonts/IBMPlexSerif-MediumItalic.woff2?url'
import drawBoldUrl from './assets/fonts/Shantell_Sans-Informal_Bold.woff2?url'
import drawBoldItalicUrl from './assets/fonts/Shantell_Sans-Informal_Bold_Italic.woff2?url'
import drawUrl from './assets/fonts/Shantell_Sans-Informal_Regular.woff2?url'
import drawItalicUrl from './assets/fonts/Shantell_Sans-Informal_Regular_Italic.woff2?url'
import { useIsDark } from './use-is-dark'

const OFFLINE_FONTS: TLEditorAssetUrls['fonts'] = {
  tldraw_mono: monoUrl,
  tldraw_mono_italic: monoItalicUrl,
  tldraw_mono_bold: monoBoldUrl,
  tldraw_mono_italic_bold: monoBoldItalicUrl,
  tldraw_serif: serifUrl,
  tldraw_serif_italic: serifItalicUrl,
  tldraw_serif_bold: serifBoldUrl,
  tldraw_serif_italic_bold: serifBoldItalicUrl,
  tldraw_sans: sansUrl,
  tldraw_sans_italic: sansItalicUrl,
  tldraw_sans_bold: sansBoldUrl,
  tldraw_sans_italic_bold: sansBoldItalicUrl,
  tldraw_draw: drawUrl,
  tldraw_draw_italic: drawItalicUrl,
  tldraw_draw_bold: drawBoldUrl,
  tldraw_draw_italic_bold: drawBoldItalicUrl
}

// The UI icon system is a single SVG sprite referenced by fragment hash
// (`url(sprite.svg#tool-pencil)`). Override every icon id with the local copy
// so the toolbar never asks the CDN for anything.
const OFFLINE_ICONS: Record<string, string> = Object.fromEntries(
  iconTypes.map(name => [name, `${iconSpriteUrl}#${name}`])
)

const ASSET_URLS = { fonts: OFFLINE_FONTS, icons: OFFLINE_ICONS }

// ── Persistence ─────────────────────────────────────────────────────────────
// tldraw's schema versioning is encoded in the snapshot itself and
// `loadStoreSnapshot` migrates older data, so a blank board is the only
// recovery we need for a corrupt/unreadable save.

const SNAPSHOT_KEY = 'whiteboard-snapshot'
const SNAPSHOT_DEBOUNCE_MS = 400

function loadSavedSnapshot(ctx: PluginContext): TLStoreSnapshot | null {
  const saved = ctx.storage.get<unknown>(SNAPSHOT_KEY, null)

  if (
    saved !== null &&
    typeof saved === 'object' &&
    (saved as { store?: unknown }).store !== null &&
    typeof (saved as { schema?: unknown }).schema === 'object'
  ) {
    return saved as TLStoreSnapshot
  }

  return null
}

function createOfflineStore(ctx: PluginContext): TLStore {
  const store = createTLStore()

  const saved = loadSavedSnapshot(ctx)

  if (saved !== null) {
    try {
      store.loadStoreSnapshot(saved)
    } catch {
      // Snapshot from a newer/unreadable schema — start with a blank board.
    }
  }

  return store
}

// ── Canvas ──────────────────────────────────────────────────────────────────

interface TldrawCanvasProps {
  ctx: PluginContext
}

export function TldrawCanvas({ ctx }: TldrawCanvasProps) {
  const isDark = useIsDark()

  // Created once per mount; the saved document is loaded before the editor
  // mounts so the first paint already shows the user's drawings.
  const store = useMemo<TLStore>(() => createOfflineStore(ctx), [ctx])

  // Debounced local persistence. The store listener fires on any history
  // change (drawing, moving, camera, …); we coalesce bursts into one write.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null

    const persist = () => {
      ctx.storage.set(SNAPSHOT_KEY, store.getStoreSnapshot())
    }

    const dispose = store.listen(() => {
      if (timer !== null) {
        clearTimeout(timer)
      }

      timer = setTimeout(() => {
        timer = null
        persist()
      }, SNAPSHOT_DEBOUNCE_MS)
    })

    return () => {
      if (timer !== null) {
        clearTimeout(timer)
      }

      dispose()
    }
  }, [ctx, store])

  return (
    <div className="relative h-full w-full overflow-hidden">
      <Tldraw assetUrls={ASSET_URLS} colorScheme={isDark ? 'dark' : 'light'} store={store} />
    </div>
  )
}
