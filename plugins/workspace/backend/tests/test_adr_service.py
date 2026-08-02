"""Unit tests for ADRService business logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ADRCreate,
    ADRUpdate,
    ADRNotFoundError,
    InvalidADRStatusError,
    WorkspaceNotFoundError,
)
from plugins.workspace.backend.services.adr_service import (  # type: ignore[import-untyped]
    ADRService,
    _generate_slug,
    _unique_slug,
)


def test_generate_slug():
    assert _generate_slug("Use SQLite for Storage") == "use-sqlite-for-storage"
    assert _generate_slug("ADR: Decision #1") == "adr-decision-1"
    assert _generate_slug("  Spaces  & Special!!!  ") == "spaces-special"
    assert _generate_slug("simple") == "simple"


def test_unique_slug(storage):
    ws = storage.create_workspace("slug-test", "")
    svc = ADRService(storage)
    svc.create_adr(ADRCreate(workspace_id=ws.id, title="Test", status="proposed"))
    svc.create_adr(ADRCreate(workspace_id=ws.id, title="Test", status="proposed"))
    adrs = svc.list_adrs(ws.id)
    slugs = {a.slug for a in adrs}
    assert "test" in slugs
    assert "test-2" in slugs


def test_create_adr_validation(svc, storage):
    ws = storage.create_workspace("val-ws", "")
    adr_svc = ADRService(storage)
    # empty title — caught by Pydantic min_length=1 on ADRCreate
    with pytest.raises(Exception):
        adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title=""))
    # invalid status
    with pytest.raises(InvalidADRStatusError):
        adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="X", status="bogus"))
    # missing workspace
    with pytest.raises(WorkspaceNotFoundError):
        adr_svc.create_adr(ADRCreate(workspace_id="nonexistent", title="X"))


def test_create_and_get_adr(svc, storage):
    ws = storage.create_workspace("crud-ws", "")
    adr_svc = ADRService(storage)
    adr = adr_svc.create_adr(
        ADRCreate(
            workspace_id=ws.id,
            title="My ADR",
            status="proposed",
            category="Architecture",
            markdown="# Title\n\nContent.",
            tags=["arch", "db"],
        )
    )
    assert adr.title == "My ADR"
    assert adr.slug == "my-adr"
    assert adr.markdown == "# Title\n\nContent."
    assert set(adr.tags) == {"arch", "db"}

    found = adr_svc.get_adr(adr.id)
    assert found.title == "My ADR"


def test_update_adr(svc, storage):
    ws = storage.create_workspace("upd-ws", "")
    adr_svc = ADRService(storage)
    adr = adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="Old"))
    updated = adr_svc.update_adr(
        adr.id,
        ADRUpdate(title="New", status="accepted", markdown="updated body", tags=["new-tag"]),
    )
    assert updated.title == "New"
    assert updated.slug == "new"
    assert updated.status == "accepted"
    assert updated.markdown == "updated body"
    assert updated.tags == ["new-tag"]


def test_delete_adr(svc, storage):
    ws = storage.create_workspace("del-ws", "")
    adr_svc = ADRService(storage)
    adr = adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="Gone"))
    adr_svc.delete_adr(adr.id)
    with pytest.raises(ADRNotFoundError):
        adr_svc.get_adr(adr.id)


def test_list_adrs_filtering(svc, storage):
    ws = storage.create_workspace("filter-ws", "")
    adr_svc = ADRService(storage)
    adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="A", status="accepted", markdown="alpha"))
    adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="B", status="proposed", markdown="beta", tags=["x"]))

    assert len(adr_svc.list_adrs(ws.id)) == 2
    assert len(adr_svc.list_adrs(ws.id, status="accepted")) == 1
    assert len(adr_svc.list_adrs(ws.id, tag="x")) == 1
    assert len(adr_svc.list_adrs(ws.id, query="alpha")) == 1


def test_adr_transaction_rollback(svc, storage):
    """Duplicate slug within a transaction must not leave partial ADR state."""
    ws = storage.create_workspace("tx-ws", "")
    adr_svc = ADRService(storage)
    adr_svc.create_adr(ADRCreate(workspace_id=ws.id, title="Unique"))

    # This should fail inside a transaction — the content row must roll back.
    with pytest.raises(Exception):
        with storage.transaction():
            # Force a duplicate slug by manually calling storage layer
            storage.create_adr(
                workspace_id=ws.id,
                repository_id=None,
                title="Conflict",
                slug="unique",  # already taken
                status="proposed",
                category="",
                markdown="should not persist",
                tags=[],
            )

    adrs = adr_svc.list_adrs(ws.id)
    assert len(adrs) == 1
