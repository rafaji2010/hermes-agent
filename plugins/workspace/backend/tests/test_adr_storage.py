"""Unit tests for ADR storage operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ADRNotFoundError,
    DuplicateSlugError,
)


def test_create_adr(storage):
    ws = storage.create_workspace("adr-ws", "")
    adr = storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="Use SQLite",
        slug="use-sqlite",
        status="proposed",
        category="Architecture",
        markdown="# ADR\n\nWe chose SQLite.",
        tags=["database", "architecture"],
    )
    assert adr.id
    assert adr.title == "Use SQLite"
    assert adr.slug == "use-sqlite"
    assert adr.status == "proposed"
    assert adr.markdown == "# ADR\n\nWe chose SQLite."
    assert set(adr.tags) == {"architecture", "database"}


def test_create_adr_duplicate_slug(storage):
    ws = storage.create_workspace("slug-ws", "")
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="A",
        slug="my-slug",
        status="proposed",
        category="",
        markdown="",
        tags=[],
    )
    with pytest.raises(DuplicateSlugError):
        storage.create_adr(
            workspace_id=ws.id,
            repository_id=None,
            title="B",
            slug="my-slug",
            status="proposed",
            category="",
            markdown="",
            tags=[],
        )


def test_get_adr(storage):
    ws = storage.create_workspace("get-adr-ws", "")
    adr = storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="Test",
        slug="test",
        status="accepted",
        category="Backend",
        markdown="body",
        tags=["tag1"],
    )
    found = storage.get_adr(adr.id)
    assert found is not None
    assert found.title == "Test"
    assert found.markdown == "body"
    assert found.tags == ["tag1"]


def test_get_adr_missing(storage):
    assert storage.get_adr("nonexistent") is None


def test_update_adr(storage):
    ws = storage.create_workspace("update-ws", "")
    adr = storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="Old",
        slug="old-slug",
        status="proposed",
        category="",
        markdown="old body",
        tags=[],
    )
    updated = storage.update_adr(
        adr.id,
        title="New",
        status="accepted",
        markdown="new body",
        tags=["updated"],
    )
    assert updated.title == "New"
    assert updated.status == "accepted"
    assert updated.markdown == "new body"
    assert updated.tags == ["updated"]


def test_update_adr_missing(storage):
    with pytest.raises(ADRNotFoundError):
        storage.update_adr("nonexistent", title="X")


def test_delete_adr(storage):
    ws = storage.create_workspace("del-ws", "")
    adr = storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="Delete Me",
        slug="delete-me",
        status="proposed",
        category="",
        markdown="",
        tags=[],
    )
    storage.delete_adr(adr.id)
    assert storage.get_adr(adr.id) is None


def test_delete_adr_missing(storage):
    with pytest.raises(ADRNotFoundError):
        storage.delete_adr("nonexistent")


def test_list_adrs(storage):
    ws = storage.create_workspace("list-adr-ws", "")
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="B",
        slug="b",
        status="accepted",
        category="Backend",
        markdown="",
        tags=["backend"],
    )
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="A",
        slug="a",
        status="proposed",
        category="Frontend",
        markdown="react",
        tags=["frontend"],
    )
    all_adrs = storage.list_adrs(ws.id)
    assert len(all_adrs) == 2

    filtered = storage.list_adrs(ws.id, status="accepted")
    assert len(filtered) == 1
    assert filtered[0].title == "B"

    by_tag = storage.list_adrs(ws.id, tag="frontend")
    assert len(by_tag) == 1

    by_query = storage.list_adrs(ws.id, query="react")
    assert len(by_query) == 1


def test_get_adr_by_slug(storage):
    ws = storage.create_workspace("slug-lookup", "")
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="X",
        slug="my-adr",
        status="proposed",
        category="",
        markdown="",
        tags=[],
    )
    found = storage.get_adr_by_slug(ws.id, "my-adr")
    assert found is not None

    missing = storage.get_adr_by_slug(ws.id, "nonexistent")
    assert missing is None


def test_tags(storage):
    ws = storage.create_workspace("tag-ws", "")
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="T1",
        slug="t1",
        status="proposed",
        category="",
        markdown="",
        tags=["alpha", "beta"],
    )
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="T2",
        slug="t2",
        status="proposed",
        category="",
        markdown="",
        tags=["beta", "gamma"],
    )
    tags = storage.get_distinct_tags(ws.id)
    assert set(tags) == {"alpha", "beta", "gamma"}


def test_categories(storage):
    ws = storage.create_workspace("cat-ws", "")
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="C1",
        slug="c1",
        status="proposed",
        category="Architecture",
        markdown="",
        tags=[],
    )
    storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="C2",
        slug="c2",
        status="proposed",
        category="Database",
        markdown="",
        tags=[],
    )
    cats = storage.get_distinct_categories(ws.id)
    assert set(cats) == {"Architecture", "Database"}
