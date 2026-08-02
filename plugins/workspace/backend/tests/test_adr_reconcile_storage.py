"""Unit tests for ADR reconciliation storage operations (S7.3A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import ADRNotFoundError  # type: ignore[import-untyped]


def _mk_adr(storage):
    ws = storage.create_workspace("recon-storage-ws", "")
    return ws, storage.create_adr(
        workspace_id=ws.id,
        repository_id=None,
        title="Proj",
        slug="proj",
        status="proposed",
        category="",
        markdown="# Proj\n",
        tags=[],
    )


def test_defaults_legacy(storage):
    ws, adr = _mk_adr(storage)
    assert adr.reconcile_state == "db_legacy"
    assert adr.source == "workspace_db"
    assert adr.canonical_path == ""
    assert adr.content_hash == ""


def test_update_reconcile_meta(storage):
    ws, adr = _mk_adr(storage)
    updated = storage.update_adr_reconcile_meta(
        adr.id,
        canonical_path="docs/adr/0001-proj.md",
        content_hash="abc123",
        reconcile_state="synced",
        source="git_file",
        last_indexed="2026-08-02T00:00:00Z",
        last_error="",
    )
    assert updated.canonical_path == "docs/adr/0001-proj.md"
    assert updated.content_hash == "abc123"
    assert updated.reconcile_state == "synced"
    assert updated.source == "git_file"
    assert updated.last_indexed == "2026-08-02T00:00:00Z"

    read = storage.get_adr(adr.id)
    assert read.source == "git_file"
    assert read.reconcile_state == "synced"


def test_update_reconcile_meta_partial(storage):
    ws, adr = _mk_adr(storage)
    storage.update_adr_reconcile_meta(adr.id, reconcile_state="missing_file")
    read = storage.get_adr(adr.id)
    assert read.reconcile_state == "missing_file"
    assert read.source == "workspace_db"  # untouched
    assert read.canonical_path == ""  # untouched


def test_update_reconcile_meta_missing_adr(storage):
    with pytest.raises(ADRNotFoundError):
        storage.update_adr_reconcile_meta("nope", reconcile_state="synced")


def test_find_adr_by_canonical_path(storage):
    ws, adr = _mk_adr(storage)
    assert storage.find_adr_by_canonical_path(ws.id, "docs/adr/0001-proj.md") is None
    storage.update_adr_reconcile_meta(
        adr.id, canonical_path="docs/adr/0001-proj.md", source="git_file"
    )
    found = storage.find_adr_by_canonical_path(ws.id, "docs/adr/0001-proj.md")
    assert found is not None
    assert found.id == adr.id
    assert found.source == "git_file"


def test_find_adr_by_canonical_path_workspace_isolation(storage):
    ws_a = storage.create_workspace("iso-a", "")
    ws_b = storage.create_workspace("iso-b", "")
    a = storage.create_adr(
        workspace_id=ws_a.id, repository_id=None, title="A",
        slug="a", status="proposed", category="", markdown="# A\n", tags=[],
    )
    storage.update_adr_reconcile_meta(
        a.id, canonical_path="docs/adr/0001-a.md", source="git_file"
    )
    # Same canonical path in another workspace must NOT match.
    assert storage.find_adr_by_canonical_path(ws_b.id, "docs/adr/0001-a.md") is None


def test_update_adr_preserves_reconcile_fields(storage):
    """Ordinary metadata updates must not clobber projection fields."""
    ws, adr = _mk_adr(storage)
    storage.update_adr_reconcile_meta(
        adr.id, canonical_path="docs/adr/0001-proj.md",
        content_hash="h1", reconcile_state="synced", source="git_file",
    )
    updated = storage.update_adr(adr.id, status="accepted")
    assert updated.status == "accepted"
    assert updated.source == "git_file"
    assert updated.reconcile_state == "synced"
    assert updated.content_hash == "h1"
