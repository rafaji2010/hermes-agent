"""Unit tests for migration 007 (canonical ADR reconciliation)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.migrations import (  # type: ignore[import-untyped]
    MigrationRunner,
    _MIGRATIONS_DIR,
)

_MIGRATION_FILES = sorted(_MIGRATIONS_DIR.glob("*.sql"))


def _apply_through(conn: sqlite3.Connection, up_to: int) -> None:
    """Apply migration files 001..up_to in lexical order, recording each."""
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        " version INTEGER PRIMARY KEY, description TEXT NOT NULL,"
        " applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    for path in _MIGRATION_FILES:
        version = int(path.name[:3])
        if version > up_to:
            break
        conn.executescript(path.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO _migrations (version, description) VALUES (?, ?)",
            (version, path.name[:-4]),
        )
    conn.commit()


def test_migration_007_applied(temp_db):
    """Migration 007 must be recorded after DB init."""
    conn = temp_db.get_connection()
    versions = {r[0] for r in conn.execute("SELECT version FROM _migrations")}
    assert 7 in versions, "migration 007 not recorded"


def test_reconcile_columns_exist(temp_db):
    """adrs must expose the reconciliation columns."""
    conn = temp_db.get_connection()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(adrs)")}
    for col in (
        "canonical_path",
        "content_hash",
        "reconcile_state",
        "source",
        "last_indexed",
        "last_error",
    ):
        assert col in cols, f"column {col} missing"


def test_defaults_are_legacy_safe(temp_db):
    """New/existing rows default to db_legacy + workspace_db (no authority flip)."""
    conn = temp_db.get_connection()
    cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(adrs)")}
    assert cols["reconcile_state"]["dflt_value"] == "'db_legacy'"
    assert cols["source"]["dflt_value"] == "'workspace_db'"
    assert cols["canonical_path"]["dflt_value"] is None


def test_reconcile_indexes_exist(temp_db):
    conn = temp_db.get_connection()
    idxs = {r["name"] for r in conn.execute("PRAGMA index_list(adrs)")}
    assert "idx_adrs_reconcile_state" in idxs
    assert "idx_adrs_canonical_path" in idxs


def test_upgrade_from_006_preserves_legacy_adr():
    """Upgrading a 006-era DB must preserve existing ADRs as db_legacy."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        _apply_through(conn, 6)

        conn.execute(
            "INSERT INTO workspaces (id, name, path) VALUES ('w1', 'legacy', '')"
        )
        conn.execute(
            "INSERT INTO adrs (id, workspace_id, title, slug, status, category) "
            "VALUES ('adr1', 'w1', 'Old Decision', 'old-decision', 'accepted', '')"
        )
        conn.execute(
            "INSERT INTO adr_content (adr_id, markdown) VALUES ('adr1', '# Old')"
        )
        conn.commit()

        runner = MigrationRunner(conn)
        applied = runner.run_pending()
        assert applied == 1, "only migration 007 should be pending"

        row = conn.execute(
            "SELECT * FROM adrs WHERE id = 'adr1'"
        ).fetchone()
        assert row["title"] == "Old Decision"
        assert row["reconcile_state"] == "db_legacy"
        assert row["source"] == "workspace_db"
        assert row["canonical_path"] is None
        content = conn.execute(
            "SELECT markdown FROM adr_content WHERE adr_id = 'adr1'"
        ).fetchone()
        assert content["markdown"] == "# Old"

        versions = {r[0] for r in conn.execute("SELECT version FROM _migrations")}
        assert 7 in versions
    finally:
        conn.close()


def test_upgrade_then_runner_idempotent():
    """After upgrading to 007, another run applies nothing."""
    conn = sqlite3.connect(":memory:")
    try:
        _apply_through(conn, 7)
        runner = MigrationRunner(conn)
        assert runner.run_pending() == 0
    finally:
        conn.close()


def test_reconcile_meta_roundtrip(temp_db):
    """The new columns are writable (projection bookkeeping)."""
    conn = temp_db.get_connection()
    conn.execute(
        "INSERT INTO workspaces (id, name) VALUES ('w2', 'rt')"
    )
    conn.execute(
        "INSERT INTO adrs (id, workspace_id, title, slug) "
        "VALUES ('adr2', 'w2', 'Round', 'round')"
    )
    conn.execute(
        "UPDATE adrs SET canonical_path = 'docs/adr/0001-round.md', "
        "content_hash = 'abc123', reconcile_state = 'synced', "
        "source = 'git_file', last_indexed = '2026-08-02T00:00:00Z', "
        "last_error = '' WHERE id = 'adr2'"
    )
    row = conn.execute(
        "SELECT canonical_path, content_hash, reconcile_state, source, "
        "last_indexed, last_error FROM adrs WHERE id = 'adr2'"
    ).fetchone()
    assert row["canonical_path"] == "docs/adr/0001-round.md"
    assert row["content_hash"] == "abc123"
    assert row["reconcile_state"] == "synced"
    assert row["source"] == "git_file"
    assert row["last_indexed"] == "2026-08-02T00:00:00Z"
