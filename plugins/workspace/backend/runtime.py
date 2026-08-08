"""Profile-scoped Workspace runtime ownership (U1D-A).

Eliminates first-profile pinning in the Workspace backend.

Every profile-sensitive component — database manager, storage, security
components (authorization, limits, sandbox, audit) and all services — is
owned by a :class:`WorkspaceRuntime` bound to ONE effective Hermes home.
Runtimes are cached by the normalized effective home and looked up at
CALL TIME via :func:`get_hermes_home` (which follows the context-local
override installed by ``set_hermes_home_override``), so a single process
serving multiple profiles resolves a distinct runtime per profile and
never leaks one profile's ``workspace.db`` or audit state into another.

Normal Hermes Desktop deployments run one backend process per profile, so
the cache holds a single entry; app-global remote mode (one process,
many profiles) gets one entry per profile.  No import-time HERMES_HOME
capture happens anywhere in this module or the runtime it builds.
"""

from __future__ import annotations

import atexit
import logging
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, Optional

from hermes_constants import (  # type: ignore[import-untyped]
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)

from .database import DatabaseManager
from .security.audit import AuditLogger
from .security.authorization import AuthorizationMiddleware
from .security.capabilities import CapabilityRegistry
from .security.resource_limits import ResourceLimiter
from .security.sandbox import PathSandbox
from .services.adr_reconcile_service import ADRReconcileService
from .services.adr_service import ADRService
from .services.analytics_service import AnalyticsService
from .services.assistant_service import WorkspaceAssistantService
from .services.graph_service import GraphService
from .services.journal_service import JournalService
from .services.roadmap_service import RoadmapService
from .services.scope_resolver import ProjectScopeResolver
from .services.search_service import SearchService
from .services.task_service import TaskService
from .services.workspace_service import WorkspaceService
from .storage.sqlite_storage import SQLiteStorage

_log = logging.getLogger("hermes.plugins.workspace.runtime")


class WorkspaceRuntime:
    """Owns every profile-sensitive Workspace component for one Hermes home.

    Construction is intentionally dependency-injectable: tests supply an
    in-memory ``DatabaseManager`` and a temp-file ``AuditLogger``; the
    production path supplies neither and binds to ``<home>/workspace.db``
    and ``<home>/logs/audit.log``.
    """

    def __init__(
        self,
        home: Path,
        database: Optional[DatabaseManager] = None,
        audit: Optional[AuditLogger] = None,
        storage: Optional[SQLiteStorage] = None,
    ):
        self._home = Path(home)

        # Database and audit bind to THIS home — never the first profile seen.
        self._database = database or DatabaseManager(db_path=self._home / "workspace.db")
        self._audit = audit or self._default_audit_logger()
        self._storage = storage or SQLiteStorage(db_manager=self._database)

        # Security components (audit-scoped to this runtime's home).
        self._limits = ResourceLimiter()
        self._sandbox = PathSandbox()
        self._authz = AuthorizationMiddleware(
            registry=CapabilityRegistry(),
            audit_logger=self._audit,
        )

        # Services — one shared storage per runtime.
        self._workspace_service = WorkspaceService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
            sandbox=self._sandbox,
        )
        self._adr_service = ADRService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
        )
        self._adr_reconcile_service = ADRReconcileService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
            sandbox=self._sandbox,
        )
        self._journal_service = JournalService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
        )
        self._roadmap_service = RoadmapService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
        )
        self._task_service = TaskService(
            storage=self._storage,
            authz=self._authz,
            limits=self._limits,
        )
        self._search_service = SearchService(storage=self._storage)
        self._graph_service = GraphService(storage=self._storage)
        self._analytics_service = AnalyticsService(storage=self._storage)
        self._assistant_service = WorkspaceAssistantService(
            search=self._search_service,
            graph=self._graph_service,
            analytics=self._analytics_service,
        )
        self._scope_resolver = ProjectScopeResolver(storage=self._storage)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_audit_logger(self) -> AuditLogger:
        if str(self._home) == ":memory:":
            # In-memory homes have no filesystem; keep audit out of CWD.
            tmp = (
                Path(tempfile.gettempdir())
                / f"hermes-workspace-audit-{uuid.uuid4().hex[:12]}.log"
            )
            return AuditLogger(log_path=tmp)
        return AuditLogger(log_path=self._home / "logs" / "audit.log")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release the owned database connection (idempotent)."""
        try:
            self._database.close()
        except Exception:
            _log.exception("Failed to close Workspace runtime database for %s", self._home)

    # ------------------------------------------------------------------
    # Owned components
    # ------------------------------------------------------------------

    @property
    def home(self) -> Path:
        """The Hermes home this runtime is bound to."""
        return self._home

    @property
    def database(self) -> DatabaseManager:
        return self._database

    @property
    def storage(self) -> SQLiteStorage:
        return self._storage

    @property
    def audit(self) -> AuditLogger:
        return self._audit

    @property
    def authz(self) -> AuthorizationMiddleware:
        return self._authz

    @property
    def limits(self) -> ResourceLimiter:
        return self._limits

    @property
    def sandbox(self) -> PathSandbox:
        return self._sandbox

    @property
    def workspace_service(self) -> WorkspaceService:
        return self._workspace_service

    @property
    def adr_service(self) -> ADRService:
        return self._adr_service

    @property
    def adr_reconcile_service(self) -> ADRReconcileService:
        return self._adr_reconcile_service

    @property
    def journal_service(self) -> JournalService:
        return self._journal_service

    @property
    def roadmap_service(self) -> RoadmapService:
        return self._roadmap_service

    @property
    def task_service(self) -> TaskService:
        return self._task_service

    @property
    def search_service(self) -> SearchService:
        return self._search_service

    @property
    def graph_service(self) -> GraphService:
        return self._graph_service

    @property
    def analytics_service(self) -> AnalyticsService:
        return self._analytics_service

    @property
    def assistant_service(self) -> WorkspaceAssistantService:
        return self._assistant_service

    @property
    def scope_resolver(self) -> ProjectScopeResolver:
        return self._scope_resolver


# ---------------------------------------------------------------------------
# Runtime cache — keyed by the normalized effective Hermes home
# ---------------------------------------------------------------------------

_runtimes: Dict[str, WorkspaceRuntime] = {}
_runtimes_lock = threading.Lock()

# Test/embedding seam mirroring the pre-existing ``database._db`` pin
# convention: when set, every lookup returns this runtime regardless of
# the effective home. Production never sets it.
_pinned: Optional[WorkspaceRuntime] = None


def get_workspace_runtime() -> WorkspaceRuntime:
    """Return the Workspace runtime for the EFFECTIVE Hermes home.

    The effective home is resolved at call time via :func:`get_hermes_home`
    — context-local override aware — so a single process serving multiple
    profiles (or a host that swaps HERMES_HOME mid-flight) always resolves
    the correct profile's runtime.
    """
    if _pinned is not None:
        return _pinned

    home = Path(get_hermes_home()).resolve()
    key = str(home)

    with _runtimes_lock:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = _build_runtime(home)
            _runtimes[key] = runtime
        return runtime


def _build_runtime(home: Path) -> WorkspaceRuntime:
    """Construct a runtime with every lazy home binding pinned to ``home``.

    Construction runs inside ``set_hermes_home_override`` so any lazy
    ``get_hermes_home()`` evaluation during construction resolves to the
    runtime's own home — the override is task-local and restored after.
    """
    token = set_hermes_home_override(str(home))
    try:
        return WorkspaceRuntime(home=home)
    finally:
        reset_hermes_home_override(token)


def pin_workspace_runtime(runtime: WorkspaceRuntime) -> None:
    """Pin a runtime for the process (test seam — mirrors ``database._db``)."""
    global _pinned
    _pinned = runtime


def reset_workspace_runtimes() -> None:
    """Close and clear every cached runtime and drop the pin.

    Safe to call repeatedly; used by tests between cases and at process
    shutdown.  A later lookup lazily rebuilds the runtime for its home.
    """
    global _pinned
    _pinned = None

    with _runtimes_lock:
        runtimes = list(_runtimes.values())
        _runtimes.clear()
    for runtime in runtimes:
        runtime.close()


def close_all_runtimes() -> None:
    """Release every cached runtime (registered at exit)."""
    reset_workspace_runtimes()


atexit.register(close_all_runtimes)
