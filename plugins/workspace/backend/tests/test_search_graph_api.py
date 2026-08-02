"""API integration tests for search + graph endpoints."""

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


def test_search_returns_all_types():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "s-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get(f"/v1/search?workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 2


def test_search_text_query():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "t-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Fix auth bug"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Update docs"})

    resp = c.get(f"/v1/search?q=auth&workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any("auth" in r["title"].lower() for r in data["results"])


def test_search_type_filter():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "f-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get(f"/v1/search?workspace_id={ws_id}&type=task")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["type"] == "task" for r in data["results"])


def test_related_items():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "rel-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    rr = c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    roadmap_id = rr.json()["roadmaps"][0]["id"]
    c.post(f"/v1/roadmaps/{roadmap_id}/milestones", json={"title": "M"})

    resp = c.get(f"/v1/entities/roadmap/{roadmap_id}/related")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entity_type"] == "roadmap"
    assert len(data["items"]) >= 1


def test_get_graph():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "g-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/roadmaps", json={"workspace_id": ws_id, "name": "R"})
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get(f"/v1/graph?workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) >= 3
    assert len(data["edges"]) >= 2


def test_graph_stats():
    """S7.3A: graph stats are workspace-scoped — never a global aggregate."""
    c = client()
    r = c.post("/v1/workspaces", json={"name": "stats-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get(f"/v1/graph/stats?workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_entities" in data
    assert "total_edges" in data
    assert "orphan_entities" in data
    assert data["total_entities"] >= 1

    # Unscoped → fail closed (403), never global.
    resp = c.get("/v1/graph/stats")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_graph_shortest_path_scoped():
    """S7.3A: shortest-path traverses only the resolved workspace graph."""
    c = client()
    r = c.post("/v1/workspaces", json={"name": "sp-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    t = c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"}).json()["tasks"][0]
    task_id = t["id"]

    resp = c.get(
        f"/v1/graph/shortest-path?source_type=workspace&source_id={ws_id}"
        f"&target_type=task&target_id={task_id}&workspace_id={ws_id}"
    )
    assert resp.status_code == 200
    assert "path" in resp.json()

    resp = c.get(
        f"/v1/graph/shortest-path?source_type=workspace&source_id={ws_id}"
        f"&target_type=task&target_id={task_id}"
    )
    assert resp.status_code == 403  # unscoped → fail closed


def test_health_includes_graph_stats():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "h-graph-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "graph_entity_count" in data
    assert "graph_edge_count" in data
    assert "graph_orphan_count" in data
