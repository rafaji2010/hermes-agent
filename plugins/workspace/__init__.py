"""Workspace Plugin — Python Backend

Provides REST endpoints that the Workspace desktop plugin consumes
via ``ctx.rest()`` (mounted at ``/api/plugins/workspace/``).

Endpoints (M0):
    GET  /health               Health check

Endpoints (M1):
    GET  /v1/health            v1 health with DB status
    GET  /v1/workspaces        List workspaces
    POST /v1/workspaces        Create workspace
    GET  /v1/repositories      List repositories
    POST /v1/repositories      Register repository

Storage layer: ``backend/storage/`` (AbstractStorage → SQLiteStorage).
Migrations: ``backend/migrations/`` (runner + numbered SQL files).
Database: ``<hermes_home>/workspace.db`` (auto-created on first use).

The plugin's ``register()`` function is called by the PluginManager when
the plugin is enabled via ``plugins.enabled`` in config.yaml.

S7.4 — LLM context adapter.  The plugin registers a ``pre_llm_call`` hook
that injects a SMALL, bounded Workspace context block into the CURRENT user
message only (the ``api_content`` sidecar — never the system prompt, which
is prompt-cache-sacred).  Fail-closed: context is assembled ONLY when scope
resolves to exactly ``mapped``; every other state injects an empty string.
This is operational structured context (aggregates + provenance + ranked
recall), NOT a memory system and NOT an instruction system — those remain
owned by Hermes.
"""

import logging

_log = logging.getLogger(__name__)

# Registered lazily to avoid import-time cost when the plugin is disabled.
_CONTEXT_HOOK_NAME = "pre_llm_call"


def _make_pre_llm_callback():
    """Build the pre_llm_call hook callback (imports the adapter lazily).

    Returning ``""`` (or ``{}``) injects nothing; returning
    ``{"context": "..."}`` appends a bounded block to the user message.
    """
    from plugins.workspace.backend.context_adapter import assemble_workspace_context

    def _on_pre_llm_call(**kwargs):
        # Fail-closed: missing session -> empty context.  The adapter's
        # scope resolver treats empty anchors as unresolved -> "".
        session_id = kwargs.get("session_id", "") or ""
        user_message = kwargs.get("user_message", "") or ""
        return {"context": assemble_workspace_context(
            session_id=session_id,
            user_message=user_message,
        )}

    return _on_pre_llm_call


def register(ctx) -> None:
    """Called by PluginManager when this plugin is enabled.

    Registers the ``pre_llm_call`` context hook (S7.4).  The hook injects
    a bounded Workspace context block on every turn where the effective
    scope resolves to ``mapped``, and injects nothing otherwise.
    """
    try:
        ctx.register_hook(_CONTEXT_HOOK_NAME, _make_pre_llm_callback())
        _log.info("[Workspace Plugin] Registered pre_llm_call context hook")
    except Exception:
        _log.exception("[Workspace Plugin] Failed to register pre_llm_call hook")
    _log.info("[Workspace Plugin] Loaded successfully (S7.4)")
