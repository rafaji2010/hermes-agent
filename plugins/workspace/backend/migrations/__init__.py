"""Migration System.

A migration is a plain SQL file in the ``migrations/`` directory named
``NNN_description.sql`` (e.g. ``001_initial.sql``).  Migrations are
applied in lexical order and are idempotent — once applied they are
never re-run.

U1D-B hardening:

- **Atomic application.** Each migration and its ``_migrations`` version
  record run inside one ``BEGIN IMMEDIATE ... COMMIT`` transaction, so a
  failed migration leaves no partial schema behind.
- **Crash recovery.** If a migration's schema effects are already present
  but its version record is missing (a crash between schema application
  and recording), the runner records the version without re-applying —
  using per-migration sentinel checks instead of re-running ``ALTER``
  statements that would fail.
- **Deterministic order.** Migrations run in numeric order only.
- **Newer-schema guard.** Versions recorded in ``_migrations`` that this
  build does not know are logged and never touched (no downgrade, no
  re-application).
- **Test seam.** ``MigrationRunner(conn, migrations_dir=...)`` accepts an
  alternative migration directory for isolated failure-recovery tests.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("hermes.plugins.workspace.migrations")

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_(\w+)\.sql$")

# ---------------------------------------------------------------------------
# Sentinel checks — schema evidence that a migration already took effect.
# Used for crash recovery when the version row is missing, so an ALTER
# (or table) that actually ran is never re-applied.
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if not _table_exists(conn, table):
        return False
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(r[1]) == column for r in cols)


# version -> sentinel predicate.  Kept in sync with migrations 001-007;
# a new migration MUST add its sentinel here.
_SENTINELS: Dict[int, Callable[[sqlite3.Connection], bool]] = {
    1: lambda c: _table_exists(c, "workspaces"),
    2: lambda c: _table_exists(c, "adrs"),
    3: lambda c: _table_exists(c, "journal_entries"),
    4: lambda c: _table_exists(c, "roadmaps"),
    5: lambda c: _table_exists(c, "tasks"),
    6: lambda c: _column_exists(c, "workspaces", "hermes_project_id"),
    7: lambda c: _column_exists(c, "adrs", "canonical_path"),
}


def _discover_migrations(
    migrations_dir: Optional[Path] = None,
) -> List[Tuple[int, str, Path]]:
    """Return ``[(version, name, path), ...]`` sorted by version."""
    directory = Path(migrations_dir) if migrations_dir else _MIGRATIONS_DIR
    result: List[Tuple[int, str, Path]] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        m = _MIGRATION_FILE_RE.match(entry.name)
        if m is None:
            continue
        version = int(m.group(1))
        name = m.group(2)
        result.append((version, name, entry))
    return result


class MigrationRunner:
    """Applies pending SQL migrations to a database connection."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        migrations_dir: Optional[Path] = None,
    ):
        self._conn = conn
        self._dir = migrations_dir

    def run_pending(self) -> int:
        """Apply any un-applied migrations.  Returns count applied."""
        applied_versions = self._applied_versions()
        discovered = _discover_migrations(self._dir)
        known_versions = {v for v, _, _ in discovered}

        newer = sorted(applied_versions - known_versions)
        if newer:
            _log.warning(
                "workspace.db records migrations from a newer schema (%s); "
                "leaving them untouched",
                newer,
            )

        pending = [(v, n, p) for v, n, p in discovered if v not in applied_versions]

        count = 0
        for version, name, path in pending:
            if self._sentinel_applied(version):
                # Crash recovery: schema effects exist but the version was
                # never recorded — record it without re-applying.
                self._record_version(version, f"{version:03d}_{name}")
                _log.info(
                    "[Workspace Plugin] Migration %03d_%s recovered (already applied)",
                    version,
                    name,
                )
                count += 1
                continue
            self._apply_one(version, name, path)
            count += 1

        return count

    # -- internal -------------------------------------------------------------

    def _applied_versions(self) -> set:
        """Return the set of already-applied migration versions."""
        try:
            rows = self._conn.execute(
                "SELECT version FROM _migrations ORDER BY version"
            ).fetchall()
            return {int(r[0]) for r in rows}
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                # _migrations table doesn't exist yet — no migrations applied.
                return set()
            raise

    def _sentinel_applied(self, version: int) -> bool:
        check = _SENTINELS.get(version)
        if check is None:
            return False
        try:
            return check(self._conn)
        except sqlite3.Error:
            return False

    def _record_version(self, version: int, description: str) -> None:
        self._conn.execute(
            "INSERT INTO _migrations (version, description) VALUES (?, ?)",
            (version, description),
        )

    def _apply_one(self, version: int, name: str, path: Path) -> None:
        sql = path.read_text(encoding="utf-8")
        _log.info("[Workspace Plugin] Applying migration %03d_%s", version, name)

        if self._conn.in_transaction:
            # Nested inside an outer transaction (defensive): join it.  The
            # outer owner commits/rolls back.
            try:
                self._conn.executescript(sql)
                self._record_version(version, f"{version:03d}_{name}")
            except Exception:
                _log.exception(
                    "[Workspace Plugin] Migration %03d_%s FAILED", version, name
                )
                raise
        else:
            # Atomic: the migration script AND its version record share one
            # BEGIN IMMEDIATE ... COMMIT transaction.  A failure rolls the
            # whole migration back, so it can never appear half-applied.
            script = (
                f"BEGIN IMMEDIATE;\n{sql}\n"
                f"INSERT INTO _migrations (version, description) "
                f"VALUES ({version}, '{name}');\nCOMMIT;"
            )
            try:
                self._conn.executescript(script)
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                _log.exception(
                    "[Workspace Plugin] Migration %03d_%s FAILED", version, name
                )
                raise
        _log.info("[Workspace Plugin] Migration %03d_%s applied", version, name)
