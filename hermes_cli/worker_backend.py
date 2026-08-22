"""``hermes workers run`` — AgentExecutionBackend + task→worker routing.

Builds the execution layer on top of the ``hermes_cli/workers.py`` capability
registry: an ``AgentExecutionBackend`` abstraction (architecture doc §18) for
launching and supervising the external coding-agent harnesses (pi, codex,
opencode, commandcode, dsh), a ``WorkerSpec`` / ``WorkerExecution`` contract
(§19), the execution lifecycle (§20), compact context handoff (§22), retry /
switch-on-failure handling (§29), and execution persistence + resume/recovery
(§30). The persisted execution history
(``<HERMES_HOME>/worker_executions.json``) and the mirrored live registry
(``<HERMES_HOME>/worker_live.json``) back the fleet observability view (§28)
— ``hermes workers status`` shows what is running/blocked live and what has
completed/failed in the last N runs.

Routing (§13) is evidence-driven: the task text is scanned for capability
hints ("architecture/reasoning" → pi, "implement/code" → codex/opencode,
"test" → opencode, "review" → commandcode, "long-horizon/deep" → dsh), the
hints are intersected with the installed-fleet capability map, and the best
installed worker wins. When benchmark evidence exists
(``<HERMES_HOME>/benchmark_results.json``, written by ``hermes workers
benchmark``), the router instead scores each installed worker by its stored
pass/fail record for the task's inferred category (+2 per PASS, −1 per FAIL,
+0.5 per PASS elsewhere, +1 for a capability match) and picks the highest
score — falling back to the capability hints only when no worker has any
evidence. This backend complements Hermes' internal ``delegate_task``
subagents — it drives *external* harnesses as separate processes, it does not
replace the built-in delegation tool.

Design notes:

- Stdlib only (subprocess, dataclasses, time, uuid, select) — no new deps.
- Spawns via ``subprocess.Popen([...])`` with an argument list, never
  ``shell=True``; task text is passed as an ARG. Workspaces are isolated by
  default (``WorkerSpec.environment_policy == "isolated"``).
- ``start()`` is non-blocking; ``status()``/``read()``/``wait()`` poll the
  process handle stored in a per-backend registry keyed by ``execution_id``,
  mirroring ``terminal(background=true)`` semantics.
"""

from __future__ import annotations

import json
import logging
import os
import re
import select
import subprocess
import sys
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

from hermes_constants import get_hermes_home

from hermes_cli import workers

# ---------------------------------------------------------------------------
# Lifecycle (§20)
# ---------------------------------------------------------------------------

PLANNED = "PLANNED"
DISPATCHING = "DISPATCHING"
RUNNING = "RUNNING"
BLOCKED = "BLOCKED"
DONE = "DONE"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

#: Statuses that end an execution (wait() stops polling on these).
_TERMINAL_STATUSES = frozenset({DONE, FAILED, CANCELLED})

#: Default availability order when no routing rule matches (§13).
DEFAULT_ORDER = ("codex", "opencode", "pi", "commandcode", "dsh")


# ---------------------------------------------------------------------------
# Data contract (§19)
# ---------------------------------------------------------------------------

@dataclass
class WorkerSpec:
    """Everything needed to launch one external-worker execution.

    ``context`` is the compact context handoff (§22) — a distilled summary of
    the parent session's relevant state, never the full memory store.
    ``constraints`` carries per-execution overrides such as ``model`` and
    ``provider`` for the codex/opencode harnesses.
    """

    worker_type: str  # pi | codex | opencode | commandcode | dsh | custom
    task: str
    workspace: str = ""
    context: str = ""
    constraints: dict = field(default_factory=dict)
    acceptance_criteria: str = ""
    timeout: int = 600
    parent_task_id: str = ""
    environment_policy: str = "isolated"


@dataclass
class WorkerExecution:
    """The observable state of one execution, keyed by ``execution_id``.

    ``task`` is the task text the execution ran — carried so the persisted
    history (§30) and the live fleet view (§28) can answer "what is it
    doing?" without re-deriving it.
    """

    execution_id: str
    worker_type: str
    task: str = ""
    status: str = PLANNED
    started_at: float = 0.0
    updated_at: float = 0.0
    result: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Backend abstraction (§18)
# ---------------------------------------------------------------------------

class AgentExecutionBackend(ABC):
    """Supervise external worker executions.

    ``start`` returns an ``execution_id`` immediately; everything else is
    polled against the returned id (the process handle lives in a registry
    inside the backend). Subclasses implement the worker-specific launch.
    """

    @abstractmethod
    def start(self, request: WorkerSpec) -> str:
        """Launch an execution for ``request``; return its ``execution_id``."""

    @abstractmethod
    def send(self, execution_id: str, message: str) -> None:
        """Send a line to a running execution (interactive workers)."""

    @abstractmethod
    def read(self, execution_id: str, options: dict | None = None) -> str:
        """Read output accumulated for an execution (optionally incremental)."""

    @abstractmethod
    def wait(
        self,
        execution_id: str,
        condition=None,
        timeout: float | None = None,
        status_callback=None,
    ) -> dict:
        """Block until ``condition`` holds; returns the final execution dict."""

    @abstractmethod
    def status(self, execution_id: str) -> dict:
        """Return the current execution state as a dict."""

    @abstractmethod
    def stop(self, execution_id: str) -> None:
        """Stop a running execution (terminates the child process)."""

    @abstractmethod
    def resume(self, execution_id: str) -> None:
        """Resume a blocked execution."""


# ---------------------------------------------------------------------------
# Subprocess backend (shared machinery)
# ---------------------------------------------------------------------------

class SubprocessBackend(AgentExecutionBackend):
    """Generic external-worker backend running the harness as a subprocess.

    One process per ``execution_id``; stdout/stderr are merged into a single
    pipe that is drained non-blockingly while the process runs and fully at
    exit. The per-worker launch command is built by ``_build_command``, which
    per-type subclasses override.
    """

    worker_type = "custom"

    _POLL_INTERVAL = 0.2

    def __init__(self, worker_type: str | None = None):
        self.worker_type = worker_type or self.worker_type
        self._processes: dict[str, subprocess.Popen] = {}
        self._executions: dict[str, WorkerExecution] = {}
        self._output_buffers: dict[str, list[bytes]] = {}
        self._read_positions: dict[str, int] = {}
        self._deadlines: dict[str, float] = {}

    # -- command building ----------------------------------------------------

    def _build_command(self, request: WorkerSpec) -> list[str]:
        """Launch command for this worker type (custom → bare binary name)."""
        return [self.worker_type, request.task]

    # -- launch ---------------------------------------------------------------

    def start(self, request: WorkerSpec, command: list[str] | None = None) -> str:
        execution_id = uuid.uuid4().hex
        now = time.time()
        execution = WorkerExecution(
            execution_id=execution_id,
            worker_type=self.worker_type,
            task=request.task,
            status=PLANNED,
            started_at=now,
            updated_at=now,
        )
        self._executions[execution_id] = execution
        self._output_buffers[execution_id] = []
        self._read_positions[execution_id] = 0
        if request.timeout:
            self._deadlines[execution_id] = now + request.timeout

        self._transition(execution_id, DISPATCHING)
        if command is None:
            command = self._build_command(request)
        cwd = request.workspace or None
        try:
            proc = self._spawn(command, cwd)
        except OSError as exc:
            execution = self._executions[execution_id]
            execution.status = FAILED
            execution.error = f"spawn failed: {exc}"
            execution.updated_at = time.time()
            self._persist_execution_state(execution_id)
            return execution_id
        self._processes[execution_id] = proc
        self._transition(execution_id, RUNNING)
        return execution_id

    def _spawn(self, command: list[str], cwd: str | None) -> subprocess.Popen:
        """Spawn the harness with an argument list (never ``shell=True``)."""
        return subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )

    # -- process supervision ---------------------------------------------------

    def _transition(self, execution_id: str, status: str) -> None:
        execution = self._executions.get(execution_id)
        if execution is None:
            return
        execution.status = status
        execution.updated_at = time.time()
        self._persist_execution_state(execution_id)

    def _persist_execution_state(self, execution_id: str) -> None:
        """Write-through the execution's persisted state (§30).

        Non-terminal executions are merged into the live registry so a
        separate ``hermes workers status`` process can show them (§28); a
        terminal execution is dropped from the live registry and appended to
        the persisted execution history. The live registry is read-merged so
        concurrent executions from other processes are preserved. Best-effort
        — an unwritable store never fails the execution.
        """
        execution = self._executions.get(execution_id)
        if execution is None:
            return
        try:
            live = _read_live_map()
            if execution.status in _TERMINAL_STATUSES:
                live.pop(execution.execution_id, None)
                append_execution_history(execution)
            else:
                record = _live_record(execution)
                proc = self._processes.get(execution_id)
                if proc is not None and getattr(proc, "pid", None):
                    record["pid"] = proc.pid
                live[execution.execution_id] = record
            write_live_executions(live)
        except OSError:
            pass

    def _drain(self, execution_id: str) -> None:
        proc = self._processes.get(execution_id)
        if proc is None:
            return
        if proc.poll() is not None:
            # Exited: read everything remaining (EOF, so no blocking).
            try:
                rest = proc.stdout.read()
            except (AttributeError, OSError, ValueError):
                rest = b""
            self._append_output(execution_id, rest)
            return
        # Running: best-effort non-blocking drain on real pipes (POSIX).
        try:
            fd = proc.stdout.fileno()
        except (AttributeError, OSError, ValueError):
            return
        try:
            ready, _, _ = select.select([fd], [], [], 0)
        except (OSError, ValueError):
            return
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                chunk = b""
            self._append_output(execution_id, chunk)

    def _append_output(self, execution_id: str, chunk) -> None:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8", errors="replace")
        if chunk:
            self._output_buffers.setdefault(execution_id, []).append(chunk)

    def _all_output(self, execution_id: str) -> str:
        chunks = self._output_buffers.get(execution_id, [])
        raw = b"".join(chunks) if chunks else b""
        return raw.decode("utf-8", errors="replace")

    def _kill(self, execution_id: str) -> None:
        proc = self._processes.get(execution_id)
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except (subprocess.SubprocessError, OSError):
            try:
                proc.kill()
            except OSError:
                pass

    def _cleanup(self, execution_id: str) -> None:
        """Drop the process handle once the execution has settled.

        The execution record and output buffer are retained so ``read()`` and
        ``status()`` still work after completion.
        """
        self._processes.pop(execution_id, None)

    def _update_status(self, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        proc = self._processes.get(execution_id)
        if execution is None or proc is None:
            return
        if execution.status != RUNNING:
            return
        rc = proc.poll()
        if rc is not None:
            self._drain(execution_id)
            if rc == 0:
                execution.status = DONE
                execution.result = self._all_output(execution_id)
            else:
                execution.status = FAILED
                execution.error = f"exit code {rc}"
                execution.result = self._all_output(execution_id)
            execution.updated_at = time.time()
            self._persist_execution_state(execution_id)
            self._cleanup(execution_id)
            return
        self._drain(execution_id)
        deadline = self._deadlines.get(execution_id)
        if deadline is not None and time.time() > deadline:
            self._kill(execution_id)
            execution.status = FAILED
            execution.error = "timeout"
            execution.result = self._all_output(execution_id)
            execution.updated_at = time.time()
            self._persist_execution_state(execution_id)
            self._cleanup(execution_id)

    # -- backend interface ------------------------------------------------------

    def send(self, execution_id: str, message: str) -> None:
        proc = self._processes.get(execution_id)
        if proc is None or proc.poll() is not None:
            return
        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            return
        try:
            stdin.write(message.encode("utf-8") if not isinstance(message, bytes) else message)
            stdin.write(b"\n")
            stdin.flush()
        except (OSError, ValueError):
            pass

    def read(self, execution_id: str, options: dict | None = None) -> str:
        self._update_status(execution_id)
        all_out = self._all_output(execution_id)
        position = self._read_positions.get(execution_id, 0)
        options = options or {}
        if options.get("mode") == "incremental":
            chunk = all_out[position:]
            self._read_positions[execution_id] = len(all_out)
            return chunk
        self._read_positions[execution_id] = len(all_out)
        return all_out

    def wait(
        self,
        execution_id: str,
        condition=None,
        timeout: float | None = None,
        status_callback=None,
    ) -> dict:
        if execution_id not in self._executions:
            raise KeyError(f"unknown execution '{execution_id}'")
        predicate = _terminal_predicate(condition)
        deadline: float | None = None
        if timeout is not None:
            deadline = time.time() + timeout
        elif self._deadlines.get(execution_id):
            deadline = self._deadlines[execution_id]
        while True:
            self._update_status(execution_id)
            execution = self._executions[execution_id]
            if status_callback is not None:
                status_callback(execution.status)
            if predicate(execution.status):
                return _execution_dict(execution)
            if deadline is not None and time.time() >= deadline:
                self._timeout_execution(execution_id)
                return _execution_dict(self._executions[execution_id])
            time.sleep(self._POLL_INTERVAL)

    def _timeout_execution(self, execution_id: str) -> None:
        execution = self._executions.get(execution_id)
        if execution is None or execution.status in _TERMINAL_STATUSES:
            return
        self._kill(execution_id)
        self._drain(execution_id)
        execution.status = FAILED
        execution.error = "timeout"
        execution.result = self._all_output(execution_id)
        execution.updated_at = time.time()
        self._persist_execution_state(execution_id)
        self._cleanup(execution_id)

    def status(self, execution_id: str) -> dict:
        if execution_id not in self._executions:
            raise KeyError(f"unknown execution '{execution_id}'")
        self._update_status(execution_id)
        return _execution_dict(self._executions[execution_id])

    def stop(self, execution_id: str) -> None:
        if execution_id not in self._executions:
            raise KeyError(f"unknown execution '{execution_id}'")
        self._kill(execution_id)
        self._drain(execution_id)
        execution = self._executions[execution_id]
        if execution.status not in _TERMINAL_STATUSES:
            execution.status = CANCELLED
            execution.updated_at = time.time()
            self._persist_execution_state(execution_id)
        self._cleanup(execution_id)

    def resume(self, execution_id: str) -> None:
        if execution_id not in self._executions:
            raise KeyError(f"unknown execution '{execution_id}'")
        execution = self._executions[execution_id]
        if execution.status == BLOCKED:
            self._transition(execution_id, RUNNING)


def _terminal_predicate(condition):
    """Normalize a ``wait()`` condition into a status predicate."""
    if condition is None:
        return lambda st: st in _TERMINAL_STATUSES
    if isinstance(condition, str):
        return lambda st, target=condition: st == target
    if isinstance(condition, (set, frozenset, tuple, list)):
        targets = frozenset(condition)
        return lambda st: st in targets
    if callable(condition):
        return condition
    return lambda st: False


def _execution_dict(execution: WorkerExecution) -> dict:
    return asdict(execution)


# ---------------------------------------------------------------------------
# Execution history + live registry (§30 / §28)
# ---------------------------------------------------------------------------
#
# Every execution's final state is persisted to
# ``<HERMES_HOME>/worker_executions.json`` (append-only, capped at
# HISTORY_CAP entries with the oldest dropped) so `hermes workers status
# --all` can answer "what complete? what failed?" across restarts. In-flight
# executions are mirrored to ``<HERMES_HOME>/worker_live.json`` so a separate
# `hermes workers status` process can answer "which workers are running? what
# task? which blocked?" without sharing the in-memory process registry.

#: Execution-history store filename inside HERMES_HOME (§30).
EXECUTION_HISTORY_STORE = "worker_executions.json"

#: Live-registry store filename inside HERMES_HOME (§28).
LIVE_EXECUTIONS_STORE = "worker_live.json"

#: Max history entries kept (§30) — the oldest are dropped first.
HISTORY_CAP = 100

#: Length of the result text tail persisted per execution (§30).
RESULT_TAIL_CHARS = 200

#: Harnesses whose CLI advertises a resume/continue flag (§30). dsh and
#: custom workers have no resume flag — ``resume`` re-issues those tasks as
#: a fresh run instead and reports the new execution id.
_RESUME_FLAGS: dict[str, str] = {
    "pi": "--resume",
    "codex": "--continue",
    "opencode": "--continue",
    "commandcode": "--continue",
}


def execution_history_path(hermes_home: Path | None = None) -> Path:
    """Path to the persisted execution history (profile-aware, §30)."""
    return (hermes_home or get_hermes_home()) / EXECUTION_HISTORY_STORE


# Persistence for per-harness model picker (profile-scoped, atomic).
#: Filename for worker_models.json inside HERMES_HOME.
WORKER_MODELS_FILE = "worker_models.json"

#: Allowed provider slugs for worker model picker.
ALLOWED_MODEL_PROVIDERS = frozenset({"opencode-go", "commandcode", "openrouter"})

#: Regex for model IDs (prompt § Validation).
_MODEL_RE = re.compile(r"^[A-Za-z0-9._/:-]{1,128}$")

#: Allowed harness/worker names that may have a pinned model.
#  Re-export BUILTIN_WORKERS keys for validation; custom workers handled via workers._read_custom_workers.
_ALLOWED_WORKERS = frozenset(workers.BUILTIN_WORKERS.keys())


def worker_models_path(hermes_home: Path | None = None) -> Path:
    """Path to worker_models.json (profile-aware)."""
    return (hermes_home or get_hermes_home()) / WORKER_MODELS_FILE


def _read_worker_models(hermes_home: Path | None = None) -> dict:
    """Load worker_models.json → {worker: {provider, model}}.

    On corrupted JSON, backs up to worker_models.json.corrupt.<ts> and returns {}.
    """
    path = worker_models_path(hermes_home)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Backup corrupt file
        try:
            ts = time.strftime("%Y%m%d-%H%M%S")
            backup = path.with_name(f"{path.name}.corrupt.{ts}")
            # Avoid clobber
            if not backup.exists():
                # Use read_bytes to preserve content even if partially written
                try:
                    data = path.read_bytes()
                    backup.write_bytes(data)
                except OSError:
                    pass
        except Exception:
            pass
        _log_worker_models_warning(f"worker_models.json corrupted, treating as empty: {exc}")
        return {}
    if not isinstance(raw, dict):
        return {}
    # Keep only dict entries with required keys sanitized
    out: dict = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        kk = k.strip()
        if not kk:
            continue
        if not isinstance(v, dict):
            continue
        provider = str(v.get("provider") or "").strip()
        model = str(v.get("model") or "").strip()
        # Basic sanity: provider in allowlist or empty, model regex; keep entry even if invalid per file read
        # so callers can surface it; but filter to only valid shapes here for safety.
        # Keep raw entry; validation is done on write/path.
        out[kk] = {"provider": provider, "model": model}
    return out


def _log_worker_models_warning(msg: str) -> None:
    try:
        import logging

        logging.getLogger(__name__).warning(msg)
    except Exception:
        pass


def get_worker_model(worker: str, hermes_home: Path | None = None) -> dict | None:
    """Return {provider, model} for a worker or None when not pinned."""
    data = _read_worker_models(hermes_home)
    entry = data.get(worker)
    if isinstance(entry, dict) and entry.get("provider") and entry.get("model"):
        return {"provider": str(entry["provider"]), "model": str(entry["model"])}
    return None


def set_worker_model(
    worker: str,
    provider: str,
    model: str,
    hermes_home: Path | None = None,
) -> dict:
    """Persist a worker model pin atomically (tmp+rename, 0o600). Returns entry.

    Also propagates the pick into the harness's native config
    (~/.opencode/opencode.jsonc, ~/.commandcode/config.json,
    ~/.codex/config.toml, ~/.dsh/settings.yaml) so direct CLI runs use the
    same model as fleet dispatch. Best-effort: a sync failure never fails
    the pin itself.
    """
    home = hermes_home or get_hermes_home()
    path = worker_models_path(home)
    # Read current (handles corrupt backup)
    current = _read_worker_models(home)
    current[worker] = {"provider": provider, "model": model}
    _write_worker_models_atomic(current, home)
    # Propagate to native harness configs (Fleet picker → everywhere).
    try:
        from hermes_cli.harness_model_sync import sync_native_configs

        report = sync_native_configs(worker, provider, model, home)
        for err in report.get("errors", ()):
            logging.getLogger(__name__).warning(
                "harness model sync error (%s/%s@%s): %s", worker, provider, model, err
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("harness model sync failed: %s", exc)
    return {"provider": provider, "model": model}


def clear_worker_model(worker: str, hermes_home: Path | None = None) -> bool:
    """Remove a worker entry. Returns True if existed."""
    home = hermes_home or get_hermes_home()
    current = _read_worker_models(home)
    if worker not in current:
        return False
    current.pop(worker, None)
    _write_worker_models_atomic(current, home)
    return True


def _write_worker_models_atomic(data: dict, hermes_home: Path | None = None) -> Path:
    """Atomic write of worker_models.json (tmp+rename, 0o600)."""
    home = hermes_home or get_hermes_home()
    path = worker_models_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp in same dir for atomic rename
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return path


def load_worker_models(hermes_home: Path | None = None) -> dict:
    """Public read helper for tests/routes."""
    return _read_worker_models(hermes_home)


def live_executions_path(hermes_home: Path | None = None) -> Path:
    """Path to the live-executions registry (profile-aware, §28)."""
    return (hermes_home or get_hermes_home()) / LIVE_EXECUTIONS_STORE


def _read_history(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def _history_record(execution: WorkerExecution) -> dict:
    """Serialize an execution into its persisted-history shape (§30)."""
    return {
        "execution_id": execution.execution_id,
        "worker_type": execution.worker_type,
        "task": execution.task,
        "status": execution.status,
        "started_at": execution.started_at,
        "ended_at": execution.updated_at,
        "result_tail": (execution.result or "")[-RESULT_TAIL_CHARS:],
        "error": execution.error,
    }


def _append_history_record(record: dict, hermes_home: Path | None = None) -> Path:
    path = execution_history_path(hermes_home)
    records = _read_history(path)
    records.append(record)
    if len(records) > HISTORY_CAP:
        records = records[-HISTORY_CAP:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def append_execution_history(
    execution: WorkerExecution, hermes_home: Path | None = None
) -> Path:
    """Append an execution's final state to the persisted history (§30).

    Newest entries append at the end; entries beyond HISTORY_CAP are dropped
    (oldest first). Returns the path written.
    """
    return _append_history_record(_history_record(execution), hermes_home)


def load_execution_history(n: int = 10, hermes_home: Path | None = None) -> list[dict]:
    """Return the most recent ``n`` terminal executions, newest first (§30).

    Each record carries ``execution_id``, ``worker_type``, ``task``,
    ``status``, ``started_at``, ``ended_at``, ``result_tail`` and ``error``.
    Returns [] when no history file exists yet (backward compatible).
    """
    records = _read_history(execution_history_path(hermes_home))
    recent = records[-n:] if n and n > 0 else records
    return list(reversed(recent))


def _live_record(execution: WorkerExecution) -> dict:
    """Serialize an execution into its live-registry shape (§28)."""
    return {
        "execution_id": execution.execution_id,
        "worker_type": execution.worker_type,
        "task": execution.task,
        "status": execution.status,
        "started_at": execution.started_at,
        "updated_at": execution.updated_at,
    }


def _read_live_map(hermes_home: Path | None = None) -> dict[str, dict]:
    try:
        raw = json.loads(live_executions_path(hermes_home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if isinstance(value, dict)}


def write_live_executions(
    executions: dict[str, dict], hermes_home: Path | None = None
) -> Path:
    """Persist the live registry (``{execution_id: record}``, §28).

    Returns the path written. Missing/legacy stores are simply overwritten.
    """
    path = live_executions_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(executions, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def load_live_executions(hermes_home: Path | None = None) -> list[dict]:
    """Currently in-flight executions, newest started first (§28).

    Records carry ``execution_id``, ``worker_type``, ``task``, ``status``,
    ``started_at`` and ``updated_at``. Returns [] when nothing is running.
    """
    records = list(_read_live_map(hermes_home).values())

    def _started_at(record: dict) -> float:
        value = record.get("started_at")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    records.sort(key=_started_at, reverse=True)
    return records


# ---------------------------------------------------------------------------
# Per-worker backends — the launch commands
# ---------------------------------------------------------------------------

_PI_FLAG_SENTINEL = object()
_PI_FLAG: str | None | object = _PI_FLAG_SENTINEL


def detect_pi_flag(timeout: int = 5) -> str | None:
    """Detect pi's headless print-mode flag from ``pi --help``.

    Returns ``--print`` / ``-p`` when present (the cached result), or None
    when pi doesn't advertise a print flag (caller falls back to interactive
    mode governed by the spec timeout). Never blocks the caller — the
    ``--help`` probe has its own short timeout.
    """
    global _PI_FLAG
    if _PI_FLAG is not _PI_FLAG_SENTINEL:
        return _PI_FLAG
    flag: str | None = None
    try:
        result = subprocess.run(
            ["pi", "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        text = f"{result.stdout or ''}\n{result.stderr or ''}"
    except (OSError, subprocess.SubprocessError):
        text = ""
    if "--print" in text:
        flag = "--print"
    elif re.search(r"(^|[^-\w])-p([^p\w]|$)", text):
        flag = "-p"
    _PI_FLAG = flag
    return flag


def reset_pi_flag_cache() -> None:
    """Clear the cached ``pi --help`` probe (used by tests)."""
    global _PI_FLAG
    _PI_FLAG = _PI_FLAG_SENTINEL


def _worker_config(worker_type: str, constraints: dict, hermes_home) -> tuple[str, str]:
    """Best-effort model/provider defaults for a worker.

    Resolution order: ``constraints`` (explicit CLI ``--model``/``--provider``
    overrides) → worker_models.json (per-harness pin) → a ``workers.<type>.model`` / ``workers.<type>.provider`` block
    in config.yaml → (``""``, ``""``). The global Hermes ``model`` section is
    deliberately NOT forwarded — external harnesses carry their own model
    config and Hermes' chat/image models rarely map to theirs (and the section
    is a nested dict, not a scalar). Reads config.yaml with the workers
    registry's stdlib mini-YAML parser, so no yaml dependency is pulled in.
    """
    model = str(constraints.get("model") or "")
    provider = str(constraints.get("provider") or "")
    if model and provider:
        return model, provider

    # worker_models.json precedence (profile-scoped)
    try:
        wm = _read_worker_models(hermes_home)
        entry_wm = wm.get(worker_type)
        if isinstance(entry_wm, dict):
            if not model:
                m = str(entry_wm.get("model") or "")
                if m:
                    model = m
            if not provider:
                p = str(entry_wm.get("provider") or "")
                if p:
                    provider = p
            if model and provider:
                return model, provider
    except Exception:
        pass

    parsed: dict = {}
    path = (hermes_home or get_hermes_home()) / "config.yaml"
    if path.is_file():
        try:
            parsed = workers._parse_mini_yaml(path.read_text(encoding="utf-8"))
        except OSError:
            parsed = {}

    workers_section = parsed.get("workers")
    entry = workers_section.get(worker_type) if isinstance(workers_section, dict) else None
    if isinstance(entry, dict):
        if not model:
            model = str(entry.get("model") or "")
        if not provider:
            provider = str(entry.get("provider") or "")
    return model, provider


class PiBackend(SubprocessBackend):
    """``pi -p "<task>"`` in the workspace (headless print mode)."""

    worker_type = "pi"

    def _build_command(self, request: WorkerSpec) -> list[str]:
        # Per-harness model pin via worker_models.json → config.yaml
        model, _ = _worker_config("pi", request.constraints, None)
        command = ["pi"]
        flag = detect_pi_flag()
        if flag:
            command.append(flag)
        # Only pass --model if pi advertises it; otherwise skip (documented fallback)
        if model and _pi_supports_model():
            command += ["--model", model]
        command.append(request.task)
        return command


_PI_MODEL_FLAG: object | str | None = object()

def _pi_supports_model(timeout: int = 5) -> bool:
    """Probe whether ``pi --help`` advertises a ``--model`` flag. Cached."""
    global _PI_MODEL_FLAG
    if _PI_MODEL_FLAG is not object():
        return bool(_PI_MODEL_FLAG)
    flag: str | None = None
    try:
        import subprocess as _sp
        result = _sp.run(
            ["pi", "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        txt = f"{result.stdout or ''}\n{result.stderr or ''}"
        if "--model" in txt:
            flag = "--model"
    except Exception:
        flag = None
    _PI_MODEL_FLAG = flag
    return bool(flag)

def reset_pi_model_flag_cache() -> None:
    global _PI_MODEL_FLAG
    _PI_MODEL_FLAG = object()


class CodexBackend(SubprocessBackend):
    """Codex verification via the codex-verify.sh wrapper (reliable).

    Codex's native ``--sandbox danger-full-access`` hits bubblewrap namespace
    denial + interactive approval prompts on this host. The wrapper runs codex
    with --dangerously-bypass-approvals-and-sandbox (safe: verification is
    read-only) + --output-schema, producing a structured PASS/FAIL/UNCERTAIN
    verdict with evidence. Codex is verification-only per credit policy.
    """

    worker_type = "codex"

    def _build_command(self, request: WorkerSpec) -> list[str]:
        return ["codex-verify.sh", request.task, request.workspace or ""]


class OpencodeBackend(SubprocessBackend):
    """``opencode run --auto "<task>"`` (with ``--model`` when configured).

    The harness determines its own working directory from the tty/session
    rather than the spawned process ``cwd``, so when the spec carries a
    ``workspace`` it is passed explicitly via ``--dir`` — otherwise tasks
    would silently run in the parent process's directory and leak out of an
    isolated workspace.
    """

    worker_type = "opencode"

    def _build_command(self, request: WorkerSpec) -> list[str]:
        model, _ = _worker_config("opencode", request.constraints, None)
        command = ["opencode", "run", "--auto"]
        if model:
            command += ["--model", model]
        if request.workspace:
            command += ["--dir", request.workspace]
        command.append(request.task)
        return command


class CommandCodeBackend(SubprocessBackend):
    """``commandcode -p --yolo "<task>"``.

    The binary is resolved via the workers registry's extra locations
    (``~/.nvm/versions/node/*/bin/commandcode``) when not on PATH.

    """

    worker_type = "commandcode"

    def _build_command(self, request: WorkerSpec) -> list[str]:
        model, _ = _worker_config("commandcode", request.constraints, None)
        binary = resolve_binary("commandcode")
        command = [str(binary) if binary is not None else "commandcode"]
        command += ["-p", "--yolo"]
        if model:
            command += ["-m", model]
        command.append(request.task)
        return command


class DshBackend(SubprocessBackend):
    """``dsh --profile headless "<task>"`` — experimental deep-reasoning harness.

    Uses the headless profile (one task, print result, exit) — the web profile
    boots the web UI and does not accept a task. Provider: opencode-go gateway
    (see ~/.dsh/settings.yaml) — commandcode's Provider API 401s on Go plan.
    """

    worker_type = "dsh"

    def _build_command(self, request: WorkerSpec) -> list[str]:
        # Per spec File-write layering: do NOT write ~/.dsh/settings.yaml here.
        return ["dsh", "--profile", "headless", request.task]


#: Per-type backend registry.
BACKENDS: dict[str, type[SubprocessBackend]] = {
    "pi": PiBackend,
    "codex": CodexBackend,
    "opencode": OpencodeBackend,
    "commandcode": CommandCodeBackend,
    "dsh": DshBackend,
}


def resolve_binary(name: str):
    """Resolve a harness binary honoring the registry's extra locations."""
    return workers._resolve_command(name)


def get_backend(worker_type: str) -> AgentExecutionBackend:
    """Return a fresh backend instance for a worker type.

    Unknown worker types (registered custom workers) get the generic
    subprocess backend whose launch command is the bare binary name.
    """
    cls = BACKENDS.get(worker_type)
    if cls is not None:
        return cls()
    return SubprocessBackend(worker_type=worker_type)


# ---------------------------------------------------------------------------
# Routing (§13) — evidence-driven task → worker selection
# ---------------------------------------------------------------------------

#: Routing rules in priority order — the most *specific* signal wins. Each
#: rule carries the preferred worker order (best first) plus the capabilities
#: the task implies, which is also used to qualify registered custom workers
#: (they don't appear in any builtin order, but they can cover the implied
#: capability set and get ranked after the builtin preference). Workers that
#: are not installed are skipped.
@dataclass(frozen=True)
class RoutingRule:
    pattern: re.Pattern
    order: tuple[str, ...]
    implied: frozenset[str]


ROUTING_RULES: list[RoutingRule] = [
    RoutingRule(
        re.compile(
            r"\b(long[- ]horizon|deep reasoning|deep dive|large scale|"
            r"multi[- ]step|long[- ]running|autonomous)\b",
            re.IGNORECASE,
        ),
        ("dsh", "pi", "codex"),
        frozenset({"long_horizon", "deep_reasoning"}),
    ),
    RoutingRule(
        re.compile(
            r"\b(review|audit|code review|pr review|critique|feedback)\b", re.IGNORECASE
        ),
        ("commandcode", "codex", "opencode"),
        frozenset({"review"}),
    ),
    RoutingRule(
        re.compile(
            r"\b(test|tests|testing|unit test|regression|verify)\b", re.IGNORECASE
        ),
        ("opencode", "codex"),
        frozenset({"testing"}),
    ),
    RoutingRule(
        re.compile(
            r"\b(architecture|architect|design|reason|reasoning|think through|"
            r"explore|research|investigat|analy[sz]|whiteboard)\b",
            re.IGNORECASE,
        ),
        ("pi", "codex", "dsh"),
        frozenset({"reasoning", "exploration"}),
    ),
    RoutingRule(
        re.compile(
            r"\b(implement|implementation|feature|code|refactor|write|build|"
            r"add|fix|develop)\b",
            re.IGNORECASE,
        ),
        ("codex", "opencode", "pi"),
        frozenset({"coding", "implementation"}),
    ),
]


# ---------------------------------------------------------------------------
# Evidence-driven scoring (§13)
# ---------------------------------------------------------------------------
#
# When benchmark evidence exists, each installed worker is scored for the
# task's inferred category: +2 per PASS in that category, −1 per FAIL there,
# +0.5 per PASS in any other category (general reliability), and +1 when the
# worker's capabilities contain the category's implied capability. The
# highest score wins; ties break on lower latency, then capability-hint order.

#: Keyword → evidence-category rules. The first rule whose pattern matches the
#: task text wins; every category is the name under which ``hermes workers
#: benchmark`` stores its evidence (see ``hermes_cli/benchmark.py``).
_EVIDENCE_CATEGORY_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(r"\b(review|code review|pr review|audit|critique|feedback)\b", re.IGNORECASE),
        "review",
    ),
    (
        re.compile(
            r"\b(architecture|architect|explain|understand|summary|summariz|"
            r"research|analy[sz]|design|whiteboard)\b",
            re.IGNORECASE,
        ),
        "repository_understanding",
    ),
    (
        re.compile(r"\b(fix|bug|debug|defect|failing)\b", re.IGNORECASE),
        "recovery",
    ),
    (
        re.compile(r"\b(test|tests|testing|unit test|regression|verify)\b", re.IGNORECASE),
        "coding",
    ),
    (
        re.compile(
            r"\b(long[- ]horizon|multi[- ]step|long[- ]running|autonomous|deep dive)\b",
            re.IGNORECASE,
        ),
        "long_horizon",
    ),
    (
        re.compile(r"\b(tool|shell|terminal|run|execute|script|command line)\b", re.IGNORECASE),
        "tool_heavy",
    ),
    (
        re.compile(r"\b(multi[- ]file|module(?:s)?|multiple files)\b", re.IGNORECASE),
        "multi_file",
    ),
    (
        re.compile(r"\b(context|long document|document|read the)\b", re.IGNORECASE),
        "context_heavy",
    ),
)

#: Alias sets for evidence-category matching — lets an inferred category match
#: benchmark records stored under a nearby name. The store uses the §24
#: category names, and ``repository_understanding`` is the §13 label for the
#: §24 ``repository`` category.
_EVIDENCE_CATEGORY_ALIASES: dict[str, frozenset[str]] = {
    "repository_understanding": frozenset({"repository_understanding", "repository"}),
    "context_heavy": frozenset({"context_heavy", "context"}),
    "tool_heavy": frozenset({"tool_heavy", "tool"}),
    "multi_file": frozenset({"multi_file", "multifile"}),
}

#: §13 capability implied by each evidence category — the +1 capability-match
#: bonus applies when an installed worker declares it.
_CATEGORY_CAPABILITIES: dict[str, str] = {
    "coding": "coding",
    "review": "review",
    "repository_understanding": "repository_reasoning",
    "recovery": "implementation",
    "long_horizon": "long_horizon",
    "tool_heavy": "coding",
    "multi_file": "implementation",
    "context_heavy": "reasoning",
}


def infer_evidence_category(task_text: str) -> str:
    """Map task text to the §13 evidence category (default: ``coding``)."""
    lowered = task_text.lower() if task_text else ""
    for pattern, category in _EVIDENCE_CATEGORY_RULES:
        if pattern.search(lowered):
            return category
    return "coding"


def _evidence_category_matches(inferred: str, record_category) -> bool:
    """Does a stored evidence record's category satisfy an inferred category?"""
    inferred_lower = inferred.lower()
    record_lower = str(record_category or "").lower()
    if record_lower == inferred_lower:
        return True
    return record_lower in _EVIDENCE_CATEGORY_ALIASES.get(inferred_lower, frozenset())


def _evidence_counts(
    worker: str, category: str, evidence: list[dict]
) -> dict:
    """Tally one worker's pass/fail evidence against an inferred category."""
    passes = fails = other_passes = 0
    category_latencies: list[float] = []
    all_latencies: list[float] = []

    def _is_number(value) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    for record in evidence:
        if record.get("worker") != worker:
            continue
        latency = record.get("latency_s")
        if _is_number(latency):
            all_latencies.append(float(latency))
        is_pass = bool(record.get("pass"))
        if _evidence_category_matches(category, record.get("category")):
            if _is_number(latency):
                category_latencies.append(float(latency))
            if is_pass:
                passes += 1
            else:
                fails += 1
        elif is_pass:
            other_passes += 1

    def _average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "passes": passes,
        "fails": fails,
        "other_passes": other_passes,
        "category_latency": _average(category_latencies),
        "latency": _average(all_latencies),
    }


def score_workers(
    task_text: str,
    capabilities_required: list[str] | None = None,
    evidence: list[dict] | None = None,
    hermes_home=None,
) -> list[dict]:
    """Score every installed worker against benchmark evidence, best first.

    Returns one dict per installed worker, sorted by descending score, then
    ascending latency, then capability-hint order:

      ``{"worker", "category", "score", "evidence", "latency"}``

    Scoring (§13): +2 per PASS in the inferred category, −1 per FAIL there,
    +0.5 per PASS in any other category, and +1 when the worker's capabilities
    contain the category's implied capability. ``latency`` is the worker's
    mean category latency (falling back to its overall mean) — the tie-break.
    When a ``capabilities_required`` filter is given it is applied leniently
    (unchanged ranking stands if no worker qualifies), mirroring
    ``rank_workers``.
    """
    hermes_home = hermes_home or get_hermes_home()
    installed = sorted(
        set(workers.detect_workers(hermes_home)) | set(workers._read_custom_workers(hermes_home))
    )
    if not installed:
        return []
    evidence = evidence or []
    category = infer_evidence_category(task_text)
    capability = _CATEGORY_CAPABILITIES.get(category, "coding")
    hint_order = {
        name: index for index, name in enumerate(rank_workers(task_text, None, hermes_home))
    }

    scored: list[dict] = []
    for name in installed:
        counts = _evidence_counts(name, category, evidence)
        score = (
            2 * counts["passes"]
            - counts["fails"]
            + 0.5 * counts["other_passes"]
            + (1 if capability in _capabilities_for(name, hermes_home) else 0)
        )
        latency = (
            counts["category_latency"] if counts["category_latency"] is not None else counts["latency"]
        )
        scored.append(
            {
                "worker": name,
                "category": category,
                "score": round(score, 3),
                "evidence": {
                    "passes": counts["passes"],
                    "fails": counts["fails"],
                    "other_passes": counts["other_passes"],
                },
                "latency": latency,
            }
        )

    if capabilities_required:
        required = set(capabilities_required)
        qualified = [
            entry
            for entry in scored
            if _covers(_capabilities_for(entry["worker"], hermes_home), required)
        ]
        if qualified:
            scored = qualified

    scored.sort(
        key=lambda entry: (
            -entry["score"],
            entry["latency"] if entry["latency"] is not None else float("inf"),
            hint_order.get(entry["worker"], len(hint_order)),
        )
    )
    return scored


def _has_evidence(scored: list[dict]) -> bool:
    """True when any scored worker has pass/fail evidence behind its score."""
    return any(
        entry["evidence"]["passes"]
        or entry["evidence"]["fails"]
        or entry["evidence"]["other_passes"]
        for entry in scored
    )


class WorkerBackendError(RuntimeError):
    """Raised when a task cannot be routed to any installed worker."""


def _capabilities_for(name: str, hermes_home) -> list[str]:
    if name in workers.BUILTIN_WORKERS:
        return list(workers.BUILTIN_WORKERS[name]["capabilities"])
    custom = workers._read_custom_workers(hermes_home)
    return list(custom.get(name, []))


def _covers(worker_caps: list[str], required: set[str]) -> bool:
    return required <= set(worker_caps)


def rank_workers(
    task_text: str,
    capabilities_required: list[str] | None = None,
    hermes_home=None,
) -> list[str]:
    """Rank installed workers for a task, best first.

    Scans the task for capability hints, intersects the hint order with the
    installed fleet, then applies any ``capabilities_required`` filter
    (lenient: if no installed worker covers every required capability the
    unfiltered ranking stands). Returns an empty list when nothing is
    installed.
    """
    hermes_home = hermes_home or get_hermes_home()
    installed = set(workers.detect_workers(hermes_home))
    custom = workers._read_custom_workers(hermes_home)
    installed |= set(custom)
    if not installed:
        return []

    task_lower = task_text.lower() if task_text else ""
    matched: RoutingRule | None = None
    for rule in ROUTING_RULES:
        if rule.pattern.search(task_lower):
            matched = rule
            break

    ranked: list[str] = []
    if matched is not None:
        for worker in matched.order:
            if worker in installed and worker not in ranked:
                ranked.append(worker)
        implied = set(matched.implied)
        if capabilities_required:
            implied |= set(capabilities_required)
        if implied:
            for name in sorted(custom):
                if name not in ranked and _covers(_capabilities_for(name, hermes_home), implied):
                    ranked.append(name)
    if not ranked:
        for worker in DEFAULT_ORDER:
            if worker in installed:
                ranked.append(worker)
        if not ranked:
            ranked = sorted(installed)

    if capabilities_required:
        required = set(capabilities_required)
        qualified = [
            worker
            for worker in ranked
            if _covers(_capabilities_for(worker, hermes_home), required)
        ]
        if qualified:
            ranked = qualified
    return ranked


# ---------------------------------------------------------------------------
# Risk/Confidence routing (§10) — triage classifier
# ---------------------------------------------------------------------------

_RISK_SIGNALS: tuple[str, ...] = (
    "rm -rf",
    "delete",
    "drop table",
    "truncate",
    "production",
    "prod",
    "secrets",
    "credentials",
    "sudo",
    "chmod 777",
    "reset",
    "migrate",
    "overwrite",
)

_DEEP_SIGNALS: tuple[str, ...] = (
    "refactor",
    "architecture",
    "design",
    "database",
    "migration",
    "distributed",
    "security",
    "optimize",
    "concurrency",
    "multi-agent",
    "orchestrat",
)

_CHEAP_SIGNALS: tuple[str, ...] = (
    "rename",
    "typo",
    "format",
    "comment",
    "docstring",
    "add a test for",
    "fix typo",
    "bump",
    "small",
)

_HUMAN_KEYWORDS: tuple[str, ...] = (
    "deploy",
    "release",
    "rollback",
    "credentials",
    "api key",
)

_DEEP_ORDER: tuple[str, ...] = ("commandcode", "codex", "pi", "dsh", "opencode")
_CHEAP_ORDER: tuple[str, ...] = ("opencode", "pi", "codex", "commandcode", "dsh")

_FILE_EXTS = r"py|js|ts|tsx|go|rs|md|json|yaml|yml|sh|css|html|sql"
_FILE_RE = re.compile(rf"""(?:["'`])?([\w./-]+\.(?:{_FILE_EXTS}))\b""")
_QUOTED_FILE_RE = re.compile(rf"""["'`]([^"'`\n]+\.(?:{_FILE_EXTS}))["'`]""")


def classify_task(task_text: str) -> dict:
    """Triage a task into risk/confidence lanes (§10, deterministic, stdlib-only).

    Returns ``{"lane", "confidence", "risk", "reasons"}`` where lane is
    ``cheap|standard|deep|human`` and risk is ``low|medium|high``.
    """
    lowered = (task_text or "").lower()
    reasons: list[str] = []
    risk: str = "low"
    lane: str = "standard"
    confidence: float = 0.5

    matched_risk: list[str] = []
    for sig in _RISK_SIGNALS:
        if sig == "prod":
            # Word-boundary: "prod" as a word (not "productive"); "production"
            # is its own signal, so skip when only the substring matched.
            if re.search(r"\bprod\b", lowered):
                matched_risk.append(sig)
            continue
        if sig in lowered:
            matched_risk.append(sig)
    if matched_risk:
        risk = "high"
        reasons.append(f"risk signal: {matched_risk[0]}")

    is_human = False
    human_reason: str | None = None
    for kw in _HUMAN_KEYWORDS:
        if kw in lowered:
            is_human = True
            human_reason = kw
            break
    if not is_human and risk == "high" and ("production" in lowered or re.search(r"\bprod\b", lowered)):
        is_human = True
        human_reason = "production/prod with high risk"

    if is_human:
        lane = "human"
        confidence = 0.9
        if human_reason:
            reasons.append(f"human signal: {human_reason}")
        if risk != "high":
            risk = "high"
        return {"lane": lane, "confidence": confidence, "risk": risk, "reasons": reasons or [f"human lane: {human_reason}"]}

    matched_deep: list[str] = [s for s in _DEEP_SIGNALS if s in lowered]
    if matched_deep:
        lane = "deep"
        confidence = 0.8
        reasons.append(f"deep signal: {matched_deep[0]}")
        return {"lane": lane, "confidence": confidence, "risk": risk, "reasons": reasons}

    matched_cheap: list[str] = [s for s in _CHEAP_SIGNALS if s in lowered]
    if matched_cheap:
        lane = "cheap"
        confidence = 0.85
        reasons.append(f"cheap signal: {matched_cheap[0]}")
        return {"lane": lane, "confidence": confidence, "risk": risk, "reasons": reasons}

    if risk == "high":
        confidence = 0.7
    return {"lane": lane, "confidence": confidence, "risk": risk, "reasons": reasons or ["default: standard lane"]}


def route_task(
    task_text: str,
    capabilities_required: list[str] | None = None,
    evidence: list[dict] | None = None,
    hermes_home=None,
    risk_aware: bool = False,
) -> str:
    """Route a task to the best installed worker type (§13).

    With benchmark ``evidence`` (see ``benchmark.load_results``), each
    installed worker is scored against the task's inferred category (see
    ``score_workers``) and the highest score wins — ties break on lower
    latency, then the capability-hint order. When no worker has any evidence
    (or ``evidence`` is None/empty), falls back to the capability-hint routing
    (unchanged behavior).

    With ``risk_aware=True`` (§10) the triage classifier gates routing:
    human lane returns ``"human"``, deep prefers the strongest harness
    (commandcode/codex), cheap prefers the cheapest (opencode/pi), otherwise
    evidence routing is used.
    """
    if risk_aware:
        classification = classify_task(task_text)
        lane = classification.get("lane")
        if lane == "human":
            return "human"
        installed_risk = set(workers.detect_workers(hermes_home)) | set(
            workers._read_custom_workers(hermes_home)
        )
        if not installed_risk:
            raise WorkerBackendError(
                "no worker harness installed — run `hermes workers` to see what is available"
            )
        if capabilities_required:
            required = set(capabilities_required)
            qualified = {
                w for w in installed_risk if _covers(_capabilities_for(w, hermes_home), required)
            }
            if qualified:
                installed_risk = qualified
        if lane == "deep":
            for w in _DEEP_ORDER:
                if w in installed_risk:
                    return w
            for w in sorted(installed_risk):
                if w not in _DEEP_ORDER:
                    return w
        elif lane == "cheap":
            for w in _CHEAP_ORDER:
                if w in installed_risk:
                    return w
            for w in sorted(installed_risk):
                if w not in _CHEAP_ORDER:
                    return w
    if evidence:
        scored = score_workers(task_text, capabilities_required, evidence, hermes_home)
        if _has_evidence(scored):
            return scored[0]["worker"]
    ranked = rank_workers(task_text, capabilities_required, hermes_home)
    if not ranked:
        raise WorkerBackendError(
            "no worker harness installed — run `hermes workers` to see what is available"
        )
    return ranked[0]


def scan_task_dependencies(task_text: str, workspace: str = "") -> dict:
    """Extract file paths from task text and classify overlap risk (§11)."""
    text = task_text or ""
    found: list[str] = []
    seen: set[str] = set()
    for pat in (_QUOTED_FILE_RE, _FILE_RE):
        for m in pat.finditer(text):
            p = m.group(1).strip()
            if p not in seen:
                seen.add(p)
                found.append(p)
    base = Path(workspace) if workspace else Path.cwd()
    existing: list[str] = []
    missing: list[str] = []
    for p in found:
        cand = Path(p) if Path(p).is_absolute() else (base / p)
        if cand.is_file():
            existing.append(p)
        elif not cand.exists():
            missing.append(p)
    # Effective overlap: default "none"; flip to "unknown" when any referenced
    # path is missing (can't prove the file set — conservative).
    overlap: str = "none"
    if not found:
        overlap = "unknown"
    elif missing:
        overlap = "unknown"
    return {"files": found, "existing": existing, "overlap_risk": overlap}


def can_parallelize(tasks: list[dict]) -> dict:
    """Decide whether tasks can run in parallel based on file overlap (§11)."""
    file_to_tasks: dict[object, list[int]] = {}
    task_files: list[list[str]] = []
    for idx, t in enumerate(tasks):
        text = t.get("task_text") or t.get("task") or ""
        ws = t.get("workspace") or ""
        info = scan_task_dependencies(text, ws)
        files = info.get("files", [])
        existing: list[str] = []
        base = Path(ws) if ws else Path.cwd()
        for f in files:
            cand = Path(f) if Path(f).is_absolute() else (base / f)
            if cand.is_file():
                existing.append(f)
        # Normalize paths for overlap: resolve relative to the task workspace
        # and use (workspace, normalized) as the collision key so "./auth.py"
        # == "auth.py" == "src/auth.py" (same resolved file), while the same
        # basename in different workspaces does NOT collide. Absolute paths
        # are keyed by norm alone (they are workspace-independent).
        normalized: list[str] = []
        for f in files:
            cand = Path(f) if Path(f).is_absolute() else (base / f)
            try:
                norm = cand.resolve().as_posix()
            except OSError:
                norm = (base / f).as_posix().lstrip("./")
            normalized.append(norm)
        task_files.append(existing)
        for f_orig, norm in zip(files, normalized):
            key = norm if Path(f_orig).is_absolute() else (ws, norm)
            file_to_tasks.setdefault(key, []).append(idx)

    shared: list[object] = [
        f for f, idxs in file_to_tasks.items() if len(idxs) > 1
    ]
    if shared:
        def _path_of(k: object) -> str:
            if isinstance(k, tuple):
                return str(k[-1])
            return str(k)

        shared_names = sorted({os.path.basename(_path_of(f)) for f in shared})
        return {
            "parallel": False,
            "reason": f"shared files: {shared_names}",
            "groups": [[t.get('task_text') or t.get('task') or '' for t in tasks]],
        }
    groups: list[list[str]] = []
    for t in tasks:
        groups.append([t.get("task_text") or t.get("task") or ""])
    if not shared and tasks:
        return {"parallel": True, "groups": groups, "reason": "no shared files"}
    return {"parallel": True, "groups": groups, "reason": "no shared files"}


def run_workers_plan_command(args) -> int:
    """``hermes workers plan <task1> <task2> ...`` — parallelism decision (§11)."""
    tasks_raw: list[str] = list(getattr(args, "tasks", None) or getattr(args, "task", None) or [])
    if isinstance(tasks_raw, str):
        tasks_raw = [tasks_raw]
    workspace = getattr(args, "workspace", None) or ""
    tasks: list[dict] = [{"task_text": t, "workspace": workspace} for t in tasks_raw if str(t).strip()]
    if not tasks:
        print("error: at least one task is required", file=sys.stderr)
        return 1
    as_json = bool(getattr(args, "json", False))
    result = can_parallelize(tasks)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"parallel: {result['parallel']}")
        print(f"reason: {result['reason']}")
        print(f"groups: {result['groups']}")
    return 0


def next_best_worker(
    worker_type: str,
    task_text: str,
    capabilities_required: list[str] | None = None,
    hermes_home=None,
) -> str:
    """Return the next-best worker after ``worker_type`` (§29 switch)."""
    ranked = rank_workers(task_text, capabilities_required, hermes_home)
    if worker_type in ranked:
        index = ranked.index(worker_type)
        if index + 1 < len(ranked):
            return ranked[index + 1]
    if ranked:
        return ranked[0]
    raise WorkerBackendError("no installed worker to switch to")


# ---------------------------------------------------------------------------
# Synchronous execution helper (§18/§20)
# ---------------------------------------------------------------------------


def run_task(spec: WorkerSpec, *, backend: AgentExecutionBackend | None = None) -> dict:
    """Synchronously execute ``spec`` and return the final execution dict.

    Thin convenience over ``start()`` + ``wait()`` used by the benchmark
    suite (`hermes workers benchmark`): starts the execution, blocks until a
    terminal status or the spec's timeout, and returns the final execution
    dict (status / result / error) for evaluation.
    """
    instance = backend or get_backend(spec.worker_type)
    execution_id = instance.start(spec)
    return instance.wait(execution_id)


# ---------------------------------------------------------------------------
# CLI handler (`hermes workers run`)
# ---------------------------------------------------------------------------

def _format_status(worker_type: str, status: dict) -> str:
    line = f"[{worker_type}] {status['status']}"
    if status.get("error"):
        line += f": {status['error']}"
    return line


def run_workers_cli_command(args) -> int:
    """``hermes workers run <task>`` — route, start, and (optionally) wait.

    Returns the process exit code. With ``--wait`` prints the lifecycle
    transitions (PLANNED→…→DONE/FAILED) and the final result. Failure
    handling (§29): ``--retry N`` re-runs the task up to N times (fresh
    execution_id per attempt); ``--switch-on-failure`` routes the retry to
    the next-best worker once.
    """
    task = getattr(args, "task", None) or ""
    if not task.strip():
        print("error: task must not be empty", file=sys.stderr)
        return 1

    hermes_home = get_hermes_home()
    explicit = getattr(args, "worker", None) or ""
    capabilities_required = list(getattr(args, "capabilities", None) or [])
    workspace = getattr(args, "workspace", None) or ""
    timeout = max(1, int(getattr(args, "timeout", 600) or 600))
    retry = max(0, int(getattr(args, "retry", 0) or 0))
    switch_on_failure = bool(getattr(args, "switch_on_failure", False))
    wait = bool(getattr(args, "wait", False))
    context = getattr(args, "context", None) or ""
    risk_aware = bool(getattr(args, "risk_aware", False))
    allow_human = bool(getattr(args, "allow_human", False))
    constraints = {}
    model = getattr(args, "model", None) or ""
    provider = getattr(args, "provider", None) or ""
    if model:
        constraints["model"] = model
    if provider:
        constraints["provider"] = provider

    custom_workers = set(workers._read_custom_workers(hermes_home))

    # Safety gate FIRST: human-lane classification must refuse BEFORE any
    # explicit worker dispatch, or `--worker X` would bypass the human lane.
    if risk_aware:
        classification = classify_task(task)
        if classification.get("lane") == "human" and not allow_human:
            print(
                "error: task classified as 'human' lane — requires --allow-human to run",
                file=sys.stderr,
            )
            print(f"classification: {classification}", file=sys.stderr)
            return 1
    else:
        classification = None

    if explicit:
        worker_type = explicit
        if worker_type not in BACKENDS and worker_type not in custom_workers:
            print(
                f"error: unknown worker type '{worker_type}' "
                f"(known: {', '.join(sorted(BACKENDS))})",
                file=sys.stderr,
            )
            return 1
        if worker_type in BACKENDS and resolve_binary(worker_type) is None:
            print(
                f"error: worker '{worker_type}' is not installed on this machine",
                file=sys.stderr,
            )
            return 1
    else:
        evidence, evidence_path = _load_routing_evidence(hermes_home)
        try:
            worker_type = route_task(task, capabilities_required, evidence, hermes_home, risk_aware=risk_aware)
        except WorkerBackendError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if risk_aware:
            classification = classify_task(task)
            print(f"[risk-aware] lane={classification['lane']} risk={classification['risk']} confidence={classification['confidence']} reasons={classification['reasons']}")
        ranked = rank_workers(task, capabilities_required, hermes_home)
        if evidence:
            print(
                f"[evidence] routing from {evidence_path} "
                f"({len(evidence)} benchmark record(s))"
            )
        print(f"Routed to worker '{worker_type}' (ranked: {', '.join(ranked)})")

    if worker_type == "dsh":
        print("[experimental] dsh backend — long-horizon / deep-reasoning harness")

    attempts = retry + 1
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            if switch_on_failure:
                try:
                    worker_type = next_best_worker(
                        worker_type, task, capabilities_required, hermes_home
                    )
                except WorkerBackendError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return 1
                print(f"[retry {attempt}/{attempts}] switched to worker '{worker_type}'")
            else:
                print(f"[retry {attempt}/{attempts}]")

        spec = WorkerSpec(
            worker_type=worker_type,
            task=task,
            workspace=workspace,
            context=context,
            constraints=constraints,
            timeout=timeout,
            environment_policy="isolated",
        )
        backend = get_backend(worker_type)
        execution_id = backend.start(spec)
        print(f"execution_id: {execution_id}")

        if not wait:
            print(_format_status(worker_type, backend.status(execution_id)))
            return 0

        last_status: str | None = None
        while True:
            current = backend.status(execution_id)
            status = current["status"]
            if status != last_status:
                print(f"[{worker_type}] {status}")
                last_status = status
            if status in _TERMINAL_STATUSES:
                final = current
                break
            time.sleep(SubprocessBackend._POLL_INTERVAL)

        if final["status"] == DONE:
            print("--- result ---")
            print(final.get("result") or "")
            return 0
        print(
            f"[{worker_type}] {final['status']}: {final.get('error') or 'unknown error'}",
            file=sys.stderr,
        )
        if final.get("result"):
            print("--- output ---", file=sys.stderr)
            print(final["result"], file=sys.stderr)

    return 1


# ---------------------------------------------------------------------------
# Resume / recovery (`hermes workers resume`, §30)
# ---------------------------------------------------------------------------

def build_resume_command(worker_type: str, task: str) -> list[str]:
    """Resume argv for harnesses that advertise a resume/continue flag.

    The harness is relaunched against the same task text with its
    ``--resume``/``--continue`` flag. Harnesses without a resume flag return
    an empty list — ``resume`` re-issues those as a fresh run instead.
    """
    if worker_type == "pi":
        return ["pi", "--resume", task]
    if worker_type == "codex":
        return ["codex", "exec", "--continue", task]
    if worker_type == "opencode":
        return ["opencode", "run", "--continue", task]
    if worker_type == "commandcode":
        binary = resolve_binary("commandcode")
        return [str(binary) if binary is not None else "commandcode", "--continue", task]
    return []


def _find_execution_record(
    execution_id: str, hermes_home: Path | None = None
) -> tuple[dict, str] | None:
    """Locate an execution in the live registry or persisted history.

    Returns ``(record, source)`` where source is ``"live"`` or ``"history"``,
    or None when the execution is unknown.
    """
    for record in _read_live_map(hermes_home).values():
        if record.get("execution_id") == execution_id:
            return record, "live"
    for record in _read_history(execution_history_path(hermes_home)):
        if record.get("execution_id") == execution_id:
            return record, "history"
    return None


def _pid_alive(pid) -> bool:
    """Best-effort liveness probe for a harness process (§30).

    A ``kill(pid, 0)`` probe: the process exists when the call succeeds or
    raises PermissionError (owned by another user); anything else (no such
    process, unsupported signal) means it is gone.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def mark_execution_interrupted(
    execution_id: str,
    worker_type: str,
    task: str,
    started_at: float,
    hermes_home: Path | None = None,
) -> Path:
    """Append a ``FAILED (interrupted)`` history record for a gone execution (§30).

    The process backing an execution that should have been resumable no longer
    exists (crashed / host restarted), so its terminal state is corrected to
    ``FAILED`` with error ``"interrupted"`` and the task text is surfaced for
    re-running. Returns the path written.
    """
    return _append_history_record(
        {
            "execution_id": execution_id,
            "worker_type": worker_type,
            "task": task,
            "status": FAILED,
            "started_at": started_at,
            "ended_at": time.time(),
            "result_tail": "",
            "error": "interrupted",
        },
        hermes_home,
    )


def run_workers_resume_command(args) -> int:
    """``hermes workers resume <execution_id>`` — recover an execution (§30).

    For an execution whose harness supports resumption, re-attach to it when
    its process is still alive (relaunch the harness with its
    ``--resume``/``--continue`` flag); when the process is gone, mark the
    execution ``FAILED (interrupted)`` and offer the task text to re-run via
    ``hermes workers run``. Honesty rule: when the harness has no resume flag,
    ``resume`` re-issues the task as a fresh run and reports the new execution
    id. Returns the process exit code.
    """
    execution_id = getattr(args, "execution_id", None) or ""
    if not execution_id.strip():
        print("error: execution_id must not be empty", file=sys.stderr)
        return 1
    hermes_home = get_hermes_home()
    found = _find_execution_record(execution_id.strip(), hermes_home)
    if found is None:
        print(
            f"error: unknown execution '{execution_id}' — "
            "not in the live registry or execution history",
            file=sys.stderr,
        )
        return 1
    record, source = found
    worker_type = str(record.get("worker_type") or "")
    task = str(record.get("task") or "")
    resume_flag = _RESUME_FLAGS.get(worker_type)

    if worker_type in BACKENDS and resolve_binary(worker_type) is None:
        print(f"error: worker '{worker_type}' is not installed on this machine", file=sys.stderr)
        return 1

    if resume_flag:
        # The harness supports resumption: re-attach only when the backing
        # process is genuinely still alive.
        still_alive = source == "live" and _pid_alive(record.get("pid"))
        if still_alive:
            command = build_resume_command(worker_type, task)
            backend = get_backend(worker_type)
            new_id = backend.start(
                WorkerSpec(worker_type=worker_type, task=task), command=command
            )
            print(f"resumed {execution_id} as {new_id} (re-attached via {resume_flag})")
            return 0
        # The process is gone (history record, or a stale live entry whose pid
        # no longer exists) — mark the execution interrupted and offer the
        # task text to re-run.
        started_at = record.get("started_at")
        mark_execution_interrupted(
            execution_id, worker_type, task, started_at or 0.0, hermes_home
        )
        live = _read_live_map(hermes_home)
        live.pop(execution_id, None)
        write_live_executions(live, hermes_home)
        print(
            f"execution {execution_id}: process is gone — marked FAILED (interrupted)",
            file=sys.stderr,
        )
        print(f"task: {task}", file=sys.stderr)
        print(
            f're-run with: hermes workers run "{task}" --worker {worker_type}',
            file=sys.stderr,
        )
        return 1

    if not worker_type:
        print(f"error: execution '{execution_id}' has no worker type to resume", file=sys.stderr)
        return 1

    # Honesty rule (§30): the harness has no resume flag, so re-issue the
    # task as a fresh run and report the new execution id.
    backend = get_backend(worker_type)
    new_id = backend.start(WorkerSpec(worker_type=worker_type, task=task))
    print(
        f"resumed {execution_id} as {new_id} "
        f"(no resume flag for '{worker_type}' — re-issued the task as a fresh run)"
    )
    return 0


# ---------------------------------------------------------------------------
# Evidence loading + `hermes workers route` (§13)
# ---------------------------------------------------------------------------


def _load_routing_evidence(hermes_home) -> tuple[list[dict] | None, Path]:
    """Load the routing-evidence store if it exists.

    Returns ``(evidence_or_None, store_path)`` — evidence is None when no
    store exists yet, so callers fall back to capability-hint routing.
    """
    from hermes_cli.benchmark import benchmark_results_path, load_results

    store_path = benchmark_results_path(hermes_home)
    if not store_path.is_file():
        return None, store_path
    return load_results(path=store_path), store_path


def _fmt_latency(latency) -> str:
    return f"{latency:.1f}s" if isinstance(latency, (int, float)) else "—"


def _fmt_evidence(counts: dict) -> str:
    parts = []
    if counts.get("passes"):
        parts.append(f"{counts['passes']} pass")
    if counts.get("fails"):
        parts.append(f"{counts['fails']} fail")
    if counts.get("other_passes"):
        parts.append(f"{counts['other_passes']} other pass")
    return ", ".join(parts) if parts else "no evidence"


def _print_route_table(
    task: str,
    chosen: str,
    store_path: Path,
    evidence: list[dict] | None,
    display: list[dict],
) -> None:
    print(f"task:        {task}")
    category = display[0]["category"] if display else infer_evidence_category(task)
    print(f"category:    {category}")
    if evidence:
        print(f"evidence:    {store_path} ({len(evidence)} record(s))")
    else:
        print("evidence:    none — capability-hint routing")

    headers = ("WORKER", "SCORE", "LATENCY", "EVIDENCE")
    rows = [
        (
            entry["worker"],
            f"{entry['score']:g}",
            _fmt_latency(entry.get("latency")),
            _fmt_evidence(entry["evidence"]),
        )
        for entry in display
    ]
    all_rows = [headers, *rows]
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    for index, row in enumerate(all_rows):
        print("  ".join(str(row[j]).ljust(widths[j]) for j in range(len(headers))).rstrip())
        if index == 0:
            print("  ".join("-" * w for w in widths))
    print()
    print(f"chosen: {chosen}")


def run_workers_route_command(args) -> int:
    """``hermes workers route <task>`` — preview the evidence-driven routing.

    Prints the inferred benchmark category, the scored worker ranking, and the
    chosen worker — the same selection ``hermes workers run`` would make.
    With ``--json`` emits the same information as structured JSON. Returns the
    process exit code.
    """
    task = getattr(args, "task", None) or ""
    if not task.strip():
        print("error: task must not be empty", file=sys.stderr)
        return 1
    hermes_home = get_hermes_home()
    capabilities_required = list(getattr(args, "capabilities", None) or [])
    as_json = bool(getattr(args, "json", False))
    risk_aware = bool(getattr(args, "risk_aware", False))

    evidence, store_path = _load_routing_evidence(hermes_home)
    try:
        chosen = route_task(task, capabilities_required, evidence, hermes_home, risk_aware=risk_aware)
    except WorkerBackendError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    classification = classify_task(task) if risk_aware else None
    if classification:
        extra = f"lane={classification['lane']} risk={classification['risk']} confidence={classification['confidence']} reasons={classification['reasons']}"
        if as_json:
            pass
        else:
            print(f"[risk-aware] {extra}")

    scored = score_workers(task, capabilities_required, evidence, hermes_home)
    evidence_used = _has_evidence(scored)
    if evidence_used:
        display = scored
    else:
        display = [
            {
                "worker": name,
                "category": infer_evidence_category(task),
                "score": 0.0,
                "evidence": {"passes": 0, "fails": 0, "other_passes": 0},
                "latency": None,
            }
            for name in rank_workers(task, capabilities_required, hermes_home)
        ]

    payload = {
        "task": task,
        "category": infer_evidence_category(task),
        "chosen": chosen,
        "evidence_used": evidence_used,
        "evidence_file": str(store_path),
        "evidence_count": len(evidence or []),
        "workers": [
            {
                "worker": entry["worker"],
                "score": entry["score"],
                "latency": entry["latency"],
                "evidence": entry["evidence"],
            }
            for entry in display
        ],
    }
    if classification:
        payload["classification"] = classification

    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        _print_route_table(task, chosen, store_path, evidence, display)
    return 0
