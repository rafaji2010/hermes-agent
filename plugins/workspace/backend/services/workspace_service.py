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
from typing import List, Optional

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

_log = logging.getLogger("hermes.plugins.workspace.service")


class WorkspaceService:
    """Business logic for workspace and repository management.

    The service layer validates inputs, auto-detects git roots, and
    delegates persistence to the injected ``AbstractStorage`` backend.
    """

    def __init__(self, storage: AbstractStorage):
        self._storage = storage

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def create_workspace(self, payload: WorkspaceCreate) -> Workspace:
        """Create a workspace after validation."""
        name = payload.name.strip()
        if not name:
            raise InvalidPathError("", "Workspace name must not be empty.")

        path = payload.path.strip()
        if path:
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
    # Repositories
    # ------------------------------------------------------------------

    def register_repository(self, payload: RepositoryRegister) -> Repository:
        """Register a repository after validation and git-root detection."""
        # --- validate workspace exists ---
        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)

        # --- validate name ---
        name = payload.name.strip()
        if not name:
            raise InvalidPathError("", "Repository name must not be empty.")

        # --- validate / resolve path ---
        repo_path = payload.path.strip()
        if not repo_path:
            raise InvalidPathError("", "Repository path is required.")

        resolved = Path(repo_path).resolve()
        self._validate_directory(resolved, "Repository path")

        # --- validate git ---
        git_root = payload.git_root
        if git_root:
            git_root = str(Path(git_root).resolve())
            self._validate_directory(Path(git_root), "Git root")
            self._validate_git_repo(Path(git_root))
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
