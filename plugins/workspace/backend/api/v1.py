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

All responses use a consistent envelope.  Errors return ``ErrorDetail``
with a machine-readable ``code`` field.
"""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

from ..database import get_database
from ..models import (
    ADRCreate,
    ADRList,
    ADRUpdate,
    ErrorDetail,
    JournalEntryCreate,
    JournalEntryList,
    JournalEntryUpdate,
    RepositoryList,
    RepositoryRegister,
    StatusResponse,
    WorkspaceCreate,
    WorkspaceError,
    WorkspaceList,
)
from ..services.adr_service import ADRService
from ..services.journal_service import JournalService
from ..services.workspace_service import WorkspaceService
from ..storage.sqlite_storage import SQLiteStorage

_log = logging.getLogger("hermes.plugins.workspace.api.v1")

router = APIRouter(prefix="/v1", tags=["workspace-v1"])

# ---------------------------------------------------------------------------
# Service singleton (lazy)
# ---------------------------------------------------------------------------

_svc: WorkspaceService | None = None
_adr_svc: ADRService | None = None


def _service() -> WorkspaceService:
    """Return the module-level ``WorkspaceService`` singleton."""
    global _svc
    if _svc is None:
        _svc = WorkspaceService(storage=SQLiteStorage())
    return _svc


def _adr_service() -> ADRService:
    """Return the module-level ``ADRService`` singleton."""
    global _adr_svc
    if _adr_svc is None:
        _adr_svc = ADRService(storage=SQLiteStorage())
    return _adr_svc


_journal_svc: JournalService | None = None


def _journal_service() -> JournalService:
    """Return the module-level ``JournalService`` singleton."""
    global _journal_svc
    if _journal_svc is None:
        _journal_svc = JournalService(storage=SQLiteStorage())
    return _journal_svc


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
    db = get_database()
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
        else:
            ws_count = 0
            repo_count = 0
            journal_count = 0
    except Exception:
        latest_version = "unknown"
        migration_status = "Error"
        ws_count = 0
        repo_count = 0
        journal_count = 0

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
    workspace_id: str = Query(..., description="Workspace ID."),
    status: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="Search title + body."),
):
    """List ADRs with optional filters."""
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


@router.post("/adrs", response_model=ADRList, status_code=201)
def create_adr(payload: ADRCreate):
    """Create an ADR.  Slug is auto-generated from title."""
    try:
        adr = _adr_service().create_adr(payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 409 if code in ("DUPLICATE_SLUG",) else 400
        raise _error(status, exc) from exc
    return ADRList(adrs=[adr])


@router.get("/adrs/{adr_id}", response_model=ADRList)
def get_adr(adr_id: str):
    """Get a single ADR by id."""
    try:
        adr = _adr_service().get_adr(adr_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return ADRList(adrs=[adr])


@router.put("/adrs/{adr_id}", response_model=ADRList)
def update_adr(adr_id: str, payload: ADRUpdate):
    """Update an ADR.  All fields optional — omitted fields are unchanged."""
    try:
        adr = _adr_service().update_adr(adr_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 404 if code == "ADR_NOT_FOUND" else 400
        if code == "DUPLICATE_SLUG":
            status = 409
        raise _error(status, exc) from exc
    return ADRList(adrs=[adr])


@router.delete("/adrs/{adr_id}")
def delete_adr(adr_id: str):
    """Delete an ADR and its content/tags."""
    try:
        _adr_service().delete_adr(adr_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# Engineering Journal
# ---------------------------------------------------------------------------


@router.get("/journal", response_model=JournalEntryList)
def list_journal_entries(
    workspace_id: str = Query(..., description="Workspace ID."),
    repository_id: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    date: Optional[str] = Query(default=None, description="YYYY-MM-DD."),
    q: Optional[str] = Query(default=None, description="Search title/summary/body."),
    limit: Optional[int] = Query(default=None),
):
    """List journal entries with optional filters, newest first."""
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
def create_journal_entry(payload: JournalEntryCreate):
    """Create a journal entry."""
    try:
        entry = _journal_service().create_entry(payload)
    except WorkspaceError as exc:
        raise _error(400, exc) from exc
    return JournalEntryList(entries=[entry])


@router.get("/journal/{entry_id}", response_model=JournalEntryList)
def get_journal_entry(entry_id: str):
    """Get a single journal entry."""
    try:
        entry = _journal_service().get_entry(entry_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return JournalEntryList(entries=[entry])


@router.put("/journal/{entry_id}", response_model=JournalEntryList)
def update_journal_entry(entry_id: str, payload: JournalEntryUpdate):
    """Update a journal entry.  All fields optional."""
    try:
        entry = _journal_service().update_entry(entry_id, payload)
    except WorkspaceError as exc:
        code = exc.code
        status = 404 if code == "JOURNAL_ENTRY_NOT_FOUND" else 400
        raise _error(status, exc) from exc
    return JournalEntryList(entries=[entry])


@router.delete("/journal/{entry_id}")
def delete_journal_entry(entry_id: str):
    """Delete a journal entry."""
    try:
        _journal_service().delete_entry(entry_id)
    except WorkspaceError as exc:
        raise _error(404, exc) from exc
    return {"ok": True}
