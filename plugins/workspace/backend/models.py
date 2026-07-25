"""Domain Models.

Pydantic models for the workspace API — request bodies, responses, and
structured error types.  These are the contract between the REST layer
and the service layer.  The storage implementation is responsible for
converting between database rows and these models.
"""

from __future__ import annotations

from typing import List, Optional

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
