# Workspace Plugin

Scaffold (M0) — minimum viable plugin that proves loading in Hermes Desktop.

## Folder Structure

```
plugins/workspace/                       # Python backend plugin (repo root)
├── plugin.yaml                          # Plugin manifest
├── __init__.py                          # register(ctx) entry point
├── README.md                            # This file
└── dashboard/
    ├── manifest.json                    # Dashboard API discovery
    └── plugin_api.py                    # FastAPI router — REST endpoints

apps/desktop/src/plugins/workspace/      # Desktop renderer plugin
├── plugin.tsx                           # HermesPlugin default export
└── workspace-page.tsx                   # Workspace scaffold page
```

## Plugin Lifecycle

### Desktop Renderer Plugin

1. **Discovery.** `discoverBundledPlugins()` in `src/contrib/plugins.ts` uses a
   Vite glob (`../plugins/*/plugin.{ts,tsx}`) that matches
   `src/plugins/workspace/plugin.tsx` at build time.

2. **Registration.** The `HermesPlugin` default export is loaded. Its
   `register(ctx)` method registers two contributions through the
   `ContributionRegistry`:

   - A `routes` area contribution with `data: { path: '/workspace' }` —
     mounts `<WorkspacePage />` at the `/workspace` route.
   - A `sidebar.nav` area contribution with `{ codicon: 'organization',
     label: 'Workspace', path: '/workspace' }` — adds a sidebar navigation
     row.

3. **Navigation.** When the user clicks "Workspace" in the sidebar (or
   navigates to `#/workspace`), the `ChatRoutesSurface` renders the
   contributed route's component wrapped in a `<ContribBoundary>`.

4. **Health check.** `<WorkspacePage />` calls `ctx.rest("/health")`, which
   delegates to `pluginRest("workspace", "/health")` → an HTTP request to
   `GET /api/plugins/workspace/health` on the backend.

5. **Unmount/reload.** The plugin's `register()` returns disposers for each
   registration. A reload disposes them via `loaded` map entries, then
   registers fresh.

### Python Backend Plugin

1. **Discovery.** `discover_plugins()` in `hermes_cli/plugins.py` scans
   `plugins/workspace/` at the repo root (bundled source). The plugin
   manifest is parsed from `plugin.yaml`.

2. **Loading.** If the plugin is enabled (see Configuration below), the
   `PluginManager` imports `__init__.py` and calls `register(ctx)`.

3. **API mounting.** `_mount_plugin_api_routes()` in
   `hermes_cli/web_server.py` discovers `dashboard/manifest.json`, reads the
   `api` field (`"plugin_api.py"`), imports it, and mounts its `router` at
   `/api/plugins/workspace/`. For bundled plugins, this happens automatically
   (the API mounts unless the plugin is explicitly disabled).

4. **Endpoints.** The FastAPI router in `plugin_api.py` exposes:

   | Method | Path | Response |
   |--------|------|----------|
   | `GET` | `/health` | `{"status":"ok","plugin":"workspace","version":"0.1.0"}` |

## Registration

Both plugin halves use the standard Hermes registration contracts:

### Desktop — `HermesPlugin`

```typescript
interface HermesPlugin {
  id: string                    // "workspace" — stable slug
  name?: string                 // "Workspace" — human label
  defaultEnabled?: boolean      // true — auto-activate on first discovery
  register(ctx: PluginContext): void  // wire contributions through ctx
}
```

### Python — `register(ctx)`

```python
def register(ctx) -> None:
    """Called by PluginManager. Register tools, hooks, commands here."""
    ctx.register_tool(...)
    ctx.register_hook(...)
```

## Configuration

For the desktop plugin (renderer): **no configuration required.** Bundled
desktop plugins are discovered and activated automatically at build time.

For the Python backend plugin:

1. Add `workspace` to `plugins.enabled` in `config.yaml`:
   ```yaml
   plugins:
     enabled:
       - workspace
   ```
   Or run: `hermes plugins enable workspace`

2. Restart the backend (`hermes serve` or restart Hermes Desktop).

Without this, the dashboard API will still mount (bundled plugins bypass
the `plugins.enabled` gate for API routes), but the Python `register(ctx)`
function will not be called.

## Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| Hermes starts normally | ✓ | No core files modified |
| Workspace appears in navigation | ✓ | sidebar.nav contribution registered |
| Clicking Workspace opens the page | ✓ | routes contribution at /workspace |
| Health endpoint responds | ✓ | GET /api/plugins/workspace/health returns 200 |
| Frontend displays backend status | ✓ | Shows "Connected" or "Not Connected" |
| No Hermes core files modified | ✓ | All changes are in plugin directories |

## Future Milestones

This scaffold has **no business logic, no database, no features.** It proves
the plugin loading pipeline end-to-end.

Milestone plan (see `docs/reverse_engineering/WorkspacePluginDesign.md`):

- **M1** — Workspace Dashboard (repo list, health scores)
- **M2** — Architecture Decision Records (CRUD, file sync, status tracking)
- **M3** — Engineering Journal (entries, tags, calendar, templates)
- **M4** — Sprint Management (kanban bridge, burndown, velocity)
- **M5** — Roadmaps (timeline, milestones, sprint linking)
- **M6** — Context Engine Integration (inject workspace context into agent turns)
- **M7** — Polish (i18n, keybinds, notifications, tests)
