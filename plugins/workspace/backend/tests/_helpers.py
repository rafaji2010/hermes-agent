"""Shared test helpers for the Workspace backend tests.

U1D-A: API routes resolve their services through the profile-scoped
WorkspaceRuntime, while several tests seed rows directly through the
legacy ``database._db`` singleton (``SQLiteStorage()`` without an explicit
manager).  :func:`pin_memory_workspace_state` pins BOTH to one fresh
in-memory manager so the two never diverge within a test.
"""

from __future__ import annotations

from pathlib import Path

from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
    WorkspaceRuntime,
    pin_workspace_runtime,
    reset_workspace_runtimes,
)
from plugins.workspace.backend.security.audit import AuditLogger  # type: ignore[import-untyped]


def pin_memory_workspace_state(tmp_path: Path):
    """Pin the legacy DB singleton AND the Workspace runtime to ONE fresh
    in-memory manager, so direct-DB seeding and API routes share a database.
    """
    import plugins.workspace.backend.database as db_mod

    db_mod._db = None
    reset_workspace_runtimes()
    mem = DatabaseManager(db_path=Path(":memory:"))
    mem.get_connection()
    db_mod._db = mem
    pin_workspace_runtime(
        WorkspaceRuntime(
            home=Path(":memory:"),
            database=mem,
            audit=AuditLogger(log_path=tmp_path / "audit.log"),
        )
    )
    return mem


def unpin_memory_workspace_state():
    """Undo :func:`pin_memory_workspace_state`."""
    import plugins.workspace.backend.database as db_mod

    reset_workspace_runtimes()
    db_mod._db = None
