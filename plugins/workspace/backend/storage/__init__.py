"""Storage Abstraction Layer.

Defines ``AbstractStorage`` — the single interface every storage backend
must implement.  The WorkspaceService and REST API depend ONLY on this
interface, never on concrete implementations.

Concrete implementations:
    - ``SQLiteStorage`` (``backend/storage/sqlite_storage.py``)
    - Future: ``PostgresStorage``, ``RemoteSyncStorage``, etc.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import TYPE_CHECKING, Generator, List, Optional

if TYPE_CHECKING:
    from ..models import ADR, JournalEntry, Repository, Workspace


class AbstractStorage(ABC):
    """Interface for workspace persistent storage.

    Every method that mutates state MUST be implemented atomically by
    the backend (SQLite uses ``BEGIN IMMEDIATE`` transactions).

    Methods return domain model objects (``Workspace``, ``Repository``).
    The storage implementation is responsible for translating between
    the database representation and these models.
    """

    # ------------------------------------------------------------------
    # Transaction management
    # ------------------------------------------------------------------

    @abstractmethod
    def begin_transaction(self) -> None:
        """Start an explicit transaction.

        Nested calls SHOULD use savepoints so inner transactions can
        roll back independently without aborting the outer unit.
        """
        ...

    @abstractmethod
    def commit(self) -> None:
        """Commit the innermost transaction or savepoint."""
        ...

    @abstractmethod
    def rollback(self) -> None:
        """Roll back the innermost transaction or savepoint."""
        ...

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """Context manager that begins, commits, or rolls back.

        Usage::

            with storage.transaction():
                storage.create_workspace("a", "")
                storage.create_workspace("b", "/tmp")

        On unhandled exception the transaction is rolled back
        automatically; on clean exit it is committed.
        """
        self.begin_transaction()
        try:
            yield
        except Exception:
            self.rollback()
            raise
        else:
            self.commit()

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    @abstractmethod
    def create_workspace(self, name: str, path: str) -> Workspace:
        """Create a workspace.  Raises ``DuplicateWorkspaceError``."""
        ...

    @abstractmethod
    def list_workspaces(self) -> List[Workspace]:
        """Return all workspaces ordered by creation date descending."""
        ...

    @abstractmethod
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Return a single workspace or ``None``."""
        ...

    @abstractmethod
    def get_workspace_by_name(self, name: str) -> Optional[Workspace]:
        """Return a workspace by its unique name or ``None``."""
        ...

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    @abstractmethod
    def register_repository(
        self,
        workspace_id: str,
        name: str,
        path: str,
        git_root: str,
        default_branch: str,
    ) -> Repository:
        """Register a repository under a workspace.

        Raises:
            ``WorkspaceNotFoundError``
            ``DuplicateRepositoryError``
        """
        ...

    @abstractmethod
    def list_repositories(self, workspace_id: str) -> List[Repository]:
        """Return repositories for a workspace, ordered by name."""
        ...

    @abstractmethod
    def get_repository(self, repo_id: str) -> Optional[Repository]:
        """Return a single repository or ``None``."""
        ...

    @abstractmethod
    def get_repository_by_path(
        self, workspace_id: str, path: str
    ) -> Optional[Repository]:
        """Return a repository by its workspace + absolute path."""
        ...

    # ------------------------------------------------------------------
    # Settings (key-value store)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_setting(self, key: str) -> Optional[str]:
        """Read a plugin-scoped setting."""
        ...

    @abstractmethod
    def set_setting(self, key: str, value: str) -> None:
        """Write a plugin-scoped setting (upsert)."""
        ...

    # ------------------------------------------------------------------
    # Architecture Decision Records (ADRs)
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Create an ADR.  Raises ``DuplicateSlugError``."""
        ...

    @abstractmethod
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
        """Update an ADR.  Fields left ``None`` are unchanged."""
        ...

    @abstractmethod
    def delete_adr(self, adr_id: str) -> None:
        """Delete an ADR and its content/tags (cascaded)."""
        ...

    @abstractmethod
    def get_adr(self, adr_id: str) -> Optional[ADR]:
        """Return an ADR with content and tags or ``None``."""
        ...

    @abstractmethod
    def list_adrs(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        query: Optional[str] = None,
    ) -> List[ADR]:
        """Return filtered ADRs for a workspace."""
        ...

    @abstractmethod
    def get_adr_by_slug(self, workspace_id: str, slug: str) -> Optional[ADR]:
        """Return an ADR by workspace + slug."""
        ...

    @abstractmethod
    def get_distinct_categories(self, workspace_id: str) -> List[str]:
        """Return all distinct categories used in a workspace."""
        ...

    # ------------------------------------------------------------------
    # Engineering Journal
    # ------------------------------------------------------------------

    @abstractmethod
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
        """Create a journal entry."""
        ...

    @abstractmethod
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
        """Update a journal entry.  Fields left ``None`` are unchanged."""
        ...

    @abstractmethod
    def delete_journal_entry(self, entry_id: str) -> None:
        """Delete a journal entry and its tags (cascaded)."""
        ...

    @abstractmethod
    def get_journal_entry(self, entry_id: str) -> Optional[JournalEntry]:
        """Return a journal entry with tags or ``None``."""
        ...

    @abstractmethod
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
        """Return filtered journal entries, newest first."""
        ...

    @abstractmethod
    def get_journal_tag_counts(self, workspace_id: str) -> List[str]:
        """Return all distinct tags used in journal entries for a workspace."""
        ...

    @abstractmethod
    def get_distinct_categories(self, workspace_id: str) -> List[str]:
        """Return all distinct categories used in a workspace."""
