"""Unit tests for migration 006 (Hermes project scope)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_migration_006_applied(temp_db):
    """Migration 006 must be recorded after DB init."""
    conn = temp_db.get_connection()
    rows = conn.execute("SELECT version FROM _migrations").fetchall()
    versions = {r[0] for r in rows}
    assert 6 in versions, "migration 006 not recorded"


def test_hermes_project_id_column_exists(temp_db):
    """workspaces must expose the nullable hermes_project_id column."""
    conn = temp_db.get_connection()
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(workspaces)").fetchall()
    }
    assert "hermes_project_id" in cols


def test_column_is_nullable(temp_db):
    """The mapping column must be nullable — legacy workspaces stay unmapped."""
    conn = temp_db.get_connection()
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(workspaces)")}
    col = cols["hermes_project_id"]
    assert col["notnull"] == 0
    assert col["dflt_value"] is None


def test_mapped_index_exists(temp_db):
    """Index over mapped workspaces must exist."""
    conn = temp_db.get_connection()
    idxs = {
        r["name"]
        for r in conn.execute("PRAGMA index_list(workspaces)").fetchall()
    }
    assert "idx_workspaces_hermes_project" in idxs


def test_legacy_rows_unmapped(temp_db):
    """An existing workspace inserted before the column must read NULL."""
    conn = temp_db.get_connection()
    conn.execute(
        "INSERT INTO workspaces (id, name, path) VALUES ('legacy1', 'legacy', '')"
    )
    row = conn.execute(
        "SELECT hermes_project_id FROM workspaces WHERE id = 'legacy1'"
    ).fetchone()
    assert row["hermes_project_id"] is None


def test_mapping_write_roundtrip(temp_db):
    """A mapped workspace persists its project id."""
    conn = temp_db.get_connection()
    conn.execute(
        "INSERT INTO workspaces (id, name) VALUES ('w1', 'roundtrip')"
    )
    conn.execute(
        "UPDATE workspaces SET hermes_project_id = 'p_1234' WHERE id = 'w1'"
    )
    row = conn.execute(
        "SELECT hermes_project_id FROM workspaces WHERE id = 'w1'"
    ).fetchone()
    assert row["hermes_project_id"] == "p_1234"


def test_runner_still_idempotent(temp_db):
    """Re-running migrations after 006 must apply nothing."""
    from plugins.workspace.backend.migrations import MigrationRunner  # type: ignore[import-untyped]

    conn = temp_db.get_connection()
    runner = MigrationRunner(conn)
    assert runner.run_pending() == 0
