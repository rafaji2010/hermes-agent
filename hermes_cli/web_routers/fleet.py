"""Worker fleet dashboard routes (fork addition): ``/api/fleet/*``.

Re-homed from the pre-decomposition ``hermes_cli.web_server`` monolith during
the Sep 2026 upstream merge (web_server.py is now an upstream-owned shim;
routers live in ``web_routers/``). Auth rides the dashboard's ``/api/*``
middleware; the per-handler ``_require_token`` calls are preserved via a lazy
import so direct unit calls keep the monolith's 401 behavior without a
``web_server <-> web_routers.fleet`` import cycle.
"""

import json
import logging
import re
import tempfile
import threading
import time
import urllib.request
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from hermes_cli.web_deps import late

# Resolved through the web_server seam so tests patching web_server.load_env
# are honored (a top-level ``from config import load_env`` would bind early
# and ignore the patch). Call time only — no import cycle.
_load_env = late("load_env", "hermes_cli.web_server")
from hermes_cli.web_models import FleetRunRequest

router = APIRouter()

_log = logging.getLogger(__name__)


def _require_token(request: Request) -> None:
    """Monolith-compatible auth gate, resolved lazily to avoid an import cycle."""
    from hermes_cli.web_server import _require_token as _rt

    return _rt(request)


# ---------------------------------------------------------------------------
# Fleet — realtime worker fleet dashboard (§28/§30)
# ---------------------------------------------------------------------------

_fleet_events: list[dict] = []
_fleet_next_seq: int = 1
_fleet_seen: dict[str, str] = {}

# Fleet model catalog cache (per-provider isolation, TTL 1h, stale-serve, single-flight)
_FLEET_MODELS_TTL = 3600
_FLEET_MODELS_CACHE: dict = {}
_FLEET_MODELS_AT: float | None = None
_FLEET_MODELS_FETCH_LOCK: object | None = None
_FLEET_MODELS_FETCH_TASK: object | None = None
_FLEET_MODELS_TASK: object | None = None


def _fleet_models_redact(msg: str) -> str:
    try:
        msg = re.sub(r"Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*", "Bearer [REDACTED]", msg, flags=re.IGNORECASE)
        msg = re.sub(r"Authorization[^\n]*", "Authorization: [REDACTED]", msg, flags=re.IGNORECASE)
        msg = re.sub(r"(apiKey|api_key|COMMAND_CODE_API_KEY|OPENCODE_GO_API_KEY)\s*[:=]\s*[^\s,\}]+", r"\1=[REDACTED]", msg, flags=re.IGNORECASE)
        return msg
    except Exception:
        return "fetch failed"


def _fleet_models_load_opencode_key() -> str:
    try:
        env = _load_env()
        for k in ("OPENCODE_GO_API_KEY", "OPENC_GO_API_KEY"):
            v = (env.get(k) or "").strip()
            if v:
                return v
        return ""
    except Exception:
        return ""


def _fleet_models_load_commandcode_key() -> str:
    try:
        p = Path.home() / ".config" / "commandcode" / "env"
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                txt = ""
            for raw_line in txt.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                m = re.search(r"COMMAND_CODE_API_KEY\s*=\s*[\"\']?([^\"\'\s]+)[\"\']?", line)
                if m:
                    v = m.group(1).strip()
                    if v:
                        return v
                m2 = re.search(r"COMMANDCODE_API_KEY\s*=\s*[\"\']?([^\"\'\s]+)[\"\']?", line)
                if m2:
                    v = m2.group(1).strip()
                    if v:
                        return v
    except Exception:
        pass
    try:
        p2 = Path.home() / ".commandcode" / "auth.json"
        if p2.is_file():
            raw = json.loads(p2.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                v = str(raw.get("apiKey") or raw.get("api_key") or "").strip()
                if v:
                    return v
    except Exception:
        pass
    return ""


def _fleet_models_parse_ids(payload) -> list[str]:
    try:
        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        out: list[str] = []
        for entry in data:
            if isinstance(entry, dict):
                mid = entry.get("id")
                if isinstance(mid, str) and mid.strip():
                    out.append(mid.strip())
            elif isinstance(entry, str) and entry.strip():
                out.append(entry.strip())
        return out
    except Exception:
        return []


def _fleet_fetch_opencode_sync() -> list[str]:
    url = "https://opencode.ai/zen/go/v1/models"
    key = _fleet_models_load_opencode_key()
    headers: dict[str, str] = {"User-Agent": "Hermes-Fleet/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        payload = json.loads(text)
        return _fleet_models_parse_ids(payload)


def _fleet_fetch_commandcode_sync() -> list[str]:
    url = "https://api.commandcode.ai/provider/v1/models"
    key = _fleet_models_load_commandcode_key()
    headers: dict[str, str] = {"User-Agent": "Hermes-Fleet/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        payload = json.loads(text)
        return _fleet_models_parse_ids(payload)


def _fleet_fetch_openrouter_sync() -> list[str]:
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url, headers={"User-Agent": "Hermes-Fleet/1.0"}, method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
        text = raw.decode("utf-8", errors="replace")
        payload = json.loads(text)
        return _fleet_models_parse_ids(payload)


async def _fleet_fetch_all_providers() -> tuple[dict, dict]:
    global _FLEET_MODELS_CACHE, _FLEET_MODELS_AT
    existing = dict(_FLEET_MODELS_CACHE) if isinstance(_FLEET_MODELS_CACHE, dict) else {}
    providers: dict = {}
    errors: dict = {}
    # opencode-go
    try:
        models = await run_in_threadpool(_fleet_fetch_opencode_sync)
        if not isinstance(models, list):
            models = []
        providers["opencode-go"] = {"models": list(models)}
        _FLEET_MODELS_CACHE["opencode-go"] = {"models": list(models)}
    except Exception as exc:
        msg = _fleet_models_redact(str(exc) or exc.__class__.__name__)
        if "opencode-go" in existing:
            providers["opencode-go"] = {"models": list(existing["opencode-go"].get("models", []))}
        else:
            providers["opencode-go"] = {"models": []}
        errors["opencode-go"] = f"fetch failed: {msg[:300]}"
        try:
            _log.warning("fleet models fetch opencode-go failed: %s", _fleet_models_redact(str(exc)))
        except Exception:
            pass
    # commandcode
    try:
        models = await run_in_threadpool(_fleet_fetch_commandcode_sync)
        if not isinstance(models, list):
            models = []
        providers["commandcode"] = {"models": list(models)}
        _FLEET_MODELS_CACHE["commandcode"] = {"models": list(models)}
    except Exception as exc:
        msg = _fleet_models_redact(str(exc) or exc.__class__.__name__)
        if "commandcode" in existing:
            providers["commandcode"] = {"models": list(existing["commandcode"].get("models", []))}
        else:
            providers["commandcode"] = {"models": []}
        errors["commandcode"] = f"fetch failed: {msg[:300]}"
        try:
            _log.warning("fleet models fetch commandcode failed: %s", _fleet_models_redact(str(exc)))
        except Exception:
            pass
    # openrouter
    try:
        models = await run_in_threadpool(_fleet_fetch_openrouter_sync)
        if not isinstance(models, list):
            models = []
        providers["openrouter"] = {"models": list(models)}
        _FLEET_MODELS_CACHE["openrouter"] = {"models": list(models)}
    except Exception as exc:
        msg = _fleet_models_redact(str(exc) or exc.__class__.__name__)
        if "openrouter" in existing:
            providers["openrouter"] = {"models": list(existing["openrouter"].get("models", []))}
        else:
            providers["openrouter"] = {"models": []}
        errors["openrouter"] = f"fetch failed: {msg[:300]}"
        try:
            _log.warning("fleet models fetch openrouter failed: %s", _fleet_models_redact(str(exc)))
        except Exception:
            pass
    for prov in ("opencode-go", "commandcode", "openrouter"):
        if prov not in providers:
            if prov in existing:
                providers[prov] = {"models": list(existing[prov].get("models", []))}
                errors[prov] = errors.get(prov, "fetch failed: unknown")
            else:
                providers[prov] = {"models": []}
                errors[prov] = errors.get(prov, "fetch failed: unknown")
    _FLEET_MODELS_AT = time.time()
    return providers, errors




def _fleet_collect_events() -> None:
    global _fleet_next_seq
    try:
        from hermes_cli import worker_backend
    except Exception:
        return
    try:
        live = worker_backend.load_live_executions()
    except Exception:
        live = []
    try:
        history = worker_backend.load_execution_history(50)
    except Exception:
        history = []
    combined: dict[str, dict] = {}
    for rec in live:
        eid = rec.get("execution_id")
        if eid:
            combined[str(eid)] = rec
    for rec in history:
        eid = rec.get("execution_id")
        if eid and str(eid) not in combined:
            combined[str(eid)] = rec
    for eid, rec in combined.items():
        status = str(rec.get("status") or "")
        prev = _fleet_seen.get(eid)
        if prev is None:
            ts = rec.get("started_at") or rec.get("updated_at") or rec.get("ended_at") or time.time()
            _fleet_events.append({
                "seq": _fleet_next_seq,
                "type": "execution_created",
                "execution_id": eid,
                "worker_type": str(rec.get("worker_type") or ""),
                "task": str(rec.get("task") or ""),
                "status": status,
                "ts": float(ts) if isinstance(ts, (int, float)) else time.time(),
            })
            _fleet_next_seq += 1
            _fleet_seen[eid] = status
        elif prev != status:
            ts = rec.get("updated_at") or rec.get("ended_at") or time.time()
            _fleet_events.append({
                "seq": _fleet_next_seq,
                "type": "status_changed",
                "execution_id": eid,
                "worker_type": str(rec.get("worker_type") or ""),
                "task": str(rec.get("task") or ""),
                "status": status,
                "ts": float(ts) if isinstance(ts, (int, float)) else time.time(),
            })
            _fleet_next_seq += 1
            _fleet_seen[eid] = status
    if len(_fleet_events) > 500:
        del _fleet_events[: len(_fleet_events) - 500]


@router.get("/api/fleet/status")
async def fleet_status(request: Request):
    _require_token(request)
    try:
        from hermes_cli import worker_backend
        from hermes_cli import workers as _workers
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        live = worker_backend.load_live_executions()
    except Exception:
        live = []
    try:
        history = worker_backend.load_execution_history(20)
    except Exception:
        history = []
    try:
        workers_list = _workers.load_all_workers()
        workers_out = [
            {"name": w.get("name"), "version": w.get("version"), "capabilities": list(w.get("capabilities") or [])}
            for w in workers_list
        ]
    except Exception:
        workers_out = []
    # Include worker_models for picker highlight
    try:
        from hermes_cli import worker_backend as _wb2
        worker_models = _wb2.load_worker_models()
    except Exception:
        worker_models = {}
    return {"live": live, "history": history, "workers": workers_out, "timestamp": time.time(), "worker_models": worker_models}


@router.get("/api/fleet/events")
async def fleet_events(request: Request, cursor: int = Query(0, ge=0)):
    _require_token(request)
    _fleet_collect_events()
    try:
        c = int(cursor)
    except Exception:
        c = 0
    events = [e for e in _fleet_events if int(e.get("seq", 0)) > c]
    next_cursor = _fleet_events[-1]["seq"] if _fleet_events else c
    # If cursor beyond end, next_cursor stays at latest
    if c >= next_cursor and _fleet_events:
        next_cursor = _fleet_events[-1]["seq"]
    elif not _fleet_events:
        next_cursor = c
    return {"events": events, "next_cursor": next_cursor}


@router.post("/api/fleet/run")
async def fleet_run(request: Request, body: FleetRunRequest):
    _require_token(request)
    task = (body.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="task must not be empty")
    from hermes_cli.config import get_hermes_home as _get_home
    from hermes_cli import worker_backend as _wb
    hermes_home = _get_home()
    worker_type = (body.worker or "").strip()
    if worker_type:
        # Validate known worker name
        custom = {}
        try:
            from hermes_cli import workers as _workers
            custom = _workers._read_custom_workers(hermes_home)
        except Exception:
            custom = {}
        if worker_type not in _wb.BACKENDS and worker_type not in custom:
            # Allow any known builtin name even if not installed; reject truly unknown
            from hermes_cli.workers import BUILTIN_WORKERS
            if worker_type not in BUILTIN_WORKERS and worker_type not in custom:
                raise HTTPException(status_code=400, detail=f"unknown worker '{worker_type}'")
    else:
        try:
            evidence, _ = _wb._load_routing_evidence(hermes_home)
            worker_type = _wb.route_task(task, None, evidence, hermes_home)
        except Exception:
            try:
                from hermes_cli import workers as _workers
                avail = _workers.load_all_workers(hermes_home)
                if avail:
                    worker_type = str(avail[0].get("name") or "codex")
                else:
                    worker_type = "codex"
            except Exception:
                worker_type = "codex"
    # Temp workspace for isolated execution
    workspace = tempfile.mkdtemp(prefix="hermes-fleet-")
    try:
        spec = _wb.WorkerSpec(worker_type=worker_type, task=task, workspace=workspace, timeout=600)
        backend = _wb.get_backend(worker_type)
        execution_id = backend.start(spec)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Completion watcher: the backend instance is per-request, so poll its
    # process until terminal so the live registry transitions DONE/FAILED
    # even after this request returns (the dashboard shows real states).
    import threading

    def _watch() -> None:
        try:
            while True:
                backend._update_status(execution_id)
                exec_ = backend._executions.get(execution_id)
                if exec_ is None or exec_.status in _wb._TERMINAL_STATUSES:
                    break
                time.sleep(1.0)
        except Exception:
            pass

    threading.Thread(target=_watch, daemon=True).start()
    # Prime the event feed so the new execution appears on next poll
    try:
        _fleet_collect_events()
    except Exception:
        pass
    return {"execution_id": execution_id, "worker_type": worker_type}



# ---------------------------------------------------------------------------
# Fleet model picker (§ fleet model per-harness) — catalog + persistence
# ---------------------------------------------------------------------------

@router.get("/api/fleet/models")
async def fleet_models(request: Request, refresh: int = Query(0, ge=0, le=1)):
    _require_token(request)
    global _FLEET_MODELS_TASK, _FLEET_MODELS_CACHE, _FLEET_MODELS_AT
    # TTL check: serve cached if within 1h (bypass if ?refresh=1)
    now = time.time()
    is_refresh = bool(refresh) or request.query_params.get("refresh") == "1"
    if not is_refresh and _FLEET_MODELS_AT is not None and _FLEET_MODELS_CACHE and (now - _FLEET_MODELS_AT) < _FLEET_MODELS_TTL:
        # Stale-cache guard: if cached providers have empty models arrays (403-era),
        # treat as cache miss even within TTL and force a fresh fetch.
        has_empty = False
        try:
            for prov in ("opencode-go", "commandcode", "openrouter"):
                entry = _FLEET_MODELS_CACHE.get(prov)
                if not isinstance(entry, dict):
                    has_empty = True
                    break
                models = entry.get("models")
                if not isinstance(models, list) or len(models) == 0:
                    has_empty = True
                    break
        except Exception:
            has_empty = False
        if not has_empty:
            # Remaining TTL for header
            remaining = max(0, int(_FLEET_MODELS_TTL - (now - _FLEET_MODELS_AT)))
            # Re-compose providers shape from cache
            providers = {k: {"models": list(v.get("models", []))} for k, v in _FLEET_MODELS_CACHE.items()}
            # Ensure all providers present
            for prov in ("opencode-go", "commandcode", "openrouter"):
                if prov not in providers:
                    providers[prov] = {"models": []}
            headers = {"Cache-Control": f"public, max-age={remaining}"}
            return JSONResponse({"providers": providers}, headers=headers)
    # Single-flight: if fetch in-flight, await it
    if _FLEET_MODELS_TASK is not None:
        try:
            # It is an asyncio Task
            import asyncio as _asyncio
            if isinstance(_FLEET_MODELS_TASK, _asyncio.Task):
                providers, errors = await _FLEET_MODELS_TASK
                # Serve result of ongoing fetch
                body: dict = {"providers": providers}
                if errors:
                    body["errors"] = errors
                headers = {"Cache-Control": "public, max-age=3600" if not errors else "public, max-age=3600, must-revalidate"}
                return JSONResponse(body, headers=headers)
        except Exception:
            pass
        # Fall through to new fetch if awaiting failed

    # Start new fetch
    import asyncio as _asyncio
    task = _asyncio.create_task(_fleet_fetch_all_providers())
    _FLEET_MODELS_TASK = task
    try:
        providers, errors = await task
    finally:
        _FLEET_MODELS_TASK = None
    body: dict = {"providers": providers}
    if errors:
        body["errors"] = errors
        headers = {"Cache-Control": "public, max-age=3600, must-revalidate"}
    else:
        headers = {"Cache-Control": "public, max-age=3600"}
    return JSONResponse(body, headers=headers)


@router.get("/api/fleet/models/{worker}")
async def fleet_models_get_worker(request: Request, worker: str):
    _require_token(request)
    w = (worker or "").strip()
    if not w:
        raise HTTPException(status_code=400, detail="worker must not be empty")
    # Validate worker against builtins + custom
    try:
        from hermes_cli import worker_backend as _wb
        from hermes_cli.workers import BUILTIN_WORKERS as _BUILTIN
        from hermes_cli.workers import _read_custom_workers as _read_custom
        from hermes_cli.config import get_hermes_home as _get_home
        home = _get_home()
        custom = {}
        try:
            custom = _read_custom(home)
        except Exception:
            custom = {}
        if w not in _BUILTIN and w not in custom and w not in _wb.BACKENDS:
            # allow any BUILTIN name even if not in BACKENDS? Already checked _BUILTIN, so this is truly unknown
            raise HTTPException(status_code=404, detail=f"unknown worker '{w}'")
    except HTTPException:
        raise
    except Exception:
        pass
    try:
        from hermes_cli import worker_backend as _wb2
        data = _wb2.load_worker_models()
        entry = data.get(w)
        if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
            return {"worker": w, "provider": str(entry.get("provider")), "model": str(entry.get("model"))}
        return {"worker": w, "provider": "", "model": ""}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/api/fleet/models/{worker}")
async def fleet_models_set_worker(request: Request, worker: str):
    _require_token(request)
    w = (worker or "").strip()
    if not w:
        raise HTTPException(status_code=400, detail="worker must not be empty")
    # Parse body
    try:
        body = await request.json()
    except Exception:
        body = {}
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    # Validate worker allowlist
    try:
        from hermes_cli import worker_backend as _wb
        from hermes_cli.workers import BUILTIN_WORKERS as _BUILTIN
        from hermes_cli.workers import _read_custom_workers as _read_custom
        from hermes_cli.config import get_hermes_home as _get_home
        home = _get_home()
        custom = {}
        try:
            custom = _read_custom(home)
        except Exception:
            custom = {}
        allowed_workers = set(_BUILTIN.keys()) | set(custom.keys()) | set(_wb.BACKENDS.keys())
        # Also allow any BUILTIN even if not in BACKENDS
        if w not in allowed_workers:
            raise HTTPException(status_code=404, detail=f"unknown worker '{w}'")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Handle clear: if both empty, delete entry
    if not provider and not model:
        # Clear operation
        try:
            from hermes_cli import worker_backend as _wb
            _wb.clear_worker_model(w)
            return {"worker": w, "provider": "", "model": ""}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
    # Validate provider
    allowed_providers = {"opencode-go", "commandcode", "openrouter"}
    if provider not in allowed_providers:
        raise HTTPException(status_code=400, detail=f"invalid provider '{provider}'")
    # Validate model regex
    import re as _re
    if not _re.match(r"^[A-Za-z0-9._/:-]{1,128}$", model):
        raise HTTPException(status_code=400, detail="invalid model id")
    # Model ID validation against catalog if populated for that provider
    try:
        # Look at cached catalog
        cached = _FLEET_MODELS_CACHE.get(provider) if isinstance(_FLEET_MODELS_CACHE, dict) else None
        models_list = []
        if isinstance(cached, dict):
            models_list = cached.get("models") or []
        if models_list:
            # Catalog populated for this provider → strict check
            if model not in models_list:
                raise HTTPException(status_code=400, detail=f"unknown model '{model}' for provider '{provider}'")
        else:
            # Catalog empty/failed for this provider → allow (warn in log)
            try:
                _log.warning("fleet models: allowing set for %s/%s without catalog validation (catalog empty)", provider, model)
            except Exception:
                pass
    except HTTPException:
        raise
    except Exception:
        pass
    # Persist atomically
    try:
        from hermes_cli import worker_backend as _wb
        entry = _wb.set_worker_model(w, provider, model)
        return {"worker": w, "provider": entry["provider"], "model": entry["model"]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/api/fleet/models/{worker}")
async def fleet_models_delete_worker(request: Request, worker: str):
    _require_token(request)
    w = (worker or "").strip()
    if not w:
        raise HTTPException(status_code=400, detail="worker must not be empty")
    try:
        from hermes_cli.workers import BUILTIN_WORKERS as _BUILTIN
        from hermes_cli.workers import _read_custom_workers as _read_custom
        from hermes_cli import worker_backend as _wb
        from hermes_cli.config import get_hermes_home as _get_home
        home = _get_home()
        custom = {}
        try:
            custom = _read_custom(home)
        except Exception:
            custom = {}
        allowed_workers = set(_BUILTIN.keys()) | set(custom.keys()) | set(_wb.BACKENDS.keys())
        if w not in allowed_workers:
            raise HTTPException(status_code=404, detail=f"unknown worker '{w}'")
    except HTTPException:
        raise
    except Exception:
        pass
    try:
        from hermes_cli import worker_backend as _wb
        _wb.clear_worker_model(w)
        return {"worker": w, "provider": "", "model": ""}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
