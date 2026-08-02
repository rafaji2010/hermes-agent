# Workspace Plugin — Required Configuration

This file documents the **single configuration change** needed to activate
the Workspace Python backend plugin.  No core files were modified to
implement this plugin.

---

## Configuration Change

The plugin is a `kind: standalone` bundled plugin.  Standalone bundled
plugins must be explicitly enabled in the user's `config.yaml` for their
Python `register()` function to be called by the PluginManager.

Add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - workspace
```

Or run:

```bash
hermes plugins enable workspace
```

Then restart Hermes (or restart Hermes Desktop).

---

## Why This Is Needed

The plugin discovery/loading logic in `hermes_cli/plugins.py` (lines
1450-1469) gates standalone plugins behind `plugins.enabled`:

```
# Everything else (standalone, user-installed backends,
# entry-point plugins) is opt-in via plugins.enabled.
```

This is by design — standalone plugins can register hooks, tools, and
commands that affect the agent's behavior, so they require explicit
user consent.

---

## What Works Without This Change

Even without `plugins.enabled`, the **dashboard API routes** auto-mount
for bundled plugins (the `_mount_plugin_api_routes` function in
`web_server.py` only gates user plugins behind `plugins.enabled`):

- `GET /api/plugins/workspace/health` will respond with 200

The **desktop renderer plugin** (in `apps/desktop/src/plugins/workspace/`)
requires no configuration — it is auto-discovered at build time by
`discoverBundledPlugins()` via the Vite glob.

The only thing that won't happen without the config change is the Python
`register(ctx)` function being called — which in M0 only logs a message,
so it has no practical effect.

---

## No Core Files Modified

The following Hermes core files were **not modified** in any way:

- `electron/main.ts`
- `electron/preload.ts`
- `src/app/` (any file)
- `src/components/` (any file)
- `src/store/` (any file)
- `src/contrib/` (any file)
- `hermes_cli/plugins.py`
- `hermes_cli/web_server.py`

All changes are confined to the plugin directories:
- `plugins/workspace/` (Python backend plugin)
- `apps/desktop/src/plugins/workspace/` (desktop renderer plugin)

Both are standard plugin installation locations that the existing
discovery mechanisms already scan.
