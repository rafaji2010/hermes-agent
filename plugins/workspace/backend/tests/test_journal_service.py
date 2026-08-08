"""Unit tests for JournalService."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    JournalEntryCreate,
    JournalEntryUpdate,
    JournalEntryNotFoundError,
    WorkspaceNotFoundError,
)
from plugins.workspace.backend.services.journal_service import JournalService  # type: ignore[import-untyped]


def test_create_and_get(svc, storage):
    ws = storage.create_workspace("js-ws", "")
    js = JournalService(storage)
    entry = js.create_entry(
        JournalEntryCreate(
            workspace_id=ws.id, title="My Entry", summary="Working on API",
            markdown="# Today\n\nBuilt stuff.", entry_date="2026-07-15",
            tags=["api", "backend"],
        )
    )
    assert entry.title == "My Entry"
    assert entry.summary == "Working on API"
    assert set(entry.tags) == {"api", "backend"}
    assert entry.entry_date == "2026-07-15"

    found = js.get_entry(entry.id)
    assert found.title == "My Entry"


def test_create_empty_title(svc, storage):
    ws = storage.create_workspace("je-ws", "")
    js = JournalService(storage)
    with pytest.raises(Exception):
        js.create_entry(JournalEntryCreate(workspace_id=ws.id, title=""))


def test_create_missing_workspace(svc, storage):
    js = JournalService(storage)
    with pytest.raises(WorkspaceNotFoundError):
        js.create_entry(JournalEntryCreate(workspace_id="nonexistent", title="X"))


def test_update(svc, storage):
    ws = storage.create_workspace("ju-ws", "")
    js = JournalService(storage)
    e = js.create_entry(JournalEntryCreate(workspace_id=ws.id, title="Old"))
    updated = js.update_entry(e.id, JournalEntryUpdate(title="New", summary="S", markdown="M", tags=["updated"]))
    assert updated.title == "New"
    assert updated.tags == ["updated"]


def test_update_missing(svc, storage):
    js = JournalService(storage)
    with pytest.raises(JournalEntryNotFoundError):
        js.update_entry("nonexistent", JournalEntryUpdate(title="X"))


def test_delete(svc, storage):
    ws = storage.create_workspace("jd-ws", "")
    js = JournalService(storage)
    e = js.create_entry(JournalEntryCreate(workspace_id=ws.id, title="Gone"))
    js.delete_entry(e.id)
    with pytest.raises(JournalEntryNotFoundError):
        js.get_entry(e.id)


def test_list_filtering(svc, storage):
    ws = storage.create_workspace("jf-ws", "")
    js = JournalService(storage)
    js.create_entry(JournalEntryCreate(workspace_id=ws.id, title="A", markdown="alpha", tags=["x"]))
    js.create_entry(JournalEntryCreate(workspace_id=ws.id, title="B", markdown="beta", tags=["y"]))

    assert len(js.list_entries(ws.id)) == 2
    assert len(js.list_entries(ws.id, tag="x")) == 1
    assert len(js.list_entries(ws.id, query="alpha")) == 1


def test_transaction_rollback(storage):
    ws = storage.create_workspace("tj-ws", "")
    js = JournalService(storage)
    js.create_entry(JournalEntryCreate(workspace_id=ws.id, title="Keep"))
    # Force a storage-layer failure inside a transaction
    with pytest.raises(Exception):
        with storage.transaction():
            storage.create_journal_entry(
                workspace_id=ws.id, repository_id=None, title="Rollback",
                summary="", markdown="", entry_date="", tags=[],
            )
            raise RuntimeError("simulated")
    assert len(js.list_entries(ws.id)) == 1
