"""API integration tests for task endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


@pytest.fixture(autouse=True)
def _isolated_db():
    import plugins.workspace.backend.database as db_mod

    db_mod._db = None
    mem = DatabaseManager(db_path=Path(":memory:"))
    mem.get_connection()
    db_mod._db = mem
    yield
    db_mod._db = None


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_create_and_list_tasks():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "API Task"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["tasks"][0]["title"] == "API Task"

    r2 = c.get(f"/v1/tasks?workspace_id={ws_id}")
    assert r2.status_code == 200
    assert len(r2.json()["tasks"]) == 1


def test_create_invalid_status():
    c = client()
    resp = c.post("/v1/tasks", json={"title": "Bad", "status": "impossible"})
    assert resp.status_code == 400


def test_update_task():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-upd-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rt = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Old"})
    task_id = rt.json()["tasks"][0]["id"]

    resp = c.put(f"/v1/tasks/{task_id}", json={"title": "New", "status": "in_progress"})
    assert resp.status_code == 200
    assert resp.json()["tasks"][0]["title"] == "New"


def test_delete_task():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-del-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rt = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Del"})
    task_id = rt.json()["tasks"][0]["id"]

    resp = c.delete(f"/v1/tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_comments():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-cmt-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rt = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Task"})
    task_id = rt.json()["tasks"][0]["id"]

    resp = c.post(f"/v1/tasks/{task_id}/comments", json={"body": "Great"})
    assert resp.status_code == 201
    assert resp.json()["comments"][0]["body"] == "Great"

    r2 = c.get(f"/v1/tasks/{task_id}/comments")
    assert r2.status_code == 200
    assert len(r2.json()["comments"]) == 1


def test_dependencies():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-dep-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    t1 = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T1"}).json()["tasks"][0]
    t2 = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T2"}).json()["tasks"][0]

    resp = c.put(f"/v1/tasks/{t1['id']}/dependencies", json={"depends_on_ids": [t2["id"]]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["depends_on"]) == 1

    r2 = c.get(f"/v1/tasks/{t1['id']}/dependencies")
    assert r2.status_code == 200


def test_circular_dependency():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-circ-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    t1 = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T1"}).json()["tasks"][0]
    t2 = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T2"}).json()["tasks"][0]

    c.put(f"/v1/tasks/{t1['id']}/dependencies", json={"depends_on_ids": [t2["id"]]})
    resp = c.put(f"/v1/tasks/{t2['id']}/dependencies", json={"depends_on_ids": [t1["id"]]})
    assert resp.status_code == 400


def test_search():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-search-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Fix auth bug"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Add tests"})

    resp = c.get(f"/v1/tasks/search?workspace_id={ws_id}&q=auth")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tasks"]) == 1


def test_status_filter():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-statf-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "A", "status": "done"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "B", "status": "todo"})

    resp = c.get(f"/v1/tasks?workspace_id={ws_id}&status=done")
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) == 1


def test_health_includes_task_counts():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-ct-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Open", "status": "todo"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Blocked", "status": "blocked"})

    resp = c.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_count"] >= 2
    assert data["open_task_count"] >= 2
    assert data["blocked_task_count"] >= 1
