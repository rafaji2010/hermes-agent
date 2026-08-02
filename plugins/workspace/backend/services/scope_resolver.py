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
``workspaces.hermes_project_id`` mapping.

U1D-D authority alignment:

* The effective profile (``HERMES_HOME``) is the OUTER boundary — every
  lookup resolves against the runtime's home at call time, so Profile A
  can never authorize Workspace access in Profile B.
* Session metadata is read through a NARROW READ-ONLY adapter
  (``session_context.read_session_meta``) that keys ONLY on the durable
  SessionDB row id.  Volatile Desktop runtime session ids are not in
  that namespace — unknown identities fail closed (``unresolved``).
  Archived sessions are never authoritative.
* Explicit request CWD is authoritative path evidence: when supplied it
  is used alone for project lookup (the session's stale git root is NOT
  consulted as a conflicting fallback).
* Explicit project mappings are honoured ONLY while the linked Project
  exists and is not archived; archived mappings are stale and treated
  as unmapped.
* Reverse lookup (project -> workspace) fails closed on ambiguity:
  a project mapped to more than one Workspace yields state
  ``ambiguous`` — never a silent first-row choice.
* Every result carries ``provenance`` explaining WHY a Workspace was
  selected (profile home, session id, cwd, git root, project id).

Resolution precedence (deterministic, documented in README):
    1. explicit ``workspace_id`` (mapping must be valid, non-archived)
    2. explicit request ``cwd`` -> project -> reverse mapping
    3. durable session ``cwd`` -> project -> reverse mapping
    4. durable session ``git_repo_root`` -> project -> reverse mapping
    5. otherwise ``unresolved`` / ``unmapped`` / ``partial``

Invariants
----------
* A resolution NEVER degrades to "global scope".  If nothing can be
  identified, the state is ``unresolved`` and callers must reject the
  request rather than widen it.
* ``partial`` means a project OR a workspace was identified but the
  explicit mapping is missing — the caller may propose one (backfill).
* The resolver reads ``projects.db`` and ``state.db`` through injectable
  callables so the plugin can be tested and used without importing
  Hermes Core at module load time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

from ..models import (
    ResolvedProjectScope,
    ScopeResolveRequest,
    WorkspaceNotFoundError,
)
from ..session_context import read_session_meta
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.scope")

# project_lookup(path) -> (project_id, slug) | None
ProjectLookup = Callable[[str], Optional[Tuple[str, str]]]
# session_meta(session_id) -> dict (cwd / git_repo_root / archived keys) | None
SessionMeta = Callable[[str], Optional[Dict]]
# project_slug(project_id) -> slug | None (None when missing/archived)
ProjectSlug = Callable[[str], Optional[str]]


def _default_project_lookup(path: str) -> Optional[Tuple[str, str]]:
    """Resolve a path to a Hermes project via ``projects_db``.

    Lazy import: projects_db lives in Hermes Core and must not be a
    module-level dependency of the plugin.  ``project_for_path`` matches
    the longest-prefix folder and excludes archived projects by default.
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
    """Look up a project's slug by id.

    Archived projects return ``None`` — an archived mapping is stale and
    must never authorize.
    """
    if not project_id:
        return None
    from hermes_cli.projects_db import connect_closing, get_project

    with connect_closing() as conn:
        project = get_project(conn, project_id)
    if project is None or bool(getattr(project, "archived", False)):
        return None
    return project.slug


def _default_session_meta(session_id: str) -> Optional[Dict]:
    """Fetch durable session metadata (read-only, fail-closed)."""
    if not session_id:
        return None
    return read_session_meta(session_id)


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
        provenance = self._provenance(req)

        workspace = None
        if req.workspace_id:
            workspace = self._storage.get_workspace(req.workspace_id)
            if workspace is None:
                raise WorkspaceNotFoundError(req.workspace_id)

        cwd, git_root, explicit_cwd, session_ok = self._collect_paths(req)
        provenance["cwd"] = cwd
        provenance["git_root"] = git_root

        # Precedence 1: explicit mapping on a known workspace.
        if workspace is not None and workspace.hermes_project_id:
            slug = self._project_slug(workspace.hermes_project_id)
            if slug is not None:
                # Mapping is valid and the project is not archived.
                return ResolvedProjectScope(
                    workspace_id=workspace.id,
                    workspace_name=workspace.name,
                    project_id=workspace.hermes_project_id,
                    project_slug=slug,
                    state="mapped",
                    match_source="mapping",
                    matched_path="",
                    provenance=provenance,
                )
            # Stale mapping (archived/deleted project) — fall through.
            provenance["notes"] = "mapping is stale (project archived/deleted)"

        # Precedence 2/3: path evidence → project.
        project = self._project_from_paths(cwd, git_root, explicit_cwd)
        source = self._path_source(cwd, git_root, explicit_cwd)
        provenance["project_id"] = project[0] if project else None
        provenance["match_source"] = source

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
                    provenance=provenance,
                )
            return ResolvedProjectScope(
                workspace_id=workspace.id,
                workspace_name=workspace.name,
                state="unmapped",
                match_source="none",
                provenance=provenance,
            )

        if project is not None:
            # Precedence 4: reverse mapping — which workspace owns this
            # project?  Ambiguity (multiple workspaces) fails closed.
            mapped = self._storage.list_workspaces_by_project_id(project[0])
            if len(mapped) > 1:
                provenance["notes"] = "project mapped to multiple workspaces"
                return ResolvedProjectScope(
                    project_id=project[0],
                    project_slug=project[1] or None,
                    state="ambiguous",
                    match_source=source,
                    matched_path=self._matched_path(cwd, git_root, source),
                    provenance=provenance,
                )
            if len(mapped) == 1:
                return ResolvedProjectScope(
                    workspace_id=mapped[0].id,
                    workspace_name=mapped[0].name,
                    project_id=project[0],
                    project_slug=project[1] or None,
                    state="mapped",
                    match_source=source,
                    matched_path=self._matched_path(cwd, git_root, source),
                    provenance=provenance,
                )
            return ResolvedProjectScope(
                project_id=project[0],
                project_slug=project[1] or None,
                state="partial",
                match_source=source,
                matched_path=self._matched_path(cwd, git_root, source),
                provenance=provenance,
            )

        if req.session_id and not session_ok:
            # Session id given but unreadable — treat as unresolved, NOT global.
            return ResolvedProjectScope(
                state="unresolved",
                provenance=provenance,
            )

        return ResolvedProjectScope(
            state="unresolved",
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _project_id_exists(self, project_id: str) -> bool:
        """True when a Hermes project with this id exists and is active.

        Delegates to the injected slug lookup so the API layer can
        validate link targets without importing Hermes Core.  Archived
        projects are rejected (U1D-D).
        """
        if not project_id:
            return False
        return self._project_slug(project_id) is not None

    def _provenance(self, req: ScopeResolveRequest) -> Dict:
        """Record the identity anchors used for this resolution.

        The profile home is included (own context only — never another
        profile's data).
        """
        try:
            home = str(Path(get_hermes_home()).resolve())
        except Exception:
            home = ""
        return {
            "profile_home": home,
            "session_id": req.session_id or "",
            "explicit_workspace_id": req.workspace_id or "",
            "explicit_cwd": (req.cwd or "").strip(),
        }

    def _collect_paths(
        self, req: ScopeResolveRequest
    ) -> Tuple[str, str, bool, bool]:
        """Return (cwd, git_root, explicit_cwd, session_ok)."""
        cwd = (req.cwd or "").strip()
        explicit_cwd = bool(cwd)
        git_root = ""
        session_ok = False
        if req.session_id:
            meta = self._session_meta(req.session_id)
            if meta:
                session_ok = True
                if not cwd:
                    cwd = (meta.get("cwd") or "").strip()
                # git root is only consulted when the explicit request cwd
                # did not already anchor the resolution (see
                # _project_from_paths) — a caller-supplied cwd is
                # authoritative path evidence and must not be contradicted
                # by a stale session git root.
                git_root = (meta.get("git_repo_root") or "").strip()
        return cwd, git_root, explicit_cwd, session_ok

    def _project_from_paths(
        self,
        cwd: str,
        git_root: str,
        explicit_cwd: bool,
    ) -> Optional[Tuple[str, str]]:
        if cwd:
            project = self._project_lookup(cwd)
            if project is not None:
                return project
            if explicit_cwd:
                # The caller anchored the request with this exact cwd and it
                # resolved to nothing — do NOT fall back to a conflicting
                # session git root.  Deterministic, fail-closed.
                return None
        if git_root and git_root != cwd:
            project = self._project_lookup(git_root)
            if project is not None:
                return project
        return None

    def _path_source(
        self,
        cwd: str,
        git_root: str,
        explicit_cwd: bool,
    ) -> str:
        if cwd and self._project_lookup(cwd) is not None:
            return "explicit_cwd" if explicit_cwd else "session_cwd"
        if git_root and git_root != cwd and self._project_lookup(git_root) is not None:
            return "session_git_root"
        return "none"

    def _matched_path(self, cwd: str, git_root: str, source: str) -> str:
        if source in ("explicit_cwd", "session_cwd"):
            return cwd
        if source == "session_git_root":
            return git_root
        return ""
