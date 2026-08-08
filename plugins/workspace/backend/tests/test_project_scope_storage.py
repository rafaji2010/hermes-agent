"""Unit tests for the Hermes project mapping storage operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    DuplicateProjectMappingError,
    ProjectLinkError,
    WorkspaceNotFoundError,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


@pytest.fixture
def ws(storage: SQLiteStorage):
    return storage.create_workspace("scope-a", "/tmp/a")


def test_link_and_read_back(storage, ws):
    """Linking persists the mapping and returns the updated workspace."""
    updated = storage.link_project(ws.id, "p_abcd")
    assert updated.hermes_project_id == "p_abcd"
    assert storage.get_project_link(ws.id) == "p_abcd"
    assert storage.get_workspace_by_project_id("p_abcd").id == ws.id  # type: ignore[union-attr]


def test_link_updates_workspace_model(storage, ws):
    """The workspace row read through get_workspace carries the mapping."""
    storage.link_project(ws.id, "p_1234")
    assert storage.get_workspace(ws.id).hermes_project_id == "p_1234"  # type: ignore[union-attr]


def test_unlink_clears_mapping(storage, ws):
    storage.link_project(ws.id, "p_abcd")
    updated = storage.unlink_project(ws.id)
    assert updated.hermes_project_id is None
    assert storage.get_project_link(ws.id) is None
    assert storage.get_workspace_by_project_id("p_abcd") is None


def test_unlink_is_idempotent(storage, ws):
    updated = storage.unlink_project(ws.id)
    assert updated.hermes_project_id is None


def test_link_missing_workspace_raises(storage):
    with pytest.raises(WorkspaceNotFoundError):
        storage.link_project("nope", "p_abcd")


def test_link_empty_project_id_raises(storage, ws):
    with pytest.raises(ProjectLinkError):
        storage.link_project(ws.id, "")


def test_duplicate_mapping_rejected(storage, ws):
    storage.link_project(ws.id, "p_abcd")
    ws2 = storage.create_workspace("scope-b", "/tmp/b")
    with pytest.raises(DuplicateProjectMappingError):
        storage.link_project(ws2.id, "p_abcd")


def test_re_link_same_workspace_is_noop(storage, ws):
    storage.link_project(ws.id, "p_abcd")
    updated = storage.link_project(ws.id, "p_abcd")
    assert updated.hermes_project_id == "p_abcd"
    assert len(storage.list_workspaces_by_project_id("p_abcd")) == 1


def test_remap_to_another_project(storage, ws):
    storage.link_project(ws.id, "p_one")
    updated = storage.link_project(ws.id, "p_two")
    assert updated.hermes_project_id == "p_two"
    assert storage.get_workspace_by_project_id("p_one") is None
    assert storage.get_workspace_by_project_id("p_two").id == ws.id  # type: ignore[union-attr]


def test_list_workspaces_by_project_empty(storage):
    assert storage.list_workspaces_by_project_id("p_zzz") == []


def test_list_workspaces_by_project(storage, ws):
    storage.link_project(ws.id, "p_abcd")
    assert [w.id for w in storage.list_workspaces_by_project_id("p_abcd")] == [ws.id]


def test_mapping_survives_workspace_roundtrip(storage, ws):
    storage.link_project(ws.id, "p_abcd")
    rows = storage.list_workspaces()
    mapped = [w for w in rows if w.id == ws.id]
    assert len(mapped) == 1
    assert mapped[0].hermes_project_id == "p_abcd"
