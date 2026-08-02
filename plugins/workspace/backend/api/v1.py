"""Workspace Plugin — v1 REST API.

Endpoints:

    GET  /v1/health         Rich status (plugin, storage, DB, counts)
    GET  /v1/workspaces     List workspaces
    POST /v1/workspaces     Create workspace
    GET  /v1/repositories   List repositories (query: ?workspace_id=)
    POST /v1/repositories   Register repository
    GET  /v1/adrs           List ADRs (query: ?workspace_id=&status=&category=&tag=&q=)
    POST /v1/adrs           Create ADR
    GET  /v1/adrs/{id}      Get ADR
    PUT  /v1/adrs/{id}      Update ADR
    DELETE /v1/adrs/{id}    Delete ADR
    GET  /v1/journal        List journal entries (query: ?workspace_id=&tag=&date=&q=)
    POST /v1/journal        Create journal entry
    GET  /v1/journal/{id}   Get journal entry
    PUT  /v1/journal/{id}   Update journal entry
    DELETE /v1/journal/{id} Delete journal entry
    GET  /v1/roadmaps       List roadmaps (query: ?workspace_id=)
    POST /v1/roadmaps       Create roadmap
    GET  /v1/roadmaps/{id}  Get roadmap with milestones + progress
    PUT  /v1/roadmaps/{id}  Update roadmap
    DELETE /v1/roadmaps/{id} Delete roadmap
    GET  /v1/roadmaps/{id}/milestones  List milestones
    POST /v1/roadmaps/{id}/milestones  Create milestone
    PUT  /v1/roadmaps/{id}/milestones/{mid}  Update milestone
    DELETE /v1/roadmaps/{id}/milestones/{mid}  Delete milestone
    PUT  /v1/roadmaps/{id}/milestones/reorder  Reorder milestones
    GET  /v1/tasks          List tasks (query: ?workspace_id=&status=&priority=&label=...)
    POST /v1/tasks          Create task
    GET  /v1/tasks/{id}     Get task with labels, dependencies, comments
    PUT  /v1/tasks/{id}     Update task
    DELETE /v1/tasks/{id}   Delete task
    GET  /v1/tasks/{id}/comments  List comments
    POST /v1/tasks/{id}/comments  Add comment
    GET  /v1/tasks/{id}/dependencies  Get dependency graph
    PUT  /v1/tasks/{id}/dependencies  Set dependencies
    GET  /v1/tasks/search   Search tasks with full filter support

All responses use a consistent envelope.  Errors return ``ErrorDetail``
with a machine-readable ``code`` field.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from hermes_constants import get_hermes_home  # type: ignore[import-untyped]
from pydantic import BaseModel

from ..models import (
    ADRCanonicalDeleteError,
    ADRCanonicalUpdateError,
    ADRCreate,
    ADRFileUpdateRequest,
    ADRList,
    ADRMaterializeRequest,
    ADRMaterializeResult,
    ADRReconcileError,
    ADRReconcileRequest,
    ADRReconcileStatus,
    ADRReconcileStatusList,
    ADRReconcileSummary,
    ADRUpdate,
    AnalyticsResponse,
    AssistantContext,
    ChatRequest,
    ChatResponse,
    DuplicateProjectMappingError,
    ErrorDetail,
    ExportRequest,
    GraphResponse,
    GraphStats,
    InsightsResponse,
    JournalEntryCreate,
    JournalEntryList,
    JournalEntryUpdate,
    MilestoneCreate,
    MilestoneList,
    MilestoneReorder,
    MilestoneUpdate,
    ProjectLink,
    ProjectLinkError,
    ProjectNotFoundError,
    RelatedItems,
    RepositoryList,
    RepositoryRegister,
    ResolvedProjectScope,
    RoadmapCreate,
    RoadmapList,
    RoadmapUpdate,
    ScopeBackfillRequest,
    ScopeBackfillResponse,
    ScopeResolveRequest,
    ScopeResolutionError,
    SearchResponse,
    ShortestPathResponse,
    StatusResponse,
    SuggestionsResponse,
    TaskCommentCreate,
    TaskCommentList,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyList,
    TaskList,
    TaskSearchParams,
    TaskUpdate,
    TrendsResponse,
    WorkspaceCreate,
    WorkspaceError,
    WorkspaceList,
    WorkspaceNotFoundError,
)
from ..services.adr_reconcile_service import ADRReconcileService
from ..services.adr_service import ADRService
from ..services.analytics_service import AnalyticsService
from ..services.assistant_service import WorkspaceAssistantService
from ..services.graph_service import GraphService
from ..services.journal_service import JournalService
from ..services.roadmap_service import RoadmapService
from ..services.scope_resolver import ProjectScopeResolver
from ..services.search_service import SearchService
from ..services.task_service import TaskService
from ..services.workspace_service import WorkspaceService
from ..security.authorization import AuthorizationMiddleware
from ..security.exceptions import (
    ApprovalRequired,
    AuthorizationDenied,
    PolicyViolation,
    SecurityError,
)
from ..security.resource_limits import ResourceLimitExceeded, ResourceLimiter
from ..security.sandbox import PathSandbox

_log = logging.getLogger("hermes.plugins.workspace.api.v1")

router = APIRouter(prefix="/v1", tags=["workspace-v1"])

# ---------------------------------------------------------------------------
# Profile-scoped runtime (U1D-A)
# ---------------------------------------------------------------------------
#
# Every profile-sensitive component (database, storage, services, security,
# audit) is owned by the WorkspaceRuntime bound to the EFFECTIVE Hermes
# home, resolved at call time.  These thin accessors keep the route layer
# stable while eliminating first-profile pinning.

from ..runtime import get_workspace_runtime  # noqa: E402


def _runtime():
    """Return the Workspace runtime for the current effective home."""
    return get_workspace_runtime()


def _get_authz() -> AuthorizationMiddleware:
    """Return the runtime's authorization middleware."""
    return _runtime().authz


def _get_limits() -> ResourceLimiter:
    """Return the runtime's resource limiter."""
    return _runtime().limits


def _get_sandbox() -> PathSandbox:
    """Return the runtime's path sandbox."""
    return _runtime().sandbox


# ---------------------------------------------------------------------------
# Service accessors (runtime-owned)
# ---------------------------------------------------------------------------


def _service() -> WorkspaceService:
    """Return the runtime's ``WorkspaceService``."""
    return _runtime().workspace_service


def _adr_service() -> ADRService:
    """Return the runtime's ``ADRService``."""
    return _runtime().adr_service


def _adr_reconcile_service() -> ADRReconcileService:
    """Return the runtime's ``ADRReconcileService``."""
    return _runtime().adr_reconcile_service


def _journal_service() -> JournalService:
    """Return the runtime's ``JournalService``."""
    return _runtime().journal_service


def _roadmap_service() -> RoadmapService:
    """Return the runtime's ``RoadmapService``."""
    return _runtime().roadmap_service


def _task_service() -> TaskService:
    """Return the runtime's ``TaskService``."""
    return _runtime().task_service


def _search_service() -> SearchService:
    """Return the runtime's ``SearchService``."""
    return _runtime().search_service


def _graph_service() -> GraphService:
    """Return the runtime's ``GraphService``."""
    return _runtime().graph_service


def _analytics_service() -> AnalyticsService:
    """Return the runtime's ``AnalyticsService``."""
    return _runtime().analytics_service


def _assistant_service() -> WorkspaceAssistantService:
    """Return the runtime's ``WorkspaceAssistantService``."""
    return _runtime().assistant_service


def _scope_resolver() -> ProjectScopeResolver:
    """Return the runtime's ``ProjectScopeResolver``.

    Tests inject a fake-backed resolver onto the active runtime.
    """
    return _runtime().scope_resolver


# ---------------------------------------------------------------------------
# Scope enforcement helpers
# ---------------------------------------------------------------------------
#
# These helpers implement the S7.2 authority boundary: an empty scope
# NEVER falls back to a global query.  When the caller supplies no
# workspace, the ProjectScopeResolver is consulted (session/cwd/path
# evidence); if nothing resolves, the request is rejected.


def _enforce_scope(
    workspace_id: str = "",
    session_id: str = "",
    cwd: str = "",
) -> str:
    """Return the effective workspace scope for a request.

    An explicit ``workspace_id`` passes through unchanged.  Otherwise the
    scope resolver is consulted; an unresolvable scope raises
    ``ScopeResolutionError`` (HTTP 403) instead of widening to global.
    """
    if workspace_id:
        return workspace_id
    resolved = _scope_resolver().resolve(
        ScopeResolveRequest(session_id=session_id, cwd=cwd)
    )
    if resolved.workspace_id:
        return resolved.workspace_id
    raise ScopeResolutionError(
        "Scope could not be resolved for this request (no workspace, "
        "session, or path evidence). Refusing to fall back to global scope."
    )


def _require_scope(
    workspace_id: str = "",
    session_id: str = "",
    cwd: str = "",
) -> str:
    """Enforce scope AND the ``workspace.scope.read`` capability.

    Raises ``HTTPException(403, SCOPE_UNRESOLVED)`` when the scope cannot
    be resolved — callers must NOT fall back to a global scope.  An
    authorization denial from the capability guard is translated to 403.
    """
    try:
        _get_authz().guard(
            "workspace.scope.read",
            resource_type="workspace",
            resource_id=workspace_id or "resolve",
            details={"session_id": session_id},
        )
    except AuthorizationDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=ErrorDetail(
                error="Authorization denied",
                detail=str(exc),
                code="AUTHORIZATION_DENIED",
            ).model_dump(),
        ) from exc
    try:
        return _enforce_scope(workspace_id, session_id, cwd)
    except ScopeResolutionError as exc:
        raise HTTPException(
            status_code=403,
            detail=ErrorDetail(
                error="Scope unresolved",
                detail=str(exc),
                code=exc.code,
            ).model_dump(),
        ) from exc
    except WorkspaceNotFoundError as exc:
        raise _error(404, exc) from exc


def _guard_membership(entity_workspace_id: str, workspace_id: str) -> None:
    """Reject access when an entity belongs to a different workspace.

    Returns 404 (not 403) so cross-workspace lookups cannot distinguish
    "exists elsewhere" from "does not exist" — no existence leak.
    """
    if entity_workspace_id and workspace_id and entity_workspace_id != workspace_id:
        raise WorkspaceNotFoundError(workspace_id)


def _guard_reassignment(current_task, payload) -> None:
    """Prevent task reassignment across Hermes project scopes.

    A task may move between workspaces only when both sides belong to
    the same project (or either side is unmapped — nothing to verify).
    """
    new_ws_id = ""
    if payload.workspace_id is not None:
        new_ws_id = str(payload.workspace_id).strip()
    cur_ws_id = str(getattr(current_task, "workspace_id", "") or "")
    if not new_ws_id or new_ws_id == cur_ws_id:
        return
    cur_project = _service().get_project_link(cur_ws_id) if cur_ws_id else None
    new_project = _service().get_project_link(new_ws_id) if new_ws_id else None
    if cur_project and new_project and cur_project != new_project:
        raise ProjectLinkError(
            f"Cannot reassign task across project scopes "
            f"({cur_project} -> {new_project})",
            code="CROSS_PROJECT_REASSIGNMENT",
        )


def _validate_task_refs(workspace_id: str, payload) -> None:
    """Every entity a task references must belong to the effective scope.

    U1D-C: indirect cross-workspace traversal through task references is
    rejected with 404 (no existence leak) before the task is created.
    """
    if payload.repository_id:
        repo = _service().get_repository(payload.repository_id)
        _guard_membership(repo.workspace_id if repo else "", workspace_id)
    if payload.roadmap_id:
        roadmap = _roadmap_service().get_roadmap(payload.roadmap_id)
        _guard_membership(roadmap.workspace_id, workspace_id)
    if payload.milestone_id:
        ms = _runtime().storage.get_milestone(payload.milestone_id)
        if ms is None:
            raise WorkspaceNotFoundError(payload.milestone_id)
        roadmap = _roadmap_service().get_roadmap(ms.roadmap_id)
        _guard_membership(roadmap.workspace_id, workspace_id)
    if payload.adr_id:
        adr = _adr_service().get_adr(payload.adr_id)
        _guard_membership(adr.workspace_id, workspace_id)
    if payload.journal_id:
        je = _journal_service().get_entry(payload.journal_id)
        _guard_membership(je.workspace_id, workspace_id)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def _error(status: int, exc: WorkspaceError) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail=ErrorDetail(
            error=exc.__class__.__name__,
            detail=str(exc),
            code=exc.code,
        ).model_dump(),
    )


def _error_detail(status: int, error: str, detail: str, code: str) -> HTTPException:
    """Build a structured HTTP error payload."""
    return HTTPException(
        status_code=status,
        detail=ErrorDetail(
            error=error,
            detail=detail,
            code=code,
        ).model_dump(),
    )


def _api_error(exc: Exception) -> HTTPException:
    """Translate domain/security/conflict errors into HTTP responses.

    U1D-C: one narrow translation boundary so resource-membership and
    capability enforcement produce predictable, non-leaky responses.
    Internal/unknown failures become a logged 500 without internal detail.
    """
    if isinstance(exc, WorkspaceError):
        code = getattr(exc, "code", "") or ""
        status = 404
        if code in (
            "INVALID_TASK_STATUS",
            "INVALID_TASK_PRIORITY",
            "CIRCULAR_DEPENDENCY",
            "INVALID_MILESTONE_STATUS",
            "CROSS_PROJECT_REASSIGNMENT",
            "EMPTY_TITLE",
            "INVALID_ADR_STATUS",
        ):
            status = 400
        if code in (
            "DUPLICATE_SLUG",
            "ADR_CANONICAL_UPDATE",
            "ADR_CANONICAL_DELETE",
        ):
            status = 409
        return _error(status, exc)
    if isinstance(exc, AuthorizationDenied):
        return _error_detail(403, "Authorization denied", str(exc), "AUTHORIZATION_DENIED")
    if isinstance(exc, ApprovalRequired):
        return _error_detail(403, "Approval required", str(exc), "APPROVAL_REQUIRED")
    if isinstance(exc, PolicyViolation):
        return _error_detail(403, "Policy violation", str(exc), "POLICY_VIOLATION")
    if isinstance(exc, ResourceLimitExceeded):
        return _error_detail(413, "Resource limit exceeded", str(exc), "RESOURCE_LIMIT_EXCEEDED")
    if isinstance(exc, sqlite3.IntegrityError):
        return _error_detail(409, "Integrity conflict", str(exc), "INTEGRITY_CONFLICT")
    if isinstance(exc, SecurityError):
        return _error_detail(403, "Security error", str(exc), "SECURITY_ERROR")
    _log.exception("Unhandled Workspace API error", exc_info=exc)
    return _error_detail(500, "Internal error", "Internal server error.", "INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# Health / Status
# ---------------------------------------------------------------------------


def _display_path(p: str) -> str:
    """Human-readable path with ``~`` for the home directory."""
    try:
        home = os.path.expanduser("~")
        return p.replace(home, "~") if p.startswith(home) else p
    except Exception:
        return p


def _build_status() -> StatusResponse:
    """Assemble the enriched status payload.

    Querying the database here is intentional — the desktop Status page
    calls this endpoint on every open and on manual refresh.  It is NOT
    called on the hot path (agent turns, streaming).
    """
    db = _runtime().database
    try:
        db.get_connection()
    except Exception:
        _log.exception("Failed to initialize database for status check")
    db_connected = db.is_initialised
    db_path = _display_path(str(db.db_path))

    # Migration status
    latest_version = ""
    migration_status = "Unknown"
    try:
        if db_connected:
            conn = db.get_connection()
            row = conn.execute(
                "SELECT version, description FROM _migrations "
                "ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if row:
                latest_version = f"{int(row['version']):03d}_{row['description']}"
                migration_status = "Up to date"
            else:
                migration_status = "No migrations applied"
            ws_count = conn.execute(
                "SELECT COUNT(*) FROM workspaces"
            ).fetchone()[0]
            repo_count = conn.execute(
                "SELECT COUNT(*) FROM repositories"
            ).fetchone()[0]
            journal_count = conn.execute(
                "SELECT COUNT(*) FROM journal_entries"
            ).fetchone()[0]
            # Roadmap counts may fail if migration 004 hasn't run yet
            try:
                roadmap_count = conn.execute(
                    "SELECT COUNT(*) FROM roadmaps"
                ).fetchone()[0]
                milestone_count = conn.execute(
                    "SELECT COUNT(*) FROM roadmap_milestones"
                ).fetchone()[0]
                completed_milestone_count = conn.execute(
                    "SELECT COUNT(*) FROM roadmap_milestones WHERE status = 'completed'"
                ).fetchone()[0]
            except Exception:
                roadmap_count = 0
                milestone_count = 0
                completed_milestone_count = 0
            # Task counts may fail if migration 005 hasn't run yet
            try:
                task_count = conn.execute(
                    "SELECT COUNT(*) FROM tasks"
                ).fetchone()[0]
                open_task_count = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status NOT IN ('done','cancelled')"
                ).fetchone()[0]
                blocked_task_count = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'blocked'"
                ).fetchone()[0]
                overdue_task_count = conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE due_date != '' AND due_date < date('now') "
                    "AND status NOT IN ('done','cancelled')"
                ).fetchone()[0]
            except Exception:
                task_count = 0
                open_task_count = 0
                blocked_task_count = 0
                overdue_task_count = 0
            # Graph stats — entity counts from existing queries
            try:
                adr_count = conn.execute("SELECT COUNT(*) FROM adrs").fetchone()[0]
                je_count = conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0]
                graph_entity_count = (
                    ws_count + repo_count + roadmap_count + milestone_count +
                    adr_count + je_count + task_count
                )
                dep_edge_count = conn.execute(
                    "SELECT COUNT(*) FROM task_dependencies"
                ).fetchone()[0]
                graph_edge_count = (
                    repo_count + roadmap_count + milestone_count +
                    adr_count + je_count + task_count + dep_edge_count
                )
                graph_orphan_count = 0
            except Exception:
                graph_entity_count = 0
                graph_edge_count = 0
                graph_orphan_count = 0
        else:
            ws_count = 0
            repo_count = 0
            journal_count = 0
            roadmap_count = 0
            milestone_count = 0
            completed_milestone_count = 0
            task_count = 0
            open_task_count = 0
            blocked_task_count = 0
            overdue_task_count = 0
            graph_entity_count = 0
            graph_edge_count = 0
            graph_orphan_count = 0
    except Exception:
        latest_version = "unknown"
        migration_status = "Error"
        ws_count = 0
        repo_count = 0
        journal_count = 0
        roadmap_count = 0
        milestone_count = 0
        completed_milestone_count = 0
        task_count = 0
        open_task_count = 0
        blocked_task_count = 0
        overdue_task_count = 0
        graph_entity_count = 0
        graph_edge_count = 0
        graph_orphan_count = 0

    hermes_home = _display_path(str(get_hermes_home()))

    return StatusResponse(
        status="ok",
        plugin="workspace",
        plugin_version="0.1.0",
        api_version="v1",
        storage_provider="SQLiteStorage",
        database_connected=db_connected,
        database_path=db_path,
        transaction_support=True,
        nested_transactions="SAVEPOINT",
        schema_version=latest_version or "001_initial",
        migration_status=migration_status,
        workspace_count=ws_count,
        repository_count=repo_count,
        journal_count=journal_count,
        roadmap_count=roadmap_count,
        milestone_count=milestone_count,
        completed_milestone_count=completed_milestone_count,
        task_count=task_count,
        open_task_count=open_task_count,
        blocked_task_count=blocked_task_count,
        overdue_task_count=overdue_task_count,
        graph_entity_count=graph_entity_count,
        graph_edge_count=graph_edge_count,
        graph_orphan_count=graph_orphan_count,
        hermes_home=hermes_home,
    )


@router.get("/health", response_model=StatusResponse)
def health():
    """Return comprehensive plugin status.

    Consumed by the desktop Workspace Status page on every open and
    on manual refresh.  Aggregates plugin, storage, database, and
    system information in a single round-trip.
    """
    return _build_status()


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


@router.get("/workspaces", response_model=WorkspaceList)
def list_workspaces():
    """Return all workspaces ordered by creation date descending."""
    workspaces = _service().list_workspaces()
    return WorkspaceList(workspaces=workspaces)


@router.post(
    "/workspaces",
    response_model=WorkspaceList,
    status_code=201,
)
def create_workspace(payload: WorkspaceCreate):
    """Create a new workspace.

    Returns ``409`` when a workspace with the same name already exists.
    """
    try:
        ws = _service().create_workspace(payload)
    except WorkspaceError as exc:
        status = 409 if "already exists" in str(exc).lower() else 400
        raise _error(status, exc) from exc
    return WorkspaceList(workspaces=[ws])


# ---------------------------------------------------------------------------
# Hermes Project mapping
# ---------------------------------------------------------------------------
#
# A workspace may be softly linked to at most one Hermes Project
# (``projects.db``).  The link is validated against the project store
# and is the authority boundary for workspace-scoped queries.


class _ProjectLinkPayload(BaseModel):
    project_id: str


@router.get("/workspaces/{workspace_id}/project", response_model=ProjectLink)
def get_workspace_project(workspace_id: str):
    """Return the Hermes Project mapping for a workspace."""
    try:
        _get_authz().guard(
            "workspace.scope.read",
            resource_type="workspace",
            resource_id=workspace_id,
        )
        project_id = _service().get_project_link(workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    except Exception as exc:
        from ..security.exceptions import AuthorizationDenied

        if isinstance(exc, AuthorizationDenied):
            raise _error(403, exc) from exc
        raise
    slug = _scope_resolver()._project_slug(project_id) if project_id else None
    return ProjectLink(
        workspace_id=workspace_id,
        project_id=project_id,
        project_slug=slug,
        state="mapped" if project_id else "unmapped",
    )


@router.put("/workspaces/{workspace_id}/project", response_model=ProjectLink)
def link_workspace_project(workspace_id: str, payload: _ProjectLinkPayload):
    """Link a workspace to a Hermes Project.

    ``404`` — workspace missing.  ``404`` (PROJECT_NOT_FOUND) — the
    project does not exist in ``projects.db``.  ``409`` — the project is
    already mapped to a different workspace.
    """
    project_id = (payload.project_id or "").strip()
    try:
        _get_authz().guard(
            "workspace.scope.link",
            resource_type="workspace",
            resource_id=workspace_id,
            details={"project_id": project_id},
        )
        # Validate the project exists in the Hermes project store.
        if not _scope_resolver()._project_id_exists(project_id):
            raise ProjectNotFoundError(project_id)
        updated = _service().link_project(workspace_id, project_id)
    except (WorkspaceNotFoundError, ProjectNotFoundError) as exc:
        raise _error(404, exc) from exc
    except DuplicateProjectMappingError as exc:
        raise _error(409, exc) from exc
    except ProjectLinkError as exc:
        raise _error(400, exc) from exc
    except Exception as exc:
        from ..security.exceptions import AuthorizationDenied, ApprovalRequired

        if isinstance(exc, (AuthorizationDenied, ApprovalRequired)):
            raise _error(403, exc) from exc
        raise
    slug = _scope_resolver()._project_slug(project_id)
    return ProjectLink(
        workspace_id=workspace_id,
        project_id=project_id,
        project_slug=slug,
        state="mapped",
    )


@router.delete("/workspaces/{workspace_id}/project", response_model=ProjectLink)
def unlink_workspace_project(workspace_id: str):
    """Clear the Hermes Project mapping for a workspace."""
    try:
        _get_authz().guard(
            "workspace.scope.link",
            resource_type="workspace",
            resource_id=workspace_id,
        )
        updated = _service().unlink_project(workspace_id)
    except WorkspaceNotFoundError as exc:
        raise _error(404, exc) from exc
    except Exception as exc:
        from ..security.exceptions import AuthorizationDenied, ApprovalRequired

        if isinstance(exc, (AuthorizationDenied, ApprovalRequired)):
            raise _error(403, exc) from exc
        raise
    return ProjectLink(
        workspace_id=workspace_id,
        project_id=None,
        project_slug=None,
        state="unmapped",
    )


# ---------------------------------------------------------------------------
# Project scope resolution & backfill
# ---------------------------------------------------------------------------


@router.post("/scope/resolve", response_model=ResolvedProjectScope)
def resolve_scope(payload: ScopeResolveRequest):
    """Resolve the Hermes Project scope for a session/workspace/cwd.

    Diagnostic endpoint: returns the full state (including ``partial``
    and ``unresolved``) so callers can propose a mapping.  Strict
    enforcement (403 on unresolved) applies to the query endpoints via
    ``_require_scope``, not here.
    """
    _get_authz().guard(
        "workspace.scope.read",
        resource_type="workspace",
        resource_id=payload.workspace_id or "resolve",
        details={"session_id": payload.session_id},
    )
    try:
        return _scope_resolver().resolve(payload)
    except WorkspaceNotFoundError as exc:
        raise _error(404, exc) from exc


@router.post("/scope/backfill", response_model=ScopeBackfillResponse)
def backfill_scope(payload: ScopeBackfillRequest):
    """Propose or apply a workspace ↔ project mapping.

    Backfill is always ambiguity-aware and inspection-first:
      * ``dry_run=True`` (default) — returns a proposal, changes nothing.
      * 0 workspaces mapped → proposal/apply the link.
      * 1 workspace mapped → ``already_linked``, no change.
      * >1 workspaces mapped → ``ambiguous``, no change.
    """
    project_id = (payload.project_id or "").strip()
    workspace_id = (payload.workspace_id or "").strip()
    try:
        _get_authz().guard(
            "workspace.scope.link",
            resource_type="workspace",
            resource_id=workspace_id or "resolve",
            details={"project_id": project_id, "dry_run": payload.dry_run},
        )
        if project_id:
            if not _scope_resolver()._project_id_exists(project_id):
                raise ProjectNotFoundError(project_id)
        linked = _service().list_workspaces_by_project_id(project_id)
    except ProjectNotFoundError as exc:
        raise _error(404, exc) from exc
    except Exception as exc:
        from ..security.exceptions import AuthorizationDenied, ApprovalRequired

        if isinstance(exc, (AuthorizationDenied, ApprovalRequired)):
            raise _error(403, exc) from exc
        raise

    if len(linked) > 1:
        return ScopeBackfillResponse(
            status="ambiguous",
            project_id=project_id,
            candidates=[w.id for w in linked],
            message="Project is mapped to multiple workspaces; refusing to auto-link.",
        )
    if len(linked) == 1:
        return ScopeBackfillResponse(
            status="already_linked",
            workspace_id=linked[0].id,
            project_id=project_id,
            message=f"Project already linked to workspace {linked[0].id}.",
        )
    if not workspace_id:
        return ScopeBackfillResponse(
            status="proposed",
            project_id=project_id,
            message="No workspace mapped; supply workspace_id to apply the link.",
        )
    if payload.dry_run:
        return ScopeBackfillResponse(
            status="proposed",
            workspace_id=workspace_id,
            project_id=project_id,
            message="Proposed link — dry_run, no change made.",
        )
    try:
        updated = _service().link_project(workspace_id, project_id)
    except WorkspaceNotFoundError as exc:
        raise _error(404, exc) from exc
    except DuplicateProjectMappingError as exc:
        raise _error(409, exc) from exc
    return ScopeBackfillResponse(
        status="applied",
        workspace_id=updated.id,
        project_id=project_id,
        message=f"Workspace {updated.id} linked to project {project_id}.",
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


@router.get("/repositories", response_model=RepositoryList)
def list_repositories(
    workspace_id: str = Query(
        ...,
        description="Workspace ID to list repositories for.",
    ),
):
    """Return repositories registered under a workspace.

    Returns ``404`` when the workspace does not exist.
    """
    try:
        repos = _service().list_repositories(workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return RepositoryList(repositories=repos)


@router.post(
    "/repositories",
    response_model=RepositoryList,
    status_code=201,
)
def register_repository(payload: RepositoryRegister):
    """Register a repository under a workspace.

    Auto-detects the git root if ``git_root`` is omitted.
    Validates that the path exists, is a directory, and contains a git
    repository.

    Returns:
        ``400`` — path does not exist, is not a directory, or is not a
            git repository.
        ``404`` — workspace not found.
        ``409`` — repository with the same path already registered.
    """
    try:
        repo = _service().register_repository(payload)
    except WorkspaceError as exc:
        code = exc.code
        if code == "WORKSPACE_NOT_FOUND":
            status = 404
        elif code in ("DUPLICATE_REPOSITORY",):
            status = 409
        elif code == "NOT_A_GIT_REPOSITORY":
            status = 422
        else:
            status = 400
        raise _error(status, exc) from exc
    return RepositoryList(repositories=[repo])


# ---------------------------------------------------------------------------
# Architecture Decision Records (ADRs)
# ---------------------------------------------------------------------------


@router.get("/adrs", response_model=ADRList)
def list_adrs(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
    status: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Search title + body."),
):
    """List ADRs with optional filters — never wider than the effective
    Workspace scope."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        adrs = _adr_service().list_adrs(
            workspace_id,
            status=status,
            category=category,
            tag=tag,
            query=q,
        )
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return ADRList(adrs=adrs)


# ---------------------------------------------------------------------------
# ADR reconciliation (S7.3A)
# ---------------------------------------------------------------------------
#
# Canonical ADR CONTENT lives in Git files under the resolved project
# repository (docs/adr/).  These endpoints manage the DB projection and
# the explicit, previewable file operations.  All operations resolve the
# S7.2 project/workspace scope first and never fall back to global.


@router.post("/adrs/reconcile", response_model=ADRReconcileSummary)
def reconcile_adrs(payload: ADRReconcileRequest):
    """Run (or preview) ADR reconciliation for a workspace.

    ``dry_run`` previews every transition without writing.  Real mode
    indexes new canonical files and refreshes stale projections (files
    win); conflicts and legacy DB-only ADRs stay visible.
    """
    workspace_id = _require_scope(payload.workspace_id, payload.session_id)
    capability = "adr.reconcile.read" if payload.dry_run else "adr.reconcile.write"
    _get_authz().guard(
        capability, resource_type="workspace", resource_id=workspace_id,
        details={"dry_run": payload.dry_run, "session_id": payload.session_id},
    )
    try:
        return _adr_reconcile_service().reconcile(
            workspace_id, dry_run=payload.dry_run, session_id=payload.session_id
        )
    except WorkspaceError as exc:
        raise _error(404, exc) from exc


@router.get("/adrs/reconcile/status", response_model=ADRReconcileStatusList)
def adr_reconcile_status(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Return live reconciliation status for every ADR in a workspace."""
    workspace_id = _require_scope(workspace_id, session_id)
    _get_authz().guard(
        "adr.reconcile.read", resource_type="workspace", resource_id=workspace_id,
        details={"session_id": session_id},
    )
    try:
        statuses = _adr_reconcile_service().status(workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return ADRReconcileStatusList(statuses=statuses)


@router.post("/adrs/{adr_id}/materialize", response_model=ADRMaterializeResult)
def materialize_adr(
    adr_id: str,
    payload: ADRMaterializeRequest,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Materialize a legacy DB-only ADR into a canonical file.

    ``dry_run`` (default) returns a preview.  Real mode writes the file
    atomically (with frontmatter + provenance), then promotes the DB
    record to the ``git_file`` projection.
    """
    try:
        adr = _adr_service().get_adr(adr_id)
        resolved_ws = _require_scope(workspace_id or adr.workspace_id, session_id)
        _guard_membership(adr.workspace_id, resolved_ws)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    capability = "adr.reconcile.read" if payload.dry_run else "adr.reconcile.write"
    _get_authz().guard(
        capability, resource_type="adr", resource_id=adr_id,
        details={"dry_run": payload.dry_run, "session_id": session_id},
    )
    try:
        result = _adr_reconcile_service().materialize(
            adr_id, dry_run=payload.dry_run, session_id=session_id
        )
    except ADRReconcileError as exc:
        code = exc.code
        status = 409 if code in ("ADR_ALREADY_CANONICAL", "MATERIALIZE_TARGET_EXISTS") else 400
        raise _error(status, exc) from exc
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    if result.status == "target_exists":
        raise HTTPException(
            status_code=409,
            detail=ErrorDetail(
                error="Materialization target exists",
                detail=result.message,
                code="MATERIALIZE_TARGET_EXISTS",
            ).model_dump(),
        )
    return result


@router.put("/adrs/{adr_id}/file", response_model=ADRMaterializeResult)
def update_adr_file(
    adr_id: str,
    payload: ADRFileUpdateRequest,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update the canonical file content of a git_file ADR.

    Writes the file atomically, then refreshes the projection.  Legacy
    (DB-only) ADRs must be materialized first.
    """
    try:
        adr = _adr_service().get_adr(adr_id)
        resolved_ws = _require_scope(workspace_id or adr.workspace_id, session_id)
        _guard_membership(adr.workspace_id, resolved_ws)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    capability = "adr.reconcile.read" if payload.dry_run else "adr.reconcile.write"
    _get_authz().guard(
        capability, resource_type="adr", resource_id=adr_id,
        details={"dry_run": payload.dry_run, "session_id": session_id},
    )
    try:
        return _adr_reconcile_service().update_file(
            adr_id, payload.markdown, dry_run=payload.dry_run, session_id=session_id
        )
    except ADRCanonicalUpdateError as exc:
        raise _error(409, exc) from exc
    except ADRReconcileError as exc:
        code = exc.code
        status = 409 if code in ("ADR_ALREADY_CANONICAL", "ADR_MISSING_FILE") else 400
        raise _error(status, exc) from exc
    except WorkspaceError as exc:
        raise _error(404, exc) from exc


@router.post("/adrs", response_model=ADRList, status_code=201)
def create_adr(payload: ADRCreate, session_id: str = Query(default="")):
    """Create an ADR.  Slug is auto-generated from title.

    The ADR is created in the effective Workspace scope — a caller
    cannot create an ADR in a workspace it has not declared as its scope.
    """
    payload.workspace_id = _require_scope(payload.workspace_id, session_id)
    try:
        adr = _adr_service().create_adr(payload)
    except Exception as exc:
        raise _api_error(exc) from exc
    return ADRList(adrs=[adr])


@router.get("/adrs/{adr_id}", response_model=ADRList)
def get_adr(
    adr_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Get a single ADR by id.

    The effective Workspace scope is required — an ADR belonging to a
    different workspace returns 404 (no existence leak).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        adr = _adr_service().get_adr(adr_id)
        _guard_membership(adr.workspace_id, workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return ADRList(adrs=[adr])


@router.put("/adrs/{adr_id}", response_model=ADRList)
def update_adr(
    adr_id: str,
    payload: ADRUpdate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update an ADR.  All fields optional — omitted fields are unchanged.

    Canonical (git_file) ADRs cannot be edited through the DB CRUD path —
    edit the canonical file via ``PUT /v1/adrs/{id}/file`` instead.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _adr_service().get_adr(adr_id)
        _guard_membership(current.workspace_id, workspace_id)
        adr = _adr_service().update_adr(adr_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 404 if code in ("ADR_NOT_FOUND", "WORKSPACE_NOT_FOUND") else 400
        if code == "DUPLICATE_SLUG":
            status = 409
        if code in ("ADR_CANONICAL_UPDATE",):
            status = 409
        raise _error(status, exc) from exc
    return ADRList(adrs=[adr])


@router.delete("/adrs/{adr_id}")
def delete_adr(
    adr_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Delete a DB-only ADR and its content/tags.

    Canonical (git_file) ADRs cannot be deleted through the DB — delete
    the canonical file in the repository, then reconcile.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _adr_service().get_adr(adr_id)
        _guard_membership(current.workspace_id, workspace_id)
        _adr_service().delete_adr(adr_id)
    except ADRCanonicalDeleteError as exc:
        raise _error(409, exc) from exc
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Engineering Journal
# ---------------------------------------------------------------------------


@router.get("/journal", response_model=JournalEntryList)
def list_journal_entries(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
    repository_id: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD."),
    q: Optional[str] = Query(default=None, description="Search title/summary/body."),
    limit: Optional[int] = Query(default=None),
):
    """List journal entries with optional filters, newest first — never
    wider than the effective Workspace scope."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        entries = _journal_service().list_entries(
            workspace_id,
            repository_id=repository_id,
            tag=tag,
            entry_date=date,
            query=q,
            limit=limit,
        )
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return JournalEntryList(entries=entries)


@router.post("/journal", response_model=JournalEntryList, status_code=201)
def create_journal_entry(payload: JournalEntryCreate, session_id: str = Query(default="")):
    """Create a journal entry in the effective Workspace scope."""
    payload.workspace_id = _require_scope(payload.workspace_id, session_id)
    try:
        entry = _journal_service().create_entry(payload)
    except Exception as exc:
        raise _api_error(exc) from exc
    return JournalEntryList(entries=[entry])


@router.get("/journal/{entry_id}", response_model=JournalEntryList)
def get_journal_entry(
    entry_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Get a single journal entry.

    The effective Workspace scope is required — an entry belonging to a
    different workspace returns 404 (no existence leak).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        entry = _journal_service().get_entry(entry_id)
        _guard_membership(entry.workspace_id, workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return JournalEntryList(entries=[entry])


@router.put("/journal/{entry_id}", response_model=JournalEntryList)
def update_journal_entry(
    entry_id: str,
    payload: JournalEntryUpdate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update a journal entry.  All fields optional."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _journal_service().get_entry(entry_id)
        _guard_membership(current.workspace_id, workspace_id)
        entry = _journal_service().update_entry(entry_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 404 if code in ("JOURNAL_ENTRY_NOT_FOUND", "WORKSPACE_NOT_FOUND") else 400
        raise _error(status, exc) from exc
    return JournalEntryList(entries=[entry])


@router.delete("/journal/{entry_id}")
def delete_journal_entry(
    entry_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Delete a journal entry."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _journal_service().get_entry(entry_id)
        _guard_membership(current.workspace_id, workspace_id)
        _journal_service().delete_entry(entry_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Roadmaps
# ---------------------------------------------------------------------------


@router.get("/roadmaps", response_model=RoadmapList)
def list_roadmaps(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """List all roadmaps in a workspace, ordered by creation date descending —
    never wider than the effective Workspace scope."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        roadmaps = _roadmap_service().list_roadmaps(workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return RoadmapList(roadmaps=roadmaps)


@router.post("/roadmaps", response_model=RoadmapList, status_code=201)
def create_roadmap(payload: RoadmapCreate):
    """Create a roadmap in a workspace."""
    try:
        roadmap = _roadmap_service().create_roadmap(payload)
    except WorkspaceError as exc:
        raise _error(400, exc) from exc
    return RoadmapList(roadmaps=[roadmap])


@router.get("/roadmaps/{roadmap_id}", response_model=RoadmapList)
def get_roadmap(
    roadmap_id: str,
    workspace_id: str = Query(default="", description="Membership check scope."),
):
    """Get a single roadmap with its milestones and progress.

    When ``workspace_id`` is supplied the roadmap's workspace is
    verified; cross-workspace lookups return 404 (no existence leak).
    """
    try:
        roadmap = _roadmap_service().get_roadmap(roadmap_id)
        _guard_membership(roadmap.workspace_id, workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return RoadmapList(roadmaps=[roadmap])


@router.put("/roadmaps/{roadmap_id}", response_model=RoadmapList)
def update_roadmap(
    roadmap_id: str,
    payload: RoadmapUpdate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update a roadmap. All fields optional."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _roadmap_service().get_roadmap(roadmap_id)
        _guard_membership(current.workspace_id, workspace_id)
        roadmap = _roadmap_service().update_roadmap(roadmap_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 404 if code in ("ROADMAP_NOT_FOUND", "WORKSPACE_NOT_FOUND") else 400
        raise _error(status, exc) from exc
    return RoadmapList(roadmaps=[roadmap])


@router.delete("/roadmaps/{roadmap_id}")
def delete_roadmap(
    roadmap_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Delete a roadmap and all its milestones."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _roadmap_service().get_roadmap(roadmap_id)
        _guard_membership(current.workspace_id, workspace_id)
        _roadmap_service().delete_roadmap(roadmap_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Milestones
# ---------------------------------------------------------------------------


def _guarded_roadmap(roadmap_id: str, workspace_id: str, session_id: str = ""):
    """Resolve the effective scope and verify the roadmap belongs to it.

    U1D-C: when a caller declares a scope, roadmap membership is enforced
    (404, no existence leak).  When NO scope is declared, the roadmap's
    own workspace anchors the request (parent-resource anchoring — the
    current Desktop plugin does not yet send ``workspace_id`` on milestone
    calls; see U1D-G for strict enforcement).  Never widens to global.
    """
    try:
        roadmap = _roadmap_service().get_roadmap(roadmap_id)
        if workspace_id:
            workspace_id = _require_scope(workspace_id, session_id)
            _guard_membership(roadmap.workspace_id, workspace_id)
        return roadmap.workspace_id
    except WorkspaceError as exc:
        raise _error(404, exc) from exc


@router.put(
    "/roadmaps/{roadmap_id}/milestones/reorder",
    response_model=MilestoneList,
)
def reorder_milestones(
    roadmap_id: str,
    payload: MilestoneReorder,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Reorder milestones within a roadmap.

    IMPORTANT: must be registered BEFORE the ``{milestone_id}`` routes
    so FastAPI doesn't interpret "reorder" as a milestone ID.
    """
    _guarded_roadmap(roadmap_id, workspace_id, session_id)
    try:
        milestones = _roadmap_service().reorder_milestones(roadmap_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 400 if code == "INVALID_MILESTONE_STATUS" else 404
        raise _error(status, exc) from exc
    return MilestoneList(milestones=milestones)


@router.get("/roadmaps/{roadmap_id}/milestones", response_model=MilestoneList)
def list_milestones(
    roadmap_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """List all milestones for a roadmap, ordered by sort_order."""
    _guarded_roadmap(roadmap_id, workspace_id, session_id)
    try:
        milestones = _roadmap_service().list_milestones(roadmap_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return MilestoneList(milestones=milestones)


@router.post(
    "/roadmaps/{roadmap_id}/milestones",
    response_model=MilestoneList,
    status_code=201,
)
def create_milestone(
    roadmap_id: str,
    payload: MilestoneCreate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Create a milestone within a roadmap."""
    _guarded_roadmap(roadmap_id, workspace_id, session_id)
    try:
        milestone = _roadmap_service().create_milestone(roadmap_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 400 if code == "INVALID_MILESTONE_STATUS" else 404
        raise _error(status, exc) from exc
    return MilestoneList(milestones=[milestone])


@router.put(
    "/roadmaps/{roadmap_id}/milestones/{milestone_id}",
    response_model=MilestoneList,
)
def update_milestone(
    roadmap_id: str,
    milestone_id: str,
    payload: MilestoneUpdate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update a milestone. All fields optional."""
    _guarded_roadmap(roadmap_id, workspace_id, session_id)
    try:
        milestone = _roadmap_service().update_milestone(milestone_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 400 if code == "INVALID_MILESTONE_STATUS" else 404
        raise _error(status, exc) from exc
    return MilestoneList(milestones=[milestone])


@router.delete("/roadmaps/{roadmap_id}/milestones/{milestone_id}")
def delete_milestone(
    roadmap_id: str,
    milestone_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Delete a milestone."""
    _guarded_roadmap(roadmap_id, workspace_id, session_id)
    try:
        _roadmap_service().delete_milestone(milestone_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=TaskList)
def list_tasks(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    label: Optional[str] = Query(default=None),
    repository_id: Optional[str] = Query(default=None),
    roadmap_id: Optional[str] = Query(default=None),
    milestone_id: Optional[str] = Query(default=None),
    adr_id: Optional[str] = Query(default=None),
    journal_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    overdue: Optional[bool] = Query(default=None),
    limit: Optional[int] = Query(default=None),
):
    """List tasks with optional filters.

    When ``workspace_id`` is empty the project scope is resolved from
    ``session_id``; an unresolvable scope returns 403 (never global).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    params = TaskSearchParams(
        workspace_id=workspace_id,
        status=status,
        priority=priority,
        label=label,
        repository_id=repository_id,
        roadmap_id=roadmap_id,
        milestone_id=milestone_id,
        adr_id=adr_id,
        journal_id=journal_id,
        q=q,
        overdue=overdue,
    )
    tasks = _task_service().list_tasks(params, limit=limit)
    return TaskList(tasks=tasks)


@router.get("/tasks/search", response_model=TaskList)
def search_tasks(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    label: Optional[str] = Query(default=None),
    repository_id: Optional[str] = Query(default=None),
    roadmap_id: Optional[str] = Query(default=None),
    milestone_id: Optional[str] = Query(default=None),
    adr_id: Optional[str] = Query(default=None),
    journal_id: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None),
    overdue: Optional[bool] = Query(default=None),
    limit: Optional[int] = Query(default=None),
):
    """Search tasks with all filter parameters.

    Scope enforcement identical to ``GET /tasks``.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    params = TaskSearchParams(
        workspace_id=workspace_id,
        status=status,
        priority=priority,
        label=label,
        repository_id=repository_id,
        roadmap_id=roadmap_id,
        milestone_id=milestone_id,
        adr_id=adr_id,
        journal_id=journal_id,
        q=q,
        overdue=overdue,
    )
    tasks = _task_service().search_tasks(params)
    return TaskList(tasks=tasks)


@router.post("/tasks", response_model=TaskList, status_code=201)
def create_task(payload: TaskCreate, session_id: str = Query(default="")):
    """Create a task in the effective Workspace scope.

    A task can only be created inside the effective scope; caller-supplied
    cross-workspace references are rejected by the service.
    """
    payload.workspace_id = _require_scope(payload.workspace_id or "", session_id)
    try:
        _validate_task_refs(payload.workspace_id, payload)
        task = _task_service().create_task(payload)
    except Exception as exc:
        raise _api_error(exc) from exc
    return TaskList(tasks=[task])


@router.get("/tasks/{task_id}", response_model=TaskList)
def get_task(
    task_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Get a task with its labels, dependencies, and comment count.

    The effective Workspace scope is required — a task belonging to a
    different workspace returns 404 (no existence leak).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        task = _task_service().get_task(task_id)
        _guard_membership(task.workspace_id, workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return TaskList(tasks=[task])


@router.put("/tasks/{task_id}", response_model=TaskList)
def update_task(
    task_id: str,
    payload: TaskUpdate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Update a task. All fields optional.

    Reassigning a task to a workspace mapped to a DIFFERENT Hermes
    Project is rejected (``CROSS_PROJECT_REASSIGNMENT``).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _task_service().get_task(task_id)
        _guard_membership(current.workspace_id, workspace_id)
        _guard_reassignment(current, payload)
        task = _task_service().update_task(task_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 400 if code in ("INVALID_TASK_STATUS", "INVALID_TASK_PRIORITY",
                                  "CIRCULAR_DEPENDENCY",
                                  "CROSS_PROJECT_REASSIGNMENT") else 404
        raise _error(status, exc) from exc
    return TaskList(tasks=[task])


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Delete a task, its labels, dependencies, and comments."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        current = _task_service().get_task(task_id)
        _guard_membership(current.workspace_id, workspace_id)
        _task_service().delete_task(task_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Task Comments
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/comments", response_model=TaskCommentList)
def list_comments(
    task_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """List all comments on a task."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        task = _task_service().get_task(task_id)
        _guard_membership(task.workspace_id, workspace_id)
        comments = _task_service().list_comments(task_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return TaskCommentList(comments=comments)


@router.post("/tasks/{task_id}/comments", response_model=TaskCommentList, status_code=201)
def add_comment(
    task_id: str,
    payload: TaskCommentCreate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Add a comment to a task.

    When a scope is declared, task membership is enforced.  When none is
    declared, the task's own workspace anchors the request (parent-resource
    anchoring — the current Desktop plugin does not yet send
    ``workspace_id`` on comment-add; see U1D-G for strict enforcement).
    """
    try:
        task = _task_service().get_task(task_id)
        if workspace_id:
            workspace_id = _require_scope(workspace_id, session_id)
            _guard_membership(task.workspace_id, workspace_id)
        comment = _task_service().add_comment(task_id, payload)
    except Exception as exc:
        raise _api_error(exc) from exc
    return TaskCommentList(comments=[comment])


# ---------------------------------------------------------------------------
# Task Dependencies
# ---------------------------------------------------------------------------


@router.get("/tasks/{task_id}/dependencies", response_model=TaskDependencyList)
def get_dependencies(
    task_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Get tasks that depend on this and tasks this depends on."""
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        task = _task_service().get_task(task_id)
        _guard_membership(task.workspace_id, workspace_id)
        return _task_service().get_dependencies(task_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc


@router.put("/tasks/{task_id}/dependencies", response_model=TaskDependencyList)
def set_dependencies(
    task_id: str,
    payload: TaskDependencyCreate,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Replace the dependency list for a task. Detects circular dependencies.

    BOTH ends of every dependency must belong to the effective Workspace
    scope — a dependency pointing at another workspace's task is rejected.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        task = _task_service().get_task(task_id)
        _guard_membership(task.workspace_id, workspace_id)
        for dep_id in payload.depends_on_ids or []:
            dep = _task_service().get_task(dep_id)
            _guard_membership(dep.workspace_id, workspace_id)
        return _task_service().set_dependencies(task_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 400 if code == "CIRCULAR_DEPENDENCY" else 404
        raise _error(status, exc) from exc


# ---------------------------------------------------------------------------
# Global Search
# ---------------------------------------------------------------------------


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(default=""),
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
    type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    label: Optional[str] = Query(default=None),
    roadmap: Optional[str] = Query(default=None),
    repository: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
):
    """Search across all entity types with optional filters.

    Scope enforcement identical to ``GET /tasks`` — an empty
    ``workspace_id`` resolves via ``session_id`` or is rejected.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    explicit_filters: Dict[str, str] = {}
    if type:
        explicit_filters["type"] = type
    if status:
        explicit_filters["status"] = status
    if priority:
        explicit_filters["priority"] = priority
    if label:
        explicit_filters["label"] = label
    if roadmap:
        explicit_filters["roadmap"] = roadmap
    if repository:
        explicit_filters["repository"] = repository
    return _search_service().search(
        q=q, filters=explicit_filters, workspace_id=workspace_id, limit=limit,
    )


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_type}/{entity_id}/related", response_model=RelatedItems)
def get_related(
    entity_type: str,
    entity_id: str,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Get all entities related to the given entity (backlinks and forward-links).

    The entity must belong to the effective Workspace scope — related-item
    traversal can never escape the workspace or reveal a cross-workspace
    entity (404, no existence leak).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    try:
        return _graph_service().get_related(entity_type, entity_id, workspace_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc


# ---------------------------------------------------------------------------
# Knowledge Graph
# ---------------------------------------------------------------------------


@router.get("/graph", response_model=GraphResponse)
def get_graph(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Build and return the knowledge graph.

    Scope enforcement identical to ``GET /tasks``.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    return _graph_service().get_graph(workspace_id)


@router.get("/graph/shortest-path", response_model=ShortestPathResponse)
def shortest_path(
    source_type: str = Query(...),
    source_id: str = Query(...),
    target_type: str = Query(...),
    target_id: str = Query(...),
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Find the shortest path between two entities in the knowledge graph.

    S7.3A: the traversed graph is scoped to the resolved workspace —
    never an accidental global traversal.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    return _graph_service().shortest_path(
        source_type, source_id, target_type, target_id, workspace_id
    )


@router.get("/graph/stats", response_model=GraphStats)
def graph_stats(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Return aggregate statistics about the knowledge graph.

    S7.3A: statistics are computed over the resolved workspace scope —
    never an accidental global aggregate.
    """
    workspace_id = _require_scope(workspace_id, session_id)
    return _graph_service().get_graph_stats(workspace_id)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics", response_model=AnalyticsResponse)
def get_analytics(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Return engineering analytics.

    Scope enforcement identical to ``GET /tasks``; metrics are computed
    for the resolved workspace only (never silently global).
    """
    workspace_id = _require_scope(workspace_id, session_id)
    return _analytics_service().get_analytics(workspace_id)


@router.get("/analytics/trends", response_model=TrendsResponse)
def get_trends(
    period_days: int = Query(default=30),
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Return trend data for task completion, milestones, etc."""
    workspace_id = _require_scope(workspace_id, session_id)
    return _analytics_service().get_trends(period_days, workspace_id)


@router.get("/analytics/insights", response_model=InsightsResponse)
def get_insights(
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Return auto-generated engineering insights."""
    workspace_id = _require_scope(workspace_id, session_id)
    return _analytics_service().get_insights(workspace_id)


@router.post("/analytics/export")
def export_analytics(
    payload: ExportRequest,
    workspace_id: str = Query(default=""),
    session_id: str = Query(default=""),
):
    """Export analytics in markdown, json, or csv format.

    Scope enforcement identical to ``GET /analytics``.
    """
    from fastapi.responses import PlainTextResponse
    workspace_id = _require_scope(workspace_id, session_id)
    data = _analytics_service().get_analytics(workspace_id)
    fmt = payload.format.lower()

    if fmt == "json":
        return data.model_dump()
    elif fmt == "csv":
        lines = ["section,metric,value"]
        lines.append(f"roadmaps,total,{data.roadmaps.total}")
        lines.append(f"roadmaps,active,{data.roadmaps.active}")
        lines.append(f"roadmaps,completed,{data.roadmaps.completed}")
        lines.append(f"tasks,total,{data.tasks.total}")
        lines.append(f"tasks,open,{data.tasks.open}")
        lines.append(f"tasks,completed,{data.tasks.completed}")
        lines.append(f"tasks,blocked,{data.tasks.blocked}")
        lines.append(f"tasks,overdue,{data.tasks.overdue}")
        return PlainTextResponse("\n".join(lines), media_type="text/csv")
    else:
        lines = [
            "# Workspace Analytics",
            "",
            "## Roadmaps",
            f"- Total: {data.roadmaps.total}",
            f"- Active: {data.roadmaps.active}",
            f"- Completed: {data.roadmaps.completed}",
            f"- Avg Progress: {data.roadmaps.avg_progress}%",
            f"- Milestones: {data.roadmaps.total_milestones} ({data.roadmaps.milestones_completed} completed)",
            "",
            "## Tasks",
            f"- Total: {data.tasks.total}",
            f"- Open: {data.tasks.open}",
            f"- Completed: {data.tasks.completed}",
            f"- Blocked: {data.tasks.blocked}",
            f"- Overdue: {data.tasks.overdue}",
            "",
            "## Repositories",
            f"- Total: {data.repositories.total}",
            f"- Active: {data.repositories.active}",
            f"- Most Active: {data.repositories.most_active} ({data.repositories.most_active_task_count} tasks)",
            "",
            "## ADRs",
            f"- Total: {data.adrs.total}",
            f"- Recently Added (30d): {data.adrs.recently_added}",
            "",
            "## Journal",
            f"- Entries This Week: {data.journal.entries_this_week}",
            f"- Entries This Month: {data.journal.entries_this_month}",
            f"- Writing Streak: {data.journal.writing_streak_days} days",
            "",
            "## Knowledge Graph",
            f"- Entities: {data.graph_entities}",
            f"- Edges: {data.graph_edges}",
            f"- Orphans: {data.graph_orphans}",
        ]
        return PlainTextResponse("\n".join(lines), media_type="text/markdown")


# ---------------------------------------------------------------------------
# AI Assistant
# ---------------------------------------------------------------------------


@router.post("/assistant/chat", response_model=ChatResponse)
def assistant_chat(req: ChatRequest):
    """Ask the workspace assistant a question.

    Scope enforcement identical to ``GET /tasks``; the effective
    workspace replaces ``req.workspace_id`` before answering.
    """
    req.workspace_id = _require_scope(req.workspace_id, req.session_id)
    return _assistant_service().chat(req)


@router.post("/assistant/context", response_model=AssistantContext)
def assistant_context(question: str = Query(...),
                      workspace_id: str = Query(default=""),
                      session_id: str = Query(default="")):
    """Build and return the context the assistant would use for a question."""
    workspace_id = _require_scope(workspace_id, session_id)
    return _assistant_service().build_context(question, workspace_id)


@router.get("/assistant/suggestions", response_model=SuggestionsResponse)
def assistant_suggestions(workspace_id: str = Query(default=""),
                          session_id: str = Query(default="")):
    """Get recommended actions and prompts for the assistant."""
    workspace_id = _require_scope(workspace_id, session_id)
    return _assistant_service().get_suggestions(workspace_id)
