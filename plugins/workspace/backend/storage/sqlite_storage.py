"""SQLite Storage Implementation.

Implements ``AbstractStorage`` backed by a single SQLite database.
Uses the module-level ``get_database()`` singleton for connection
management.

All write operations are wrapped in ``BEGIN IMMEDIATE`` transactions.
Row → model conversion is done with ``sqlite3.Row`` and direct
attribute access (no ORM).

Transaction and Savepoint Nesting
---------------------------------
When a write method is called OUTSIDE an explicit ``transaction()``
block, it auto-commits immediately (the existing M1 behaviour).
When called INSIDE a ``transaction()`` block, the commit is deferred
to the outermost caller — individual method calls do NOT commit.

Nested ``transaction()`` blocks use *savepoints* so an inner
transaction can roll back independently without losing the outer
unit's work.  This mirrors PostgreSQL / SQL Server semantics::

    with storage.transaction():              # BEGIN IMMEDIATE
        storage.create_workspace("a", "")
        with storage.transaction():          # SAVEPOINT sp_2
            storage.register_repository(...)
            raise SomeError                  # → ROLLBACK TO sp_2
        # outer transaction still open
        storage.create_workspace("b", "")    # still uncommitted
    # → COMMIT (both "a" and "b" land)
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from ..database import get_database
from ..models import ADR, JournalEntry, Repository, Workspace
from ..models import (
    ADRError,
    ADRNotFoundError,
    DuplicateRepositoryError,
    DuplicateSlugError,
    DuplicateWorkspaceError,
    JournalEntryNotFoundError,
    WorkspaceNotFoundError,
)
from . import AbstractStorage

if TYPE_CHECKING:
    import sqlite3

_log = logging.getLogger("hermes.plugins.workspace.storage")


def _new_id() -> str:
    """Generate a short random hex id."""
    return uuid.uuid4().hex[:12]


def _datetime_now_date() -> str:
    """Return today's date as ISO-8601 string."""
    import datetime
    return datetime.date.today().isoformat()


def _workspace_from_row(row: sqlite3.Row) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        path=row["path"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _repository_from_row(row: sqlite3.Row) -> Repository:
    return Repository(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        path=row["path"],
        git_root=row["git_root"] or "",
        default_branch=row["default_branch"] or "main",
        created_at=row["created_at"],
    )


class SQLiteStorage(AbstractStorage):
    """SQLite-backed persistent storage for workspaces and repositories."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path
        self._transaction_depth = 0

    @property
    def _conn(self) -> "sqlite3.Connection":
        return get_database(self._db_path).get_connection()

    # ------------------------------------------------------------------
    # Transaction management
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        """Start a transaction or savepoint."""
        self._transaction_depth += 1
        if self._transaction_depth == 1:
            self._conn.execute("BEGIN IMMEDIATE")
        else:
            self._conn.execute(f"SAVEPOINT sp_{self._transaction_depth}")

    def commit(self) -> None:
        """Commit the innermost unit.

        Raises ``RuntimeError`` when no transaction is active.
        """
        if self._transaction_depth < 1:
            raise RuntimeError("commit() called with no active transaction")
        if self._transaction_depth == 1:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute(f"RELEASE SAVEPOINT sp_{self._transaction_depth}")
        self._transaction_depth -= 1

    def rollback(self) -> None:
        """Roll back the innermost unit.

        Raises ``RuntimeError`` when no transaction is active.
        """
        if self._transaction_depth < 1:
            raise RuntimeError("rollback() called with no active transaction")
        if self._transaction_depth == 1:
            self._conn.execute("ROLLBACK")
        else:
            self._conn.execute(
                f"ROLLBACK TO SAVEPOINT sp_{self._transaction_depth}"
            )
        self._transaction_depth -= 1

    @property
    def in_transaction(self) -> bool:
        """``True`` when an explicit transaction is active."""
        return self._transaction_depth > 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _maybe_commit(self) -> None:
        """Commit only when NOT inside an explicit transaction block.

        When ``in_transaction`` is ``True`` the outermost
        ``transaction()`` context manager owns the commit — individual
        write methods must NOT commit internally.
        """
        if not self.in_transaction:
            self._conn.commit()

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def create_workspace(self, name: str, path: str) -> Workspace:
        existing = self.get_workspace_by_name(name)
        if existing is not None:
            raise DuplicateWorkspaceError(name)

        workspace_id = _new_id()
        self._conn.execute(
            "INSERT INTO workspaces (id, name, path) VALUES (?, ?, ?)",
            (workspace_id, name, path),
        )
        self._maybe_commit()

        _log.info(
            "[Workspace Plugin] Workspace created: id=%s name=%r",
            workspace_id,
            name,
        )
        return self.get_workspace(workspace_id)  # type: ignore[return-value]

    def list_workspaces(self) -> List[Workspace]:
        rows = self._conn.execute(
            "SELECT * FROM workspaces ORDER BY created_at DESC"
        ).fetchall()
        return [_workspace_from_row(r) for r in rows]

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
        ).fetchone()
        return _workspace_from_row(row) if row else None

    def get_workspace_by_name(self, name: str) -> Optional[Workspace]:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE name = ?", (name,)
        ).fetchone()
        return _workspace_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    def register_repository(
        self,
        workspace_id: str,
        name: str,
        path: str,
        git_root: str,
        default_branch: str,
    ) -> Repository:
        ws = self.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)

        dup = self.get_repository_by_path(workspace_id, path)
        if dup is not None:
            raise DuplicateRepositoryError(workspace_id, path)

        repo_id = _new_id()
        self._conn.execute(
            "INSERT INTO repositories "
            "(id, workspace_id, name, path, git_root, default_branch) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (repo_id, workspace_id, name, path, git_root, default_branch),
        )
        self._maybe_commit()

        _log.info(
            "[Workspace Plugin] Repository registered: id=%s name=%r path=%r",
            repo_id,
            name,
            path,
        )
        return self.get_repository(repo_id)  # type: ignore[return-value]

    def list_repositories(self, workspace_id: str) -> List[Repository]:
        rows = self._conn.execute(
            "SELECT * FROM repositories WHERE workspace_id = ? ORDER BY name",
            (workspace_id,),
        ).fetchall()
        return [_repository_from_row(r) for r in rows]

    def get_repository(self, repo_id: str) -> Optional[Repository]:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        return _repository_from_row(row) if row else None

    def get_repository_by_path(
        self, workspace_id: str, path: str
    ) -> Optional[Repository]:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE workspace_id = ? AND path = ?",
            (workspace_id, path),
        ).fetchone()
        return _repository_from_row(row) if row else None

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._maybe_commit()

    # ------------------------------------------------------------------
    # Architecture Decision Records (ADRs)
    # ------------------------------------------------------------------

    def create_adr(
        self,
        workspace_id: str,
        repository_id: Optional[str],
        title: str,
        slug: str,
        status: str,
        category: str,
        markdown: str,
        tags: List[str],
    ) -> ADR:
        existing = self.get_adr_by_slug(workspace_id, slug)
        if existing is not None:
            raise DuplicateSlugError(slug)

        adr_id = _new_id()
        self._conn.execute(
            "INSERT INTO adrs (id, workspace_id, repository_id, title, slug, "
            "status, category) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (adr_id, workspace_id, repository_id, title, slug, status, category),
        )
        self._conn.execute(
            "INSERT INTO adr_content (adr_id, markdown) VALUES (?, ?)",
            (adr_id, markdown),
        )
        self._set_adr_tags(adr_id, tags)
        self._maybe_commit()
        _log.info("[Workspace Plugin] ADR created: id=%s slug=%r", adr_id, slug)
        return self.get_adr(adr_id)  # type: ignore[return-value]

    def update_adr(
        self,
        adr_id: str,
        *,
        title: Optional[str] = None,
        slug: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        markdown: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ADR:
        existing = self.get_adr(adr_id)
        if existing is None:
            raise ADRNotFoundError(adr_id)

        if slug is not None and slug != existing.slug:
            dup = self.get_adr_by_slug(existing.workspace_id, slug)
            if dup is not None and dup.id != adr_id:
                raise DuplicateSlugError(slug)

        set_clauses = ["updated_at = datetime('now')"]
        params: list = []

        for col, val in (
            ("title", title),
            ("slug", slug),
            ("status", status),
            ("category", category),
        ):
            if val is not None:
                set_clauses.append(f"{col} = ?")
                params.append(val)

        if set_clauses:
            params.append(adr_id)
            self._conn.execute(
                f"UPDATE adrs SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )

        if markdown is not None:
            self._conn.execute(
                "INSERT INTO adr_content (adr_id, markdown) VALUES (?, ?) "
                "ON CONFLICT(adr_id) DO UPDATE SET markdown = excluded.markdown",
                (adr_id, markdown),
            )

        if tags is not None:
            self._set_adr_tags(adr_id, tags)

        self._maybe_commit()
        _log.info("[Workspace Plugin] ADR updated: id=%s", adr_id)
        return self.get_adr(adr_id)  # type: ignore[return-value]

    def delete_adr(self, adr_id: str) -> None:
        existing = self.get_adr(adr_id)
        if existing is None:
            raise ADRNotFoundError(adr_id)
        self._conn.execute("DELETE FROM adrs WHERE id = ?", (adr_id,))
        self._maybe_commit()
        _log.info("[Workspace Plugin] ADR deleted: id=%s", adr_id)

    def get_adr(self, adr_id: str) -> Optional[ADR]:
        row = self._conn.execute(
            "SELECT a.*, ac.markdown FROM adrs a "
            "LEFT JOIN adr_content ac ON ac.adr_id = a.id "
            "WHERE a.id = ?",
            (adr_id,),
        ).fetchone()
        if row is None:
            return None
        tags = self._get_adr_tags(adr_id)
        return _adr_from_row(row, tags)

    def list_adrs(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        query: Optional[str] = None,
    ) -> List[ADR]:
        wheres = ["a.workspace_id = ?"]
        params: list = [workspace_id]

        if status:
            wheres.append("a.status = ?")
            params.append(status)
        if category:
            wheres.append("a.category = ?")
            params.append(category)
        if query:
            wheres.append("(a.title LIKE ? OR ac.markdown LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])
        if tag:
            wheres.append(
                "EXISTS (SELECT 1 FROM adr_tags t "
                "WHERE t.adr_id = a.id AND t.tag = ?)"
            )
            params.append(tag)

        sql = (
            "SELECT a.*, ac.markdown FROM adrs a "
            "LEFT JOIN adr_content ac ON ac.adr_id = a.id "
            f"WHERE {' AND '.join(wheres)} "
            "ORDER BY a.created_at DESC"
        )
        rows = self._conn.execute(sql, params).fetchall()
        result: List[ADR] = []
        for r in rows:
            tags = self._get_adr_tags(r["id"])
            result.append(_adr_from_row(r, tags))
        return result

    def get_adr_by_slug(self, workspace_id: str, slug: str) -> Optional[ADR]:
        row = self._conn.execute(
            "SELECT a.*, ac.markdown FROM adrs a "
            "LEFT JOIN adr_content ac ON ac.adr_id = a.id "
            "WHERE a.workspace_id = ? AND a.slug = ?",
            (workspace_id, slug),
        ).fetchone()
        if row is None:
            return None
        tags = self._get_adr_tags(row["id"])
        return _adr_from_row(row, tags)

    def get_distinct_tags(self, workspace_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT t.tag FROM adr_tags t "
            "JOIN adrs a ON a.id = t.adr_id "
            "WHERE a.workspace_id = ? ORDER BY t.tag",
            (workspace_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def get_distinct_categories(self, workspace_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT category FROM adrs "
            "WHERE workspace_id = ? AND category != '' ORDER BY category",
            (workspace_id,),
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # ADR helpers
    # ------------------------------------------------------------------

    def _set_adr_tags(self, adr_id: str, tags: List[str]) -> None:
        self._conn.execute("DELETE FROM adr_tags WHERE adr_id = ?", (adr_id,))
        for t in tags:
            tag = t.strip().lower()
            if tag:
                self._conn.execute(
                    "INSERT INTO adr_tags (adr_id, tag) VALUES (?, ?)",
                    (adr_id, tag),
                )

    def _get_adr_tags(self, adr_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT tag FROM adr_tags WHERE adr_id = ? ORDER BY tag",
            (adr_id,),
        ).fetchall()
        return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Engineering Journal
    # ------------------------------------------------------------------

    def create_journal_entry(
        self,
        workspace_id: str,
        repository_id: Optional[str],
        title: str,
        summary: str,
        markdown: str,
        entry_date: str,
        tags: List[str],
    ) -> JournalEntry:
        entry_id = _new_id()
        date = entry_date or _datetime_now_date()
        self._conn.execute(
            "INSERT INTO journal_entries (id, workspace_id, repository_id, "
            "title, summary, markdown, entry_date) VALUES (?,?,?,?,?,?,?)",
            (entry_id, workspace_id, repository_id, title, summary, markdown, date),
        )
        self._set_journal_tags(entry_id, tags)
        self._maybe_commit()
        _log.info("[Workspace Plugin] Journal entry created: id=%s", entry_id)
        return self.get_journal_entry(entry_id)  # type: ignore[return-value]

    def update_journal_entry(
        self,
        entry_id: str,
        *,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        markdown: Optional[str] = None,
        entry_date: Optional[str] = None,
        repository_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> JournalEntry:
        existing = self.get_journal_entry(entry_id)
        if existing is None:
            raise JournalEntryNotFoundError(entry_id)

        sets = ["updated_at = datetime('now')"]
        params: list = []
        for col, val in (
            ("title", title), ("summary", summary), ("markdown", markdown),
            ("entry_date", entry_date), ("repository_id", repository_id),
        ):
            if val is not None:
                sets.append(f"{col} = ?")
                params.append(val)
        params.append(entry_id)
        self._conn.execute(
            f"UPDATE journal_entries SET {', '.join(sets)} WHERE id = ?", params,
        )
        if tags is not None:
            self._set_journal_tags(entry_id, tags)
        self._maybe_commit()
        _log.info("[Workspace Plugin] Journal entry updated: id=%s", entry_id)
        return self.get_journal_entry(entry_id)  # type: ignore[return-value]

    def delete_journal_entry(self, entry_id: str) -> None:
        if self.get_journal_entry(entry_id) is None:
            raise JournalEntryNotFoundError(entry_id)
        self._conn.execute("DELETE FROM journal_entries WHERE id = ?", (entry_id,))
        self._maybe_commit()
        _log.info("[Workspace Plugin] Journal entry deleted: id=%s", entry_id)

    def get_journal_entry(self, entry_id: str) -> Optional[JournalEntry]:
        row = self._conn.execute(
            "SELECT * FROM journal_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return None
        tags = self._get_journal_tags(entry_id)
        return _journal_from_row(row, tags)

    def list_journal_entries(
        self,
        workspace_id: str,
        *,
        repository_id: Optional[str] = None,
        tag: Optional[str] = None,
        entry_date: Optional[str] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[JournalEntry]:
        wheres = ["workspace_id = ?"]
        params: list = [workspace_id]
        if repository_id:
            wheres.append("repository_id = ?")
            params.append(repository_id)
        if entry_date:
            wheres.append("entry_date = ?")
            params.append(entry_date)
        if query:
            wheres.append("(title LIKE ? OR summary LIKE ? OR markdown LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if tag:
            wheres.append(
                "EXISTS (SELECT 1 FROM journal_tags t WHERE t.entry_id = id AND t.tag = ?)"
            )
            params.append(tag)

        sql = (
            "SELECT * FROM journal_entries WHERE " + " AND ".join(wheres) +
            " ORDER BY entry_date DESC, created_at DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, params).fetchall()
        result: List[JournalEntry] = []
        for r in rows:
            tags = self._get_journal_tags(r["id"])
            result.append(_journal_from_row(r, tags))
        return result

    def get_journal_tag_counts(self, workspace_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT t.tag FROM journal_tags t "
            "JOIN journal_entries e ON e.id = t.entry_id "
            "WHERE e.workspace_id = ? ORDER BY t.tag",
            (workspace_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _set_journal_tags(self, entry_id: str, tags: List[str]) -> None:
        self._conn.execute("DELETE FROM journal_tags WHERE entry_id = ?", (entry_id,))
        for t in tags:
            tag = t.strip().lower()
            if tag:
                self._conn.execute(
                    "INSERT INTO journal_tags (entry_id, tag) VALUES (?, ?)",
                    (entry_id, tag),
                )

    def _get_journal_tags(self, entry_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT tag FROM journal_tags WHERE entry_id = ? ORDER BY tag",
            (entry_id,),
        ).fetchall()
        return [r[0] for r in rows]


def _journal_from_row(row: sqlite3.Row, tags: List[str]) -> JournalEntry:
    return JournalEntry(
        id=row["id"],
        workspace_id=row["workspace_id"],
        repository_id=row["repository_id"] or None,
        title=row["title"],
        summary=row["summary"] or "",
        markdown=row["markdown"] or "",
        entry_date=row["entry_date"],
        tags=tags,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _adr_from_row(row: sqlite3.Row, tags: List[str]) -> ADR:
    return ADR(
        id=row["id"],
        workspace_id=row["workspace_id"],
        repository_id=row["repository_id"] or None,
        title=row["title"],
        slug=row["slug"],
        status=row["status"],
        category=row["category"] or "",
        markdown=row["markdown"] or "",
        tags=tags,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
