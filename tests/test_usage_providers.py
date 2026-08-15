"""Tests for the Usage tab provider-spend endpoint (GET /api/usage/providers).

Covers the three real data sources:

* OpenRouter — REST credits API (exact USD spend + remaining credits), keyed
  by OPENROUTER_API_KEY from the profile .env or the process env.
* opencode — local SQLite store (~/.local/share/opencode/opencode.db),
  token/cost fields inside the JSON ``data`` blobs (or the newer columnar
  ``session`` aggregate columns).
* commandcode — no usage API; local JSONL transcripts under
  ~/.commandcode/projects are counted, spend is null.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


# --- OpenRouter -------------------------------------------------------------


class _FakeOpenRouterResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_openrouter_fetch_mapping(monkeypatch):
    """OpenRouter credits response maps total_usage→spend, total_credits→remaining."""
    from hermes_cli import web_server

    captured = {}

    def _fake_get(url, *, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _FakeOpenRouterResponse({"data": {"total_credits": 35, "total_usage": 7.83}})

    monkeypatch.setattr(web_server.httpx, "get", _fake_get)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    result = web_server._get_openrouter_usage(None)

    assert captured["url"] == "https://openrouter.ai/api/v1/credits"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"
    assert captured["timeout"] == 8.0
    assert result == {
        "provider": "openrouter",
        "spend_usd": 7.83,
        "credits_remaining": 35.0,
        "period": "billing-month",
        "source": "api",
    }


def test_openrouter_missing_key(monkeypatch):
    """No OPENROUTER_API_KEY anywhere → a clean error entry, no fetch attempt."""
    from hermes_cli import web_server

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    fetched = []

    def _fake_get(*a, **k):
        fetched.append(True)
        raise AssertionError("should not fetch without a key")

    monkeypatch.setattr(web_server.httpx, "get", _fake_get)

    result = web_server._get_openrouter_usage(None)

    assert result == {"provider": "openrouter", "error": "no key"}
    assert not fetched


def test_openrouter_fetch_failure(monkeypatch):
    """HTTP error → graceful error entry, key never leaks into the response."""
    from hermes_cli import web_server

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def _fake_get(*a, **k):
        return _FakeOpenRouterResponse({"error": "boom"}, status=401)

    monkeypatch.setattr(web_server.httpx, "get", _fake_get)

    result = web_server._get_openrouter_usage(None)

    assert result["provider"] == "openrouter"
    assert "error" in result
    assert "sk-or-test" not in json.dumps(result)


# --- opencode ---------------------------------------------------------------


def _make_opencode_db(db_path: Path, messages: list, session_cols: bool = False):
    """Build a temp opencode DB mirroring the real message/part/session schema."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, "
            "time_updated INTEGER, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, "
            "time_created INTEGER, time_updated INTEGER, data TEXT)"
        )
        if session_cols:
            conn.execute(
                "CREATE TABLE session (id TEXT, cost REAL, tokens_input INTEGER, "
                "tokens_output INTEGER, tokens_cache_read INTEGER, "
                "tokens_cache_write INTEGER)"
            )
        else:
            conn.execute("CREATE TABLE session (id TEXT)")
        for i, blob in enumerate(messages):
            conn.execute(
                "INSERT INTO message (id, session_id, data) VALUES (?, ?, ?)",
                (f"msg_{i}", "ses_1", json.dumps(blob)),
            )
        conn.commit()
    finally:
        conn.close()


def test_opencode_db_json_aggregation(tmp_path, monkeypatch):
    """JSON ``data`` blobs on the message table aggregate into tokens/sessions."""
    from hermes_cli import web_server

    db_path = tmp_path / "opencode.db"
    # Sample blobs shaped like the verified opencode message.data JSON.
    messages = [
        {"role": "user", "time": {"created": 1}},
        {
            "role": "assistant",
            "cost": 0.0042,
            "tokens": {"total": 1000, "input": 800, "output": 200,
                       "cache": {"read": 5000, "write": 10}},
        },
        {
            "role": "assistant",
            "cost": 0.0011,
            "tokens": {"total": 500, "input": 300, "output": 200,
                       "cache": {"read": 2500, "write": 5}},
        },
    ]
    _make_opencode_db(db_path, messages, session_cols=False)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("INSERT INTO session (id) VALUES (?)", ("ses_1",))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(web_server, "_opencode_db_path", lambda: db_path)

    result = web_server._get_opencode_usage()

    assert result["provider"] == "opencode"
    assert result["spend_usd"] == pytest.approx(0.0053, abs=1e-4)
    assert result["tokens"] == {"input": 1100, "output": 400, "cache_read": 7500}
    assert result["sessions"] == 1
    assert result["source"] == "local-db"


def test_opencode_db_columnar_aggregation(tmp_path, monkeypatch):
    """Newer stores with session-level cost/tokens_* columns are preferred."""
    from hermes_cli import web_server

    db_path = tmp_path / "opencode.db"
    _make_opencode_db(db_path, [], session_cols=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session (id, cost, tokens_input, tokens_output, "
            "tokens_cache_read, tokens_cache_write) VALUES (?, ?, ?, ?, ?, ?)",
            ("ses_1", 7.83, 243000, 28200, 7500000, 0),
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(web_server, "_opencode_db_path", lambda: db_path)

    result = web_server._get_opencode_usage()

    assert result["provider"] == "opencode"
    assert result["spend_usd"] == 7.83
    assert result["tokens"] == {"input": 243000, "output": 28200, "cache_read": 7500000}
    assert result["sessions"] == 1


def test_opencode_db_absent(tmp_path, monkeypatch):
    """Missing opencode DB → graceful error entry, no crash."""
    from hermes_cli import web_server

    missing = tmp_path / "nope" / "opencode.db"
    monkeypatch.setattr(web_server, "_opencode_db_path", lambda: missing)

    result = web_server._get_opencode_usage()

    assert result["provider"] == "opencode"
    assert result["error"] == "db not found"
    assert result["source"] == "local-db"


# --- commandcode ------------------------------------------------------------


def test_commandcode_session_count(tmp_path, monkeypatch):
    """JSONL transcripts are counted; checkpoints files are excluded."""
    from hermes_cli import web_server

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "p1.jsonl").write_text("""{"id":"a","createdAt":"t","prompt":"p","messageCount":3,"files":[]}
""")
    (projects / "p2.jsonl").write_text("""{"id":"b","createdAt":"t","prompt":"p","messageCount":0,"files":[]}
""")
    (projects / "p1.checkpoints.jsonl").write_text("{}")
    monkeypatch.setattr(web_server, "_commandcode_projects_dir", lambda: projects)

    result = web_server._get_commandcode_usage()

    assert result["provider"] == "commandcode"
    assert result["sessions"] == 2
    assert result["spend_usd"] is None
    assert result["source"] == "local-transcripts"
    assert "server-side only" in result["note"]


def test_commandcode_nested_projects(tmp_path, monkeypatch):
    """Transcripts under nested project directories are all counted."""
    from hermes_cli import web_server

    projects = tmp_path / "projects"
    nested = projects / "tmp-some-project"
    nested.mkdir(parents=True)
    for name in ("a.jsonl", "b.jsonl", "c.checkpoints.jsonl", "d.meta.json"):
        (nested / name).write_text("{}")
    monkeypatch.setattr(web_server, "_commandcode_projects_dir", lambda: projects)

    result = web_server._get_commandcode_usage()

    assert result["sessions"] == 2


def test_commandcode_missing_dir(tmp_path, monkeypatch):
    """No transcripts dir → zero sessions, no error."""
    from hermes_cli import web_server

    monkeypatch.setattr(
        web_server, "_commandcode_projects_dir", lambda: tmp_path / "absent"
    )

    result = web_server._get_commandcode_usage()

    assert result["sessions"] == 0
    assert result["spend_usd"] is None


# --- endpoint ---------------------------------------------------------------


def test_usage_providers_endpoint_shape(tmp_path, monkeypatch):
    """GET /api/usage/providers returns 200 with the aggregated providers JSON."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server

    # Pin each source to deterministic output so the endpoint shape is what we
    # assert, not whatever the live machine happens to have.
    monkeypatch.setattr(
        web_server,
        "_get_openrouter_usage",
        lambda profile: {
            "provider": "openrouter",
            "spend_usd": 7.83,
            "credits_remaining": 35.0,
            "period": "billing-month",
            "source": "api",
        },
    )
    monkeypatch.setattr(
        web_server,
        "_get_opencode_usage",
        lambda: {
            "provider": "opencode",
            "spend_usd": 0.0,
            "tokens": {"input": 243000, "output": 28200, "cache_read": 7500000},
            "sessions": 1,
            "source": "local-db",
        },
    )
    monkeypatch.setattr(
        web_server,
        "_get_commandcode_usage",
        lambda: {
            "provider": "commandcode",
            "spend_usd": None,
            "sessions": 12,
            "note": "server-side only — plan/credits not exposed via API",
            "source": "local-transcripts",
        },
    )

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    resp = client.get("/api/usage/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"providers"}
    providers = data["providers"]
    assert [p["provider"] for p in providers] == ["openrouter", "opencode", "commandcode"]

    or_entry = providers[0]
    assert or_entry["spend_usd"] == 7.83
    assert or_entry["credits_remaining"] == 35.0
    assert or_entry["period"] == "billing-month"

    oc_entry = providers[1]
    assert oc_entry["tokens"] == {"input": 243000, "output": 28200, "cache_read": 7500000}
    assert oc_entry["sessions"] == 1

    cc_entry = providers[2]
    assert cc_entry["spend_usd"] is None
    assert cc_entry["sessions"] == 12
