"""Project / session / profile authority resolution tests (U1D-D).

Uses REAL temporary HERMES_HOME directories with a real ``projects.db``
(``hermes_cli.projects_db``), a real durable ``state.db`` (SessionDB
schema), and a real ``workspace.db`` — proving the authority model
against the actual upstream mechanisms rather than fakes.

Authority under test:
    profile/home (outer boundary)
      -> durable session context (read-only SessionDB)
      -> explicit CWD / session CWD / git root
      -> Hermes Project (projects.db)
      -> Workspace (reverse mapping, ambiguity fail-closed)
"""

from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes_constants import (  # type: ignore[import-untyped]
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_cli import projects_db  # type: ignore[import-untyped]
from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ScopeResolveRequest,
)
from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
    reset_workspace_runtimes,
)
from plugins.workspace.backend.services.scope_resolver import (  # type: ignore[import-untyped]
    ProjectScopeResolver,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


@contextmanager
def _effective_home(home: Path) -> Iterator[None]:
    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture(autouse=True)
def _unpin_runtime():
    reset_workspace_runtimes()
    yield
    reset_workspace_runtimes()


def _add_project(home: Path, name: str, folders) -> str:
    """Create a real project in the home's projects.db; return its id."""
    with projects_db.connect_closing(home / "projects.db") as conn:
        pid = projects_db.create_project(
            conn, name=name, folders=folders, primary_path=folders[0]
        )
    return pid


def _archive_project(home: Path, project_id: str) -> None:
    with projects_db.connect_closing(home / "projects.db") as conn:
        projects_db.archive_project(conn, project_id)


def _add_session(home: Path, session_id: str, cwd: str, git_root: str = "",
                 archived: int = 0) -> None:
    """Insert a durable session row into the home's state.db (SessionDB schema)."""
    db = home / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                started_at REAL NOT NULL,
                cwd TEXT,
                git_repo_root TEXT,
                profile_name TEXT,
                archived INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, source, started_at, cwd, git_repo_root, profile_name, archived) "
            "VALUES (?, 'test', ?, ?, ?, '', ?)",
            (session_id, time.time(), cwd, git_root, archived),
        )
        conn.commit()
    finally:
        conn.close()


class _Env:
    """A fully materialized authority environment for one home."""

    def __init__(self, home: Path):
        self.home = home
        home.mkdir(parents=True, exist_ok=True)
        self.db = DatabaseManager(home / "workspace.db")
        self.storage = SQLiteStorage(db_manager=self.db)
        self.resolver = ProjectScopeResolver(storage=self.storage)

    def close(self) -> None:
        self.db.close()

    # -- workspace helpers ------------------------------------------------

    def workspace(self, name: str, project_id: str | None = None) -> str:
        ws = self.storage.create_workspace(name, "")
        if project_id:
            self.storage.link_project(ws.id, project_id)
        return ws.id

    # -- resolution ---------------------------------------------------------

    def resolve(self, **kwargs):
        with _effective_home(self.home):
            return self.resolver.resolve(ScopeResolveRequest(**kwargs))


@pytest.fixture
def env_a(tmp_path) -> Iterator[_Env]:
    e = _Env(tmp_path / "home-a")
    yield e
    e.close()


@pytest.fixture
def env_b(tmp_path) -> Iterator[_Env]:
    e = _Env(tmp_path / "home-b")
    yield e
    e.close()


# ---------------------------------------------------------------------------
# A. Valid resolution
# ---------------------------------------------------------------------------


def test_active_session_resolves_to_mapped_workspace(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)
    _add_session(env_a.home, "stored-1", "/work/p1/sub", "/work/p1")

    result = env_a.resolve(session_id="stored-1")
    assert result.state == "mapped"
    assert result.workspace_id == ws
    assert result.project_id == project
    assert result.match_source == "session_cwd"


def test_explicit_cwd_resolves_to_workspace(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)

    result = env_a.resolve(cwd="/work/p1")
    assert result.state == "mapped"
    assert result.workspace_id == ws


def test_nested_path_resolves_to_workspace(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)

    result = env_a.resolve(cwd="/work/p1/a/b/c")
    assert result.state == "mapped"
    assert result.workspace_id == ws


def test_project_without_workspace_is_partial(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])

    result = env_a.resolve(cwd="/work/p1")
    assert result.state == "partial"
    assert result.project_id == project
    assert result.workspace_id == ""


# ---------------------------------------------------------------------------
# B. Runtime vs durable identity
# ---------------------------------------------------------------------------


def test_volatile_runtime_id_never_resolves_durable_session(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "stored-key-1", "/work/p1", "/work/p1")

    # The volatile Desktop runtime id is NOT the durable row id.
    result = env_a.resolve(session_id="volatile-runtime-abc123")
    assert result.state == "unresolved"

    # The durable row id resolves.
    result = env_a.resolve(session_id="stored-key-1")
    assert result.state == "mapped"


def test_unknown_durable_session_fails_closed(env_a):
    result = env_a.resolve(session_id="no-such-session")
    assert result.state == "unresolved"


# ---------------------------------------------------------------------------
# C. Archived / stale / missing
# ---------------------------------------------------------------------------


def test_archived_session_is_not_authoritative(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "old-session", "/work/p1", "/work/p1", archived=1)

    result = env_a.resolve(session_id="old-session")
    assert result.state == "unresolved"


def test_session_with_missing_cwd_fails_closed(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "empty-cwd", "", "")
    _add_session(env_a.home, "ghost-cwd", "/work/does-not-exist", "")

    assert env_a.resolve(session_id="empty-cwd").state == "unresolved"
    assert env_a.resolve(session_id="ghost-cwd").state == "unresolved"


def test_archived_project_mapping_is_stale(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)
    _archive_project(env_a.home, project)

    # Explicit mapping to an archived project is stale -> unmapped.
    result = env_a.resolve(workspace_id=ws)
    assert result.state == "unmapped"
    assert result.project_id is None

    # Path evidence for an archived project resolves nothing.
    result = env_a.resolve(cwd="/work/p1")
    assert result.state == "unresolved"


def test_deleted_project_mapping_is_stale(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)
    with projects_db.connect_closing(env_a.home / "projects.db") as conn:
        projects_db.delete_project(conn, project)

    result = env_a.resolve(workspace_id=ws)
    assert result.state == "unmapped"


# ---------------------------------------------------------------------------
# D. Ambiguity / conflicting signals
# ---------------------------------------------------------------------------


def test_duplicate_project_mapping_fails_closed(env_a):
    """Legacy/corrupt duplicate mappings must resolve to AMBIGUOUS, never
    a silent first-row choice.  ``link_project`` already forbids creating
    duplicates, so the corrupt state is seeded via raw SQL."""
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    w1 = env_a.workspace("w-a", project)
    w2 = env_a.workspace("w-b")
    conn = env_a.db.get_connection()
    conn.execute("UPDATE workspaces SET hermes_project_id = ? WHERE id = ?", (project, w2))
    conn.commit()

    result = env_a.resolve(cwd="/work/p1")
    assert result.state == "ambiguous"
    assert result.workspace_id == ""


def test_explicit_cwd_overrides_conflicting_session_git_root(env_a):
    p1 = _add_project(env_a.home, "proj-a", ["/work/p1"])
    _add_project(env_a.home, "proj-b", ["/work/p2"])
    env_a.workspace("w-a", p1)
    _add_session(env_a.home, "sess-mixed", "/work/p1", "/work/p2")

    # Explicit request cwd is authoritative — the session's conflicting
    # git root (project B) must not win.
    result = env_a.resolve(session_id="sess-mixed", cwd="/work/p1")
    assert result.project_id == p1
    assert result.match_source == "explicit_cwd"


def test_explicit_cwd_that_resolves_nothing_does_not_fall_back_to_git_root(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "sess-stale", "/work/p1", "/work/p1")

    # Explicit cwd points outside any project: fail closed — do NOT use
    # the session's stale git root as a conflicting fallback.
    result = env_a.resolve(session_id="sess-stale", cwd="/elsewhere")
    assert result.state == "unresolved"


# ---------------------------------------------------------------------------
# E. Profile boundary
# ---------------------------------------------------------------------------


def test_same_session_id_in_profiles_a_and_b_does_not_cross(env_a, env_b, tmp_path):
    shared = str(tmp_path / "shared-repo")

    pa = _add_project(env_a.home, "proj-a", [shared])
    pb = _add_project(env_b.home, "proj-b", [shared])
    wa = env_a.workspace("w-a", pa)
    wb = env_b.workspace("w-b", pb)
    # IDENTICAL durable session id and IDENTICAL cwd in both profiles.
    _add_session(env_a.home, "sess-1", shared, shared)
    _add_session(env_b.home, "sess-1", shared, shared)

    assert env_a.resolve(session_id="sess-1").workspace_id == wa
    assert env_b.resolve(session_id="sess-1").workspace_id == wb
    # A -> B -> A returns each profile's own Workspace.
    assert env_a.resolve(session_id="sess-1").workspace_id == wa


def test_session_only_in_other_profile_is_unresolved(env_a, env_b, tmp_path):
    shared = str(tmp_path / "shared-repo")
    _add_project(env_b.home, "proj-b", [shared])
    env_b.workspace("w-b", _add_project(env_b.home, "proj-b2", [shared]))
    _add_session(env_b.home, "b-only", shared, shared)

    # Profile A has no such session row -> fail closed.
    result = env_a.resolve(session_id="b-only")
    assert result.state == "unresolved"


# ---------------------------------------------------------------------------
# F. CWD edge cases / process cwd
# ---------------------------------------------------------------------------


def test_no_cwd_no_session_is_unresolved(env_a):
    result = env_a.resolve()
    assert result.state == "unresolved"


def test_process_cwd_is_not_authority(env_a, tmp_path, monkeypatch):
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    project = _add_project(env_a.home, "proj-a", [str(proj_dir)])
    env_a.workspace("w-a", project)
    # The process working directory IS the project folder…
    monkeypatch.chdir(proj_dir)
    # …but nothing anchors the request, so it must NOT silently resolve.
    result = env_a.resolve()
    assert result.state == "unresolved"


def test_repository_root_and_subtree_resolve_identically(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a", project)

    assert env_a.resolve(cwd="/work/p1").workspace_id == ws
    assert env_a.resolve(cwd="/work/p1/deep/nested/dir").workspace_id == ws


# ---------------------------------------------------------------------------
# G. Scope operations through the API (real runtime, real resolver)
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(env_a, tmp_path):
    """Mount the workspace router with the real home-A runtime pinned.

    Every request runs inside the effective-home override so the
    resolver's real projects.db / SessionDB lookups resolve against
    home A, exactly as a profile-scoped backend process would.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins.workspace.backend.runtime import (
        get_workspace_runtime,
        pin_workspace_runtime,
    )
    from plugins.workspace.dashboard.plugin_api import router

    with _effective_home(env_a.home):
        rt = get_workspace_runtime()
    pin_workspace_runtime(rt)

    app = FastAPI()
    app.include_router(router)
    inner = TestClient(app)

    class _ScopedClient:
        def __init__(self, client: TestClient):
            self._client = client

        def _call(self, method: str, *args, **kwargs):
            with _effective_home(env_a.home):
                return getattr(self._client, method)(*args, **kwargs)

        def get(self, *a, **kw):
            return self._call("get", *a, **kw)

        def post(self, *a, **kw):
            return self._call("post", *a, **kw)

        def put(self, *a, **kw):
            return self._call("put", *a, **kw)

        def delete(self, *a, **kw):
            return self._call("delete", *a, **kw)

    return _ScopedClient(inner)


def test_api_ambiguous_scope_returns_403(api_client, env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    w1 = env_a.workspace("w-a", project)
    w2 = env_a.workspace("w-b")
    # Seed the corrupt duplicate-mapping state via raw SQL.
    conn = env_a.db.get_connection()
    conn.execute("UPDATE workspaces SET hermes_project_id = ? WHERE id = ?", (project, w2))
    conn.commit()
    _add_session(env_a.home, "stored-amb", "/work/p1", "/work/p1")

    resp = api_client.get("/v1/tasks", params={"session_id": "stored-amb"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_AMBIGUOUS"


def test_api_unresolved_scope_returns_403(api_client, env_a):
    resp = api_client.get("/v1/tasks", params={"cwd": "/nowhere"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_api_resolves_scope_from_durable_session(api_client, env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "stored-1", "/work/p1", "/work/p1")

    resp = api_client.get("/v1/tasks", params={"session_id": "stored-1"})
    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


def test_backfill_rejects_archived_project(api_client, env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a")
    _archive_project(env_a.home, project)

    resp = api_client.post(
        "/v1/scope/backfill",
        json={"project_id": project, "workspace_id": ws, "dry_run": False},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_link_rejects_archived_project(api_client, env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    ws = env_a.workspace("w-a")
    _archive_project(env_a.home, project)

    resp = api_client.put(
        f"/v1/workspaces/{ws}/project",
        json={"project_id": project},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_resolution_provenance_recorded(env_a):
    project = _add_project(env_a.home, "proj-a", ["/work/p1"])
    env_a.workspace("w-a", project)
    _add_session(env_a.home, "stored-1", "/work/p1", "/work/p1")

    result = env_a.resolve(session_id="stored-1")
    prov = result.provenance
    assert prov["session_id"] == "stored-1"
    assert prov["cwd"] == "/work/p1"
    assert prov["project_id"] == project
    assert prov["profile_home"].endswith("home-a")
