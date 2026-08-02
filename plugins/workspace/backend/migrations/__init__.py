"""Migration System.

A migration is a plain SQL file in the ``migrations/`` directory named
``NNN_description.sql`` (e.g. ``001_initial.sql``).  Migrations are
applied in lexical order and are idempotent — once applied they are
never re-run.

The migration runner uses the ``_migrations`` table (created by
``001_initial.sql``) to track which migrations have been applied.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import List, Tuple

_log = logging.getLogger("hermes.plugins.workspace.migrations")

_MIGRATIONS_DIR = Path(__file__).resolve().parent
_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_(\w+)\.sql$")


def _discover_migrations() -> List[Tuple[int, str, Path]]:
    """Return ``[(version, name, path), ...]`` sorted by version."""
    result: List[Tuple[int, str, Path]] = []
    for entry in sorted(_MIGRATIONS_DIR.iterdir()):
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

    def __init__(self, conn):
        self._conn = conn

    def run_pending(self) -> int:
        """Apply any un-applied migrations.  Returns count applied."""
        applied_versions = self._applied_versions()
        pending = [
            (v, n, p)
            for v, n, p in _discover_migrations()
            if v not in applied_versions
        ]

        if not pending:
            return 0

        count = 0
        for version, name, path in pending:
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
            return {row[0] for row in rows}
        except Exception:
            # _migrations table doesn't exist yet — no migrations applied.
            return set()

    def _apply_one(self, version: int, name: str, path: Path) -> None:
        sql = path.read_text(encoding="utf-8")
        _log.info("[Workspace Plugin] Applying migration %03d_%s", version, name)
        try:
            self._conn.executescript(sql)
        except Exception:
            _log.exception(
                "[Workspace Plugin] Migration %03d_%s FAILED", version, name
            )
            raise
        self._conn.execute(
            "INSERT INTO _migrations (version, description) VALUES (?, ?)",
            (version, f"{version:03d}_{name}"),
        )
        _log.info("[Workspace Plugin] Migration %03d_%s applied", version, name)
