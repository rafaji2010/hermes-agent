"""Unit tests for the WorkspaceService business logic."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    InvalidPathError,
    NotAGitRepositoryError,
    RepositoryRegister,
    WorkspaceCreate,
    WorkspaceNotFoundError,
)


def test_create_workspace_validation(svc):
    ws = svc.create_workspace(WorkspaceCreate(name="svc-valid"))
    assert ws.name == "svc-valid"

    with pytest.raises(Exception) as exc:
        svc.create_workspace(WorkspaceCreate(name="svc-valid"))
    assert "already exists" in str(exc.value).lower()


def test_create_workspace_empty_name(svc):
    with pytest.raises(Exception):
        svc.create_workspace(WorkspaceCreate(name="   "))


def test_create_workspace_bad_path(svc):
    with pytest.raises(InvalidPathError):
        svc.create_workspace(
            WorkspaceCreate(name="bad-path-svc", path="/nonexistent/path/xyz/999")
        )


def test_list_workspaces(svc):
    svc.create_workspace(WorkspaceCreate(name="svc-list-a"))
    svc.create_workspace(WorkspaceCreate(name="svc-list-b"))
    all_ws = svc.list_workspaces()
    assert len(all_ws) == 2


def test_get_workspace_missing(svc):
    with pytest.raises(WorkspaceNotFoundError):
        svc.get_workspace("nonexistent-id")


def test_register_repository_with_git_detection(svc, temp_git_repo):
    ws = svc.create_workspace(WorkspaceCreate(name="git-detect-svc"))
    repo = svc.register_repository(
        RepositoryRegister(
            workspace_id=ws.id,
            name="detected-repo",
            path=str(temp_git_repo),
        )
    )
    assert repo.name == "detected-repo"
    assert repo.git_root == str(temp_git_repo.resolve())


def test_register_repo_not_git(svc):
    ws = svc.create_workspace(WorkspaceCreate(name="no-git-svc"))
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(NotAGitRepositoryError):
            svc.register_repository(
                RepositoryRegister(
                    workspace_id=ws.id,
                    name="bad",
                    path=td,
                )
            )


def test_register_repo_missing_workspace(svc, temp_git_repo):
    with pytest.raises(WorkspaceNotFoundError):
        svc.register_repository(
            RepositoryRegister(
                workspace_id="nonexistent",
                name="r",
                path=str(temp_git_repo),
            )
        )


def test_register_repo_nonexistent_path(svc):
    ws = svc.create_workspace(WorkspaceCreate(name="nonexistent-path-svc"))
    with pytest.raises(InvalidPathError):
        svc.register_repository(
            RepositoryRegister(
                workspace_id=ws.id,
                name="r",
                path="/nonexistent/repo/path/999",
            )
        )


def test_list_repositories_missing_workspace(svc):
    with pytest.raises(WorkspaceNotFoundError):
        svc.list_repositories("nonexistent")


def test_list_repositories(svc, temp_git_repo):
    ws = svc.create_workspace(WorkspaceCreate(name="list-repo-svc"))
    svc.register_repository(
        RepositoryRegister(
            workspace_id=ws.id,
            name="unique-repo",
            path=str(temp_git_repo),
        )
    )
    repos = svc.list_repositories(ws.id)
    assert len(repos) == 1
    assert repos[0].name == "unique-repo"
