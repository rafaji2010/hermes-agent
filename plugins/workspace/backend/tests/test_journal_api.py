"""API integration tests for journal endpoints."""

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


def test_create_and_get():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "j-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post("/v1/journal", json={
        "workspace_id": ws_id, "title": "Day 1",
        "summary": "Started project", "markdown": "# Notes",
        "entry_date": "2026-07-20", "tags": ["project", "start"],
    })
    assert resp.status_code == 201
    e = resp.json()["entries"][0]
    assert e["title"] == "Day 1"
    assert e["summary"] == "Started project"
    assert e["entry_date"] == "2026-07-20"
    assert set(e["tags"]) == {"project", "start"}

    resp2 = c.get(f"/v1/journal/{e['id']}")
    assert resp2.status_code == 200


def test_list_with_filters():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "jf-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/journal", json={"workspace_id": ws_id, "title": "A", "markdown": "alpha", "entry_date": "2026-07-01", "tags": ["x"]})
    c.post("/v1/journal", json={"workspace_id": ws_id, "title": "B", "markdown": "beta", "entry_date": "2026-07-02", "tags": ["y"]})

    resp = c.get(f"/v1/journal?workspace_id={ws_id}")
    assert len(resp.json()["entries"]) == 2

    resp = c.get(f"/v1/journal?workspace_id={ws_id}&tag=x")
    assert len(resp.json()["entries"]) == 1

    resp = c.get(f"/v1/journal?workspace_id={ws_id}&date=2026-07-01")
    assert len(resp.json()["entries"]) == 1

    resp = c.get(f"/v1/journal?workspace_id={ws_id}&q=alpha")
    assert len(resp.json()["entries"]) == 1


def test_update_and_delete():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "jud-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    e = c.post("/v1/journal", json={"workspace_id": ws_id, "title": "Old"}).json()["entries"][0]

    resp = c.put(f"/v1/journal/{e['id']}", json={"title": "New", "summary": "Updated"})
    assert resp.status_code == 200
    assert resp.json()["entries"][0]["title"] == "New"

    resp = c.delete(f"/v1/journal/{e['id']}")
    assert resp.status_code == 200
    assert c.get(f"/v1/journal/{e['id']}").status_code == 404


def test_health_includes_journal_count():
    c = client()
    c.post("/v1/workspaces", json={"name": "jcnt-api"})
    # No journal entries yet
    resp = c.get("/v1/health")
    assert resp.json()["journal_count"] == 0


def test_create_empty_title():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "empty-j-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    resp = c.post("/v1/journal", json={"workspace_id": ws_id, "title": ""})
    assert resp.status_code in (400, 422)
