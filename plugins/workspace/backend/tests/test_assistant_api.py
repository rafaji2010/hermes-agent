"""API integration tests for assistant endpoints."""

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
def _isolated_db():
    import plugins.workspace.backend.database as db_mod

    db_mod._db = None
    mem = DatabaseManager(db_path=Path(":memory:"))
    mem.get_connection()
    db_mod._db = mem
    yield
    db_mod._db = None


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_analytics_cache()


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_chat_endpoint():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "asst-api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "Task 1"})

    resp = c.post("/v1/assistant/chat", json={
        "question": "What tasks do we have?",
        "workspace_id": ws_id,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["answer"]) > 0
    assert "conversation_id" in data


def test_chat_follow_up():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "fu-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/tasks", json={"workspace_id": ws_id, "title": "T", "status": "blocked"})

    resp1 = c.post("/v1/assistant/chat", json={
        "question": "What is blocked?",
        "workspace_id": ws_id,
    })
    cid = resp1.json()["conversation_id"]
    resp2 = c.post("/v1/assistant/chat", json={
        "question": "Tell me more about that", "conversation_id": cid,
        "workspace_id": ws_id,
    })
    assert resp2.status_code == 200
    assert resp2.json()["conversation_id"] == cid


def test_context_endpoint():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "ctx-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    resp = c.post(f"/v1/assistant/context?question=roadmap&workspace_id={ws_id}")
    assert resp.status_code == 200
    assert "entities" in resp.json()


def test_context_endpoint_unscoped_rejected():
    """An empty scope must be rejected — never silently global."""
    c = client()
    resp = c.post("/v1/assistant/context?question=roadmap")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_suggestions_endpoint():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "sug-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    resp = c.get(f"/v1/assistant/suggestions?workspace_id={ws_id}")
    assert resp.status_code == 200
    assert len(resp.json()["suggestions"]) > 0
