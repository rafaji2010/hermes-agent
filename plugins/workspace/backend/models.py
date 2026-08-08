"""Domain Models.

Pydantic models for the workspace API — request bodies, responses, and
structured error types.  These are the contract between the REST layer
and the service layer.  The storage implementation is responsible for
converting between database rows and these models.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class WorkspaceCreate(BaseModel):
    """Payload for ``POST /v1/workspaces``."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Unique workspace name.",
        examples=["hermes-agent"],
    )
    path: str = Field(
        default="",
        max_length=1024,
        description="Optional root directory for repo scanning.",
    )


class Workspace(BaseModel):
    """Returned by workspace endpoints."""

    id: str
    name: str
    path: str
    created_at: str
    updated_at: str
    hermes_project_id: Optional[str] = Field(
        default=None,
        description="Optional Hermes Project mapping (projects.db id).",
    )


class WorkspaceList(BaseModel):
    """Wrapper so list endpoints have a consistent envelope."""

    workspaces: List[Workspace]


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class RepositoryRegister(BaseModel):
    """Payload for ``POST /v1/repositories``."""

    workspace_id: str = Field(
        ...,
        description="Workspace to register this repository under.",
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Human-readable repository name.",
    )
    path: str = Field(
        ...,
        max_length=4096,
        description="Absolute path to the repository on disk.",
    )
    git_root: Optional[str] = Field(
        default=None,
        max_length=4096,
        description="Git root directory. Auto-detected if omitted.",
    )
    default_branch: str = Field(
        default="main",
        max_length=256,
        description="Default branch name.",
    )


class Repository(BaseModel):
    """Returned by repository endpoints."""

    id: str
    workspace_id: str
    name: str
    path: str
    git_root: str
    default_branch: str
    created_at: str


class RepositoryList(BaseModel):
    """Wrapper so list endpoints have a consistent envelope."""

    repositories: List[Repository]


# ---------------------------------------------------------------------------
# Status  (GET /v1/health  — enriched for the Workspace Status page)
# ---------------------------------------------------------------------------


class StatusResponse(BaseModel):
    """Comprehensive plugin status returned by ``GET /v1/health``.

    Single endpoint that the desktop Workspace Status page consumes to
    render all dashboard sections without additional round-trips.
    """

    # -- Plugin ---------------------------------------------------------
    plugin: str = "workspace"
    plugin_version: str = "0.1.0"
    status: str = "ok"

    # -- Backend --------------------------------------------------------
    api_version: str = "v1"

    # -- Storage --------------------------------------------------------
    storage_provider: str = "SQLiteStorage"
    database_connected: bool = True
    database_path: str = ""
    transaction_support: bool = True
    nested_transactions: str = "SAVEPOINT"

    # -- Database -------------------------------------------------------
    schema_version: str = "001_initial"
    migration_status: str = "Up to date"
    workspace_count: int = 0
    repository_count: int = 0
    journal_count: int = 0
    roadmap_count: int = 0
    milestone_count: int = 0
    completed_milestone_count: int = 0
    task_count: int = 0
    open_task_count: int = 0
    blocked_task_count: int = 0
    overdue_task_count: int = 0
    graph_entity_count: int = 0
    graph_edge_count: int = 0
    graph_orphan_count: int = 0

    # -- System ---------------------------------------------------------
    hermes_home: str = ""


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    """Structured error returned by the API on failure."""

    error: str
    detail: str
    code: str  # machine-readable error code


class WorkspaceError(Exception):
    """Base exception for all workspace-layer errors."""

    def __init__(self, message: str, code: str = "WORKSPACE_ERROR"):
        super().__init__(message)
        self.code = code


class WorkspaceNotFoundError(WorkspaceError):
    def __init__(self, identifier: str):
        super().__init__(
            f"Workspace not found: {identifier}",
            code="WORKSPACE_NOT_FOUND",
        )


class DuplicateWorkspaceError(WorkspaceError):
    def __init__(self, name: str):
        super().__init__(
            f"Workspace already exists: {name}",
            code="DUPLICATE_WORKSPACE",
        )


class RepositoryNotFoundError(WorkspaceError):
    def __init__(self, identifier: str):
        super().__init__(
            f"Repository not found: {identifier}",
            code="REPOSITORY_NOT_FOUND",
        )


class DuplicateRepositoryError(WorkspaceError):
    def __init__(self, workspace_id: str, path: str):
        super().__init__(
            f"Repository already registered in workspace {workspace_id}: {path}",
            code="DUPLICATE_REPOSITORY",
        )


class InvalidPathError(WorkspaceError):
    def __init__(self, path: str, reason: str = ""):
        msg = f"Invalid path: {path}"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg, code="INVALID_PATH")


class NotAGitRepositoryError(WorkspaceError):
    def __init__(self, path: str):
        super().__init__(
            f"Not a git repository: {path}",
            code="NOT_A_GIT_REPOSITORY",
        )


# ---------------------------------------------------------------------------
# Architecture Decision Records (ADRs)
# ---------------------------------------------------------------------------

VALID_ADR_STATUSES = {"proposed", "accepted", "rejected", "superseded", "deprecated"}

# S7.3A — canonical ADR reconciliation states.
# The canonical authority for ADR CONTENT is the Markdown file inside the
# resolved project repository; workspace.db is an index/projection.
ADR_RECONCILE_STATES = (
    "synced",        # projection matches the canonical file (hash equal)
    "file_new",      # canonical file discovered with no projection row (dry-run report)
    "file_changed",  # canonical file changed since last index (dry-run report)
    "db_legacy",     # DB-only record (no canonical file) — visible, recoverable
    "missing_file",  # projection references a file that no longer exists
    "conflict",      # DB record and canonical file both exist with differing content
    "invalid",       # malformed canonical file (no H1 / bad frontmatter / duplicate identity)
)
ADR_SOURCES = ("workspace_db", "git_file")


class ADRCreate(BaseModel):
    """Payload for ``POST /v1/adrs``."""

    workspace_id: str = Field(..., description="Workspace this ADR belongs to.")
    repository_id: Optional[str] = Field(
        default=None, description="Optional linked repository."
    )
    title: str = Field(..., min_length=1, max_length=256)
    status: str = Field(default="proposed")
    category: str = Field(default="", max_length=128)
    markdown: str = Field(default="")
    tags: List[str] = Field(default_factory=list)


class ADRUpdate(BaseModel):
    """Payload for ``PUT /v1/adrs/{id}``.  All fields are optional."""

    title: Optional[str] = Field(default=None, max_length=256)
    status: Optional[str] = None
    category: Optional[str] = Field(default=None, max_length=128)
    markdown: Optional[str] = None
    tags: Optional[List[str]] = None


class ADR(BaseModel):
    """Returned by ADR endpoints."""

    id: str
    workspace_id: str
    repository_id: Optional[str] = None
    title: str
    slug: str
    status: str
    category: str
    markdown: str = ""
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    # S7.3A — canonical reconciliation projection fields.
    canonical_path: str = Field(
        default="",
        description="Project-relative canonical file path (e.g. docs/adr/0001-x.md).",
    )
    content_hash: str = Field(
        default="", description="SHA-256 of the canonical file bytes."
    )
    reconcile_state: str = Field(
        default="db_legacy",
        description="One of ADR_RECONCILE_STATES (projection vs canonical file).",
    )
    source: str = Field(
        default="workspace_db",
        description="workspace_db (DB-only/legacy) or git_file (canonical).",
    )
    last_indexed: str = Field(
        default="", description="When the projection was last refreshed from the file."
    )
    last_error: str = Field(
        default="", description="Machine-readable reason (malformed/duplicate/…)."
    )


class ADRList(BaseModel):
    """Envelope for ADR list endpoints."""

    adrs: List[ADR]


class ADRError(WorkspaceError):
    """Base exception for ADR-layer errors."""

    def __init__(self, message: str, code: str = "ADR_ERROR"):
        super().__init__(message, code=code)


class ADRNotFoundError(ADRError):
    def __init__(self, identifier: str):
        super().__init__(f"ADR not found: {identifier}", code="ADR_NOT_FOUND")


class DuplicateSlugError(ADRError):
    def __init__(self, slug: str):
        super().__init__(
            f"ADR with slug already exists: {slug}", code="DUPLICATE_SLUG"
        )


class InvalidADRStatusError(ADRError):
    def __init__(self, status: str):
        super().__init__(
            f"Invalid ADR status: {status}. "
            f"Valid: {', '.join(sorted(VALID_ADR_STATUSES))}",
            code="INVALID_ADR_STATUS",
        )


# ---------------------------------------------------------------------------
# S7.3A — Canonical ADR Reconciliation
# ---------------------------------------------------------------------------
# Git/file ADRs are canonical.  These models describe the projection and
# the explicit, previewable operations that move records between the
# DB-only and canonical-file states.


class ADRReconcileError(ADRError):
    """Base error for ADR reconciliation operations."""

    def __init__(self, message: str, code: str = "ADR_RECONCILE_ERROR"):
        super().__init__(message, code=code)


class ADRCanonicalUpdateError(ADRReconcileError):
    """A canonical (git_file) ADR cannot be edited via the DB CRUD path."""

    def __init__(self, adr_id: str):
        super().__init__(
            f"ADR {adr_id} is canonical (git file). Edit the canonical file "
            "via the file endpoint, then reconcile.",
            code="ADR_CANONICAL_UPDATE",
        )


class ADRCanonicalDeleteError(ADRReconcileError):
    """A canonical (git_file) ADR cannot be silently deleted from the DB."""

    def __init__(self, adr_id: str):
        super().__init__(
            f"ADR {adr_id} is canonical (git file). Delete the canonical file "
            "in the repository, then reconcile; the projection will follow.",
            code="ADR_CANONICAL_DELETE",
        )


class ADRMaterializationTargetExistsError(ADRReconcileError):
    """The target canonical file already exists."""

    def __init__(self, path: str):
        super().__init__(
            f"Materialization target already exists: {path}",
            code="MATERIALIZE_TARGET_EXISTS",
        )


class ADRNoRepositoryError(ADRReconcileError):
    """A workspace has no registered repository to reconcile against."""

    def __init__(self, workspace_id: str):
        super().__init__(
            f"Workspace {workspace_id} has no registered repository; "
            "register a repository before ADR reconciliation.",
            code="ADR_NO_REPOSITORY",
        )


class ADRReconcileStatus(BaseModel):
    """Per-ADR reconciliation state (projection vs canonical file)."""

    id: str
    workspace_id: str
    title: str
    slug: str
    status: str
    reconcile_state: str
    source: str
    canonical_path: str = ""
    canonical_id: str = ""
    content_hash: str = ""
    last_indexed: str = ""
    last_error: str = ""
    file_exists: bool = False


class ADRReconcileStatusList(BaseModel):
    """Envelope for reconciliation status list endpoints."""

    statuses: List[ADRReconcileStatus]


class ADRReconcileSummary(BaseModel):
    """Aggregate result of a reconciliation run."""

    workspace_id: str
    project_id: str = ""
    scanned_files: int = 0
    indexed: int = 0            # new canonical files projected this run
    synced: int = 0
    file_changed: int = 0       # refreshed this run (dry-run: would refresh)
    db_legacy: int = 0
    missing_file: int = 0
    conflict: int = 0
    invalid: int = 0
    invalid_paths: List[str] = Field(default_factory=list)
    dry_run: bool = False
    statuses: List[ADRReconcileStatus] = Field(default_factory=list)


class ADRReconcileRequest(BaseModel):
    """Request to run ADR reconciliation for a workspace."""

    workspace_id: str = Field(default="")
    session_id: str = Field(default="")
    dry_run: bool = Field(default=False)


class ADRMaterializeRequest(BaseModel):
    """Request to materialize a legacy DB-only ADR into a canonical file."""

    dry_run: bool = Field(default=True)


class ADRMaterializeResult(BaseModel):
    """Outcome of a legacy ADR materialization (preview or applied)."""

    id: str
    status: str  # preview | materialized | target_exists | no_repository | invalid
    target_path: str = ""
    message: str = ""


class ADRFileUpdateRequest(BaseModel):
    """Request to update the canonical file content of a git_file ADR."""

    markdown: str = Field(..., min_length=1)
    dry_run: bool = Field(default=False)


# ---------------------------------------------------------------------------
# Engineering Journal
# ---------------------------------------------------------------------------


class JournalEntryCreate(BaseModel):
    """Payload for ``POST /v1/journal``."""

    workspace_id: str = Field(..., description="Workspace this entry belongs to.")
    repository_id: Optional[str] = Field(default=None)
    title: str = Field(..., min_length=1, max_length=256)
    summary: str = Field(default="", max_length=512)
    markdown: str = Field(default="")
    entry_date: str = Field(default="")
    tags: List[str] = Field(default_factory=list)


class JournalEntryUpdate(BaseModel):
    """Payload for ``PUT /v1/journal/{id}``.  All fields optional."""

    title: Optional[str] = Field(default=None, max_length=256)
    summary: Optional[str] = Field(default=None, max_length=512)
    markdown: Optional[str] = None
    entry_date: Optional[str] = None
    repository_id: Optional[str] = None
    tags: Optional[List[str]] = None


class JournalEntry(BaseModel):
    """Returned by journal endpoints."""

    id: str
    workspace_id: str
    repository_id: Optional[str] = None
    title: str
    summary: str = ""
    markdown: str = ""
    entry_date: str
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class JournalEntryList(BaseModel):
    """Envelope for journal list endpoints."""

    entries: List[JournalEntry]


class JournalError(WorkspaceError):
    """Base exception for journal-layer errors."""

    def __init__(self, message: str, code: str = "JOURNAL_ERROR"):
        super().__init__(message, code=code)


class JournalEntryNotFoundError(JournalError):
    def __init__(self, identifier: str):
        super().__init__(f"Journal entry not found: {identifier}", code="JOURNAL_ENTRY_NOT_FOUND")


# ---------------------------------------------------------------------------
# Roadmaps & Milestones
# ---------------------------------------------------------------------------

VALID_MILESTONE_STATUSES = {"planned", "in_progress", "blocked", "completed"}


class RoadmapMilestone(BaseModel):
    """A single milestone within a roadmap."""

    id: str
    roadmap_id: str
    title: str
    description: str = ""
    status: str = "planned"
    sort_order: int = 0
    target_date: str = ""
    created_at: str
    updated_at: str


class Roadmap(BaseModel):
    """A roadmap with its milestones."""

    id: str
    workspace_id: str
    name: str
    description: str = ""
    milestones: List[RoadmapMilestone] = Field(default_factory=list)
    progress: float = 0.0
    milestone_count: int = 0
    completed_count: int = 0
    created_at: str
    updated_at: str


class RoadmapCreate(BaseModel):
    """Payload for ``POST /v1/roadmaps``."""

    workspace_id: str = Field(..., description="Workspace this roadmap belongs to.")
    name: str = Field(..., min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)


class RoadmapUpdate(BaseModel):
    """Payload for ``PUT /v1/roadmaps/{id}``. All fields optional."""

    name: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = Field(default=None, max_length=4096)


class MilestoneCreate(BaseModel):
    """Payload for ``POST /v1/roadmaps/{roadmap_id}/milestones``."""

    title: str = Field(..., min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    status: str = Field(default="planned")
    target_date: str = Field(default="", max_length=10)


class MilestoneUpdate(BaseModel):
    """Payload for ``PUT /v1/roadmaps/{roadmap_id}/milestones/{id}``. All fields optional."""

    title: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = Field(default=None, max_length=4096)
    status: Optional[str] = None
    target_date: Optional[str] = Field(default=None, max_length=10)
    sort_order: Optional[int] = None


class MilestoneReorder(BaseModel):
    """Payload for ``PUT /v1/roadmaps/{roadmap_id}/milestones/reorder``."""

    ids: List[str] = Field(..., min_length=1, description="Ordered list of milestone IDs.")


class RoadmapList(BaseModel):
    """Envelope for roadmap list endpoints."""

    roadmaps: List[Roadmap]


class MilestoneList(BaseModel):
    """Envelope for milestone list endpoints."""

    milestones: List[RoadmapMilestone]


class RoadmapError(WorkspaceError):
    """Base exception for roadmap-layer errors."""

    def __init__(self, message: str, code: str = "ROADMAP_ERROR"):
        super().__init__(message, code=code)


class RoadmapNotFoundError(RoadmapError):
    def __init__(self, identifier: str):
        super().__init__(f"Roadmap not found: {identifier}", code="ROADMAP_NOT_FOUND")


class MilestoneNotFoundError(RoadmapError):
    def __init__(self, identifier: str):
        super().__init__(f"Milestone not found: {identifier}", code="MILESTONE_NOT_FOUND")


class InvalidMilestoneStatusError(RoadmapError):
    def __init__(self, status: str):
        super().__init__(
            f"Invalid milestone status: {status}. "
            f"Valid: {', '.join(sorted(VALID_MILESTONE_STATUSES))}",
            code="INVALID_MILESTONE_STATUS",
        )


# ---------------------------------------------------------------------------
# Tasks & Action Management
# ---------------------------------------------------------------------------

VALID_TASK_STATUSES = {"todo", "in_progress", "blocked", "review", "done", "cancelled"}
VALID_TASK_PRIORITIES = {"critical", "high", "medium", "low"}


class TaskComment(BaseModel):
    """A comment on a task."""

    id: str
    task_id: str
    author: str = ""
    body: str
    created_at: str


class TaskDependency(BaseModel):
    """Represents a dependency edge."""

    task_id: str
    depends_on_id: str


class Task(BaseModel):
    """A task with its relationships, labels, and dependencies."""

    id: str
    title: str
    description: str = ""
    status: str = "todo"
    priority: str = "medium"
    workspace_id: Optional[str] = None
    repository_id: Optional[str] = None
    roadmap_id: Optional[str] = None
    milestone_id: Optional[str] = None
    adr_id: Optional[str] = None
    journal_id: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    estimate_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    due_date: str = ""
    completed_at: Optional[str] = None
    dependency_ids: List[str] = Field(default_factory=list)
    depends_on_ids: List[str] = Field(default_factory=list)
    comment_count: int = 0
    is_overdue: bool = False
    created_at: str
    updated_at: str


class TaskCreate(BaseModel):
    """Payload for ``POST /v1/tasks``."""

    title: str = Field(..., min_length=1, max_length=256)
    description: str = Field(default="", max_length=4096)
    status: str = Field(default="todo")
    priority: str = Field(default="medium")
    workspace_id: Optional[str] = None
    repository_id: Optional[str] = None
    roadmap_id: Optional[str] = None
    milestone_id: Optional[str] = None
    adr_id: Optional[str] = None
    journal_id: Optional[str] = None
    labels: List[str] = Field(default_factory=list)
    estimate_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    due_date: str = Field(default="")
    dependency_ids: List[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    """Payload for ``PUT /v1/tasks/{id}``. All fields optional."""

    title: Optional[str] = Field(default=None, max_length=256)
    description: Optional[str] = Field(default=None, max_length=4096)
    status: Optional[str] = None
    priority: Optional[str] = None
    workspace_id: Optional[str] = None
    repository_id: Optional[str] = None
    roadmap_id: Optional[str] = None
    milestone_id: Optional[str] = None
    adr_id: Optional[str] = None
    journal_id: Optional[str] = None
    labels: Optional[List[str]] = None
    estimate_hours: Optional[float] = None
    actual_hours: Optional[float] = None
    due_date: Optional[str] = None
    dependency_ids: Optional[List[str]] = None


class TaskCommentCreate(BaseModel):
    """Payload for ``POST /v1/tasks/{id}/comments``."""

    author: str = Field(default="")
    body: str = Field(..., min_length=1)


class TaskDependencyCreate(BaseModel):
    """Payload for ``PUT /v1/tasks/{id}/dependencies``."""

    depends_on_ids: List[str] = Field(default_factory=list)


class TaskSearchParams(BaseModel):
    """Query params for ``GET /v1/tasks/search``."""

    workspace_id: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    label: Optional[str] = None
    repository_id: Optional[str] = None
    roadmap_id: Optional[str] = None
    milestone_id: Optional[str] = None
    adr_id: Optional[str] = None
    journal_id: Optional[str] = None
    q: Optional[str] = None
    overdue: Optional[bool] = None


class TaskList(BaseModel):
    """Envelope for task list endpoints."""

    tasks: List[Task]


class TaskCommentList(BaseModel):
    """Envelope for comment list endpoints."""

    comments: List[TaskComment]


class TaskDependencyList(BaseModel):
    """Envelope for dependency list endpoints."""

    dependencies: List[Task] = Field(default_factory=list)
    depends_on: List[Task] = Field(default_factory=list)


class TaskStats(BaseModel):
    """Aggregate task statistics."""

    total: int = 0
    open: int = 0
    completed: int = 0
    blocked: int = 0
    overdue: int = 0


class TaskError(WorkspaceError):
    """Base exception for task-layer errors."""

    def __init__(self, message: str, code: str = "TASK_ERROR"):
        super().__init__(message, code=code)


class TaskNotFoundError(TaskError):
    def __init__(self, identifier: str):
        super().__init__(f"Task not found: {identifier}", code="TASK_NOT_FOUND")


class InvalidTaskStatusError(TaskError):
    def __init__(self, status: str):
        super().__init__(
            f"Invalid task status: {status}. "
            f"Valid: {', '.join(sorted(VALID_TASK_STATUSES))}",
            code="INVALID_TASK_STATUS",
        )


class InvalidTaskPriorityError(TaskError):
    def __init__(self, priority: str):
        super().__init__(
            f"Invalid task priority: {priority}. "
            f"Valid: {', '.join(sorted(VALID_TASK_PRIORITIES))}",
            code="INVALID_TASK_PRIORITY",
        )


class CircularDependencyError(TaskError):
    def __init__(self, task_id: str, dep_id: str):
        super().__init__(
            f"Circular dependency detected: task {task_id} cannot depend on {dep_id}",
            code="CIRCULAR_DEPENDENCY",
        )


# ---------------------------------------------------------------------------
# Global Search & Knowledge Graph
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """A single search result across entity types."""

    id: str
    type: str           # workspace, repository, roadmap, milestone, adr, journal, task
    title: str
    description: str = ""
    status: str = ""
    priority: str = ""
    labels: List[str] = Field(default_factory=list)
    workspace_id: Optional[str] = None
    workspace_name: str = ""
    created_at: str = ""
    score: float = 0.0
    # S7.3A — canonical provenance for reconciled entities.
    source_type: str = Field(
        default="",
        description="git_adr (canonical file) / workspace_adr (legacy DB-only) / …",
    )
    canonical_id: str = Field(
        default="", description="Canonical identity (e.g. ADR slug derived from the file)."
    )


class SearchResponse(BaseModel):
    """Envelope for search results."""

    results: List[SearchResult]
    total: int
    query: str = ""
    filters: dict = Field(default_factory=dict)


class RelatedEntity(BaseModel):
    """A lightweight reference to a related entity."""

    id: str
    type: str
    title: str
    status: str = ""
    relationship: str = ""  # "milestone_of", "task_of", "adr_of", etc.


class RelatedItems(BaseModel):
    """All entities related to a given entity."""

    entity_type: str
    entity_id: str
    items: List[RelatedEntity]


class GraphNode(BaseModel):
    """A node in the knowledge graph."""

    id: str
    type: str
    title: str
    status: str = ""


class GraphEdge(BaseModel):
    """An edge between two graph nodes."""

    source_id: str
    source_type: str
    target_id: str
    target_type: str
    relationship: str


class GraphResponse(BaseModel):
    """Response for graph queries."""

    nodes: List[GraphNode]
    edges: List[GraphEdge]


class ShortestPathResponse(BaseModel):
    """Result of a shortest-path query."""

    path: List[GraphNode]
    edges: List[GraphEdge]
    distance: int


class GraphStats(BaseModel):
    """Aggregate graph statistics."""

    total_entities: int = 0
    total_edges: int = 0
    orphan_entities: int = 0
    tasks_without_milestones: int = 0
    milestones_without_tasks: int = 0
    roadmaps_without_milestones: int = 0


# ---------------------------------------------------------------------------
# Analytics & Dashboards
# ---------------------------------------------------------------------------


class MetricCard(BaseModel):
    """A single metric for display."""

    label: str
    value: int | float | str
    unit: str = ""
    trend: str = ""  # "up", "down", "stable"


class RoadmapAnalytics(BaseModel):
    """Roadmap-level analytics."""

    total: int = 0
    active: int = 0
    completed: int = 0
    avg_progress: float = 0.0
    total_milestones: int = 0
    milestones_completed: int = 0
    milestones_in_progress: int = 0
    milestones_blocked: int = 0


class TaskAnalytics(BaseModel):
    """Task-level analytics."""

    total: int = 0
    open: int = 0
    completed: int = 0
    blocked: int = 0
    overdue: int = 0
    by_priority: dict = Field(default_factory=dict)
    by_status: dict = Field(default_factory=dict)


class RepositoryAnalytics(BaseModel):
    """Repository-level analytics."""

    total: int = 0
    active: int = 0
    most_active: str = ""
    most_active_task_count: int = 0


class ADRAnalytics(BaseModel):
    """ADR-level analytics."""

    total: int = 0
    recently_added: int = 0
    by_status: dict = Field(default_factory=dict)


class JournalAnalytics(BaseModel):
    """Journal-level analytics."""

    entries_this_week: int = 0
    entries_this_month: int = 0
    writing_streak_days: int = 0


class TrendPoint(BaseModel):
    """A single data point in a trend."""

    date: str
    value: int


class TrendData(BaseModel):
    """Trend data for a metric over time."""

    metric: str
    points: List[TrendPoint]


class TrendsResponse(BaseModel):
    """All trend data."""

    task_completion: List[TrendPoint] = Field(default_factory=list)
    milestone_completion: List[TrendPoint] = Field(default_factory=list)
    roadmap_progress: List[TrendPoint] = Field(default_factory=list)
    journal_activity: List[TrendPoint] = Field(default_factory=list)
    adr_growth: List[TrendPoint] = Field(default_factory=list)
    period_days: int = 30


class AutoInsight(BaseModel):
    """An automatically generated engineering insight."""

    type: str  # "warning", "info", "success", "danger"
    title: str
    description: str
    entity_type: str = ""
    entity_id: str = ""


class InsightsResponse(BaseModel):
    """All auto-generated insights."""

    insights: List[AutoInsight]


class AnalyticsResponse(BaseModel):
    """Full analytics dashboard payload."""

    roadmaps: RoadmapAnalytics
    tasks: TaskAnalytics
    repositories: RepositoryAnalytics
    adrs: ADRAnalytics
    journal: JournalAnalytics
    graph_entities: int = 0
    graph_edges: int = 0
    graph_orphans: int = 0


class ExportRequest(BaseModel):
    """Request to export analytics in a given format."""

    format: str = Field(default="markdown")  # markdown, json, csv
    sections: List[str] = Field(default_factory=lambda: ["all"])


# ---------------------------------------------------------------------------
# AI Workspace Assistant
# ---------------------------------------------------------------------------


class ReferencedEntity(BaseModel):
    """An entity referenced in an assistant response."""

    id: str
    type: str
    title: str
    status: str = ""
    relevance: str = ""  # why this entity was included


class AssistantContext(BaseModel):
    """Compact context package for a question."""

    question: str
    entities: List[ReferencedEntity]
    analytics_summary: str = ""
    entity_count: int = 0


class ChatMessage(BaseModel):
    """A single message in a conversation."""

    role: str  # "user" or "assistant"
    content: str
    referenced_entities: List[ReferencedEntity] = Field(default_factory=list)


class ChatRequest(BaseModel):
    """Request to the assistant chat endpoint."""

    question: str = Field(..., min_length=1)
    conversation_id: str = Field(default="")
    workspace_id: str = Field(default="")
    session_id: str = Field(default="")


class ChatResponse(BaseModel):
    """Response from the assistant."""

    conversation_id: str
    answer: str
    referenced_entities: List[ReferencedEntity]
    analytics_support: str = ""
    related_items: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    explanation: str = ""


class Suggestion(BaseModel):
    """A recommended action or prompt."""

    type: str  # "task", "prompt", "warning", "tip"
    title: str
    description: str
    entity_id: str = ""
    entity_type: str = ""
    priority: str = "medium"


class SuggestionsResponse(BaseModel):
    """List of recommendations."""

    suggestions: List[Suggestion]


# ---------------------------------------------------------------------------
# Hermes Project Scope
# ---------------------------------------------------------------------------
# The Workspace plugin does NOT own Hermes Projects (those live in the
# per-profile ``projects.db``).  It only keeps a soft, nullable mapping
# (``workspaces.hermes_project_id``) so workspace data can be scoped to
# the Hermes Project authority.


class ProjectLink(BaseModel):
    """A workspace ↔ Hermes project mapping."""

    workspace_id: str
    project_id: Optional[str] = None
    project_slug: Optional[str] = None
    state: str = "unmapped"  # "mapped" | "unmapped"


class ScopeResolveRequest(BaseModel):
    """Request to resolve the current project scope.

    Exactly one identity anchor is expected — a session (``session_id``)
    or an explicit ``workspace_id``.  ``cwd`` may be supplied to override
    the session's recorded working directory (messaging sessions have no
    cwd).
    """

    session_id: str = Field(default="")
    workspace_id: str = Field(default="")
    cwd: str = Field(default="")


class ResolvedProjectScope(BaseModel):
    """Result of a scope resolution.

    ``state`` semantics:
      * ``mapped``     — workspace is linked to a Hermes Project.
      * ``partial``    — a workspace or project was identified, but the
                         link is missing (an explicit mapping is needed).
      * ``unmapped``   — workspace exists but no mapping and no path
                         evidence; backfill may propose one.
      * ``ambiguous``  — more than one candidate Workspace matches the
                         current context (duplicate links); callers MUST
                         fail closed rather than choose.
      * ``unresolved`` — nothing could be identified; callers must NOT
                         fall back to a global scope.

    ``provenance`` records WHY the Workspace was selected (profile home,
    session id, cwd, git root, project id) for explainability — it never
    contains another profile's or workspace's sensitive data.
    """

    workspace_id: str = ""
    workspace_name: str = ""
    project_id: Optional[str] = None
    project_slug: Optional[str] = None
    state: str = "unresolved"
    match_source: str = "none"  # explicit | session_cwd | session_git_root | mapping | none
    matched_path: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)


class ScopeBackfillRequest(BaseModel):
    """Request to backfill a missing workspace ↔ project mapping.

    Always inspectable: ``dry_run=True`` returns a proposal without
    changing anything.
    """

    project_id: str = Field(default="")
    project_slug: str = Field(default="")
    workspace_id: str = Field(default="")
    dry_run: bool = Field(default=True)


class ScopeBackfillResponse(BaseModel):
    """Outcome of a backfill proposal/application."""

    status: str  # "applied" | "proposed" | "already_linked" | "ambiguous" | "not_found"
    workspace_id: str = ""
    project_id: str = ""
    candidates: List[str] = Field(default_factory=list)
    message: str = ""


class ProjectLinkError(WorkspaceError):
    """Base error for workspace ↔ Hermes project mapping operations."""

    def __init__(self, message: str, code: str = "PROJECT_LINK_ERROR"):
        super().__init__(message, code=code)


class ProjectNotFoundError(ProjectLinkError):
    def __init__(self, identifier: str):
        super().__init__(
            f"Hermes project not found: {identifier}",
            code="PROJECT_NOT_FOUND",
        )


class DuplicateProjectMappingError(ProjectLinkError):
    def __init__(self, project_id: str, workspace_id: str):
        super().__init__(
            f"Hermes project {project_id} is already mapped to workspace {workspace_id}",
            code="PROJECT_ALREADY_LINKED",
        )


class AmbiguousProjectMappingError(ProjectLinkError):
    def __init__(self, project_id: str, workspace_ids: List[str]):
        super().__init__(
            f"Hermes project {project_id} maps to multiple workspaces: {', '.join(workspace_ids)}",
            code="AMBIGUOUS_PROJECT_LINK",
        )


class ScopeResolutionError(WorkspaceError):
    def __init__(self, message: str):
        super().__init__(message, code="SCOPE_UNRESOLVED")


class ScopeAmbiguousError(WorkspaceError):
    """Raised when more than one Workspace matches the current context.

    Callers must fail closed — never silently pick a candidate.
    """

    def __init__(self, message: str):
        super().__init__(message, code="SCOPE_AMBIGUOUS")
