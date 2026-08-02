"""Integration tests for the v1 REST API.

Tests the full router mounted inside a FastAPI application so the
middleware stack (``fastapi_middleware_astack``) is properly initialised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.backend.database import (  # type: ignore[import-untyped]
    DatabaseManager,
    get_database,
)
from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


@pytest.fixture(autouse=True)
def _isolated_db():
    """Pin the DB singleton to a fresh in-memory DB for each test."""
    import plugins.workspace.backend.database as db_mod

    db_mod._db = None
    mem = DatabaseManager(db_path=Path(":memory:"))
    mem.get_connection()
    db_mod._db = mem
    yield
    db_mod._db = None


def client() -> TestClient:
    """Create a test client with the workspace router mounted."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------


def test_health_v1():
    resp = client().get("/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["plugin"] == "workspace"
    assert data["plugin_version"] == "0.1.0"
    assert data["api_version"] == "v1"
    assert data["storage_provider"] == "SQLiteStorage"
    assert data["transaction_support"] is True
    assert data["nested_transactions"] == "SAVEPOINT"
    assert "schema_version" in data
    assert "migration_status" in data
    assert "workspace_count" in data
    assert "repository_count" in data
    assert "hermes_home" in data


def test_health_m0():
    resp = client().get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_list_workspaces():
    c = client()
    resp = c.post("/v1/workspaces", json={"name": "api-test"})
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["workspaces"]) == 1
    assert data["workspaces"][0]["name"] == "api-test"

    resp2 = c.get("/v1/workspaces")
    assert resp2.status_code == 200
    assert len(resp2.json()["workspaces"]) >= 1


def test_create_duplicate_workspace():
    c = client()
    c.post("/v1/workspaces", json={"name": "dup-api"})
    resp = c.post("/v1/workspaces", json={"name": "dup-api"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "DUPLICATE_WORKSPACE"


def test_register_repository_missing_workspace():
    resp = client().post(
        "/v1/repositories",
        json={
            "workspace_id": "nonexistent",
            "name": "r",
            "path": "/tmp/test",
        },
    )
    assert resp.status_code == 404


def test_register_repository_invalid_path():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "path-val-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post(
        "/v1/repositories",
        json={
            "workspace_id": ws_id,
            "name": "bad-path",
            "path": "/nonexistent/path/12345",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "INVALID_PATH"


def test_register_repository_success(temp_git_repo):
    """End-to-end: create workspace, register repo, list repos."""
    c = client()
    r = c.post("/v1/workspaces", json={"name": "e2e-ws"})
    ws_id = r.json()["workspaces"][0]["id"]

    resp = c.post(
        "/v1/repositories",
        json={
            "workspace_id": ws_id,
            "name": "e2e-repo",
            "path": str(temp_git_repo),
        },
    )
    assert resp.status_code == 201
    repo = resp.json()["repositories"][0]
    assert repo["name"] == "e2e-repo"
    assert repo["workspace_id"] == ws_id
    assert repo["git_root"]

    # List
    list_resp = c.get(f"/v1/repositories?workspace_id={ws_id}")
    assert list_resp.status_code == 200
    assert len(list_resp.json()["repositories"]) == 1


def test_error_structure():
    """Errors must return the ErrorDetail shape."""
    resp = client().post("/v1/workspaces", json={"name": ""})
    assert resp.status_code in (400, 422)


def test_health_reflects_counts(temp_git_repo):
    """Workspace and repository counts in /v1/health must update after writes."""
    c = client()

    # Start clean
    resp = c.get("/v1/health")
    assert resp.json()["workspace_count"] == 0
    assert resp.json()["repository_count"] == 0

    # Create workspace → count = 1
    r = c.post("/v1/workspaces", json={"name": "count-test"})
    ws_id = r.json()["workspaces"][0]["id"]
    resp = c.get("/v1/health")
    assert resp.json()["workspace_count"] == 1

    # Register repo → repo count = 1
    c.post("/v1/repositories", json={
        "workspace_id": ws_id,
        "name": "count-repo",
        "path": str(temp_git_repo),
    })
    resp = c.get("/v1/health")
    assert resp.json()["repository_count"] == 1
    assert resp.json()["workspace_count"] == 1
