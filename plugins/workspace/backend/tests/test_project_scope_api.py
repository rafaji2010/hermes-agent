"""API integration tests for Hermes project scope (S7.2).

Exercises the full HTTP surface: workspace↔project mapping, scope
resolution, backfill, scope enforcement on previously-global endpoints,
get-by-ID membership checks, and cross-project task reassignment guards.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.backend.services.scope_resolver import (  # type: ignore[import-untyped]
    ProjectScopeResolver,
)
from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


class FakeProjectStore:
    """Fake Hermes projects.db with folder-prefix matching."""

    def __init__(self, folders: Optional[Dict[str, tuple]] = None):
        self.folders: Dict[str, tuple] = folders or {}

    def lookup(self, path: str) -> Optional[tuple]:
        if not path:
            return None
        best = None
        best_len = -1
        for folder, proj in self.folders.items():
            if path == folder or path.startswith(folder.rstrip("/") + "/"):
                if len(folder) > best_len:
                    best_len = len(folder)
                    best = proj
        return best

    def slug(self, project_id: str) -> Optional[str]:
        for _folder, (pid, slug) in self.folders.items():
            if pid == project_id:
                return slug
        return None


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Pin the legacy DB singleton AND the Workspace runtime to one
    fresh in-memory database (U1D-A)."""
    from plugins.workspace.backend.tests._helpers import (
        pin_memory_workspace_state,
        unpin_memory_workspace_state,
    )

    pin_memory_workspace_state(tmp_path)
    yield
    unpin_memory_workspace_state()


@pytest.fixture
def scope_env(monkeypatch):
    """Install a fake-backed resolver onto the active Workspace runtime."""
    import plugins.workspace.backend.api.v1 as v1

    store = FakeProjectStore()
    sessions: Dict[str, dict] = {}
    resolver = ProjectScopeResolver(
        storage=v1._runtime().storage,
        project_lookup=store.lookup,
        session_meta=lambda sid: sessions.get(sid),
        project_slug=store.slug,
    )
    monkeypatch.setattr(v1._runtime(), "_scope_resolver", resolver)
    return {"store": store, "sessions": sessions}


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def make_ws(c: TestClient, name: str) -> str:
    r = c.post("/v1/workspaces", json={"name": name})
    assert r.status_code == 201
    return r.json()["workspaces"][0]["id"]


# ---------------------------------------------------------------------------
# Workspace ↔ project mapping endpoints
# ---------------------------------------------------------------------------


def test_link_project_success(scope_env):
    c = client()
    ws = make_ws(c, "link-ok")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}

    resp = c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == ws
    assert data["project_id"] == "p_abc"
    assert data["project_slug"] == "proj-a"
    assert data["state"] == "mapped"


def test_link_project_missing_workspace(scope_env):
    c = client()
    resp = c.put("/v1/workspaces/nope/project", json={"project_id": "p_abc"})
    assert resp.status_code == 404


def test_link_project_unknown_project(scope_env):
    c = client()
    ws = make_ws(c, "link-unknown")
    resp = c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_ghost"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


def test_link_project_duplicate_mapping(scope_env):
    c = client()
    ws1 = make_ws(c, "link-dup-1")
    ws2 = make_ws(c, "link-dup-2")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws1}/project", json={"project_id": "p_abc"})
    resp = c.put(f"/v1/workspaces/{ws2}/project", json={"project_id": "p_abc"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "PROJECT_ALREADY_LINKED"


def test_get_project_mapping_roundtrip(scope_env):
    c = client()
    ws = make_ws(c, "link-get")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}

    resp = c.get(f"/v1/workspaces/{ws}/project")
    assert resp.status_code == 200
    assert resp.json()["state"] == "unmapped"

    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})
    resp = c.get(f"/v1/workspaces/{ws}/project")
    assert resp.json()["state"] == "mapped"
    assert resp.json()["project_id"] == "p_abc"


def test_unlink_project(scope_env):
    c = client()
    ws = make_ws(c, "link-unlink")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})

    resp = c.delete(f"/v1/workspaces/{ws}/project")
    assert resp.status_code == 200
    assert resp.json()["state"] == "unmapped"

    resp = c.get(f"/v1/workspaces/{ws}/project")
    assert resp.json()["state"] == "unmapped"


# ---------------------------------------------------------------------------
# Scope resolution endpoint
# ---------------------------------------------------------------------------


def test_resolve_mapped_workspace(scope_env):
    c = client()
    ws = make_ws(c, "resolve-mapped")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})

    resp = c.post("/v1/scope/resolve", json={"workspace_id": ws})
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "mapped"
    assert data["project_id"] == "p_abc"


def test_resolve_via_session_cwd(scope_env):
    c = client()
    ws = make_ws(c, "resolve-session")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})
    scope_env["sessions"]["s_1"] = {"cwd": "/proj/a/src", "git_repo_root": "/proj/a"}

    resp = c.post("/v1/scope/resolve", json={"session_id": "s_1"})
    assert resp.status_code == 200
    data = resp.json()
    # Path evidence (session cwd) identifies the project; the reverse
    # mapping confirms the linked workspace. Source is the evidence.
    assert data["state"] == "mapped"
    assert data["workspace_id"] == ws
    assert data["project_id"] == "p_abc"
    assert data["match_source"] == "session_cwd"


def test_resolve_via_session_cwd_unmapped_workspace(scope_env):
    """Path evidence supplies the project when the workspace is unmapped."""
    c = client()
    ws = make_ws(c, "resolve-session-2")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    scope_env["sessions"]["s_7"] = {"cwd": "/proj/a/src", "git_repo_root": "/proj/a"}

    resp = c.post("/v1/scope/resolve", json={"session_id": "s_7"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "partial"
    assert data["project_id"] == "p_abc"
    assert data["match_source"] == "session_cwd"


def test_resolve_unresolved_returns_state(scope_env):
    """The diagnostic endpoint reports unresolved instead of rejecting."""
    c = client()
    resp = c.post("/v1/scope/resolve", json={})
    assert resp.status_code == 200
    assert resp.json()["state"] == "unresolved"


# ---------------------------------------------------------------------------
# Backfill endpoint
# ---------------------------------------------------------------------------


def test_backfill_dry_run_proposes(scope_env):
    c = client()
    ws = make_ws(c, "bf-dry")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}

    resp = c.post("/v1/scope/backfill", json={
        "project_id": "p_abc", "workspace_id": ws, "dry_run": True,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "proposed"
    # Nothing was changed
    assert c.get(f"/v1/workspaces/{ws}/project").json()["state"] == "unmapped"


def test_backfill_apply(scope_env):
    c = client()
    ws = make_ws(c, "bf-apply")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}

    resp = c.post("/v1/scope/backfill", json={
        "project_id": "p_abc", "workspace_id": ws, "dry_run": False,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert c.get(f"/v1/workspaces/{ws}/project").json()["state"] == "mapped"


def test_backfill_already_linked(scope_env):
    c = client()
    ws = make_ws(c, "bf-already")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})

    resp = c.post("/v1/scope/backfill", json={
        "project_id": "p_abc", "workspace_id": ws, "dry_run": False,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_linked"


def test_backfill_ambiguous(scope_env):
    """Two workspaces mapped to the same project → no change.

    The duplicate is seeded via raw SQL (simulating legacy/corrupt data)
    because the storage layer refuses to create it through the API.
    """
    c = client()
    ws1 = make_ws(c, "bf-amb-1")
    ws2 = make_ws(c, "bf-amb-2")
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}

    import plugins.workspace.backend.database as db_mod
    conn = db_mod.get_database().get_connection()
    conn.execute(
        "UPDATE workspaces SET hermes_project_id = 'p_abc' WHERE id IN (?, ?)",
        (ws1, ws2),
    )
    conn.commit()

    resp = c.post("/v1/scope/backfill", json={"project_id": "p_abc"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ambiguous"
    assert set(resp.json()["candidates"]) == {ws1, ws2}


def test_backfill_unknown_project(scope_env):
    c = client()
    ws = make_ws(c, "bf-unknown")
    resp = c.post("/v1/scope/backfill", json={
        "project_id": "p_ghost", "workspace_id": ws, "dry_run": False,
    })
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "PROJECT_NOT_FOUND"


# ---------------------------------------------------------------------------
# Scope enforcement on previously-global endpoints
# ---------------------------------------------------------------------------


def test_tasks_unscoped_rejected(scope_env):
    c = client()
    resp = c.get("/v1/tasks")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_tasks_resolve_via_session(scope_env):
    c = client()
    ws = make_ws(c, "tasks-session")
    c.post("/v1/tasks", json={"workspace_id": ws, "title": "T1"})
    scope_env["store"].folders = {"/proj/a": ("p_abc", "proj-a")}
    c.put(f"/v1/workspaces/{ws}/project", json={"project_id": "p_abc"})
    scope_env["sessions"]["s_2"] = {"cwd": "/proj/a/src", "git_repo_root": "/proj/a"}

    resp = c.get("/v1/tasks?session_id=s_2")
    assert resp.status_code == 200
    titles = [t["title"] for t in resp.json()["tasks"]]
    assert "T1" in titles


def test_search_unscoped_rejected(scope_env):
    c = client()
    resp = c.get("/v1/search?q=anything")
    assert resp.status_code == 403


def test_graph_unscoped_rejected(scope_env):
    c = client()
    resp = c.get("/v1/graph")
    assert resp.status_code == 403


def test_scoped_search_only_sees_workspace(scope_env):
    """Scoped search must not leak another workspace's data."""
    c = client()
    ws_a = make_ws(c, "search-a")
    ws_b = make_ws(c, "search-b")
    c.post("/v1/tasks", json={"workspace_id": ws_a, "title": "SECRET-A"})
    c.post("/v1/tasks", json={"workspace_id": ws_b, "title": "SECRET-B"})

    resp = c.get(f"/v1/search?q=SECRET&workspace_id={ws_a}")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["id"] != "SECRET-B" for r in results)
    assert any(r["title"] == "SECRET-A" for r in results)


# ---------------------------------------------------------------------------
# Get-by-ID membership checks (no existence leak)
# ---------------------------------------------------------------------------


def test_get_task_membership(scope_env):
    c = client()
    ws_a = make_ws(c, "mem-a")
    ws_b = make_ws(c, "mem-b")
    t = c.post("/v1/tasks", json={"workspace_id": ws_a, "title": "T"}).json()["tasks"][0]
    tid = t["id"]

    assert c.get(f"/v1/tasks/{tid}?workspace_id={ws_a}").status_code == 200
    resp = c.get(f"/v1/tasks/{tid}?workspace_id={ws_b}")
    assert resp.status_code == 404


def test_get_adr_membership(scope_env):
    c = client()
    ws_a = make_ws(c, "mem-adr-a")
    ws_b = make_ws(c, "mem-adr-b")
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_a, "title": "ADR-1", "status": "proposed",
    }).json()["adrs"][0]

    assert c.get(f"/v1/adrs/{adr['id']}?workspace_id={ws_a}").status_code == 200
    assert c.get(f"/v1/adrs/{adr['id']}?workspace_id={ws_b}").status_code == 404


def test_get_journal_membership(scope_env):
    c = client()
    ws_a = make_ws(c, "mem-jr-a")
    ws_b = make_ws(c, "mem-jr-b")
    je = c.post("/v1/journal", json={
        "workspace_id": ws_a, "title": "Entry", "content": "x",
    }).json()["entries"][0]

    assert c.get(f"/v1/journal/{je['id']}?workspace_id={ws_a}").status_code == 200
    assert c.get(f"/v1/journal/{je['id']}?workspace_id={ws_b}").status_code == 404


def test_get_roadmap_membership(scope_env):
    c = client()
    ws_a = make_ws(c, "mem-rm-a")
    ws_b = make_ws(c, "mem-rm-b")
    rm = c.post("/v1/roadmaps", json={"workspace_id": ws_a, "name": "RM"}).json()["roadmaps"][0]

    assert c.get(f"/v1/roadmaps/{rm['id']}?workspace_id={ws_a}").status_code == 200
    assert c.get(f"/v1/roadmaps/{rm['id']}?workspace_id={ws_b}").status_code == 404


# ---------------------------------------------------------------------------
# Cross-project task reassignment guard
# ---------------------------------------------------------------------------


def test_reassignment_across_projects_rejected(scope_env):
    c = client()
    ws_a = make_ws(c, "reass-a")
    ws_b = make_ws(c, "reass-b")
    scope_env["store"].folders = {"/p1": ("p_1", "proj-1"), "/p2": ("p_2", "proj-2")}
    c.put(f"/v1/workspaces/{ws_a}/project", json={"project_id": "p_1"})
    c.put(f"/v1/workspaces/{ws_b}/project", json={"project_id": "p_2"})
    t = c.post("/v1/tasks", json={"workspace_id": ws_a, "title": "T"}).json()["tasks"][0]

    resp = c.put(f"/v1/tasks/{t['id']}?workspace_id={ws_a}", json={"workspace_id": ws_b})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "CROSS_PROJECT_REASSIGNMENT"


def test_reassignment_within_project_allowed(scope_env):
    c = client()
    ws_a = make_ws(c, "reass-a2")
    ws_b = make_ws(c, "reass-b2")
    scope_env["store"].folders = {"/p1": ("p_1", "proj-1")}
    c.put(f"/v1/workspaces/{ws_a}/project", json={"project_id": "p_1"})
    c.put(f"/v1/workspaces/{ws_b}/project", json={"project_id": "p_1"})
    t = c.post("/v1/tasks", json={"workspace_id": ws_a, "title": "T"}).json()["tasks"][0]

    resp = c.put(f"/v1/tasks/{t['id']}?workspace_id={ws_a}", json={"workspace_id": ws_b})
    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["workspace_id"] == ws_b


def test_reassignment_to_unmapped_workspace_allowed(scope_env):
    c = client()
    ws_a = make_ws(c, "reass-a3")
    ws_b = make_ws(c, "reass-b3")
    scope_env["store"].folders = {"/p1": ("p_1", "proj-1")}
    c.put(f"/v1/workspaces/{ws_a}/project", json={"project_id": "p_1"})
    t = c.post("/v1/tasks", json={"workspace_id": ws_a, "title": "T"}).json()["tasks"][0]

    resp = c.put(f"/v1/tasks/{t['id']}?workspace_id={ws_a}", json={"workspace_id": ws_b})
    assert resp.status_code == 200
