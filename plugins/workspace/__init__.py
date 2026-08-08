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
"""

import logging

_log = logging.getLogger(__name__)


def register(ctx) -> None:
    """Called by PluginManager when this plugin is enabled.

    In M0/M1 this is a no-op beyond the log message.  Future milestones
    will register context-engine providers, agent hooks, and slash
    commands through ``ctx`` here.
    """
    _log.info("[Workspace Plugin] Loaded successfully (M1)")
