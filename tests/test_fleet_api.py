"""Tests for the Fleet dashboard API (``/api/fleet/*``)."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_constants import get_hermes_home


@pytest.fixture(autouse=True)
def _web_server_client(_isolate_hermes_home):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    # Reset fleet in-memory event store between tests
    import hermes_cli.web_server as ws

    ws._fleet_events.clear()
    ws._fleet_seen.clear()
    ws._fleet_next_seq = 1
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


@pytest.fixture
def _unauth_client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app

    return TestClient(app)


class TestFleetStatus:
    def test_returns_live_history_workers(self, _web_server_client):
        from hermes_cli.worker_backend import WorkerExecution, append_execution_history, write_live_executions

        # Seed history
        ex = WorkerExecution(
            execution_id="hist-1",
            worker_type="codex",
            task="fix bug",
            status="DONE",
            started_at=time.time() - 10,
            updated_at=time.time(),
            result="ok",
        )
        append_execution_history(ex)
        # Seed live
        now = time.time()
        write_live_executions({
            "live-1": {
                "execution_id": "live-1",
                "worker_type": "pi",
                "task": "explore repo",
                "status": "RUNNING",
                "started_at": now - 5,
                "updated_at": now,
            }
        })

        resp = _web_server_client.get("/api/fleet/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "live" in body and "history" in body and "workers" in body and "timestamp" in body
        assert any(r["execution_id"] == "live-1" for r in body["live"])
        assert any(r["execution_id"] == "hist-1" for r in body["history"])
        assert isinstance(body["workers"], list)
        assert isinstance(body["timestamp"], float)

    def test_empty_when_no_data(self, _web_server_client):
        resp = _web_server_client.get("/api/fleet/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["live"] == []
        assert body["history"] == []
        assert isinstance(body["workers"], list)

    def test_requires_auth(self, _unauth_client):
        resp = _unauth_client.get("/api/fleet/status")
        assert resp.status_code == 401


class TestFleetEvents:
    def test_cursor_advances_and_no_dupes(self, _web_server_client):
        from hermes_cli.worker_backend import write_live_executions

        now = time.time()
        write_live_executions({
            "ev-1": {
                "execution_id": "ev-1",
                "worker_type": "codex",
                "task": "task one",
                "status": "RUNNING",
                "started_at": now,
                "updated_at": now,
            }
        })

        # First poll with cursor 0 should return events
        r1 = _web_server_client.get("/api/fleet/events?cursor=0")
        assert r1.status_code == 200
        b1 = r1.json()
        assert "events" in b1 and "next_cursor" in b1
        assert len(b1["events"]) >= 1
        cursor = b1["next_cursor"]
        assert cursor >= 1
        assert all(e["seq"] > 0 for e in b1["events"])

        # Second poll with same cursor should return no dupes
        r2 = _web_server_client.get(f"/api/fleet/events?cursor={cursor}")
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2["events"] == []
        assert b2["next_cursor"] == cursor

        # New execution appears after advancing cursor
        write_live_executions({
            "ev-1": {
                "execution_id": "ev-1",
                "worker_type": "codex",
                "task": "task one",
                "status": "RUNNING",
                "started_at": now,
                "updated_at": now,
            },
            "ev-2": {
                "execution_id": "ev-2",
                "worker_type": "pi",
                "task": "task two",
                "status": "RUNNING",
                "started_at": now,
                "updated_at": now,
            },
        })
        r3 = _web_server_client.get(f"/api/fleet/events?cursor={cursor}")
        assert r3.status_code == 200
        b3 = r3.json()
        assert len(b3["events"]) >= 1
        assert any(e["execution_id"] == "ev-2" for e in b3["events"])
        assert b3["next_cursor"] > cursor

    def test_status_change_emits_event(self, _web_server_client):
        from hermes_cli.worker_backend import write_live_executions

        now = time.time()
        write_live_executions({
            "ev-s": {
                "execution_id": "ev-s",
                "worker_type": "codex",
                "task": "task s",
                "status": "RUNNING",
                "started_at": now,
                "updated_at": now,
            }
        })
        r1 = _web_server_client.get("/api/fleet/events?cursor=0")
        c1 = r1.json()["next_cursor"]
        # Change status
        write_live_executions({
            "ev-s": {
                "execution_id": "ev-s",
                "worker_type": "codex",
                "task": "task s",
                "status": "DONE",
                "started_at": now,
                "updated_at": now + 1,
            }
        })
        r2 = _web_server_client.get(f"/api/fleet/events?cursor={c1}")
        b2 = r2.json()
        assert any(e["type"] == "status_changed" and e["execution_id"] == "ev-s" for e in b2["events"])

    def test_requires_auth(self, _unauth_client):
        resp = _unauth_client.get("/api/fleet/events?cursor=0")
        assert resp.status_code == 401


class TestFleetRun:
    def test_validates_empty_task(self, _web_server_client):
        resp = _web_server_client.post("/api/fleet/run", json={"task": ""})
        assert resp.status_code == 400
        resp2 = _web_server_client.post("/api/fleet/run", json={"task": "   "})
        assert resp2.status_code == 400

    def test_returns_execution_id_mocked(self, _web_server_client, monkeypatch):
        mock_backend = MagicMock()
        mock_backend.start.return_value = "mock-exec-123"
        monkeypatch.setattr("hermes_cli.worker_backend.get_backend", lambda wt: mock_backend)

        resp = _web_server_client.post("/api/fleet/run", json={"task": "do thing", "worker": "codex"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["execution_id"] == "mock-exec-123"
        assert body["worker_type"] == "codex"
        mock_backend.start.assert_called_once()

    def test_unknown_worker_rejected(self, _web_server_client):
        resp = _web_server_client.post("/api/fleet/run", json={"task": "hello", "worker": "notaworker"})
        assert resp.status_code == 400

    def test_requires_auth(self, _unauth_client):
        resp = _unauth_client.post("/api/fleet/run", json={"task": "hello"})
        assert resp.status_code == 401
