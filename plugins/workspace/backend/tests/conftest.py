"""Shared test fixtures for the Workspace Plugin backend tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.database import (  # type: ignore[import-untyped]
    DatabaseManager,
    _db as _db_global,
    get_database,
)
from plugins.workspace.backend.services.workspace_service import WorkspaceService  # type: ignore[import-untyped]
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


@pytest.fixture(autouse=True)
def _reset_db_singleton():
    """Ensure each test starts with a fresh database singleton."""
    # Clear the global so each test creates a fresh in-memory db
    _db_global_ref = getattr(
        __import__("plugins.workspace.backend.database", fromlist=["_db"]),
        "_db",
        None,
    )
    # Direct module-level mutation
    import plugins.workspace.backend.database as db_mod

    db_mod._db = None
    yield
    db_mod._db = None


@pytest.fixture
def temp_db() -> Generator[DatabaseManager, None, None]:
    """Create an in-memory database and clean up after the test."""
    db = DatabaseManager(db_path=Path(":memory:"))
    db.get_connection()
    yield db
    db.close()


@pytest.fixture
def storage(temp_db: DatabaseManager) -> SQLiteStorage:
    """Return a ``SQLiteStorage`` backed by the temp in-memory DB."""
    import plugins.workspace.backend.database as db_mod

    db_mod._db = temp_db  # pin the singleton
    return SQLiteStorage()


@pytest.fixture
def svc(storage: SQLiteStorage) -> WorkspaceService:
    """Return a ``WorkspaceService`` wired to the temp in-memory storage."""
    return WorkspaceService(storage=storage)


@pytest.fixture
def temp_git_repo() -> Generator[Path, None, None]:
    """Create a temporary directory and ``git init`` inside it."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(
            ["git", "init", "-b", "main", str(root)],
            capture_output=True,
            check=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "-C", str(root), "config", "user.email", "test@test.test"],
            capture_output=True,
            check=False,
            timeout=5,
        )
        (root / "README.md").write_text("# test repo")
        yield root
