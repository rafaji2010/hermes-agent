"""Tests for the Usage tab budget/limits endpoint (GET /api/usage/budget).

Covers the JSON shape and the monthly-sum / rolling-window math in
``_get_usage_budget``.  Spend is summed over the sessions table exactly like
the analytics endpoints; caps come from the ``usage`` config section with
documented defaults.
"""

import time
from unittest.mock import patch

import pytest


class _FakeRows:
    """Minimal row stand-in: dict() of a sqlite3.Row-shaped object."""

    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """A connection whose execute() returns canned rows per statement."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._calls = []

    def execute(self, sql, params=()):
        self._calls.append((sql, params))
        row = self._rows.pop(0) if self._rows else {}
        return _FakeRows(row)


class _FakeDb:
    def __init__(self, rows):
        self._conn = _FakeConn(rows)

    def close(self):
        self.closed = True


def _make_month_rows(month_cost, month_actual):
    return [
        {"month_cost": month_cost, "month_actual": month_actual},
        {
            "five_hour_cost": 0.0,
            "week_cost": 0.0,
            "five_hour_oldest": None,
            "week_oldest": None,
            "total_runs": 0,
            "runs_last_24h": 0,
        },
    ]


@pytest.fixture
def fake_db(monkeypatch):
    """Patch _open_session_db_for_profile to return a scripted fake DB."""
    from hermes_cli import web_server

    holder = {}

    def _fake_open(profile, *, read_only):
        holder["profile"] = profile
        holder["read_only"] = read_only
        return holder["db"]

    monkeypatch.setattr(web_server, "_open_session_db_for_profile", _fake_open)
    return holder


def test_usage_budget_json_shape(fake_db, monkeypatch):
    """The response carries the expected monthly/limits/runs shape."""
    from hermes_cli import web_server

    fake_db["db"] = _FakeDb(_make_month_rows(12.34, 0.0))
    monkeypatch.setattr(
        web_server, "load_config", lambda: {"usage": {"monthly_cap_usd": 70.0}}
    )

    data = web_server._get_usage_budget(None)

    assert set(data) == {"monthly", "limits", "runs"}
    assert set(data["monthly"]) == {"spend_usd", "cap_usd", "period_start", "period_end", "resets_in"}
    assert set(data["limits"]) == {"five_hour", "weekly"}
    assert set(data["limits"]["five_hour"]) == {"used", "cap", "pct", "resets_in"}
    assert set(data["limits"]["weekly"]) == {"used", "cap", "pct", "resets_in"}
    assert set(data["runs"]) == {"total", "last_24h"}

    assert data["monthly"]["spend_usd"] == 12.34
    assert data["monthly"]["cap_usd"] == 70.0
    assert data["monthly"]["period_start"] == time.strftime("%Y-%m-01")
    assert data["limits"]["five_hour"]["cap"] == 14.0
    assert data["limits"]["weekly"]["cap"] == 35.0
    assert data["runs"] == {"total": 0, "last_24h": 0}


def test_monthly_spend_sums_only_current_month(fake_db, monkeypatch):
    """Monthly spend uses the actual cost when present, else the estimate."""
    from hermes_cli import web_server

    # actual_cost wins over estimated_cost
    fake_db["db"] = _FakeDb(_make_month_rows(100.0, 12.34))
    data = web_server._get_usage_budget(None)
    assert data["monthly"]["spend_usd"] == 12.34

    # no actual cost -> falls back to the estimate
    fake_db["db"] = _FakeDb(_make_month_rows(12.34, 0.0))
    data = web_server._get_usage_budget(None)
    assert data["monthly"]["spend_usd"] == 12.34


def test_config_caps_honored(fake_db, monkeypatch):
    """Caps come from the usage config section, not hardcoded defaults."""
    from hermes_cli import web_server

    fake_db["db"] = _FakeDb(_make_month_rows(5.0, 0.0))
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda: {"usage": {
            "monthly_cap_usd": 100.0,
            "five_hour_limit_usd": 20.0,
            "weekly_limit_usd": 50.0,
        }},
    )

    data = web_server._get_usage_budget(None)
    assert data["monthly"]["cap_usd"] == 100.0
    assert data["limits"]["five_hour"]["cap"] == 20.0
    assert data["limits"]["weekly"]["cap"] == 50.0


def test_default_caps_when_config_missing(fake_db, monkeypatch):
    """A config with no usage section falls back to the documented defaults."""
    from hermes_cli import web_server

    fake_db["db"] = _FakeDb(_make_month_rows(0.0, 0.0))
    monkeypatch.setattr(web_server, "load_config", lambda: {})

    data = web_server._get_usage_budget(None)
    assert data["monthly"]["cap_usd"] == 70.0
    assert data["limits"]["five_hour"]["cap"] == 14.0
    assert data["limits"]["weekly"]["cap"] == 35.0


def test_percentages_are_used_over_cap(fake_db, monkeypatch):
    """pct = used / cap * 100, rounded to one decimal."""
    from hermes_cli import web_server

    rows = [
        {"month_cost": 0.0, "month_actual": 0.0},
        {
            "five_hour_cost": 2.08,
            "week_cost": 15.75,
            "five_hour_oldest": None,
            "week_oldest": None,
            "total_runs": 2414,
            "runs_last_24h": 88,
        },
    ]
    fake_db["db"] = _FakeDb(rows)
    monkeypatch.setattr(web_server, "load_config", lambda: {})

    data = web_server._get_usage_budget(None)
    assert data["limits"]["five_hour"]["used"] == 2.08
    assert data["limits"]["five_hour"]["pct"] == pytest.approx(14.9, abs=0.01)
    assert data["limits"]["weekly"]["used"] == 15.75
    assert data["limits"]["weekly"]["pct"] == pytest.approx(45.0, abs=0.01)
    assert data["runs"] == {"total": 2414, "last_24h": 88}


def test_rolling_windows_use_expected_cutoffs(fake_db, monkeypatch):
    """The 5h/weekly sums filter by started_at >= now - window."""
    from hermes_cli import web_server

    fake_db["db"] = _FakeDb(_make_month_rows(0.0, 0.0))
    monkeypatch.setattr(web_server, "load_config", lambda: {})

    web_server._get_usage_budget(None)

    calls = fake_db["db"]._conn._calls
    window_sql = calls[1][0]
    window_params = calls[1][1]
    now = time.time()
    assert "five_hour_cost" in window_sql
    assert "week_cost" in window_sql
    # five params: five_hour_cutoff, week_cutoff, five_hour_oldest,
    # week_oldest, last_24h cutoff
    assert len(window_params) == 5
    assert abs(window_params[0] - (now - 5 * 3600)) < 5
    assert abs(window_params[1] - (now - 7 * 86400)) < 5
    assert abs(window_params[2] - (now - 5 * 3600)) < 5
    assert abs(window_params[3] - (now - 7 * 86400)) < 5
    assert abs(window_params[4] - (now - 86400)) < 5


def test_rolling_resets_in_ages_out_oldest_spend(fake_db, monkeypatch):
    """Rolling-limit resets_in counts down to when the oldest in-window
    spend exits the window (oldest_started_at + window_duration - now)."""
    from hermes_cli import web_server

    now = time.time()
    rows = [
        {"month_cost": 0.0, "month_actual": 0.0},
        {
            "five_hour_cost": 2.08,
            "week_cost": 15.75,
            "five_hour_oldest": now - 3 * 3600,   # 3h ago → resets in 2h
            "week_oldest": now - 3 * 86400,       # 3d ago → resets in 4d
            "total_runs": 2414,
            "runs_last_24h": 88,
        },
    ]
    fake_db["db"] = _FakeDb(rows)
    monkeypatch.setattr(web_server, "load_config", lambda: {})

    data = web_server._get_usage_budget(None)
    # ~3h old spend → ~2h until it ages out (allow a little clock skew).
    assert data["limits"]["five_hour"]["resets_in"] in ("2h", "1h 59m")
    assert data["limits"]["weekly"]["resets_in"] in ("4d 0h", "3d 23h")


def test_resets_in_is_human_readable():
    """_human_delta formats seconds into compact durations."""
    from hermes_cli.web_server import _human_delta

    assert _human_delta(3600) == "1h"
    assert _human_delta(2 * 3600) == "2h"
    assert _human_delta(24 * 3600 + 13 * 3600) == "1d 13h"
    assert _human_delta(90) == "1m"
    assert _human_delta(0) == "0m"
    assert _human_delta(-10) == "0m"


def test_endpoint_returns_200_with_shape(tmp_path, monkeypatch):
    """The FastAPI route returns 200 + the expected JSON shape."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli import web_server
    from hermes_constants import get_hermes_home
    from hermes_state import SessionDB

    monkeypatch.setattr(
        __import__("hermes_state"), "DEFAULT_DB_PATH", get_hermes_home() / "state.db"
    )

    # Seed a real session with a cost so the endpoint has something to sum.
    db = SessionDB()
    try:
        db.create_session(session_id="budget-test", source="cli", model="anthropic/claude-sonnet-4")
        db._conn.execute(
            "UPDATE sessions SET estimated_cost_usd = ?, actual_cost_usd = ? WHERE id = ?",
            (5.0, 4.0, "budget-test"),
        )
        db._conn.commit()
    finally:
        db.close()

    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN

    resp = client.get("/api/usage/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"monthly", "limits", "runs"}
    assert data["monthly"]["spend_usd"] == 4.0
    assert data["monthly"]["cap_usd"] == 70.0
    assert data["limits"]["five_hour"]["cap"] == 14.0
    assert data["limits"]["weekly"]["cap"] == 35.0
    assert data["runs"]["total"] == 1
