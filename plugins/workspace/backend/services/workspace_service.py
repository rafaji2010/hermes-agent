"""Workspace Service.

Orchestrates storage, validation, and git-auto-detection for workspace
and repository operations.  Depends on ``AbstractStorage`` — never on
a concrete storage implementation.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from ..models import (
    DuplicateRepositoryError,
    DuplicateWorkspaceError,
    InvalidPathError,
    NotAGitRepositoryError,
    Repository,
    RepositoryRegister,
    Workspace,
    WorkspaceCreate,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter
    from ..security.sandbox import PathSandbox

_log = logging.getLogger("hermes.plugins.workspace.service")


class WorkspaceService:
    """Business logic for workspace and repository management.

    The service layer validates inputs, auto-detects git roots, and
    delegates persistence to the injected ``AbstractStorage`` backend.
    """

    def __init__(
        self,
        storage: AbstractStorage,
        authz: "AuthorizationMiddleware | None" = None,
        limits: "ResourceLimiter | None" = None,
        sandbox: "PathSandbox | None" = None,
    ):
        self._storage = storage
        self._authz = authz
        self._limits = limits
        self._sandbox = sandbox

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def create_workspace(self, payload: WorkspaceCreate) -> Workspace:
        """Create a workspace after validation."""
        name = payload.name.strip()
        if not name:
            raise InvalidPathError("", "Workspace name must not be empty.")

        if self._limits:
            result = self._limits.check_title_length(name, "workspace name")
            if not result.allowed:
                self._audit_violation("resource_limit", result.reason)
                from ..security.resource_limits import ResourceLimitExceeded
                raise ResourceLimitExceeded(result.resource, result.limit, result.actual)

        if self._authz:
            self._authz.guard("workspace.create", resource_type="workspace")

        path = payload.path.strip()
        if path:
            if self._sandbox:
                pv = self._sandbox.validate_path(path, operation="read")
                if not pv.is_allowed:
                    self._audit_violation("sandbox", pv.reason)
                    raise InvalidPathError(path, pv.reason)
            self._validate_directory(Path(path), "Workspace root path")

        with self._storage.transaction():
            return self._storage.create_workspace(name, path)

    def list_workspaces(self) -> List[Workspace]:
        """Return all workspaces."""
        return self._storage.list_workspaces()

    def get_workspace(self, workspace_id: str) -> Workspace:
        """Return a workspace or raise ``WorkspaceNotFoundError``."""
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return ws

    # ------------------------------------------------------------------
    # Hermes Project mapping
    # ------------------------------------------------------------------
    #
    # Passthroughs to the storage layer.  Authorization for these
    # operations (``workspace.scope.link`` / ``workspace.scope.read``)
    # happens at the API layer where the request context is known.

    def link_project(self, workspace_id: str, project_id: str) -> Workspace:
        """Map a workspace to a Hermes Project."""
        return self._storage.link_project(workspace_id, project_id)

    def unlink_project(self, workspace_id: str) -> Workspace:
        """Clear the Hermes Project mapping for a workspace."""
        return self._storage.unlink_project(workspace_id)

    def get_project_link(self, workspace_id: str) -> Optional[str]:
        """Return the mapped Hermes project id (or ``None``)."""
        return self._storage.get_project_link(workspace_id)

    def get_workspace_by_project_id(self, project_id: str) -> Optional[Workspace]:
        """Return the single workspace mapped to a project, if any."""
        return self._storage.get_workspace_by_project_id(project_id)

    def list_workspaces_by_project_id(self, project_id: str) -> List[Workspace]:
        """Return all workspaces mapped to a project."""
        return self._storage.list_workspaces_by_project_id(project_id)

    # ------------------------------------------------------------------
    # Repositories
    # ------------------------------------------------------------------

    def register_repository(self, payload: RepositoryRegister) -> Repository:
        """Register a repository after validation and git-root detection."""
        if self._authz:
            self._authz.guard("repository.register",
                              resource_type="repository",
                              resource_id=payload.workspace_id)

        name = payload.name.strip()
        if not name:
            raise InvalidPathError("", "Repository name must not be empty.")

        if self._limits:
            result = self._limits.check_title_length(name, "repository name")
            if not result.allowed:
                self._audit_violation("resource_limit", result.reason)
                from ..security.resource_limits import ResourceLimitExceeded
                raise ResourceLimitExceeded(result.resource, result.limit, result.actual)

        # --- validate workspace exists ---
        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)

        # --- validate / resolve path ---
        repo_path = payload.path.strip()
        if not repo_path:
            raise InvalidPathError("", "Repository path is required.")

        resolved = Path(repo_path).resolve()

        if self._sandbox:
            pv = self._sandbox.validate_path(str(resolved), operation="read")
            if not pv.is_allowed:
                self._audit_violation("sandbox", pv.reason)
                raise InvalidPathError(str(resolved), pv.reason)

        self._validate_directory(resolved, "Repository path")

        # --- validate git ---
        git_root = payload.git_root
        if git_root:
            git_root_path = Path(git_root).resolve()
            if self._sandbox:
                pv = self._sandbox.validate_path(str(git_root_path), operation="read")
                if not pv.is_allowed:
                    self._audit_violation("sandbox", pv.reason)
                    raise InvalidPathError(str(git_root_path), pv.reason)
            self._validate_directory(git_root_path, "Git root")
            self._validate_git_repo(git_root_path)
            git_root = str(git_root_path)
        else:
            git_root = self._detect_git_root(resolved)
            if git_root is None:
                raise NotAGitRepositoryError(str(resolved))
            git_root = str(Path(git_root).resolve())

        default_branch = (payload.default_branch or "main").strip() or "main"

        with self._storage.transaction():
            return self._storage.register_repository(
                workspace_id=payload.workspace_id,
                name=name,
                path=str(resolved),
                git_root=git_root,
                default_branch=default_branch,
            )

    def list_repositories(self, workspace_id: str) -> List[Repository]:
        """List repositories in a workspace after validating it exists."""
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return self._storage.list_repositories(workspace_id)

    def get_repository(self, repo_id: str) -> Repository:
        """Return a repository or raise ``RepositoryNotFoundError``."""
        from ..models import RepositoryNotFoundError

        repo = self._storage.get_repository(repo_id)
        if repo is None:
            raise RepositoryNotFoundError(repo_id)
        return repo

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _audit_violation(self, category: str, reason: str) -> None:
        """Log a security violation audit event."""
        if self._authz:
            self._authz.audit.log(
                action=f"s6.4.violation.{category}",
                status="DENY",
                details={"category": category, "reason": reason},
            )

    @staticmethod
    def _validate_directory(p: Path, label: str) -> None:
        if not p.exists():
            raise InvalidPathError(str(p), f"{label} does not exist.")
        if not p.is_dir():
            raise InvalidPathError(str(p), f"{label} is not a directory.")

    @staticmethod
    def _validate_git_repo(p: Path) -> None:
        git_dir = p / ".git"
        if not git_dir.exists():
            raise NotAGitRepositoryError(str(p))

    @staticmethod
    def _detect_git_root(start: Path) -> Optional[str]:
        """Run ``git rev-parse --show-toplevel`` to find the git root."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(start),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                root = result.stdout.strip()
                if root:
                    return root
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return None
