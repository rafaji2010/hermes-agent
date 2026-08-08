"""Database Connection Manager and Migration Runner.

U1D-B — hardened SQLite lifecycle:

- **Connection ownership.** File-backed ``workspace.db`` connections are
  THREAD-LOCAL (one configured connection per thread per manager), so
  concurrent FastAPI threadpool handlers never share a connection and
  never interleave transactions.  In-memory databases (tests) keep one
  shared connection, matching the legacy behaviour.  All connections are
  registered with the manager and released by ``close()``.
- **Configuration.** File connections enable WAL through the shared
  upstream helper ``hermes_state.apply_wal_with_fallback`` (honours
  ``database.journal_mode`` config and degrades safely to DELETE on
  filesystems that refuse WAL), set an explicit ``busy_timeout``
  (``HERMES_WORKSPACE_BUSY_TIMEOUT_MS``, default 10s) and enable foreign
  keys.
- **Initialization.** Migrations run once per database path per process
  under a bounded cross-process file lock, so two Hermes processes
  starting against the same ``workspace.db`` cannot race through schema
  migration.  A failed initialization is not cached and the connection is
  closed so the next access retries cleanly.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, Set

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

from .migrations import MigrationRunner

_log = logging.getLogger("hermes.plugins.workspace.database")

# ---------------------------------------------------------------------------
# Database path
# ---------------------------------------------------------------------------

_DEFAULT_DB_NAME = "workspace.db"

# Busy/lock policy: SQLite retries lock contention for this long before
# surfacing "database is locked". Mirrors the upstream kanban convention
# (env-overridable, bounded) with a shorter default suited to REST usage.
DEFAULT_BUSY_TIMEOUT_MS = 10_000

_INIT_LOCK_TIMEOUT_SECONDS = 15.0
_INIT_LOCK_POLL_SECONDS = 0.05


def _default_db_path() -> Path:
    """Resolve the default database path inside ``HERMES_HOME``."""
    return Path(get_hermes_home()) / _DEFAULT_DB_NAME


def _resolve_busy_timeout_ms() -> int:
    """Return the busy timeout in ms (env-overridable, bounded)."""
    raw = os.environ.get("HERMES_WORKSPACE_BUSY_TIMEOUT_MS", "").strip()
    if raw:
        try:
            parsed = int(raw)
        except ValueError:
            parsed = 0
        if parsed > 0:
            return parsed
    return DEFAULT_BUSY_TIMEOUT_MS


# ---------------------------------------------------------------------------
# Cross-process initialization lock (mirrors the upstream kanban convention:
# bounded non-blocking flock; on timeout proceed under the in-process lock
# because migration application is idempotent)
# ---------------------------------------------------------------------------


@contextmanager
def _cross_process_init_lock(db_path: Path):
    """Serialize first-connect migration setup across processes.

    Bounded: retries a non-blocking acquire up to a deadline; on timeout
    logs a WARNING and proceeds — the in-process ``_INIT_LOCK`` plus
    idempotent, version-tracked migrations are the correctness backstop,
    so the worst case is redundant work, not corruption.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db_path.with_name(db_path.name + ".init.lock")
    handle = lock_path.open("a+b")
    acquired = False
    try:
        deadline = time.monotonic() + _INIT_LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_INIT_LOCK_POLL_SECONDS)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except (BlockingIOError, OSError):
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(_INIT_LOCK_POLL_SECONDS)
        if not acquired:
            _log.warning(
                "Workspace init lock for %s not acquired within %.0fs — "
                "proceeding without the cross-process lock (idempotent "
                "migrations are the correctness backstop).",
                lock_path,
                _INIT_LOCK_TIMEOUT_SECONDS,
            )
        yield
    finally:
        try:
            if acquired:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


# In-process init serialization + per-path memo (like ``projects_db``).
_INIT_LOCK = threading.Lock()
_INITIALIZED_PATHS: Set[str] = set()


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class DatabaseManager:
    """Manages per-thread SQLite connections and the migration lifecycle.

    File-backed databases get one configured connection per thread
    (created lazily, invalidated by ``close()`` via an epoch counter).
    In-memory databases keep a single shared connection (test path).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = Path(db_path) if db_path is not None else _default_db_path()
        self._local = threading.local()
        self._epoch = 0
        self._shared_conn: Optional[sqlite3.Connection] = None
        self._conns: Set[sqlite3.Connection] = set()
        self._lock = threading.Lock()
        self._initialised = False
        self._busy_timeout_ms = _resolve_busy_timeout_ms()

    # -- public API -----------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def busy_timeout_ms(self) -> int:
        return self._busy_timeout_ms

    def get_connection(self) -> sqlite3.Connection:
        """Return a configured connection for the calling thread.

        File-backed databases return a thread-local connection; in-memory
        databases return the single shared connection.  First use per
        manager (re)runs initialization.
        """
        if self._is_memory():
            with self._lock:
                if self._shared_conn is None:
                    self._shared_conn = self._connect()
                return self._shared_conn

        slot = getattr(self._local, "slot", None)
        if slot is not None and slot[0] == self._epoch:
            return slot[1]

        conn = self._connect()
        self._local.slot = (self._epoch, conn)
        return conn

    def close(self) -> None:
        """Close every live connection (idempotent).

        Thread-local slots are invalidated via the epoch counter, so a
        thread that touches the manager afterwards transparently reopens.
        """
        with self._lock:
            self._epoch += 1
            conns = list(self._conns)
            self._conns.clear()
            for conn in conns:
                try:
                    conn.close()
                except Exception:
                    pass
            if self._shared_conn is not None:
                try:
                    self._shared_conn.close()
                except Exception:
                    pass
                self._shared_conn = None
            self._initialised = False

    @property
    def is_initialised(self) -> bool:
        return self._initialised

    # -- internal -------------------------------------------------------------

    def _is_memory(self) -> bool:
        return str(self._db_path) == ":memory:"

    def _connect(self) -> sqlite3.Connection:
        """Open (or create) the database with hardened configuration."""
        parent = self._db_path.parent
        parent.mkdir(parents=True, exist_ok=True)

        must_create = not self._db_path.exists()
        if self._is_memory():
            conn = sqlite3.connect(
                ":memory:",
                check_same_thread=False,
                isolation_level=None,  # manual transaction control
            )
        else:
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=self._busy_timeout_ms / 1000.0,
                isolation_level=None,  # manual transaction control
            )
        conn.row_factory = sqlite3.Row

        try:
            self._configure(conn)
            self._init_db(conn)
        except Exception:
            conn.close()
            raise

        if not self._is_memory():
            with self._lock:
                self._conns.add(conn)

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

    def _configure(self, conn: sqlite3.Connection) -> None:
        """Apply SQLite runtime configuration following upstream conventions."""
        if not self._is_memory():
            # WAL with DELETE fallback for filesystems that refuse WAL
            # (NFS/SMB/FUSE/ZFS); honours ``database.journal_mode`` config.
            from hermes_state import apply_wal_with_fallback  # type: ignore[import-untyped]

            apply_wal_with_fallback(conn, db_label="workspace.db")
            conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        conn.execute("PRAGMA foreign_keys=ON")

    def _init_db(self, conn: sqlite3.Connection) -> None:
        """Apply pending migrations exactly once per database path.

        Protected by the in-process memo and (for file databases) the
        bounded cross-process lock.  A failure raises, the connection is
        closed by the caller, and nothing is cached — the next access
        retries initialization.
        """
        if self._initialised:
            return

        if self._is_memory():
            runner = MigrationRunner(conn)
            applied = runner.run_pending()
            if applied:
                _log.info("[Workspace Plugin] Migrations applied: %d", applied)
            self._initialised = True
            return

        resolved = str(self._db_path.resolve())
        with _INIT_LOCK:
            if resolved in _INITIALIZED_PATHS:
                self._initialised = True
                return
            with _cross_process_init_lock(self._db_path):
                runner = MigrationRunner(conn)
                applied = runner.run_pending()
                if applied:
                    _log.info("[Workspace Plugin] Migrations applied: %d", applied)
                _INITIALIZED_PATHS.add(resolved)
            self._initialised = True

    # -- context manager ------------------------------------------------------

    def __enter__(self):
        return self.get_connection()

    def __exit__(self, *_):
        pass  # connections are owned by the manager — closed via .close()


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
