"""Unit tests for journal storage layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    JournalEntryNotFoundError,
)


def test_create_entry(storage):
    ws = storage.create_workspace("j-ws", "")
    entry = storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Daily Standup",
        summary="Quick sync", markdown="# Notes\n\nDone.", entry_date="2026-07-01",
        tags=["standup", "daily"],
    )
    assert entry.id
    assert entry.title == "Daily Standup"
    assert entry.summary == "Quick sync"
    assert entry.markdown == "# Notes\n\nDone."
    assert entry.entry_date == "2026-07-01"
    assert set(entry.tags) == {"daily", "standup"}


def test_get_entry(storage):
    ws = storage.create_workspace("jg-ws", "")
    e = storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="T", summary="S",
        markdown="M", entry_date="2026-01-01", tags=["a"],
    )
    found = storage.get_journal_entry(e.id)
    assert found is not None
    assert found.title == "T"
    assert found.tags == ["a"]


def test_get_entry_missing(storage):
    assert storage.get_journal_entry("nonexistent") is None


def test_update_entry(storage):
    ws = storage.create_workspace("ju-ws", "")
    e = storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Old", summary="",
        markdown="", entry_date="2026-01-01", tags=[],
    )
    updated = storage.update_journal_entry(
        e.id, title="New", summary="Updated", markdown="Body", tags=["updated"],
    )
    assert updated.title == "New"
    assert updated.summary == "Updated"
    assert updated.markdown == "Body"
    assert updated.tags == ["updated"]


def test_update_entry_missing(storage):
    with pytest.raises(JournalEntryNotFoundError):
        storage.update_journal_entry("nonexistent", title="X")


def test_delete_entry(storage):
    ws = storage.create_workspace("jd-ws", "")
    e = storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Gone", summary="",
        markdown="", entry_date="", tags=[],
    )
    storage.delete_journal_entry(e.id)
    assert storage.get_journal_entry(e.id) is None


def test_delete_entry_missing(storage):
    with pytest.raises(JournalEntryNotFoundError):
        storage.delete_journal_entry("nonexistent")


def test_list_entries_filtering(storage):
    ws = storage.create_workspace("jf-ws", "")
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Alpha",
        summary="alpha summary", markdown="alpha body", entry_date="2026-07-01", tags=["x"],
    )
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Beta",
        summary="beta", markdown="beta stuff", entry_date="2026-07-02", tags=["y"],
    )
    all_entries = storage.list_journal_entries(ws.id)
    assert len(all_entries) == 2
    assert all_entries[0].title == "Beta"  # newest first

    by_tag = storage.list_journal_entries(ws.id, tag="x")
    assert len(by_tag) == 1

    by_date = storage.list_journal_entries(ws.id, entry_date="2026-07-01")
    assert len(by_date) == 1

    by_query = storage.list_journal_entries(ws.id, query="alpha summary")
    assert len(by_query) == 1

    limited = storage.list_journal_entries(ws.id, limit=1)
    assert len(limited) == 1


def test_tag_counts(storage):
    ws = storage.create_workspace("jt-ws", "")
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="A", summary="",
        markdown="", entry_date="", tags=["tag1", "tag2"],
    )
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="B", summary="",
        markdown="", entry_date="", tags=["tag2", "tag3"],
    )
    tags = storage.get_journal_tag_counts(ws.id)
    assert set(tags) == {"tag1", "tag2", "tag3"}
