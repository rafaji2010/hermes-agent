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
    from ..models import ADR, JournalEntry, Repository, Roadmap, RoadmapMilestone, Task, TaskComment, TaskStats, Workspace


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

    @abstractmethod
    def link_project(self, workspace_id: str, project_id: str) -> Workspace:
        """Map a workspace to a Hermes Project.

        Raises ``WorkspaceNotFoundError`` when the workspace is missing
        and ``DuplicateProjectMappingError`` when the project is already
        mapped to a different workspace.
        """
        ...

    @abstractmethod
    def unlink_project(self, workspace_id: str) -> Workspace:
        """Clear the Hermes Project mapping for a workspace."""
        ...

    @abstractmethod
    def get_project_link(self, workspace_id: str) -> Optional[str]:
        """Return the mapped Hermes project id (or ``None``)."""
        ...

    @abstractmethod
    def get_workspace_by_project_id(self, project_id: str) -> Optional[Workspace]:
        """Return the single workspace mapped to a project, if any."""
        ...

    @abstractmethod
    def list_workspaces_by_project_id(self, project_id: str) -> List[Workspace]:
        """Return all workspaces mapped to a project (should be ≤ 1)."""
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
    def find_adr_by_canonical_path(
        self, workspace_id: str, canonical_path: str
    ) -> Optional[ADR]:
        """Return the ADR whose projection points at a canonical file path.

        Canonical paths are project-relative (e.g. ``docs/adr/0001-x.md``).
        """
        ...

    @abstractmethod
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
        """Update the reconciliation projection fields of an ADR.

        Fields left ``None`` are unchanged.  Never touches the canonical
        file — this is a DB-projection-only update.
        """
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


    # ------------------------------------------------------------------
    # Roadmaps
    # ------------------------------------------------------------------

    @abstractmethod
    def create_roadmap(self, workspace_id: str, name: str, description: str) -> Roadmap:
        """Create a roadmap within a workspace."""
        ...

    @abstractmethod
    def update_roadmap(self, roadmap_id: str, *, name: Optional[str] = None, description: Optional[str] = None) -> Roadmap:
        """Update a roadmap. Fields left ``None`` are unchanged."""
        ...

    @abstractmethod
    def delete_roadmap(self, roadmap_id: str) -> None:
        """Delete a roadmap and all its milestones (cascaded)."""
        ...

    @abstractmethod
    def get_roadmap(self, roadmap_id: str) -> Optional[Roadmap]:
        """Return a roadmap with its milestones, progress, and counts."""
        ...

    @abstractmethod
    def list_roadmaps(self, workspace_id: str) -> List[Roadmap]:
        """Return all roadmaps for a workspace, ordered by creation date descending."""
        ...

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    @abstractmethod
    def create_milestone(
        self,
        roadmap_id: str,
        title: str,
        description: str,
        status: str,
        target_date: str,
    ) -> RoadmapMilestone:
        """Create a milestone within a roadmap."""
        ...

    @abstractmethod
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
        """Update a milestone. Fields left ``None`` are unchanged."""
        ...

    @abstractmethod
    def delete_milestone(self, milestone_id: str) -> None:
        """Delete a milestone."""
        ...

    @abstractmethod
    def get_milestone(self, milestone_id: str) -> Optional[RoadmapMilestone]:
        """Return a single milestone or ``None``."""
        ...

    @abstractmethod
    def list_milestones(self, roadmap_id: str) -> List[RoadmapMilestone]:
        """Return all milestones for a roadmap, ordered by sort_order."""
        ...

    @abstractmethod
    def reorder_milestones(self, roadmap_id: str, ordered_ids: List[str]) -> List[RoadmapMilestone]:
        """Reorder milestones by assigning sort_order based on position in the list."""
        ...

    @abstractmethod
    def get_roadmap_counts(self) -> dict:
        """Return aggregate roadmap/milestone counts across all workspaces."""
        ...

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    @abstractmethod
    def create_task(self, *, title: str, description: str, status: str,
                    priority: str, workspace_id: Optional[str] = None,
                    repository_id: Optional[str] = None, roadmap_id: Optional[str] = None,
                    milestone_id: Optional[str] = None, adr_id: Optional[str] = None,
                    journal_id: Optional[str] = None, labels: List[str] | None = None,
                    estimate_hours: Optional[float] = None, actual_hours: Optional[float] = None,
                    due_date: str = "", dependency_ids: List[str] | None = None) -> Task:
        """Create a task with optional links and dependencies."""
        ...

    @abstractmethod
    def update_task(self, task_id: str, **kwargs) -> Task:
        """Update a task. Fields passed as keyword arguments are updated."""
        ...

    @abstractmethod
    def delete_task(self, task_id: str) -> None:
        """Delete a task, its labels, dependencies, and comments (cascaded)."""
        ...

    @abstractmethod
    def get_task(self, task_id: str) -> Optional[Task]:
        """Return a task with labels, dependencies, and comments."""
        ...

    @abstractmethod
    def list_tasks(self, workspace_id: str, *, status: Optional[str] = None,
                   priority: Optional[str] = None, label: Optional[str] = None,
                   repository_id: Optional[str] = None, roadmap_id: Optional[str] = None,
                   milestone_id: Optional[str] = None, adr_id: Optional[str] = None,
                   journal_id: Optional[str] = None, q: Optional[str] = None,
                   overdue: Optional[bool] = None,
                   limit: Optional[int] = None) -> List[Task]:
        """List tasks with optional filters."""
        ...

    # ------------------------------------------------------------------
    # Task Comments
    # ------------------------------------------------------------------

    @abstractmethod
    def add_comment(self, task_id: str, author: str, body: str) -> TaskComment:
        """Add a comment to a task."""
        ...

    @abstractmethod
    def list_comments(self, task_id: str) -> List[TaskComment]:
        """Return all comments for a task, oldest first."""
        ...

    # ------------------------------------------------------------------
    # Task Dependencies
    # ------------------------------------------------------------------

    @abstractmethod
    def set_dependencies(self, task_id: str, depends_on_ids: List[str]) -> None:
        """Replace the dependency list for a task."""
        ...

    @abstractmethod
    def get_dependencies(self, task_id: str) -> tuple:
        """Return ``(dependencies, depends_on)`` as tuple of Task lists."""
        ...

    @abstractmethod
    def get_task_stats(self) -> TaskStats:
        """Return aggregate task statistics."""
        ...
