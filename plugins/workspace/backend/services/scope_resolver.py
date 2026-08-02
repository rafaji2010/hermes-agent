"""Project Scope Resolver.

Resolves the *Hermes Project* authority for a given identity anchor
(session, workspace, or explicit cwd) and produces a
``ResolvedProjectScope`` that downstream services and endpoints can use
to scope queries.

Authority model
---------------
Hermes Projects are the canonical identity (per-profile ``projects.db``,
owned by Hermes Core).  Workspaces (``workspace.db``) hold engineering
data.  The only coupling is the soft, nullable
``workspaces.hermes_project_id`` mapping.  This resolver implements the
resolution precedence agreed in the S7.2 plan:

    explicit mapping → session cwd → session git root → mapping
    (reverse lookup) → ``unresolved`` / ``partial`` / ``unmapped``

Invariants
----------
* A resolution NEVER degrades to "global scope".  If nothing can be
  identified, the state is ``unresolved`` and callers must reject the
  request rather than widen it.
* ``partial`` means a project OR a workspace was identified but the
  explicit mapping is missing — the caller may propose one (backfill).
* The resolver reads ``projects.db`` through injectable callables so the
  plugin can be tested and used without importing Hermes Core at module
  load time.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional, Tuple

from ..models import (
    ResolvedProjectScope,
    ScopeResolveRequest,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.scope")

# project_lookup(path) -> (project_id, slug) | None
ProjectLookup = Callable[[str], Optional[Tuple[str, str]]]
# session_meta(session_id) -> dict (cwd / git_repo_root keys) | None
SessionMeta = Callable[[str], Optional[Dict]]
# project_slug(project_id) -> slug | None
ProjectSlug = Callable[[str], Optional[str]]


def _default_project_lookup(path: str) -> Optional[Tuple[str, str]]:
    """Resolve a path to a Hermes project via ``projects_db``.

    Lazy import: projects_db lives in Hermes Core and must not be a
    module-level dependency of the plugin.
    """
    if not path:
        return None
    from hermes_cli.projects_db import connect_closing, project_for_path

    with connect_closing() as conn:
        project = project_for_path(conn, path)
    if project is None:
        return None
    return (project.id, project.slug or "")


def _default_project_slug(project_id: str) -> Optional[str]:
    """Look up a project's slug by id (for mapped workspaces)."""
    if not project_id:
        return None
    from hermes_cli.projects_db import connect_closing, get_project

    with connect_closing() as conn:
        project = get_project(conn, project_id)
    return project.slug if project else None


def _default_session_meta(session_id: str) -> Optional[Dict]:
    """Fetch session metadata (cwd / git_repo_root) from state.db."""
    if not session_id:
        return None
    from hermes_state import SessionDB

    try:
        row = SessionDB().get_session(session_id)
    except Exception:
        _log.exception("Failed to read session %s", session_id)
        return None
    return row if row else None


class ProjectScopeResolver:
    """Resolve Hermes Project identity for a workspace scope request."""

    def __init__(
        self,
        storage: AbstractStorage,
        project_lookup: Optional[ProjectLookup] = None,
        session_meta: Optional[SessionMeta] = None,
        project_slug: Optional[ProjectSlug] = None,
    ):
        self._storage = storage
        self._project_lookup = project_lookup or _default_project_lookup
        self._session_meta = session_meta or _default_session_meta
        self._project_slug = project_slug or _default_project_slug

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, req: ScopeResolveRequest) -> ResolvedProjectScope:
        """Resolve the project scope for a request.

        Raises ``WorkspaceNotFoundError`` when an explicit
        ``workspace_id`` does not exist.
        """
        workspace = None
        if req.workspace_id:
            workspace = self._storage.get_workspace(req.workspace_id)
            if workspace is None:
                raise WorkspaceNotFoundError(req.workspace_id)

        cwd, git_root, session_ok = self._collect_paths(req)

        # Precedence 1: explicit mapping on a known workspace.
        if workspace is not None and workspace.hermes_project_id:
            slug = self._project_slug(workspace.hermes_project_id)
            return ResolvedProjectScope(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                project_id=workspace.hermes_project_id,
                project_slug=slug,
                state="mapped",
                match_source="mapping",
                matched_path="",
            )

        # Precedence 2/3: session path evidence → project.
        project = self._project_from_paths(cwd, git_root)
        source = self._path_source(cwd, git_root)

        if workspace is not None:
            if project is not None:
                return ResolvedProjectScope(
                    workspace_id=workspace.id,
                    workspace_name=workspace.name,
                    project_id=project[0],
                    project_slug=project[1] or None,
                    state="partial",
                    match_source=source,
                    matched_path=self._matched_path(cwd, git_root, source),
                )
            return ResolvedProjectScope(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                state="unmapped",
                match_source="none",
            )

        if project is not None:
            # Precedence 4: reverse mapping — which workspace owns this project?
            mapped_ws = self._storage.get_workspace_by_project_id(project[0])
            if mapped_ws is not None:
                return ResolvedProjectScope(
                    workspace_id=mapped_ws.id,
                    workspace_name=mapped_ws.name,
                    project_id=project[0],
                    project_slug=project[1] or None,
                    state="mapped",
                    match_source=source,
                    matched_path=self._matched_path(cwd, git_root, source),
                )
            return ResolvedProjectScope(
                project_id=project[0],
                project_slug=project[1] or None,
                state="partial",
                match_source=source,
                matched_path=self._matched_path(cwd, git_root, source),
            )

        if req.session_id and not session_ok:
            # Session id given but unreadable — treat as unresolved, NOT global.
            return ResolvedProjectScope(state="unresolved")

        return ResolvedProjectScope(state="unresolved")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_id_exists(self, project_id: str) -> bool:
        """True when a Hermes project with this id exists in projects.db.

        Delegates to the injected slug lookup so the API layer can
        validate link targets without importing Hermes Core.
        """
        if not project_id:
            return False
        return self._project_slug(project_id) is not None

    def _collect_paths(
        self, req: ScopeResolveRequest
    ) -> Tuple[str, str, bool]:
        """Return (cwd, git_root, session_ok) for a request."""
        cwd = (req.cwd or "").strip()
        git_root = ""
        session_ok = False
        if req.session_id:
            meta = self._session_meta(req.session_id)
            if meta:
                session_ok = True
                if not cwd:
                    cwd = (meta.get("cwd") or "").strip()
                git_root = (meta.get("git_repo_root") or "").strip()
        return cwd, git_root, session_ok

    def _project_from_paths(
        self, cwd: str, git_root: str
    ) -> Optional[Tuple[str, str]]:
        if cwd:
            project = self._project_lookup(cwd)
            if project is not None:
                return project
        if git_root and git_root != cwd:
            project = self._project_lookup(git_root)
            if project is not None:
                return project
        return None

    def _path_source(self, cwd: str, git_root: str) -> str:
        if cwd and self._project_lookup(cwd) is not None:
            return "session_cwd"
        if git_root and git_root != cwd and self._project_lookup(git_root) is not None:
            return "session_git_root"
        return "none"

    def _matched_path(self, cwd: str, git_root: str, source: str) -> str:
        if source == "session_cwd":
            return cwd
        if source == "session_git_root":
            return git_root
        return ""
