"""SQLite lifecycle, concurrency and migration hardening tests (U1D-B).

Covers:
- connection lifecycle (per-thread file connections, deterministic release)
- transaction rollback on failure (file-backed)
- abandoned-transaction self-heal on reused connections
- concurrent threaded access against one shared storage on a file DB
- SQLite configuration (foreign keys, busy_timeout, WAL, DELETE fallback,
  in-memory support)
- migration idempotency across managers
- migration failure recovery (atomic rollback + sentinel crash recovery)
- cross-process concurrent initialization (real subprocesses)
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes_constants import (  # type: ignore[import-untyped]
    reset_hermes_home_override,
    set_hermes_home_override,
)
from plugins.workspace.backend.database import (  # type: ignore[import-untyped]
    DEFAULT_BUSY_TIMEOUT_MS,
    DatabaseManager,
)
from plugins.workspace.backend.migrations import (  # type: ignore[import-untyped]
    MigrationRunner,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]

_REAL_MIGRATIONS_DIR = Path(
    __import__("plugins.workspace.backend.migrations", fromlist=["__file__"]).__file__
).parent

_ALL_VERSIONS = {1, 2, 3, 4, 5, 6, 7}


@pytest.fixture(autouse=True)
def _unpin_runtime():
    """These tests exercise the real home-keyed/path-keyed paths — unpin.

    The plugins conftest pins an in-memory runtime for ordinary tests;
    clearing it lets ``get_workspace_runtime()`` resolve through the
    effective-home cache instead.
    """
    from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
        reset_workspace_runtimes,
    )

    reset_workspace_runtimes()
    yield
    reset_workspace_runtimes()


def _versions(conn) -> set:
    return {int(r[0]) for r in conn.execute("SELECT version FROM _migrations")}


# ---------------------------------------------------------------------------
# A. Connection lifecycle
# ---------------------------------------------------------------------------


def test_file_connections_are_per_thread_and_released(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")

    c1 = mgr.get_connection()
    c2 = mgr.get_connection()
    assert c1 is c2  # same thread reuses its connection

    other: dict = {}
    t = threading.Thread(target=lambda: other.update(conn=mgr.get_connection()))
    t.start()
    t.join()
    assert other["conn"] is not c1  # another thread gets its own connection

    mgr.close()
    assert mgr.is_initialised is False
    assert len(mgr._conns) == 0  # every connection deterministically released

    # Reuse after close reopens transparently (new epoch).
    c3 = mgr.get_connection()
    assert c3 is not c1
    assert c3.execute("SELECT 1").fetchone()[0] == 1
    assert mgr.is_initialised


def test_repeated_operations_work(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    storage = SQLiteStorage(db_manager=mgr)

    for i in range(20):
        storage.create_workspace(f"ws-{i}", "")
        assert storage.get_workspace_by_name(f"ws-{i}") is not None

    assert len(storage.list_workspaces()) == 20


def test_runtime_close_reset_remains_safe(tmp_path):
    from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
        WorkspaceRuntime,
        get_workspace_runtime,
        reset_workspace_runtimes,
    )

    token = set_hermes_home_override(str(tmp_path))
    try:
        rt = get_workspace_runtime()
        rt.workspace_service.create_workspace(__import__(
            "plugins.workspace.backend.models", fromlist=["WorkspaceCreate"]
        ).WorkspaceCreate(name="before-close", path=""))
        assert rt.database.is_initialised

        reset_workspace_runtimes()
        assert rt.database.is_initialised is False

        # Rebuild on next lookup; data persisted in the file.
        rt2 = get_workspace_runtime()
        assert rt2 is not rt
        names = {w.name for w in rt2.workspace_service.list_workspaces()}
        assert names == {"before-close"}
    finally:
        reset_hermes_home_override(token)
        reset_workspace_runtimes()


# ---------------------------------------------------------------------------
# B. Transaction rollback + self-heal
# ---------------------------------------------------------------------------


def test_file_backed_transaction_rollback_on_error(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    storage = SQLiteStorage(db_manager=mgr)

    with pytest.raises(ValueError, match="simulated"):
        with storage.transaction():
            storage.create_workspace("gone", "")
            raise ValueError("simulated failure")

    assert storage.get_workspace_by_name("gone") is None
    # Database remains fully usable.
    storage.create_workspace("kept", "")
    assert storage.get_workspace_by_name("kept") is not None


def test_leftover_transaction_self_heals(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    conn = mgr.get_connection()
    # Simulate an abandoned transaction from a crashed request on this
    # thread's connection.
    conn.execute("BEGIN IMMEDIATE")

    storage = SQLiteStorage(db_manager=mgr)
    with storage.transaction():
        storage.create_workspace("after-heal", "")

    assert storage.get_workspace_by_name("after-heal") is not None


# ---------------------------------------------------------------------------
# C. Concurrent access (one shared storage, file-backed)
# ---------------------------------------------------------------------------


def test_concurrent_operations_shared_storage(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    storage = SQLiteStorage(db_manager=mgr)

    n_threads, n_ops = 8, 10
    errors: list = []

    def worker(tid: int) -> None:
        try:
            for i in range(n_ops):
                name = f"t{tid}-{i}"
                with storage.transaction():
                    storage.create_workspace(name, "")
                assert storage.get_workspace_by_name(name) is not None
        except Exception as exc:  # noqa: BLE001 - collected for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"thread errors: {errors}"
    assert len(storage.list_workspaces()) == n_threads * n_ops


# ---------------------------------------------------------------------------
# D. SQLite configuration
# ---------------------------------------------------------------------------


def test_foreign_keys_enabled(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    conn = mgr.get_connection()
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_busy_timeout_configured(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    conn = mgr.get_connection()
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == DEFAULT_BUSY_TIMEOUT_MS


def test_busy_timeout_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_WORKSPACE_BUSY_TIMEOUT_MS", "1234")
    mgr = DatabaseManager(tmp_path / "ws.db")
    conn = mgr.get_connection()
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    assert row[0] == 1234


def test_wal_enabled_for_file_backed_db(tmp_path):
    mgr = DatabaseManager(tmp_path / "ws.db")
    conn = mgr.get_connection()
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert str(row[0]).lower() == "wal"


def test_delete_fallback_honours_config(tmp_path, monkeypatch):
    # A config that forbids WAL (network-filesystem setups) must degrade
    # to DELETE mode and keep the database fully usable.
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "database:\n  journal_mode: delete\n", encoding="utf-8"
    )
    token = set_hermes_home_override(str(home))
    try:
        mgr = DatabaseManager(home / "workspace.db")
        conn = mgr.get_connection()
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert str(row[0]).lower() == "delete"
        storage = SQLiteStorage(db_manager=mgr)
        storage.create_workspace("delete-mode-ok", "")
        assert storage.get_workspace_by_name("delete-mode-ok") is not None
    finally:
        reset_hermes_home_override(token)


def test_in_memory_database_still_supported(tmp_path):
    mgr = DatabaseManager(Path(":memory:"))
    conn = mgr.get_connection()
    assert _versions(conn) == _ALL_VERSIONS
    row = conn.execute("PRAGMA journal_mode").fetchone()
    assert str(row[0]).lower() == "memory"


# ---------------------------------------------------------------------------
# E. Migration idempotency
# ---------------------------------------------------------------------------


def test_file_db_migrations_idempotent_across_managers(tmp_path):
    db_path = tmp_path / "ws.db"

    m1 = DatabaseManager(db_path)
    assert _versions(m1.get_connection()) == _ALL_VERSIONS
    m1.close()

    m2 = DatabaseManager(db_path)
    assert _versions(m2.get_connection()) == _ALL_VERSIONS
    runner = MigrationRunner(m2.get_connection())
    assert runner.run_pending() == 0


# ---------------------------------------------------------------------------
# F. Migration failure recovery
# ---------------------------------------------------------------------------


def _copy_real_migrations(dst: Path) -> None:
    for f in sorted(_REAL_MIGRATIONS_DIR.glob("*.sql")):
        (dst / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")


def test_failed_migration_atomic_and_recoverable(tmp_path):
    import sqlite3

    db_path = tmp_path / "ws.db"
    mig_dir = tmp_path / "migs"
    mig_dir.mkdir()
    _copy_real_migrations(mig_dir)
    (mig_dir / "008_failing.sql").write_text(
        "CREATE TABLE should_not_exist (id INTEGER);\nTHIS IS NOT SQL;\n",
        encoding="utf-8",
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    runner = MigrationRunner(conn, migrations_dir=mig_dir)

    with pytest.raises(Exception):
        runner.run_pending()

    # 001-007 recorded; the failing 008 is NOT recorded…
    assert _versions(conn) == _ALL_VERSIONS
    # …and its partial work was rolled back atomically.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'should_not_exist'"
    ).fetchone()
    assert row is None

    # Fix the migration and retry — recovery succeeds.
    (mig_dir / "008_failing.sql").write_text(
        "CREATE TABLE should_not_exist (id INTEGER);\n", encoding="utf-8"
    )
    runner2 = MigrationRunner(conn, migrations_dir=mig_dir)
    assert runner2.run_pending() == 1
    assert _versions(conn) == _ALL_VERSIONS | {8}
    assert MigrationRunner(conn, migrations_dir=mig_dir).run_pending() == 0


def test_sentinel_recovery_records_without_reapply(tmp_path):
    """Crash between schema application and version recording recovers."""
    import sqlite3

    conn = sqlite3.connect(tmp_path / "ws.db")
    conn.row_factory = sqlite3.Row
    # Pre-seeded schema where migration 007's column already exists but no
    # version is recorded (simulated crash mid-007).
    conn.executescript(
        """
        CREATE TABLE _migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE adrs (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            repository_id TEXT,
            title TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'proposed',
            category TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            canonical_path TEXT
        );
        """
    )
    conn.commit()

    runner = MigrationRunner(conn)
    applied = runner.run_pending()

    # No duplicate-column error: 007 was recovered via its sentinel and
    # every version is now recorded.
    assert applied == 7
    assert _versions(conn) == _ALL_VERSIONS
    assert MigrationRunner(conn).run_pending() == 0


def test_newer_schema_left_untouched(tmp_path):
    import sqlite3

    conn = sqlite3.connect(tmp_path / "ws.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE _migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO _migrations (version, description) VALUES
            (1, '001_initial'), (2, '002_adrs'), (3, '003_engineering_journal'),
            (4, '004_roadmaps'), (5, '005_tasks'), (6, '006_project_scope'),
            (7, '007_adr_reconciliation'), (99, 'future_migration');
        """
    )
    conn.commit()

    # A database from a NEWER schema (all known migrations recorded plus an
    # unknown future version) must not be re-migrated or touched.
    runner = MigrationRunner(conn)
    assert runner.run_pending() == 0
    assert _versions(conn) == _ALL_VERSIONS | {99}


# ---------------------------------------------------------------------------
# G. Concurrent initialization (true cross-process)
# ---------------------------------------------------------------------------


def test_concurrent_initialization_across_processes(tmp_path):
    db_path = tmp_path / "ws.db"

    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(_REPO_ROOT)!r})\n"
        "from plugins.workspace.backend.database import DatabaseManager\n"
        f"mgr = DatabaseManager({str(db_path)!r})\n"
        "conn = mgr.get_connection()\n"
        "versions = {int(r[0]) for r in conn.execute("
        "'SELECT version FROM _migrations')}\n"
        "assert versions == {1, 2, 3, 4, 5, 6, 7}, versions\n"
        "print('ok')\n"
    )

    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    results = [p.communicate(timeout=120) for p in procs]

    for proc, (out, err) in zip(procs, results):
        assert proc.returncode == 0, f"subprocess failed: {err}\n{out}"
        assert "ok" in out

    # Final on-disk state: every migration applied exactly once.
    mgr = DatabaseManager(db_path)
    conn = mgr.get_connection()
    assert _versions(conn) == _ALL_VERSIONS
    assert len(conn.execute("SELECT * FROM _migrations").fetchall()) == len(
        _ALL_VERSIONS
    )
