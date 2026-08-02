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
from ..models import ADR, JournalEntry, Repository, Roadmap, RoadmapMilestone, Task, TaskComment, TaskStats, Workspace
from ..models import (
    ADRError,
    ADRNotFoundError,
    CircularDependencyError,
    DuplicateRepositoryError,
    DuplicateProjectMappingError,
    DuplicateSlugError,
    DuplicateWorkspaceError,
    InvalidMilestoneStatusError,
    InvalidTaskPriorityError,
    InvalidTaskStatusError,
    JournalEntryNotFoundError,
    MilestoneNotFoundError,
    ProjectLinkError,
    RoadmapNotFoundError,
    TaskNotFoundError,
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
    project_id = None
    if "hermes_project_id" in row.keys():
        project_id = row["hermes_project_id"] or None
    return Workspace(
        id=row["id"],
        name=row["name"],
        path=row["path"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        hermes_project_id=project_id,
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
    # Hermes Project mapping
    # ------------------------------------------------------------------

    def link_project(self, workspace_id: str, project_id: str) -> Workspace:
        """Map a workspace to a Hermes Project.

        The mapping is a soft link: the project itself lives in the
        per-profile ``projects.db`` and is never owned here.  A project
        may be mapped to at most one workspace.
        """
        if not project_id or not str(project_id).strip():
            raise ProjectLinkError(
                "A Hermes project id is required to link a workspace",
                code="PROJECT_LINK_ERROR",
            )
        if self.get_workspace(workspace_id) is None:
            raise WorkspaceNotFoundError(workspace_id)
        existing = self.get_workspace_by_project_id(project_id)
        if existing is not None and existing.id != workspace_id:
            raise DuplicateProjectMappingError(project_id, existing.id)
        self._conn.execute(
            "UPDATE workspaces SET hermes_project_id = ?, updated_at = datetime('now') "
            "WHERE id = ?",
            (project_id, workspace_id),
        )
        self._maybe_commit()
        _log.info(
            "[Workspace Plugin] Workspace %s linked to Hermes project %s",
            workspace_id,
            project_id,
        )
        return self.get_workspace(workspace_id)  # type: ignore[return-value]

    def unlink_project(self, workspace_id: str) -> Workspace:
        """Clear the Hermes Project mapping for a workspace."""
        if self.get_workspace(workspace_id) is None:
            raise WorkspaceNotFoundError(workspace_id)
        self._conn.execute(
            "UPDATE workspaces SET hermes_project_id = NULL, updated_at = datetime('now') "
            "WHERE id = ?",
            (workspace_id,),
        )
        self._maybe_commit()
        _log.info(
            "[Workspace Plugin] Workspace %s unlinked from Hermes project",
            workspace_id,
        )
        return self.get_workspace(workspace_id)  # type: ignore[return-value]

    def get_project_link(self, workspace_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT hermes_project_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None or row["hermes_project_id"] is None:
            return None
        return row["hermes_project_id"]

    def get_workspace_by_project_id(self, project_id: str) -> Optional[Workspace]:
        row = self._conn.execute(
            "SELECT * FROM workspaces WHERE hermes_project_id = ?",
            (project_id,),
        ).fetchone()
        return _workspace_from_row(row) if row else None

    def list_workspaces_by_project_id(self, project_id: str) -> List[Workspace]:
        rows = self._conn.execute(
            "SELECT * FROM workspaces WHERE hermes_project_id = ? "
            "ORDER BY created_at ASC",
            (project_id,),
        ).fetchall()
        return [_workspace_from_row(r) for r in rows]

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
    # ADR reconciliation projection (S7.3A)
    # ------------------------------------------------------------------
    #
    # The canonical ADR CONTENT lives in Git files.  These methods manage
    # the DB projection bookkeeping only — they never touch the filesystem.

    def find_adr_by_canonical_path(
        self, workspace_id: str, canonical_path: str
    ) -> Optional[ADR]:
        if not canonical_path:
            return None
        row = self._conn.execute(
            "SELECT a.*, ac.markdown FROM adrs a "
            "LEFT JOIN adr_content ac ON ac.adr_id = a.id "
            "WHERE a.workspace_id = ? AND a.canonical_path = ?",
            (workspace_id, canonical_path),
        ).fetchone()
        if row is None:
            return None
        tags = self._get_adr_tags(row["id"])
        return _adr_from_row(row, tags)

    def update_adr_reconcile_meta(
        self,
        adr_id: str,
        *,
        canonical_path: Optional[str] = None,
        content_hash: Optional[str] = None,
        reconcile_state: Optional[str] = None,
        source: Optional[str] = None,
        last_indexed: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> ADR:
        existing = self.get_adr(adr_id)
        if existing is None:
            raise ADRNotFoundError(adr_id)

        set_clauses = ["updated_at = datetime('now')"]
        params: list = []
        for col, val in (
            ("canonical_path", canonical_path),
            ("content_hash", content_hash),
            ("reconcile_state", reconcile_state),
            ("source", source),
            ("last_indexed", last_indexed),
            ("last_error", last_error),
        ):
            if val is not None:
                set_clauses.append(f"{col} = ?")
                params.append(val)

        params.append(adr_id)
        self._conn.execute(
            f"UPDATE adrs SET {', '.join(set_clauses)} WHERE id = ?", params
        )
        self._maybe_commit()
        return self.get_adr(adr_id)  # type: ignore[return-value]

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

    # ------------------------------------------------------------------
    # Roadmaps
    # ------------------------------------------------------------------

    def create_roadmap(self, workspace_id: str, name: str, description: str) -> Roadmap:
        roadmap_id = _new_id()
        self._conn.execute(
            "INSERT INTO roadmaps (id, workspace_id, name, description) "
            "VALUES (?, ?, ?, ?)",
            (roadmap_id, workspace_id, name, description),
        )
        self._maybe_commit()
        _log.info("[Workspace Plugin] Roadmap created: id=%s name=%r", roadmap_id, name)
        return self.get_roadmap(roadmap_id)  # type: ignore[return-value]

    def update_roadmap(self, roadmap_id: str, *, name: Optional[str] = None, description: Optional[str] = None) -> Roadmap:
        existing = self.get_roadmap(roadmap_id)
        if existing is None:
            raise RoadmapNotFoundError(roadmap_id)
        set_clauses = ["updated_at = datetime('now')"]
        params: list = []
        for col, val in (("name", name), ("description", description)):
            if val is not None:
                set_clauses.append(f"{col} = ?")
                params.append(val)
        if set_clauses:
            params.append(roadmap_id)
            self._conn.execute(
                f"UPDATE roadmaps SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        self._maybe_commit()
        _log.info("[Workspace Plugin] Roadmap updated: id=%s", roadmap_id)
        return self.get_roadmap(roadmap_id)  # type: ignore[return-value]

    def delete_roadmap(self, roadmap_id: str) -> None:
        if self.get_roadmap(roadmap_id) is None:
            raise RoadmapNotFoundError(roadmap_id)
        self._conn.execute("DELETE FROM roadmaps WHERE id = ?", (roadmap_id,))
        self._maybe_commit()
        _log.info("[Workspace Plugin] Roadmap deleted: id=%s", roadmap_id)

    def get_roadmap(self, roadmap_id: str) -> Optional[Roadmap]:
        row = self._conn.execute(
            "SELECT * FROM roadmaps WHERE id = ?", (roadmap_id,)
        ).fetchone()
        if row is None:
            return None
        milestones = self.list_milestones(roadmap_id)
        total = len(milestones)
        completed = sum(1 for m in milestones if m.status == "completed")
        progress = (completed / total * 100.0) if total > 0 else 0.0
        return Roadmap(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"] or "",
            milestones=milestones,
            progress=round(progress, 1),
            milestone_count=total,
            completed_count=completed,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_roadmaps(self, workspace_id: str) -> List[Roadmap]:
        rows = self._conn.execute(
            "SELECT * FROM roadmaps WHERE workspace_id = ? ORDER BY created_at DESC, id DESC",
            (workspace_id,),
        ).fetchall()
        result: List[Roadmap] = []
        for row in rows:
            r = self.get_roadmap(row["id"])
            if r:
                result.append(r)
        return result

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    def create_milestone(
        self,
        roadmap_id: str,
        title: str,
        description: str,
        status: str,
        target_date: str,
    ) -> RoadmapMilestone:
        if self.get_roadmap(roadmap_id) is None:
            raise RoadmapNotFoundError(roadmap_id)
        if status not in ("planned", "in_progress", "blocked", "completed"):
            raise InvalidMilestoneStatusError(status)
        max_order_row = self._conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM roadmap_milestones "
            "WHERE roadmap_id = ?",
            (roadmap_id,),
        ).fetchone()
        next_order = max_order_row[0]
        milestone_id = _new_id()
        self._conn.execute(
            "INSERT INTO roadmap_milestones (id, roadmap_id, title, description, "
            "status, sort_order, target_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (milestone_id, roadmap_id, title, description, status, next_order, target_date),
        )
        self._maybe_commit()
        _log.info("[Workspace Plugin] Milestone created: id=%s title=%r", milestone_id, title)
        return self.get_milestone(milestone_id)  # type: ignore[return-value]

    def update_milestone(
        self,
        milestone_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
        target_date: Optional[str] = None,
        sort_order: Optional[int] = None,
    ) -> RoadmapMilestone:
        existing = self.get_milestone(milestone_id)
        if existing is None:
            raise MilestoneNotFoundError(milestone_id)
        if status is not None and status not in ("planned", "in_progress", "blocked", "completed"):
            raise InvalidMilestoneStatusError(status)
        set_clauses = ["updated_at = datetime('now')"]
        params: list = []
        for col, val in (
            ("title", title),
            ("description", description),
            ("status", status),
            ("target_date", target_date),
            ("sort_order", sort_order),
        ):
            if val is not None:
                set_clauses.append(f"{col} = ?")
                params.append(val)
        if set_clauses:
            params.append(milestone_id)
            self._conn.execute(
                f"UPDATE roadmap_milestones SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
        self._maybe_commit()
        _log.info("[Workspace Plugin] Milestone updated: id=%s", milestone_id)
        return self.get_milestone(milestone_id)  # type: ignore[return-value]

    def delete_milestone(self, milestone_id: str) -> None:
        if self.get_milestone(milestone_id) is None:
            raise MilestoneNotFoundError(milestone_id)
        self._conn.execute(
            "DELETE FROM roadmap_milestones WHERE id = ?", (milestone_id,)
        )
        self._maybe_commit()
        _log.info("[Workspace Plugin] Milestone deleted: id=%s", milestone_id)

    def get_milestone(self, milestone_id: str) -> Optional[RoadmapMilestone]:
        row = self._conn.execute(
            "SELECT * FROM roadmap_milestones WHERE id = ?", (milestone_id,)
        ).fetchone()
        return _milestone_from_row(row) if row else None

    def list_milestones(self, roadmap_id: str) -> List[RoadmapMilestone]:
        rows = self._conn.execute(
            "SELECT * FROM roadmap_milestones WHERE roadmap_id = ? ORDER BY sort_order",
            (roadmap_id,),
        ).fetchall()
        return [_milestone_from_row(r) for r in rows]

    def reorder_milestones(self, roadmap_id: str, ordered_ids: List[str]) -> List[RoadmapMilestone]:
        with self.transaction():
            for idx, mid in enumerate(ordered_ids):
                self._conn.execute(
                    "UPDATE roadmap_milestones SET sort_order = ?, updated_at = datetime('now') "
                    "WHERE id = ? AND roadmap_id = ?",
                    (idx, mid, roadmap_id),
                )
        _log.info("[Workspace Plugin] Milestones reordered: roadmap_id=%s", roadmap_id)
        return self.list_milestones(roadmap_id)

    def get_roadmap_counts(self) -> dict:
        total_roadmaps_row = self._conn.execute(
            "SELECT COUNT(*) FROM roadmaps"
        ).fetchone()
        total_milestones_row = self._conn.execute(
            "SELECT COUNT(*) FROM roadmap_milestones"
        ).fetchone()
        completed_milestones_row = self._conn.execute(
            "SELECT COUNT(*) FROM roadmap_milestones WHERE status = 'completed'"
        ).fetchone()
        return {
            "total_roadmaps": total_roadmaps_row[0],
            "total_milestones": total_milestones_row[0],
            "completed_milestones": completed_milestones_row[0],
        }

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, *, title: str, description: str = "", status: str = "todo",
                    priority: str = "medium", workspace_id: Optional[str] = None,
                    repository_id: Optional[str] = None, roadmap_id: Optional[str] = None,
                    milestone_id: Optional[str] = None, adr_id: Optional[str] = None,
                    journal_id: Optional[str] = None, labels: List[str] | None = None,
                    estimate_hours: Optional[float] = None, actual_hours: Optional[float] = None,
                    due_date: str = "", dependency_ids: List[str] | None = None) -> Task:
        if status not in ("todo", "in_progress", "blocked", "review", "done", "cancelled"):
            raise InvalidTaskStatusError(status)
        if priority not in ("critical", "high", "medium", "low"):
            raise InvalidTaskPriorityError(priority)
        completed_at = "datetime('now')" if status == "done" else None
        task_id = _new_id()
        self._conn.execute(
            "INSERT INTO tasks (id, title, description, status, priority, "
            "workspace_id, repository_id, roadmap_id, milestone_id, adr_id, journal_id, "
            "estimate_hours, actual_hours, due_date, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
            + ("datetime('now')" if completed_at else "NULL") + ")",
            (task_id, title, description, status, priority,
             workspace_id, repository_id, roadmap_id, milestone_id, adr_id, journal_id,
             estimate_hours, actual_hours, due_date),
        )
        if labels:
            self._set_task_labels(task_id, labels)
        if dependency_ids:
            self._set_task_dependencies(task_id, dependency_ids)
        self._maybe_commit()
        _log.info("[Workspace Plugin] Task created: id=%s title=%r", task_id, title)
        return self.get_task(task_id)  # type: ignore[return-value]

    def update_task(self, task_id: str, **kwargs) -> Task:
        existing = self.get_task(task_id)
        if existing is None:
            raise TaskNotFoundError(task_id)
        if "status" in kwargs and kwargs["status"] not in ("todo", "in_progress", "blocked", "review", "done", "cancelled"):
            raise InvalidTaskStatusError(kwargs["status"])
        if "priority" in kwargs and kwargs["priority"] not in ("critical", "high", "medium", "low"):
            raise InvalidTaskPriorityError(kwargs["priority"])
        set_clauses = ["updated_at = datetime('now')"]
        params: list = []
        col_map = {
            "title": "title", "description": "description", "status": "status",
            "priority": "priority", "workspace_id": "workspace_id",
            "repository_id": "repository_id", "roadmap_id": "roadmap_id",
            "milestone_id": "milestone_id", "adr_id": "adr_id",
            "journal_id": "journal_id", "estimate_hours": "estimate_hours",
            "actual_hours": "actual_hours", "due_date": "due_date",
        }
        for kw, col in col_map.items():
            if kw in kwargs:
                set_clauses.append(f"{col} = ?")
                params.append(kwargs[kw])
        if "status" in kwargs and kwargs["status"] == "done":
            existing_done = existing.status == "done"
            if not existing_done:
                set_clauses.append("completed_at = datetime('now')")
        elif "status" in kwargs and kwargs["status"] != "done":
            set_clauses.append("completed_at = NULL")
        if set_clauses:
            params.append(task_id)
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = ?", params,
            )
        if "labels" in kwargs and kwargs["labels"] is not None:
            self._set_task_labels(task_id, kwargs["labels"])
        if "dependency_ids" in kwargs and kwargs["dependency_ids"] is not None:
            self._set_task_dependencies(task_id, kwargs["dependency_ids"])
        self._maybe_commit()
        _log.info("[Workspace Plugin] Task updated: id=%s", task_id)
        return self.get_task(task_id)  # type: ignore[return-value]

    def delete_task(self, task_id: str) -> None:
        if self.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self._maybe_commit()
        _log.info("[Workspace Plugin] Task deleted: id=%s", task_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        labels = self._get_task_labels(task_id)
        dep_ids = self._get_task_dependency_ids(task_id)
        depends_on_ids = self._get_task_depends_on_ids(task_id)
        comment_count_row = self._conn.execute(
            "SELECT COUNT(*) FROM task_comments WHERE task_id = ?", (task_id,)
        ).fetchone()
        is_overdue = False
        due = row["due_date"]
        if due and row["status"] != "done" and row["status"] != "cancelled":
            import datetime
            try:
                is_overdue = due < datetime.date.today().isoformat()
            except Exception:
                pass
        return _task_from_row(row, labels, dep_ids, depends_on_ids,
                              comment_count_row[0], is_overdue)

    def list_tasks(self, workspace_id: str = "", *, status: Optional[str] = None,
                   priority: Optional[str] = None, label: Optional[str] = None,
                   repository_id: Optional[str] = None, roadmap_id: Optional[str] = None,
                   milestone_id: Optional[str] = None, adr_id: Optional[str] = None,
                   journal_id: Optional[str] = None, q: Optional[str] = None,
                   overdue: Optional[bool] = None,
                   limit: Optional[int] = None) -> List[Task]:
        wheres: list = ["1=1"]
        params: list = []
        if workspace_id:
            wheres.append("workspace_id = ?")
            params.append(workspace_id)
        if status:
            wheres.append("status = ?")
            params.append(status)
        if priority:
            wheres.append("priority = ?")
            params.append(priority)
        if repository_id:
            wheres.append("repository_id = ?")
            params.append(repository_id)
        if roadmap_id:
            wheres.append("roadmap_id = ?")
            params.append(roadmap_id)
        if milestone_id:
            wheres.append("milestone_id = ?")
            params.append(milestone_id)
        if adr_id:
            wheres.append("adr_id = ?")
            params.append(adr_id)
        if journal_id:
            wheres.append("journal_id = ?")
            params.append(journal_id)
        if q:
            wheres.append("(title LIKE ? OR description LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if label:
            wheres.append("EXISTS (SELECT 1 FROM task_labels tl WHERE tl.task_id = tasks.id AND tl.label = ?)")
            params.append(label)
        if overdue:
            wheres.append("due_date != '' AND due_date < date('now') AND status NOT IN ('done','cancelled')")
        elif overdue is False:
            wheres.append("(due_date = '' OR due_date >= date('now') OR status IN ('done','cancelled'))")
        sql = ("SELECT * FROM tasks WHERE " + " AND ".join(wheres) +
               " ORDER BY priority_order() DESC, created_at DESC")
        if limit:
            sql += f" LIMIT {int(limit)}"
        try:
            rows = self._conn.execute(sql, params).fetchall()
        except Exception:
            sql2 = ("SELECT * FROM tasks WHERE " + " AND ".join(wheres) +
                    " ORDER BY CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 "
                    "WHEN 'medium' THEN 2 ELSE 1 END DESC, created_at DESC")
            if limit:
                sql2 += f" LIMIT {int(limit)}"
            rows = self._conn.execute(sql2, params).fetchall()
        result: List[Task] = []
        for r in rows:
            t = self.get_task(r["id"])
            if t:
                result.append(t)
        return result

    # ------------------------------------------------------------------
    # Task Comments
    # ------------------------------------------------------------------

    def add_comment(self, task_id: str, author: str, body: str) -> TaskComment:
        if self.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        comment_id = _new_id()
        self._conn.execute(
            "INSERT INTO task_comments (id, task_id, author, body) VALUES (?, ?, ?, ?)",
            (comment_id, task_id, author, body),
        )
        self._maybe_commit()
        row = self._conn.execute(
            "SELECT * FROM task_comments WHERE id = ?", (comment_id,)
        ).fetchone()
        return _comment_from_row(row)

    def list_comments(self, task_id: str) -> List[TaskComment]:
        rows = self._conn.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at",
            (task_id,),
        ).fetchall()
        return [_comment_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Task Dependencies
    # ------------------------------------------------------------------

    def set_dependencies(self, task_id: str, depends_on_ids: List[str]) -> None:
        if self.get_task(task_id) is None:
            raise TaskNotFoundError(task_id)
        for dep_id in depends_on_ids:
            if self._detect_circular(task_id, dep_id):
                raise CircularDependencyError(task_id, dep_id)
            if self.get_task(dep_id) is None:
                raise TaskNotFoundError(dep_id)
        self._conn.execute(
            "DELETE FROM task_dependencies WHERE task_id = ?", (task_id,)
        )
        for dep_id in depends_on_ids:
            self._conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_id) "
                "VALUES (?, ?)",
                (task_id, dep_id),
            )
        self._maybe_commit()

    def get_dependencies(self, task_id: str) -> tuple:
        dep_rows = self._conn.execute(
            "SELECT td.task_id AS id FROM task_dependencies td "
            "JOIN tasks t ON t.id = td.task_id WHERE td.depends_on_id = ?",
            (task_id,),
        ).fetchall()
        depends_on_rows = self._conn.execute(
            "SELECT depends_on_id AS id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        deps = [self.get_task(r["id"]) for r in dep_rows]
        depends_on = [self.get_task(r["id"]) for r in depends_on_rows]
        deps = [d for d in deps if d is not None]
        depends_on = [d for d in depends_on if d is not None]
        return deps, depends_on

    def get_task_stats(self, workspace_id: str = "") -> TaskStats:
        if workspace_id:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()[0]
            open_count = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id = ? "
                "AND status NOT IN ('done','cancelled')",
                (workspace_id,),
            ).fetchone()[0]
            completed = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id = ? AND status = 'done'",
                (workspace_id,),
            ).fetchone()[0]
            blocked = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id = ? AND status = 'blocked'",
                (workspace_id,),
            ).fetchone()[0]
            overdue = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE workspace_id = ? "
                "AND due_date != '' AND due_date < date('now') "
                "AND status NOT IN ('done','cancelled')",
                (workspace_id,),
            ).fetchone()[0]
        else:
            total = self._conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            open_count = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done','cancelled')"
            ).fetchone()[0]
            completed = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'done'"
            ).fetchone()[0]
            blocked = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status = 'blocked'"
            ).fetchone()[0]
            overdue = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE due_date != '' AND due_date < date('now') "
                "AND status NOT IN ('done','cancelled')"
            ).fetchone()[0]
        return TaskStats(
            total=total, open=open_count, completed=completed,
            blocked=blocked, overdue=overdue,
        )

    # ------------------------------------------------------------------
    # Task helpers
    # ------------------------------------------------------------------

    def _set_task_labels(self, task_id: str, labels: List[str]) -> None:
        self._conn.execute("DELETE FROM task_labels WHERE task_id = ?", (task_id,))
        for lbl in labels:
            label = lbl.strip().lower()
            if label:
                self._conn.execute(
                    "INSERT OR IGNORE INTO task_labels (task_id, label) VALUES (?, ?)",
                    (task_id, label),
                )

    def _get_task_labels(self, task_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT label FROM task_labels WHERE task_id = ? ORDER BY label",
            (task_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _set_task_dependencies(self, task_id: str, dep_ids: List[str]) -> None:
        for dep_id in dep_ids:
            if self._detect_circular(task_id, dep_id):
                raise CircularDependencyError(task_id, dep_id)
            if self.get_task(dep_id) is None:
                raise TaskNotFoundError(dep_id)
        self._conn.execute("DELETE FROM task_dependencies WHERE task_id = ?", (task_id,))
        for dep_id in dep_ids:
            self._conn.execute(
                "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_id) "
                "VALUES (?, ?)",
                (task_id, dep_id),
            )

    def _get_task_dependency_ids(self, task_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT td.task_id FROM task_dependencies td WHERE td.depends_on_id = ?",
            (task_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _get_task_depends_on_ids(self, task_id: str) -> List[str]:
        rows = self._conn.execute(
            "SELECT depends_on_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _detect_circular(self, task_id: str, dep_id: str) -> bool:
        """Return True if adding task_id -> dep_id would create a cycle."""
        if task_id == dep_id:
            return True
        visited = set()
        stack = [dep_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            if current == task_id:
                return True
            rows = self._conn.execute(
                "SELECT depends_on_id FROM task_dependencies WHERE task_id = ?",
                (current,),
            ).fetchall()
            for r in rows:
                stack.append(r[0])
        return False


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
        canonical_path=row["canonical_path"] or "",
        content_hash=row["content_hash"] or "",
        reconcile_state=row["reconcile_state"] or "db_legacy",
        source=row["source"] or "workspace_db",
        last_indexed=row["last_indexed"] or "",
        last_error=row["last_error"] or "",
    )


def _milestone_from_row(row: sqlite3.Row) -> RoadmapMilestone:
    return RoadmapMilestone(
        id=row["id"],
        roadmap_id=row["roadmap_id"],
        title=row["title"],
        description=row["description"] or "",
        status=row["status"],
        sort_order=row["sort_order"],
        target_date=row["target_date"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _task_from_row(row: sqlite3.Row, labels: List[str], dep_ids: List[str],
                   depends_on_ids: List[str], comment_count: int,
                   is_overdue: bool) -> Task:
    return Task(
        id=row["id"],
        title=row["title"],
        description=row["description"] or "",
        status=row["status"],
        priority=row["priority"],
        workspace_id=row["workspace_id"] or None,
        repository_id=row["repository_id"] or None,
        roadmap_id=row["roadmap_id"] or None,
        milestone_id=row["milestone_id"] or None,
        adr_id=row["adr_id"] or None,
        journal_id=row["journal_id"] or None,
        labels=labels,
        estimate_hours=row["estimate_hours"],
        actual_hours=row["actual_hours"],
        due_date=row["due_date"] or "",
        completed_at=row["completed_at"] or None,
        dependency_ids=dep_ids,
        depends_on_ids=depends_on_ids,
        comment_count=comment_count,
        is_overdue=is_overdue,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _comment_from_row(row: sqlite3.Row) -> TaskComment:
    return TaskComment(
        id=row["id"],
        task_id=row["task_id"],
        author=row["author"] or "",
        body=row["body"],
        created_at=row["created_at"],
    )
