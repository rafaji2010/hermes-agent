"""Unit tests for the migration runner."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_migrations_table_created(temp_db):
    """The ``_migrations`` table must exist after DB init."""
    conn = temp_db.get_connection()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'"
    ).fetchone()
    assert row is not None, "_migrations table missing"


def test_initial_migration_applied(temp_db):
    """The 001 migration must be recorded after DB init."""
    conn = temp_db.get_connection()
    rows = conn.execute("SELECT version, description FROM _migrations").fetchall()
    versions = {r[0] for r in rows}
    assert 1 in versions, "migration 001 not recorded"


def test_schema_tables_exist(temp_db):
    """All initial-schema tables must be present."""
    conn = temp_db.get_connection()
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for required in ("workspaces", "repositories", "settings", "_migrations"):
        assert required in tables, f"table '{required}' missing"


def test_migration_is_idempotent(temp_db):
    """Running migrations twice must not error or duplicate."""
    conn = temp_db.get_connection()
    # init already ran once — run again
    from plugins.workspace.backend.migrations import MigrationRunner  # type: ignore[import-untyped]

    runner = MigrationRunner(conn)
    applied = runner.run_pending()
    assert applied == 0, "second run should apply zero migrations"
