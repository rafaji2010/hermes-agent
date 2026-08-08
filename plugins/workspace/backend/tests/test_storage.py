"""Unit tests for the storage layer.

Tests ``AbstractStorage`` contract via the ``SQLiteStorage`` implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    DuplicateRepositoryError,
    DuplicateWorkspaceError,
    WorkspaceNotFoundError,
)
from plugins.workspace.backend.storage import AbstractStorage


def test_create_and_list_workspaces(storage: AbstractStorage):
    ws = storage.create_workspace("my-project", "/home/user/project")
    assert ws.id
    assert ws.name == "my-project"
    assert ws.path == "/home/user/project"
    assert ws.created_at

    all_ws = storage.list_workspaces()
    assert len(all_ws) == 1
    assert all_ws[0].id == ws.id


def test_create_duplicate_workspace(storage: AbstractStorage):
    storage.create_workspace("dup", "")
    with pytest.raises(DuplicateWorkspaceError):
        storage.create_workspace("dup", "/other")


def test_get_workspace_by_name(storage: AbstractStorage):
    storage.create_workspace("alpha", "")
    found = storage.get_workspace_by_name("alpha")
    assert found is not None
    assert found.name == "alpha"

    missing = storage.get_workspace_by_name("nope")
    assert missing is None


def test_get_workspace(storage: AbstractStorage):
    ws = storage.create_workspace("beta", "/tmp")
    found = storage.get_workspace(ws.id)
    assert found is not None
    assert found.id == ws.id


def test_register_repository(storage: AbstractStorage):
    ws = storage.create_workspace("repo-test", "")
    repo = storage.register_repository(
        workspace_id=ws.id,
        name="my-repo",
        path="/home/user/my-repo",
        git_root="/home/user/my-repo",
        default_branch="main",
    )
    assert repo.id
    assert repo.workspace_id == ws.id
    assert repo.name == "my-repo"
    assert repo.git_root == "/home/user/my-repo"


def test_register_repo_missing_workspace(storage: AbstractStorage):
    with pytest.raises(WorkspaceNotFoundError):
        storage.register_repository(
            workspace_id="nonexistent",
            name="test",
            path="/tmp/test",
            git_root="/tmp/test",
            default_branch="main",
        )


def test_register_duplicate_repo(storage: AbstractStorage):
    ws = storage.create_workspace("dup-repo-test", "")
    storage.register_repository(
        workspace_id=ws.id,
        name="r1",
        path="/tmp/r",
        git_root="/tmp/r",
        default_branch="main",
    )
    with pytest.raises(DuplicateRepositoryError):
        storage.register_repository(
            workspace_id=ws.id,
            name="r2",
            path="/tmp/r",
            git_root="/tmp/r",
            default_branch="main",
        )


def test_list_repositories(storage: AbstractStorage):
    ws = storage.create_workspace("list-test", "")
    storage.register_repository(
        workspace_id=ws.id,
        name="z-repo",
        path="/tmp/z",
        git_root="/tmp/z",
        default_branch="main",
    )
    storage.register_repository(
        workspace_id=ws.id,
        name="a-repo",
        path="/tmp/a",
        git_root="/tmp/a",
        default_branch="develop",
    )
    repos = storage.list_repositories(ws.id)
    assert len(repos) == 2
    assert repos[0].name == "a-repo"  # ordered by name


def test_get_repository(storage: AbstractStorage):
    ws = storage.create_workspace("get-test", "")
    repo = storage.register_repository(
        workspace_id=ws.id,
        name="target",
        path="/tmp/target",
        git_root="/tmp/target",
        default_branch="main",
    )
    found = storage.get_repository(repo.id)
    assert found is not None
    assert found.name == "target"


def test_get_repository_by_path(storage: AbstractStorage):
    ws = storage.create_workspace("path-test", "")
    storage.register_repository(
        workspace_id=ws.id,
        name="r",
        path="/tmp/exact",
        git_root="/tmp/exact",
        default_branch="main",
    )
    found = storage.get_repository_by_path(ws.id, "/tmp/exact")
    assert found is not None
    assert found.path == "/tmp/exact"

    missing = storage.get_repository_by_path(ws.id, "/tmp/nope")
    assert missing is None


def test_settings(storage: AbstractStorage):
    assert storage.get_setting("schema_version") is None
    storage.set_setting("schema_version", "1")
    assert storage.get_setting("schema_version") == "1"
    storage.set_setting("schema_version", "2")
    assert storage.get_setting("schema_version") == "2"
