"""API integration tests for ADR endpoints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


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


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------


def test_create_and_get_adr():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "adr-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    # Create
    resp = c.post("/v1/adrs", json={
        "workspace_id": ws_id,
        "title": "Test ADR",
        "status": "proposed",
        "category": "Architecture",
        "markdown": "# Test\n\nContent.",
        "tags": ["api", "test"],
    })
    assert resp.status_code == 201
    adr = resp.json()["adrs"][0]
    assert adr["title"] == "Test ADR"
    assert adr["slug"] == "test-adr"
    assert adr["status"] == "proposed"
    assert adr["markdown"] == "# Test\n\nContent."
    assert set(adr["tags"]) == {"api", "test"}

    # Get
    resp2 = c.get(f"/v1/adrs/{adr['id']}")
    assert resp2.status_code == 200
    assert resp2.json()["adrs"][0]["title"] == "Test ADR"


def test_list_adrs():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "list-adr-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/adrs", json={"workspace_id": ws_id, "title": "A", "status": "accepted"})
    c.post("/v1/adrs", json={"workspace_id": ws_id, "title": "B", "status": "proposed", "tags": ["x"]})

    resp = c.get(f"/v1/adrs?workspace_id={ws_id}")
    assert resp.status_code == 200
    assert len(resp.json()["adrs"]) == 2

    resp = c.get(f"/v1/adrs?workspace_id={ws_id}&status=accepted")
    assert len(resp.json()["adrs"]) == 1

    resp = c.get(f"/v1/adrs?workspace_id={ws_id}&tag=x")
    assert len(resp.json()["adrs"]) == 1

    resp = c.get(f"/v1/adrs?workspace_id={ws_id}&q=A")
    assert len(resp.json()["adrs"]) >= 1


def test_update_adr():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "upd-adr-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Old", "status": "proposed"
    }).json()["adrs"][0]

    resp = c.put(f"/v1/adrs/{adr['id']}", json={
        "title": "New", "status": "accepted", "markdown": "updated"
    })
    assert resp.status_code == 200
    updated = resp.json()["adrs"][0]
    assert updated["title"] == "New"
    assert updated["status"] == "accepted"
    assert updated["markdown"] == "updated"


def test_delete_adr():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "del-adr-api"})
    ws_id = r.json()["workspaces"][0]["id"]
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Delete Me"
    }).json()["adrs"][0]

    resp = c.delete(f"/v1/adrs/{adr['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # verify gone
    resp2 = c.get(f"/v1/adrs/{adr['id']}")
    assert resp2.status_code == 404


def test_create_adr_invalid_status():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "inv-status-api"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Bad", "status": "bogus"
    })
    assert resp.status_code == 400


def test_create_adr_empty_title():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "empty-title-api"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": ""
    })
    assert resp.status_code in (400, 422)


def test_adr_not_found():
    c = client()
    resp = c.get("/v1/adrs/nonexistent")
    assert resp.status_code == 404
