"""Database Connection Manager and Migration Runner.

Owns the single SQLite connection for the workspace plugin.  Provides:

- ``get_database()`` — lazy-connect singleton that initialises the DB
  and runs pending migrations on first access.
- ``DatabaseManager`` — low-level connection management (exposed for
  tests that need an in-memory database).

Thread safety: SQLite WAL mode + ``check_same_thread=False``.
Writes are serialised through Python-level locking (``threading.Lock``)
because SQLite's built-in busy handler is not sufficient under high
concurrency from the FastAPI thread pool.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

from .migrations import MigrationRunner

_log = logging.getLogger("hermes.plugins.workspace.database")

# ---------------------------------------------------------------------------
# Database path
# ---------------------------------------------------------------------------

_DEFAULT_DB_NAME = "workspace.db"


def _default_db_path() -> Path:
    """Resolve the default database path inside ``HERMES_HOME``."""
    return Path(get_hermes_home()) / _DEFAULT_DB_NAME


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class DatabaseManager:
    """Manages the SQLite connection and migration lifecycle."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _default_db_path()
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._initialised = False

    # -- public API -----------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_connection(self) -> sqlite3.Connection:
        """Return the shared connection, initialising on first call."""
        if self._conn is not None:
            return self._conn

        with self._lock:
            if self._conn is not None:
                return self._conn
            self._conn = self._connect()
            self._run_migrations()
            self._initialised = True
            return self._conn

    def close(self) -> None:
        """Close the connection (idempotent)."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            self._initialised = False

    @property
    def is_initialised(self) -> bool:
        return self._initialised

    # -- internal -------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open (or create) the database with WAL mode."""
        parent = self._db_path.parent
        parent.mkdir(parents=True, exist_ok=True)

        must_create = not self._db_path.exists()

        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # manual transaction control
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        if must_create:
            _log.info(
                "[Workspace Plugin] Database created: %s",
                _display_path(self._db_path),
            )
        else:
            _log.debug(
                "[Workspace Plugin] Database opened: %s",
                _display_path(self._db_path),
            )

        return conn

    def _run_migrations(self) -> None:
        """Apply pending migrations."""
        assert self._conn is not None
        runner = MigrationRunner(self._conn)
        applied = runner.run_pending()
        if applied:
            _log.info(
                "[Workspace Plugin] Migrations applied: %d", applied
            )

    # -- context manager ------------------------------------------------------

    def __enter__(self):
        return self.get_connection()

    def __exit__(self, *_):
        pass  # connection is shared — closed explicitly via .close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_db: Optional[DatabaseManager] = None


def get_database(db_path: Optional[Path] = None) -> DatabaseManager:
    """Return the module-level ``DatabaseManager`` singleton.

    Call with ``db_path`` ONCE at startup to pin a non-default location
    (e.g. an in-memory database for tests).  Subsequent calls ignore
    the argument.
    """
    global _db
    if _db is None:
        _db = DatabaseManager(db_path)
    return _db


def _display_path(p: Path) -> str:
    """Human-readable path with ``~`` for the home directory."""
    try:
        return str(p).replace(os.path.expanduser("~"), "~")
    except Exception:
        return str(p)
