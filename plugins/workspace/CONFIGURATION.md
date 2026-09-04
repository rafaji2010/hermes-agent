# Workspace Plugin — Required Configuration

This file documents the **single configuration change** needed to activate
the Workspace Python backend plugin. The Python backend itself is additive
under plugins/workspace and requires no core edits to load.

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

## Core Delta (intentional, beyond the M0 plugin)

The M0 plugin loaded with no core edits. Later milestones intentionally
extend core at the edges, per AGENTS.md narrow-waist discipline:

- tools/approval.py: deterministic risk-tier overlay plus Shieldstral local
  guard, both evaluated before the cloud smart-approval LLM.
- tools/file_tools.py plus tools/file_operations.py: M8.2 read-layer gap
  fixes and device-write guards.
- tools/shieldstral_guard.py plus tools/flux3_video_tool.py: new edge tools.
- web/src plus hermes_cli/web_server.py: Fleet, Artifacts, Usage surfaces.
- apps/desktop plus agent prompt wiring: model pickers, workspace pane,
  context hooks.

The Python backend under plugins/workspace plus the desktop renderer under
apps/desktop/src/plugins/workspace remain the only plugin-owned trees.
Core edits above are reviewed as edge hardening, never a second hook,
memory, or instruction system.
