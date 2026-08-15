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
    """OpenRouter auth/key + credits responses map monthly usage + credits."""
    from hermes_cli import web_server

    urls = []

    def _fake_get(url, *, headers, timeout):
        urls.append(url)
        if "auth/key" in url:
            return _FakeOpenRouterResponse({
                "data": {"usage": 7.83, "usage_monthly": 0.0643, "usage_weekly": 0.0643, "usage_daily": 0.0}
            })
        return _FakeOpenRouterResponse({"data": {"total_credits": 35, "total_usage": 7.83}})

    monkeypatch.setattr(web_server.httpx, "get", _fake_get)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    result = web_server._get_openrouter_usage(None)

    assert urls == [
        "https://openrouter.ai/api/v1/auth/key",
        "https://openrouter.ai/api/v1/credits",
    ]
    assert result == {
        "provider": "openrouter",
        "spend_usd": 0.0643,  # monthly — matches OpenRouter dashboard
        "spend_weekly": 0.0643,
        "spend_daily": 0.0,
        "credits_remaining": 35.0,
        "period": "last-30-days",
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
                "CREATE TABLE session (id TEXT, model TEXT, cost REAL, tokens_input INTEGER, "
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


@pytest.fixture
def disable_opencode_cli(monkeypatch):
    """Force the opencode DB path by making the CLI collector fail (as it does
    on machines without ``opencode`` on PATH)."""
    from hermes_cli import web_server

    monkeypatch.setattr(web_server, "_run_opencode_stats_cli", lambda: None)


def test_opencode_db_json_aggregation(tmp_path, monkeypatch, disable_opencode_cli):
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


def test_opencode_db_columnar_aggregation(tmp_path, monkeypatch, disable_opencode_cli):
    """Newer stores with session-level cost/tokens_* columns are preferred."""
    from hermes_cli import web_server

    db_path = tmp_path / "opencode.db"
    _make_opencode_db(db_path, [], session_cols=True)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO session (id, model, cost, tokens_input, tokens_output, "
            "tokens_cache_read, tokens_cache_write) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ses_1", json.dumps({"id": "deepseek-v4-flash-free", "providerID": "opencode"}),
             7.83, 243000, 28200, 7500000, 0),
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
    assert result["source"] == "local-db"
    # Per-model rows come from the session table, with the JSON model blob
    # unwrapped to its id.
    assert result["models"] == [
        {
            "model": "deepseek-v4-flash-free",
            "requests": 1,
            "input": 243000,
            "output": 28200,
            "cache_read": 7500000,
            "cost": 7.83,
        }
    ]


def test_opencode_db_absent(tmp_path, monkeypatch, disable_opencode_cli):
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


def test_commandcode_per_model_counts(tmp_path, monkeypatch):
    """Assistant messages per model are counted from real transcript JSONL."""
    from hermes_cli import web_server

    projects = tmp_path / "projects"
    projects.mkdir()
    lines = "\n".join([
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps({"type": "message", "message": {"role": "user"}}),
        json.dumps({"type": "message", "message": {"role": "assistant"},
                    "model": "deepseek/deepseek-v4-flash"}),
        json.dumps({"type": "message", "message": {"role": "assistant"},
                    "model": "deepseek/deepseek-v4-flash"}),
        json.dumps({"type": "message", "message": {"role": "assistant"},
                    "model": "meta/muse-spark-1.2-contributor"}),
        json.dumps({"type": "message", "message": {"role": "assistant"}}),
    ])
    (projects / "p1.jsonl").write_text(lines + "\n")
    monkeypatch.setattr(web_server, "_commandcode_projects_dir", lambda: projects)

    result = web_server._get_commandcode_usage()

    assert result["sessions"] == 1
    assert result["models"] == [
        {"model": "deepseek-v4-flash", "requests": 2},
        {"model": "muse-spark-1.2-contributor", "requests": 1},
    ]


# --- shared helpers ----------------------------------------------------------


def test_short_model_name():
    from hermes_cli import web_server

    assert web_server._short_model_name("openrouter/deepseek/deepseek-chat") == "deepseek-chat"
    assert web_server._short_model_name("opencode/deepseek-v4-flash-free") == "deepseek-v4-flash-free"
    assert web_server._short_model_name("deepseek/deepseek-v4-flash") == "deepseek-v4-flash"
    assert web_server._short_model_name("deepseek-chat") == "deepseek-chat"
    assert web_server._short_model_name("") == "unknown"


# --- opencode CLI parsing ----------------------------------------------------


_OPENCODE_STATS_SAMPLE = """\
┌──────────────────────────────┐
│           OVERVIEW           │
├──────────────────────────────┤
│Sessions                      3 │
│Messages                    139 │
│Days                          2 │
└──────────────────────────────┘

┌──────────────────────────────┐
│        COST & TOKENS         │
├──────────────────────────────┤
│Total Cost                $0.00 │
│Avg Cost/Day              $0.00 │
│Avg Tokens/Session          4.0M │
│Median Tokens/Session       3.9M │
│Input                     386.2K │
│Output                     42.6K │
│Cache Read                 11.5M │
│Cache Write                    0 │
└──────────────────────────────┘

┌──────────────────────────────┐
│         MODEL USAGE          │
├──────────────────────────────┤
│ opencode/deepseek-v4-flash-free │
│  Messages                  136 │
│  Input Tokens            386.2K │
│  Output Tokens            42.6K │
│  Cache Read               11.5M │
│  Cache Write                  0 │
│  Cost                  $0.0000 │
├──────────────────────────────┤
└──────────────────────────────┘

┌──────────────────────────────┐
│         TOOL USAGE           │
├──────────────────────────────┤
│ bash      ██  100 (60%)      │
└──────────────────────────────┘
"""


def test_parse_opencode_stats():
    """``opencode stats --models`` ASCII output parses into structured data."""
    from hermes_cli import web_server

    parsed = web_server._parse_opencode_stats(_OPENCODE_STATS_SAMPLE)

    assert parsed is not None
    assert parsed["sessions"] == 3
    assert parsed["messages"] == 139
    assert parsed["spend_usd"] == 0.0
    assert parsed["input"] == 386200
    assert parsed["output"] == 42600
    assert parsed["cache_read"] == 11500000
    assert parsed["models"] == [
        {
            "model": "opencode/deepseek-v4-flash-free",
            "requests": 136,
            "input": 386200,
            "output": 42600,
            "cache_read": 11500000,
            "cache_write": 0,
            "cost": 0.0,
        }
    ]


def test_parse_opencode_stats_handles_ansi():
    """ANSI cursor escapes between sections don't confuse the parser."""
    from hermes_cli import web_server

    noisy = _OPENCODE_STATS_SAMPLE.replace(
        "└──────────────\n", "└──────────────\n\x1b[1A\x1b[1A"
    )
    parsed = web_server._parse_opencode_stats(noisy)
    assert parsed is not None
    assert parsed["models"][0]["model"] == "opencode/deepseek-v4-flash-free"


def test_parse_opencode_stats_garbage_returns_none():
    from hermes_cli import web_server

    assert web_server._parse_opencode_stats("not a stats table") is None


def test_opencode_cli_path(monkeypatch):
    """The CLI collector shapes models for the dashboard when available."""
    from hermes_cli import web_server

    monkeypatch.setattr(
        web_server,
        "_run_opencode_stats_cli",
        lambda: {
            "sessions": 3,
            "messages": 139,
            "days": 2,
            "spend_usd": 0.0,
            "avg_cost_per_day": 0.0,
            "avg_tokens_per_session": 4000000,
            "input": 386200,
            "output": 42600,
            "cache_read": 11500000,
            "cache_write": 0,
            "models": [
                {
                    "model": "opencode/deepseek-v4-flash-free",
                    "requests": 136,
                    "input": 386200,
                    "output": 42600,
                    "cache_read": 11500000,
                    "cache_write": 0,
                    "cost": 0.0,
                }
            ],
        },
    )

    result = web_server._get_opencode_usage()

    assert result["provider"] == "opencode"
    assert result["source"] == "cli"
    assert result["spend_usd"] == 0.0
    assert result["sessions"] == 3
    assert result["tokens"] == {"input": 386200, "output": 42600, "cache_read": 11500000}
    assert result["models"][0]["model"] == "deepseek-v4-flash-free"
    assert result["models"][0]["requests"] == 136


# --- OpenRouter per-model split ----------------------------------------------


def test_openrouter_per_model_split(monkeypatch):
    """Real spend is split across locally-recorded OpenRouter models."""
    from hermes_cli import web_server

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def _fake_get(url, *, headers, timeout):
        if "auth/key" in url:
            return _FakeOpenRouterResponse(
                {"data": {"usage": 7.83, "usage_monthly": 2.0, "usage_weekly": 1.0, "usage_daily": 0.5}}
            )
        return _FakeOpenRouterResponse(
            {"data": {"total_credits": 35, "total_usage": 7.83}}
        )

    monkeypatch.setattr(web_server.httpx, "get", _fake_get)
    monkeypatch.setattr(
        web_server,
        "_openrouter_session_models",
        lambda profile: [
            {
                "model": "openrouter/deepseek/deepseek-chat",
                "requests": 3,
                "input": 1000,
                "output": 500,
                "cache_read": 0,
                "estimated_cost": 0.75,
            },
            {
                "model": "openrouter/anthropic/claude-sonnet",
                "requests": 1,
                "input": 200,
                "output": 100,
                "cache_read": 0,
                "estimated_cost": 0.25,
            },
        ],
    )

    result = web_server._get_openrouter_usage(None)

    # Monthly (not all-time) spend is what the dashboard shows.
    assert result["spend_usd"] == 2.0
    assert result["credits_remaining"] == 35.0
    # Spend split proportionally to local estimated cost (75% / 25%).
    assert result["models"][0]["model"] == "deepseek-chat"
    assert result["models"][0]["cost"] == pytest.approx(2.0 * 0.75, abs=1e-3)
    assert result["models"][1]["model"] == "claude-sonnet"
    assert result["models"][1]["cost"] == pytest.approx(2.0 * 0.25, abs=1e-3)
    assert result["tokens"] == {"input": 1200, "output": 600}
    assert "estimated" in result["note"]


# --- consolidated requests-by-model ------------------------------------------


def test_consolidate_requests_by_model():
    from hermes_cli import web_server

    providers = [
        {
            "provider": "openrouter",
            "models": [{"model": "deepseek-chat", "requests": 5}],
        },
        {
            "provider": "opencode",
            "models": [{"model": "deepseek-v4-flash-free", "requests": 136}],
        },
        {
            "provider": "commandcode",
            "models": [
                {"model": "deepseek-v4-flash", "requests": 325},
                {"model": "deepseek-v4-flash-free", "requests": 10},
            ],
        },
    ]

    rows = web_server._consolidate_requests_by_model(providers)

    # Same short model name under two providers sums; the dominant provider
    # is credited (opencode 136 > commandcode 10).
    assert {r["model"] for r in rows} == {"deepseek-chat", "deepseek-v4-flash-free", "deepseek-v4-flash"}
    by_name = {r["model"]: r for r in rows}
    assert by_name["deepseek-v4-flash-free"] == {
        "model": "deepseek-v4-flash-free",
        "requests": 146,
        "provider": "opencode",
    }
    assert by_name["deepseek-chat"]["provider"] == "openrouter"
    assert by_name["deepseek-v4-flash"]["requests"] == 325


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
            "models": [{"model": "deepseek-chat", "requests": 5}],
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
            "models": [{"model": "deepseek-v4-flash-free", "requests": 136}],
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
            "models": [{"model": "deepseek-v4-flash", "requests": 325}],
        },
    )

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    resp = client.get("/api/usage/providers")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"providers", "requests_by_model"}
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

    # Consolidated bar-chart series is present and matches the providers.
    assert data["requests_by_model"] == [
        {"model": "deepseek-v4-flash", "requests": 325, "provider": "commandcode"},
        {"model": "deepseek-v4-flash-free", "requests": 136, "provider": "opencode"},
        {"model": "deepseek-chat", "requests": 5, "provider": "openrouter"},
    ]
