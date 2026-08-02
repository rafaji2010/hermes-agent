"""API integration tests for analytics endpoints."""

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
from plugins.workspace.backend.services.analytics_service import reset_analytics_cache  # type: ignore[import-untyped]
from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_analytics_cache()


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


@pytest.fixture
def ws_id() -> str:
    """Create a workspace and return its id (analytics endpoints are scoped)."""
    import uuid
    c = client()
    r = c.post("/v1/workspaces", json={"name": f"analytics-ws-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 201
    return r.json()["workspaces"][0]["id"]


def test_get_analytics(ws_id):
    c = client()
    resp = c.get(f"/v1/analytics?workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "roadmaps" in data
    assert "tasks" in data
    assert "repositories" in data


def test_get_analytics_with_data(ws_id):
    c = client()
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T"})

    resp = c.get(f"/v1/analytics?workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"]["total"] >= 1


def test_get_analytics_unscoped_rejected():
    """An empty scope must be rejected — never silently global."""
    c = client()
    resp = c.get("/v1/analytics")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_get_trends(ws_id):
    c = client()
    resp = c.get(f"/v1/analytics/trends?period_days=7&workspace_id={ws_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["period_days"] == 7
    assert len(data["task_completion"]) == 7


def test_get_insights(ws_id):
    c = client()
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Blocked", "status": "blocked"})

    resp = c.get(f"/v1/analytics/insights?workspace_id={ws_id}")
    assert resp.status_code == 200
    assert len(resp.json()["insights"]) >= 1


def test_export_json(ws_id):
    c = client()
    resp = c.post(f"/v1/analytics/export?workspace_id={ws_id}", json={"format": "json"})
    assert resp.status_code == 200
    data = resp.json()
    assert "roadmaps" in data


def test_export_markdown(ws_id):
    c = client()
    resp = c.post(f"/v1/analytics/export?workspace_id={ws_id}", json={"format": "markdown"})
    assert resp.status_code == 200
    assert "Workspace Analytics" in resp.text


def test_export_csv(ws_id):
    c = client()
    resp = c.post(f"/v1/analytics/export?workspace_id={ws_id}", json={"format": "csv"})
    assert resp.status_code == 200
    assert "section,metric,value" in resp.text
