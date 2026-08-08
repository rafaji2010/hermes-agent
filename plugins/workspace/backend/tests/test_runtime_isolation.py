"""Profile isolation tests for the Workspace runtime (U1D-A).

Proves that the profile-scoped WorkspaceRuntime eliminates first-profile
pinning: two effective Hermes homes in the SAME process resolve distinct
runtimes, distinct ``workspace.db`` files, and distinct audit logs, with
no data crossing between profiles.

Uses real temporary HERMES_HOME directories and the sanctioned upstream
profile-switching mechanism (``set_hermes_home_override`` /
``reset_hermes_home_override`` context-local overrides).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes_constants import (  # type: ignore[import-untyped]
    get_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    WorkspaceCreate,
)
from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
    WorkspaceRuntime,
    get_workspace_runtime,
    reset_workspace_runtimes,
)


@contextmanager
def _effective_home(home: Path) -> Iterator[None]:
    """Run inside a context-local HERMES_HOME override (upstream mechanism)."""
    token = set_hermes_home_override(str(home))
    try:
        assert Path(get_hermes_home()).resolve() == Path(home).resolve()
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture(autouse=True)
def _unpin_runtime():
    """These tests exercise the real home-keyed path — unpin first.

    The plugins conftest pins an in-memory runtime for ordinary tests;
    this fixture clears it before and after so lookups resolve through
    the effective-home cache instead.
    """
    reset_workspace_runtimes()
    yield
    reset_workspace_runtimes()


def _create_workspace(rt: WorkspaceRuntime, name: str) -> None:
    rt.workspace_service.create_workspace(WorkspaceCreate(name=name, path=""))


def _workspace_names(rt: WorkspaceRuntime) -> set:
    return {w.name for w in rt.workspace_service.list_workspaces()}


def _audit_markers(rt: WorkspaceRuntime) -> list:
    """Return the distinct test markers found in the runtime's audit log."""
    markers = []
    for event in rt.audit.read():
        details = event.get("details") or {}
        marker = details.get("marker")
        if marker:
            markers.append(marker)
    return markers


# ---------------------------------------------------------------------------
# Core A → B → A isolation scenario
# ---------------------------------------------------------------------------


def test_profile_a_and_b_isolated_in_same_process(tmp_path):
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"

    # ── Profile A ────────────────────────────────────────────────────────
    with _effective_home(home_a):
        rt_a = get_workspace_runtime()
        assert isinstance(rt_a, WorkspaceRuntime)
        # workspace.db belongs to A
        assert rt_a.database.db_path == home_a / "workspace.db"
        assert rt_a.audit.path == home_a / "logs" / "audit.log"
        rt_a.database.get_connection()
        assert (home_a / "workspace.db").exists()

        _create_workspace(rt_a, "profile-a-ws")
        rt_a.audit.log(
            action="test.profile_isolation",
            status="ALLOW",
            details={"marker": "A"},
        )

    # ── Profile B (same process, different effective home) ───────────────
    with _effective_home(home_b):
        rt_b = get_workspace_runtime()
        # B gets a DIFFERENT runtime and a DIFFERENT database file
        assert rt_b is not rt_a
        assert rt_b.database.db_path == home_b / "workspace.db"
        assert rt_b.audit.path == home_b / "logs" / "audit.log"
        rt_b.database.get_connection()
        assert (home_b / "workspace.db").exists()

        # A's Workspace data is invisible to B
        assert _workspace_names(rt_b) == set()

        _create_workspace(rt_b, "profile-b-ws")
        rt_b.audit.log(
            action="test.profile_isolation",
            status="ALLOW",
            details={"marker": "B"},
        )

    # ── Back to A: the SAME runtime, data intact, B invisible ────────────
    with _effective_home(home_a):
        rt_a_again = get_workspace_runtime()
        assert rt_a_again is rt_a
        assert _workspace_names(rt_a_again) == {"profile-a-ws"}
        assert _audit_markers(rt_a_again) == ["A"]

    # ── And back to B: same runtime, B data intact, A invisible ──────────
    with _effective_home(home_b):
        rt_b_again = get_workspace_runtime()
        assert rt_b_again is rt_b
        assert _workspace_names(rt_b_again) == {"profile-b-ws"}
        assert _audit_markers(rt_b_again) == ["B"]

    # ── Audit state never crosses profiles ───────────────────────────────
    assert (home_a / "logs" / "audit.log").exists()
    assert (home_b / "logs" / "audit.log").exists()
    assert _audit_markers(rt_a_again) == ["A"]
    assert _audit_markers(rt_b_again) == ["B"]


# ---------------------------------------------------------------------------
# Cache / lifecycle semantics
# ---------------------------------------------------------------------------


def test_same_home_reuses_runtime(tmp_path):
    home = tmp_path / "profile-a"

    with _effective_home(home):
        rt1 = get_workspace_runtime()
        rt2 = get_workspace_runtime()
        assert rt1 is rt2

    # A different home still resolves a different runtime
    other = tmp_path / "other"
    with _effective_home(other):
        rt_other = get_workspace_runtime()
        assert rt_other is not rt1


def test_reset_clears_runtimes_and_closes_database(tmp_path):
    home = tmp_path / "profile-a"

    with _effective_home(home):
        rt1 = get_workspace_runtime()
        rt1.database.get_connection()
        assert rt1.database.is_initialised
        _create_workspace(rt1, "persisted-ws")

        reset_workspace_runtimes()
        # close() releases the runtime's database handle
        assert rt1.database.is_initialised is False

        # A fresh lookup rebuilds a NEW runtime for the same home…
        rt2 = get_workspace_runtime()
        assert rt2 is not rt1
        # …whose database file still holds A's data (file persists)
        assert _workspace_names(rt2) == {"persisted-ws"}


def test_runtime_database_lives_under_effective_home(tmp_path):
    """Sanity: the runtime pins workspace.db to its home, not the first home."""
    home_a = tmp_path / "a"
    home_b = tmp_path / "b"

    with _effective_home(home_a):
        rt_a = get_workspace_runtime()
    with _effective_home(home_b):
        rt_b = get_workspace_runtime()

    assert rt_a.database.db_path != rt_b.database.db_path
    assert str(rt_a.database.db_path).startswith(str(home_a))
    assert str(rt_b.database.db_path).startswith(str(home_b))
