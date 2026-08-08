"""API integration tests for roadmap + milestone endpoints."""

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


def test_create_and_list_roadmaps():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "rm-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "Q4"})
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["roadmaps"]) == 1
    assert data["roadmaps"][0]["name"] == "Q4"
    assert data["roadmaps"][0]["progress"] == 0.0

    r2 = c.get(f"/v1/roadmaps?workspace_id={ws_id}")
    assert r2.status_code == 200
    assert len(r2.json()["roadmaps"]) == 1


def test_update_roadmap():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "upd-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "Old"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    resp = c.put(f"/v1/roadmaps/{roadmap_id}?workspace_id={ws_id}", json={"name": "New"})
    assert resp.status_code == 200
    assert resp.json()["roadmaps"][0]["name"] == "New"


def test_delete_roadmap():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "del-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "Del"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    resp = c.delete(f"/v1/roadmaps/{roadmap_id}?workspace_id={ws_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_create_milestone():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "ms-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    resp = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "Milestone 1", "status": "planned"},
    )
    assert resp.status_code == 201
    m = resp.json()["milestones"][0]
    assert m["title"] == "Milestone 1"
    assert m["status"] == "planned"
    assert m["sort_order"] == 0


def test_create_milestone_invalid_status():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "bad-ms-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    resp = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "Bad", "status": "nope"},
    )
    assert resp.status_code == 400


def test_reorder_milestones():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "ord-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    m1 = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "A"},
    ).json()["milestones"][0]
    m2 = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "B"},
    ).json()["milestones"][0]
    m3 = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "C"},
    ).json()["milestones"][0]

    resp = c.put(
        f"/v1/roadmaps/{roadmap_id}/milestones/reorder?workspace_id={ws_id}",
        json={"ids": [m3["id"], m1["id"], m2["id"]]},
    )
    assert resp.status_code == 200
    ordered = resp.json()["milestones"]
    assert ordered[0]["id"] == m3["id"]
    assert ordered[1]["id"] == m1["id"]


def test_health_includes_roadmap_counts():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "cnt-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]
    c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "M", "status": "completed"},
    )

    resp = c.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["roadmap_count"] >= 1
    assert data["milestone_count"] >= 1
    assert data["completed_milestone_count"] >= 1


def test_progress_updates_via_api():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "prog-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]

    m = c.post(
        f"/v1/roadmaps/{roadmap_id}/milestones?workspace_id={ws_id}",
        json={"title": "T", "status": "planned"},
    ).json()["milestones"][0]

    c.put(
        f"/v1/roadmaps/{roadmap_id}/milestones/{m['id']}?workspace_id={ws_id}",
        json={"status": "completed"},
    )

    resp = c.get(f"/v1/roadmaps/{roadmap_id}?workspace_id={ws_id}")
    data = resp.json()["roadmaps"][0]
    assert data["progress"] == 100.0
    assert data["completed_count"] == 1
