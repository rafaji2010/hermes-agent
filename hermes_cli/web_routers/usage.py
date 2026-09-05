"""Fork-only usage dashboard routes (fork addition): ``/api/usage/budget`` + ``/api/usage/providers``.

Re-homed from the pre-decomposition ``hermes_cli.web_server`` monolith during
the Sep 2026 upstream merge. ``GET /api/analytics/usage`` + ``/api/analytics/models``
stay owned by :mod:`hermes_cli.web_routers.analytics` (verified identical);
only the fork's budget-caps and per-provider-spend aggregation live here.
Shared session-db / profile-scope helpers are late-bound through
:mod:`hermes_cli.web_deps` (cycle-safe, monkeypatch-friendly).
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException

from hermes_cli.web_deps import late

router = APIRouter()

_log = logging.getLogger(__name__)

# Late-bound so a test's monkeypatch on the owning module wins at call time.
# Late-bound through the web_server seam (not the owner modules directly) so
# the fork's tests can monkeypatch web_server.<name> and the handlers honor
# it. Call time only — no import cycle (web_server is fully loaded by then).
_open_session_db_for_profile = late("_open_session_db_for_profile", "hermes_cli.web_server")
_config_profile_scope = late("_config_profile_scope", "hermes_cli.web_server")
# Config/env reads go through the same seam (tests patch web_server.load_*).
_ws_load_config = late("load_config", "hermes_cli.web_server")
_ws_load_env = late("load_env", "hermes_cli.web_server")


def _usage_budget_defaults(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """Budget/limit caps from the ``usage`` config section (USD).

    Missing keys fall back to the reference-plan defaults (a $70/mo plan):
    monthly cap $70, 5-hour limit $14, weekly limit $35.  These defaults live
    in ``DEFAULT_CONFIG``; this fallback only guards a hand-edited config that
    dropped the whole section.
    """
    usage_cfg = (cfg or {}).get("usage") or {}
    try:
        monthly = float(usage_cfg.get("monthly_cap_usd") or 0.0) or 70.0
        five_hour = float(usage_cfg.get("five_hour_limit_usd") or 0.0) or 14.0
        weekly = float(usage_cfg.get("weekly_limit_usd") or 0.0) or 35.0
    except (TypeError, ValueError):
        monthly, five_hour, weekly = 70.0, 14.0, 35.0
    return {"monthly_cap_usd": monthly, "five_hour_limit_usd": five_hour, "weekly_limit_usd": weekly}


def _human_delta(seconds: float) -> str:
    """Compact human duration, e.g. ``2h`` or ``24d 13h``.

    Matches the OpenRouter-style budget panel wording the Usage tab renders.
    """
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h" + (f" {minutes}m" if minutes else "")
    return f"{minutes}m"


def _get_usage_budget(profile: Optional[str] = None) -> Dict[str, Any]:
    """Budget + limits for the Usage tab's spend-vs-cap / rolling-limit cards.

    Spend is summed over the sessions table (same source as
    ``_get_usage_analytics``): monthly = current calendar month, 5-hour /
    weekly = trailing windows ending now.  Caps come from the ``usage``
    config section (``usage.monthly_cap_usd`` etc.) with documented defaults.
    """
    import calendar

    db = _open_session_db_for_profile(profile, read_only=True)
    try:
        now = time.time()
        now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
        month_start = datetime(now_dt.year, now_dt.month, 1, tzinfo=timezone.utc)
        month_start_ts = month_start.timestamp()
        month_end_ts = datetime(
            now_dt.year, now_dt.month, calendar.monthrange(now_dt.year, now_dt.month)[1],
            23, 59, 59, tzinfo=timezone.utc,
        ).timestamp()

        cur = db._conn.execute("""
            SELECT
                COALESCE(SUM(estimated_cost_usd), 0) as month_cost,
                COALESCE(SUM(actual_cost_usd), 0) as month_actual
            FROM sessions WHERE started_at >= ?
        """, (month_start_ts,))
        month_row = dict(cur.fetchone())

        # Rolling-window limits: 5h back and 7d back, ending now.
        five_hour_cutoff = now - 5 * 3600
        week_cutoff = now - 7 * 86400
        cur2 = db._conn.execute("""
            SELECT
                SUM(CASE WHEN started_at >= ? THEN COALESCE(estimated_cost_usd, 0) ELSE 0 END) as five_hour_cost,
                SUM(CASE WHEN started_at >= ? THEN COALESCE(estimated_cost_usd, 0) ELSE 0 END) as week_cost,
                MIN(CASE WHEN started_at >= ? THEN started_at END) as five_hour_oldest,
                MIN(CASE WHEN started_at >= ? THEN started_at END) as week_oldest,
                COUNT(*) as total_runs,
                SUM(CASE WHEN started_at >= ? THEN 1 ELSE 0 END) as runs_last_24h
            FROM sessions
        """, (five_hour_cutoff, week_cutoff, five_hour_cutoff, week_cutoff, now - 86400))
        window_row = dict(cur2.fetchone())

        with _config_profile_scope(profile):
            cfg = _ws_load_config()
        caps = _usage_budget_defaults(cfg)

        monthly_spend = float(month_row.get("month_cost") or 0.0)
        monthly_actual = float(month_row.get("month_actual") or 0.0)
        five_hour_spend = float(window_row.get("five_hour_cost") or 0.0)
        weekly_spend = float(window_row.get("week_cost") or 0.0)

        def _pct(used: float, cap: float) -> float:
            return round(used / cap * 100, 1) if cap > 0 else 0.0

        def _window_resets_in(oldest: Optional[float], window_s: float) -> str:
            # Rolling window: spend resets when the oldest spend in the window
            # ages out (oldest_started_at + window_duration).  No spend in the
            # window → already reset.
            if not oldest:
                return "0m"
            return _human_delta(oldest + window_s - now)

        return {
            "monthly": {
                # Prefer real (provider-billed) cost; fall back to estimate.
                "spend_usd": round(monthly_actual if monthly_actual > 0 else monthly_spend, 2),
                "cap_usd": caps["monthly_cap_usd"],
                "period_start": month_start.strftime("%Y-%m-%d"),
                "period_end": datetime.fromtimestamp(month_end_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
                "resets_in": _human_delta(month_end_ts - now),
            },
            "limits": {
                "five_hour": {
                    "used": round(five_hour_spend, 2),
                    "cap": caps["five_hour_limit_usd"],
                    "pct": _pct(five_hour_spend, caps["five_hour_limit_usd"]),
                    "resets_in": _window_resets_in(window_row.get("five_hour_oldest"), 5 * 3600),
                },
                "weekly": {
                    "used": round(weekly_spend, 2),
                    "cap": caps["weekly_limit_usd"],
                    "pct": _pct(weekly_spend, caps["weekly_limit_usd"]),
                    "resets_in": _window_resets_in(window_row.get("week_oldest"), 7 * 86400),
                },
            },
            "runs": {
                "total": int(window_row.get("total_runs") or 0),
                "last_24h": int(window_row.get("runs_last_24h") or 0),
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("GET /api/usage/budget failed")
        raise HTTPException(status_code=500, detail=f"Failed to compute usage budget: {exc}")
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/api/usage/budget")
async def get_usage_budget(profile: Optional[str] = None):
    """Budget + limits for the Usage tab (off the event loop)."""
    return await asyncio.to_thread(_get_usage_budget, profile)


# ---------------------------------------------------------------------------
# /api/usage/providers — real per-provider spend aggregation for the Usage tab.
#
# Three independent sources:
#   1. OpenRouter  — exact REST spend via GET https://openrouter.ai/api/v1/credits,
#                    keyed by OPENROUTER_API_KEY from the profile .env (or the
#                    process env).  ``total_usage`` maps to spend USD and
#                    ``total_credits`` to remaining credits.  OpenRouter exposes
#                    NO per-model listing endpoint — ``/api/v1/generation``
#                    requires a single ``id`` (the raw call 400s with a ZodError
#                    on `path: ["id"]`), so per-model token bars are estimated
#                    from the models this machine routed through OpenRouter,
#                    splitting the real ``total_usage`` proportionally.
#   2. opencode    — ``opencode stats --models`` (the CLI the user sees) is run
#                    and its ASCII table parsed for overview + per-model
#                    Messages / Input / Output / Cache Read / Cost.  Falls back
#                    to the local SQLite store at ~/.local/share/opencode/opencode.db.
#   3. commandcode — no server usage/billing API (all endpoints 404); its real
#                    plan/credits live server-side in the TUI /usage panel and
#                    are not scrapeable.  Local JSONL transcripts under
#                    ~/.commandcode/projects/ are counted for sessions; assistant
#                    message counts per model feed the requests-by-model chart.
#                    Spend stays null.
#
# The consolidated ``requests_by_model`` series (one entry per short model name,
# colored per provider) powers the Requests-by-model bar chart in the dashboard.
# ---------------------------------------------------------------------------

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STATS_BOX_CHARS = str.maketrans("", "", "│─┌┐└┘├┤")


def _short_model_name(model: str) -> str:
    """Collapse a provider-prefixed model slug to its readable tail.

    'openrouter/deepseek/deepseek-chat' -> 'deepseek-chat'
    'opencode/deepseek-v4-flash-free'   -> 'deepseek-v4-flash-free'
    'deepseek/deepseek-v4-flash'        -> 'deepseek-v4-flash'
    'deepseek-chat'                     -> 'deepseek-chat'
    """
    model = (model or "").strip()
    if not model:
        return "unknown"
    if model.startswith("openrouter/"):
        parts = model.split("/")
        return "/".join(parts[2:]) if len(parts) >= 3 else parts[-1]
    return model.split("/")[-1]


def _openrouter_api_key(profile: Optional[str]) -> str:
    """Resolve the OpenRouter API key for a profile.

    The key lives in the profile secrets file (``~/.hermes/.env``) as
    ``OPENROUTER_API_KEY``; the process env may also carry it.  Resolution
    order: process env, then the profile-scoped ``.env``.  Never logged or
    echoed in any response.
    """
    key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        return key
    with _config_profile_scope(profile):
        key = (_ws_load_env().get("OPENROUTER_API_KEY") or "").strip()
    return key


def _openrouter_session_models(profile: Optional[str]) -> List[Dict[str, Any]]:
    """Per-model usage recorded locally for OpenRouter-routed sessions.

    OpenRouter's REST API has no listing endpoint (``/api/v1/generation``
    requires a single ``id``), so the per-model token bars are estimated from
    the models this machine actually routed through OpenRouter (hermes
    ``session_model_usage`` / ``sessions`` rows whose billing_provider is
    'openrouter').  Returns [] when nothing is recorded locally.
    """
    try:
        db = _open_session_db_for_profile(profile, read_only=True)
    except Exception:
        return []
    try:
        try:
            rows = db._conn.execute(
                """
                SELECT u.model,
                       SUM(COALESCE(u.api_call_count, 0)) as api_calls,
                       SUM(u.input_tokens) as input_tokens,
                       SUM(u.output_tokens) as output_tokens,
                       SUM(COALESCE(u.cache_read_tokens, 0)) as cache_read_tokens,
                       COALESCE(SUM(u.estimated_cost_usd), 0) as estimated_cost
                FROM session_model_usage u
                JOIN sessions s ON s.id = u.session_id
                WHERE u.billing_provider = 'openrouter' OR u.model LIKE 'openrouter/%'
                GROUP BY u.model
                ORDER BY SUM(u.input_tokens) + SUM(u.output_tokens) DESC
                """
            ).fetchall()
        except Exception:
            # Fall back to the sessions table when session_model_usage is
            # unavailable (e.g. older stores).
            rows = db._conn.execute(
                """
                SELECT model,
                       SUM(COALESCE(api_call_count, 0)) as api_calls,
                       SUM(input_tokens) as input_tokens,
                       SUM(output_tokens) as output_tokens,
                       SUM(COALESCE(cache_read_tokens, 0)) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost
                FROM sessions
                WHERE (billing_provider = 'openrouter' OR model LIKE 'openrouter/%')
                  AND model IS NOT NULL
                GROUP BY model
                ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
                """
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "model": row[0],
                "requests": int(row[1] or 0),
                "input": int(row[2] or 0),
                "output": int(row[3] or 0),
                "cache_read": int(row[4] or 0),
                "estimated_cost": float(row[5] or 0.0),
            })
        return result
    finally:
        db.close()


def _get_openrouter_usage(profile: Optional[str]) -> Dict[str, Any]:
    """Fetch OpenRouter credits spend. Returns an error entry when the key is
    missing or the API call fails; the response body never contains the key.

    Per-model token bars come from :func:`_openrouter_session_models` — the
    real ``total_usage`` spend is split across those models proportionally to
    their locally recorded estimated cost (falling back to a token share when
    cost accounting is absent)."""
    key = _openrouter_api_key(profile)
    if not key:
        return {"provider": "openrouter", "error": "no key"}
    try:
        # Monthly spend (per-key): /auth/key returns usage_monthly — the real
        # number OpenRouter's own dashboard shows for this key's last-30-days
        # usage. /credits total_usage is ALL-TIME (misleading for a monthly
        # view), so it is used only for credits remaining, not spend.
        resp = httpx.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=8.0,
        )
        resp.raise_for_status()
        auth_data = (resp.json() or {}).get("data") or {}
        usage_monthly = float(auth_data.get("usage_monthly") or 0.0)
        usage_weekly = float(auth_data.get("usage_weekly") or 0.0)
        usage_daily = float(auth_data.get("usage_daily") or 0.0)

        # Credits remaining from /credits (all-time account state).
        credits_resp = httpx.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=8.0,
        )
        credits_resp.raise_for_status()
        credits_data = (credits_resp.json() or {}).get("data") or {}
        total_credits = float(credits_data.get("total_credits") or 0.0)

        entry: Dict[str, Any] = {
            "provider": "openrouter",
            "spend_usd": round(usage_monthly, 4),
            "spend_weekly": round(usage_weekly, 4),
            "spend_daily": round(usage_daily, 4),
            "credits_remaining": round(total_credits, 4),
            "period": "last-30-days",
            "source": "api",
        }
        models = _openrouter_session_models(profile)
        if models:
            total_est = sum(m["estimated_cost"] for m in models)
            total_tokens = sum(m["input"] + m["output"] for m in models)
            per_model = []
            for m in models:
                if total_est > 0:
                    share = m["estimated_cost"] / total_est
                elif total_tokens > 0:
                    share = (m["input"] + m["output"]) / total_tokens
                else:
                    share = 1.0 / len(models)
                per_model.append({
                    "model": _short_model_name(m["model"]),
                    "requests": m["requests"],
                    "input": m["input"],
                    "output": m["output"],
                    "cost": round(usage_monthly * share, 6),
                })
            entry["tokens"] = {
                "input": sum(m["input"] for m in models),
                "output": sum(m["output"] for m in models),
            }
            entry["models"] = per_model
            entry["note"] = (
                "per-model split estimated from local sessions — "
                "OpenRouter exposes no usage listing API"
            )
        return entry
    except Exception as exc:
        _log.warning("GET /api/usage/providers: OpenRouter fetch failed: %s", exc)
        return {"provider": "openrouter", "error": f"fetch failed: {exc}", "source": "api"}


def _parse_opencode_stats(text: str) -> Optional[Dict[str, Any]]:
    """Parse ``opencode stats --models`` ASCII output into structured data.

    The CLI has no ``--json`` flag; the table is box-drawn UTF-8 with ANSI
    cursor escapes.  Sections: OVERVIEW (Sessions/Messages/Days), COST & TOKENS
    (Total Cost, Input, Output, Cache Read, ...) and MODEL USAGE (per-model
    Messages / Input Tokens / Output Tokens / Cache Read / Cache Write /
    Cost).  Values are right-aligned; token counts use K/M suffixes.  Returns
    None when the expected sections/rows are missing (version drift).
    """
    text = _ANSI_ESCAPE_RE.sub("", text or "")
    lines = [ln.translate(_STATS_BOX_CHARS) for ln in text.splitlines()]

    def _section(after: str, before: str) -> List[str]:
        start = end = None
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if stripped == after:
                start = i + 1
            elif stripped == before and start is not None:
                end = i
                break
        if start is None:
            return []
        return lines[start:(end if end is not None else len(lines))]

    def _rows(block: List[str]) -> Dict[str, str]:
        parsed = {}
        for ln in block:
            m = re.match(r"^(.*?)\s+(\S+)\s*$", ln)
            if not m:
                continue
            label, value = m.group(1).strip(), m.group(2).strip()
            if label and value:
                parsed[label] = value
        return parsed

    def _num(value: str, default: float = 0.0) -> float:
        v = (value or "").strip().lstrip("$").replace(",", "")
        if not v:
            return default
        mult = 1
        if v.endswith("K"):
            mult, v = 1_000, v[:-1]
        elif v.endswith("M"):
            mult, v = 1_000_000, v[:-1]
        try:
            return float(v) * mult
        except ValueError:
            return default

    overview = _rows(_section("OVERVIEW", "COST & TOKENS"))
    cost_tokens = _rows(_section("COST & TOKENS", "MODEL USAGE"))
    model_block = _section("MODEL USAGE", "TOOL USAGE")
    if not overview or not cost_tokens:
        return None

    models: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for ln in model_block:
        if ln.startswith("  "):
            stripped = ln.strip()
            if not stripped:
                continue
            m = re.match(r"^(.*?)\s+(\S+)\s*$", stripped)
            if not m or current is None:
                continue
            label, value = m.group(1).strip(), m.group(2).strip()
            if label == "Messages":
                current["requests"] = int(_num(value))
            elif label == "Input Tokens":
                current["input"] = int(_num(value))
            elif label == "Output Tokens":
                current["output"] = int(_num(value))
            elif label == "Cache Read":
                current["cache_read"] = int(_num(value))
            elif label == "Cache Write":
                current["cache_write"] = int(_num(value))
            elif label == "Cost":
                current["cost"] = _num(value)
        elif ln.startswith(" ") and ln.strip():
            if current is not None:
                models.append(current)
            current = {"model": ln.strip()}
    if current is not None:
        models.append(current)

    return {
        "sessions": int(_num(overview.get("Sessions"))),
        "messages": int(_num(overview.get("Messages"))),
        "days": int(_num(overview.get("Days"))),
        "spend_usd": _num(cost_tokens.get("Total Cost")),
        "avg_cost_per_day": _num(cost_tokens.get("Avg Cost/Day")),
        "avg_tokens_per_session": int(_num(cost_tokens.get("Avg Tokens/Session"))),
        "input": int(_num(cost_tokens.get("Input"))),
        "output": int(_num(cost_tokens.get("Output"))),
        "cache_read": int(_num(cost_tokens.get("Cache Read"))),
        "cache_write": int(_num(cost_tokens.get("Cache Write"))),
        "models": models,
    }


def _run_opencode_stats_cli() -> Optional[Dict[str, Any]]:
    """Run ``opencode stats --models`` and parse it.

    Returns None on any failure (CLI missing from PATH, non-zero exit,
    timeout, unparseable output) so callers can fall back to the local DB.
    """
    import shutil
    import subprocess

    binary = shutil.which("opencode")
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "stats", "--models", "100"],
            capture_output=True,
            text=True,
            timeout=20.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return _parse_opencode_stats(proc.stdout)
    except Exception:
        _log.warning("GET /api/usage/providers: opencode stats parse failed")
        return None


def _opencode_db_path() -> Path:
    """Default opencode SQLite store path (machine-local, not profile-scoped)."""
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def _opencode_db_models(conn) -> List[Dict[str, Any]]:
    """Per-model aggregates from the opencode ``session`` table (fallback path).

    The ``session.model`` column stores a JSON blob like
    ``{"id":"deepseek-v4-flash-free","providerID":"opencode"}``; the numeric
    columns carry the real cost/token totals.  Returns [] when the store lacks
    the columnar schema.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(session)").fetchall()}
    if "model" not in cols:
        return []
    try:
        rows = conn.execute(
            """
            SELECT model, COUNT(*),
                   COALESCE(SUM(cost), 0),
                   COALESCE(SUM(tokens_input), 0),
                   COALESCE(SUM(tokens_output), 0),
                   COALESCE(SUM(tokens_cache_read), 0)
            FROM session WHERE model IS NOT NULL GROUP BY model
            """
        ).fetchall()
    except Exception:
        return []
    models = []
    for row in rows:
        name = row[0]
        if isinstance(name, str):
            try:
                parsed = json.loads(name)
                if isinstance(parsed, dict) and parsed.get("id"):
                    name = parsed["id"]
            except (TypeError, ValueError):
                pass
        models.append({
            "model": name,
            "requests": int(row[1] or 0),
            "cost": float(row[2] or 0.0),
            "input": int(row[3] or 0),
            "output": int(row[4] or 0),
            "cache_read": int(row[5] or 0),
        })
    return models


def _sum_opencode_json_usage(rows) -> Dict[str, float]:
    """Aggregate token/cost fields from opencode ``data`` JSON blobs.

    Assistant message blobs carry ``cost`` and ``tokens: {input, output,
    cache: {read, write}, total}``.  We sum per provider-relevant keys; the
    ``session`` table may also carry ``cost``/``tokens_*`` columns (newer
    stores) which are preferred when present.
    """
    totals = {
        "cost": 0.0,
        "tokens_input": 0,
        "tokens_output": 0,
        "tokens_cache_read": 0,
        "tokens_cache_write": 0,
    }
    for row in rows:
        raw = (row[0] or "") if row else ""
        if not raw:
            continue
        try:
            blob = json.loads(raw)
        except (TypeError, ValueError):
            continue
        cost = blob.get("cost")
        if isinstance(cost, (int, float)):
            totals["cost"] += float(cost)
        tokens = blob.get("tokens") or {}
        input_tok = tokens.get("input")
        output_tok = tokens.get("output")
        cache = tokens.get("cache") or {}
        cache_read = cache.get("read")
        cache_write = cache.get("write")
        if isinstance(input_tok, (int, float)):
            totals["tokens_input"] += int(input_tok)
        if isinstance(output_tok, (int, float)):
            totals["tokens_output"] += int(output_tok)
        if isinstance(cache_read, (int, float)):
            totals["tokens_cache_read"] += int(cache_read)
        if isinstance(cache_write, (int, float)):
            totals["tokens_cache_write"] += int(cache_write)
    return totals


def _get_opencode_usage() -> Dict[str, Any]:
    """Aggregate opencode usage — primary source is the ``opencode stats
    --models`` CLI the user sees; the local SQLite store is the fallback.

    CLI path returns exact per-model Messages/Input/Output/Cache/Cost.  The DB
    path reads the newer columnar ``session`` aggregate columns when present,
    else sums the JSON ``data`` blobs of ``message``/``part``.
    """
    stats = _run_opencode_stats_cli()
    if stats is not None:
        return {
            "provider": "opencode",
            "spend_usd": round(stats["spend_usd"], 4),
            "sessions": stats["sessions"],
            "tokens": {
                "input": stats["input"],
                "output": stats["output"],
                "cache_read": stats["cache_read"],
            },
            "models": [
                {
                    "model": _short_model_name(m["model"]),
                    "requests": m.get("requests", 0),
                    "input": m.get("input", 0),
                    "output": m.get("output", 0),
                    "cache_read": m.get("cache_read", 0),
                    "cost": round(m.get("cost", 0.0), 6),
                }
                for m in stats["models"]
            ],
            "source": "cli",
        }
    return _get_opencode_usage_from_db()


def _get_opencode_usage_from_db() -> Dict[str, Any]:
    """Aggregate opencode usage from its local SQLite store (read-only).

    Newer stores carry per-session ``cost``/``tokens_*`` columns on the
    ``session`` table; older ones keep the numbers inside the JSON ``data``
    blobs of ``message``/``part``.  Prefer the columnar aggregate when
    available, else fall back to summing the JSON.
    """
    import sqlite3

    db_path = _opencode_db_path()
    if not db_path.exists():
        return {"provider": "opencode", "error": "db not found", "source": "local-db"}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        try:
            session_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(session)").fetchall()
            }
            if {"cost", "tokens_input", "tokens_output", "tokens_cache_read"} <= session_cols:
                row = conn.execute(
                    """
                    SELECT COUNT(*),
                           COALESCE(SUM(cost), 0),
                           COALESCE(SUM(tokens_input), 0),
                           COALESCE(SUM(tokens_output), 0),
                           COALESCE(SUM(tokens_cache_read), 0)
                    FROM session
                    """
                ).fetchone()
                sessions = int(row[0] or 0)
                cost = float(row[1] or 0.0)
                tokens_input = int(row[2] or 0)
                tokens_output = int(row[3] or 0)
                tokens_cache_read = int(row[4] or 0)
            else:
                rows = conn.execute(
                    "SELECT data FROM message WHERE data IS NOT NULL"
                ).fetchall()
                totals = _sum_opencode_json_usage(rows)
                sessions = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
                cost = totals["cost"]
                tokens_input = totals["tokens_input"]
                tokens_output = totals["tokens_output"]
                tokens_cache_read = totals["tokens_cache_read"]
            models = [
                {
                    "model": _short_model_name(m["model"]),
                    "requests": m["requests"],
                    "input": m["input"],
                    "output": m["output"],
                    "cache_read": m["cache_read"],
                    "cost": round(m["cost"], 6),
                }
                for m in _opencode_db_models(conn)
            ]
        finally:
            conn.close()
        return {
            "provider": "opencode",
            "spend_usd": round(cost, 4),
            "tokens": {
                "input": tokens_input,
                "output": tokens_output,
                "cache_read": tokens_cache_read,
            },
            "sessions": sessions,
            "models": models,
            "source": "local-db",
        }
    except Exception as exc:
        _log.warning("GET /api/usage/providers: opencode DB read failed: %s", exc)
        return {"provider": "opencode", "error": f"db read failed: {exc}", "source": "local-db"}


def _commandcode_projects_dir() -> Path:
    """Default commandcode transcript root (machine-local)."""
    return Path.home() / ".commandcode" / "projects"


def _commandcode_transcript_models(path: Path) -> Dict[str, int]:
    """Count assistant messages per model in one JSONL transcript.

    Transcript messages carry a ``model`` slug (e.g. ``deepseek/deepseek-v4-flash``)
    on assistant turns; the count feeds the requests-by-model chart.
    """
    counts: Dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if entry.get("type") != "message":
                    continue
                if (entry.get("message") or {}).get("role") != "assistant":
                    continue
                model = entry.get("model")
                if model:
                    counts[model] = counts.get(model, 0) + 1
    except OSError:
        pass
    return counts


def _get_commandcode_usage() -> Dict[str, Any]:
    """Count commandcode sessions from its local JSONL transcripts.

    commandcode exposes no usage/billing REST endpoint — its real
    plan/credits are server-side in the interactive TUI /usage panel and are
    not scrapeable.  Local transcripts are counted for sessions; assistant
    messages per model feed the consolidated requests-by-model chart.  Spend
    stays null.
    """
    projects_dir = _commandcode_projects_dir()
    sessions = 0
    per_model: Dict[str, int] = {}
    try:
        if projects_dir.is_dir():
            for path in projects_dir.rglob("*.jsonl"):
                if path.name.endswith(".checkpoints.jsonl"):
                    continue
                sessions += 1
                for model, count in _commandcode_transcript_models(path).items():
                    per_model[model] = per_model.get(model, 0) + count
    except OSError as exc:
        _log.warning("GET /api/usage/providers: commandcode scan failed: %s", exc)
        return {
            "provider": "commandcode",
            "error": f"scan failed: {exc}",
            "source": "local-transcripts",
        }
    models = [
        {"model": _short_model_name(model), "requests": count}
        for model, count in sorted(per_model.items(), key=lambda kv: -kv[1])
    ]
    return {
        "provider": "commandcode",
        "spend_usd": None,
        "sessions": sessions,
        "models": models,
        "note": "server-side only — plan/credits not exposed via API",
        "source": "local-transcripts",
    }


def _consolidate_requests_by_model(
    providers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Aggregate per-provider model request counts into one bar-chart series.

    Each row is ``{model, requests, provider}`` with the short model name as
    key.  When the same model name appears under several providers the request
    counts are summed and the provider credited is the one contributing the
    most requests (keeps the x-axis unique so Plot.barY renders clean bars).
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    for provider in providers:
        provider_name = provider.get("provider")
        for m in provider.get("models") or []:
            model = m.get("model") or "unknown"
            requests = int(m.get("requests") or 0)
            if requests <= 0:
                continue
            bucket = buckets.setdefault(
                model, {"model": model, "requests": 0, "_counts": {}}
            )
            bucket["requests"] += requests
            bucket["_counts"][provider_name] = (
                bucket["_counts"].get(provider_name, 0) + requests
            )
    result = []
    for model, bucket in buckets.items():
        provider = max(bucket["_counts"].items(), key=lambda kv: kv[1])[0]
        result.append(
            {"model": model, "requests": bucket["requests"], "provider": provider}
        )
    result.sort(key=lambda r: r["requests"], reverse=True)
    return result


def _get_usage_providers(profile: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate spend from all known providers (off the event loop)."""
    providers = [
        _get_openrouter_usage(profile),
        _get_opencode_usage(),
        _get_commandcode_usage(),
    ]
    return {
        "providers": providers,
        "requests_by_model": _consolidate_requests_by_model(providers),
    }


@router.get("/api/usage/providers")
async def get_usage_providers(profile: Optional[str] = None):
    """Real per-provider spend for the Usage dashboard tab (off the event loop)."""
    return await asyncio.to_thread(_get_usage_providers, profile)

