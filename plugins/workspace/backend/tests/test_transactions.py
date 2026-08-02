"""Unit tests for transaction support.

Covers:
    - Successful commit of multiple writes in one transaction
    - Rollback on exception (nothing persisted)
    - Nested transactions via savepoints
    - Transaction isolation (concurrent readers see pre-commit state)
    - Storage-level ``begin_transaction`` / ``commit`` / ``rollback`` API
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import DuplicateWorkspaceError  # type: ignore[import-untyped]
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Successful commit
# ---------------------------------------------------------------------------


def test_transaction_commits_on_success(storage: SQLiteStorage):
    with storage.transaction():
        storage.create_workspace("tx-commit", "")

    assert storage.get_workspace_by_name("tx-commit") is not None


def test_transaction_multiple_writes(storage: SQLiteStorage):
    with storage.transaction():
        ws = storage.create_workspace("multi-write", "")
        storage.register_repository(
            workspace_id=ws.id,
            name="r1",
            path="/tmp/r1",
            git_root="/tmp/r1",
            default_branch="main",
        )
        storage.register_repository(
            workspace_id=ws.id,
            name="r2",
            path="/tmp/r2",
            git_root="/tmp/r2",
            default_branch="main",
        )

    repos = storage.list_repositories(ws.id)
    assert len(repos) == 2


# ---------------------------------------------------------------------------
# Rollback on exception
# ---------------------------------------------------------------------------


def test_transaction_rollback_on_error(storage: SQLiteStorage):
    with pytest.raises(ValueError, match="simulated"):
        with storage.transaction():
            storage.create_workspace("rollback-me", "")
            raise ValueError("simulated failure")

    assert storage.get_workspace_by_name("rollback-me") is None


def test_transaction_rollback_preserves_previous_state(storage: SQLiteStorage):
    storage.create_workspace("pre-existing", "")

    with pytest.raises(ValueError):
        with storage.transaction():
            storage.create_workspace("should-vanish", "")
            raise ValueError("boom")

    # pre-existing workspace must still be there
    assert storage.get_workspace_by_name("pre-existing") is not None
    # rolled-back workspace must NOT exist
    assert storage.get_workspace_by_name("should-vanish") is None


def test_duplicate_error_triggers_rollback(storage: SQLiteStorage):
    """A `DuplicateWorkspaceError` inside a transaction must not leave
    partial state."""
    storage.create_workspace("first", "")

    # This transaction creates "second" then fails on "first" duplicate.
    with pytest.raises(DuplicateWorkspaceError):
        with storage.transaction():
            storage.create_workspace("second", "")
            storage.create_workspace("first", "")  # duplicate → raises

    # "second" must NOT exist because the whole transaction rolled back.
    assert storage.get_workspace_by_name("second") is None


# ---------------------------------------------------------------------------
# Nested transactions (savepoints)
# ---------------------------------------------------------------------------


def test_nested_transaction_inner_rollback(storage: SQLiteStorage):
    """Inner savepoint rollback must NOT undo outer writes."""
    with storage.transaction():
        storage.create_workspace("outer", "")
        with pytest.raises(ValueError):
            with storage.transaction():
                storage.create_workspace("inner-vanish", "")
                raise ValueError("inner failure")
        # inner rollback — outer still in progress

    # Both must have committed: outer succeeded, inner rolled back
    assert storage.get_workspace_by_name("outer") is not None
    assert storage.get_workspace_by_name("inner-vanish") is None


def test_nested_transaction_both_succeed(storage: SQLiteStorage):
    with storage.transaction():
        storage.create_workspace("level-1", "")
        with storage.transaction():
            storage.create_workspace("level-2", "")

    assert storage.get_workspace_by_name("level-1") is not None
    assert storage.get_workspace_by_name("level-2") is not None


def test_nested_transaction_depth_tracking(storage: SQLiteStorage):
    """Depth must return to zero after nested transactions exit."""
    assert storage.in_transaction is False
    assert storage._transaction_depth == 0

    with storage.transaction():
        assert storage.in_transaction is True
        assert storage._transaction_depth == 1

        with storage.transaction():
            assert storage.in_transaction is True
            assert storage._transaction_depth == 2

        assert storage._transaction_depth == 1

    assert storage.in_transaction is False
    assert storage._transaction_depth == 0


# ---------------------------------------------------------------------------
# Explicit API (begin / commit / rollback)
# ---------------------------------------------------------------------------


def test_explicit_begin_commit(storage: SQLiteStorage):
    storage.begin_transaction()
    storage.create_workspace("explicit", "")
    storage.commit()

    assert storage.get_workspace_by_name("explicit") is not None


def test_explicit_begin_rollback(storage: SQLiteStorage):
    storage.begin_transaction()
    storage.create_workspace("gone", "")
    storage.rollback()

    assert storage.get_workspace_by_name("gone") is None


def test_commit_without_transaction_raises(storage: SQLiteStorage):
    with pytest.raises(RuntimeError, match="no active transaction"):
        storage.commit()


def test_rollback_without_transaction_raises(storage: SQLiteStorage):
    with pytest.raises(RuntimeError, match="no active transaction"):
        storage.rollback()


# ---------------------------------------------------------------------------
# Transaction isolation
# ---------------------------------------------------------------------------


def test_reads_inside_transaction_see_uncommitted_writes(storage: SQLiteStorage):
    """Within a transaction, writes are visible to the same connection."""
    with storage.transaction():
        storage.create_workspace("visible-inside", "")
        found = storage.get_workspace_by_name("visible-inside")
        assert found is not None


def test_no_auto_commit_inside_transaction(storage: SQLiteStorage):
    """Individual write methods do NOT auto-commit when inside a tx."""
    storage.begin_transaction()
    storage.create_workspace("not-yet-committed", "")
    # … no commit() yet …
    storage.rollback()

    assert storage.get_workspace_by_name("not-yet-committed") is None


# ---------------------------------------------------------------------------
# WorkspaceService already wraps in transactions
# ---------------------------------------------------------------------------


def test_service_write_uses_transaction(svc, storage: SQLiteStorage):
    """WorkspaceService.create_workspace must persist (proving its
    internal transaction committed)."""
    from plugins.workspace.backend.models import WorkspaceCreate  # type: ignore[import-untyped]

    ws = svc.create_workspace(WorkspaceCreate(name="svc-tx-test"))
    found = storage.get_workspace(ws.id)
    assert found is not None
    assert found.name == "svc-tx-test"
