"""Tests for fleet observability (§28) + execution persistence/recovery (§30).

Covers the live fleet view (``hermes workers status`` showing running/blocked
executions mirrored from the backend's registry, and with ``--all`` the
persisted history), the §30 execution-history store (write-through on
terminal transitions, 100-entry cap, result-tail, ``load_execution_history``
shape), the live-registry mirror, and ``hermes workers resume`` recovery
(re-attach a live process via the harness resume flag, mark a gone process
FAILED (interrupted) and offer a re-run, re-issue fresh runs for harnesses
without a resume flag).
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import worker_backend as backend
from hermes_cli import workers
from hermes_cli.subcommands.workers import build_workers_parser
from hermes_cli.worker_backend import (
    BLOCKED,
    CANCELLED,
    DONE,
    FAILED,
    RUNNING,
    WorkerExecution,
    WorkerSpec,
)
from hermes_constants import get_hermes_home

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStdout:
    """File-like stand-in for ``Popen.stdout`` with no real fd."""

    def __init__(self, data: bytes = b""):
        self._data = data

    def fileno(self):
        raise OSError("no real fd")

    def read(self, n=-1):
        data = self._data
        self._data = b""
        return data


class FakeProc:
    """Scripted ``subprocess.Popen``: ``poll()`` returns None for ``delay``
    calls, then ``rc``."""

    def __init__(self, out=b"", rc=0, delay=1, pid=4242):
        self._out = out
        self._rc = rc
        self._delay = delay
        self._polls = 0
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.stdout = FakeStdout(out)

    def poll(self):
        self._polls += 1
        if self.terminated:
            self.returncode = 0
        elif self._polls > self._delay:
            self.returncode = self._rc
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.terminated = True

    def wait(self, timeout=None):
        return self.poll()


class CaptureBackend:
    """Records specs/commands handed to ``start()``; returns a fixed id."""

    def __init__(self):
        self.started: list[tuple[WorkerSpec, list[str] | None]] = []

    def start(self, spec, command=None):
        self.started.append((spec, command))
        return "new-id-1"


def _backend_with_fake(worker_type="opencode", **proc_kwargs):
    proc = FakeProc(**proc_kwargs)
    stack = patch.object(backend.subprocess, "Popen", return_value=proc)
    return backend.get_backend(worker_type), proc, stack


def _patch_status_detection(monkeypatch):
    """Hermetic detection for ``hermes workers status`` (no real harnesses)."""
    monkeypatch.setattr(workers, "detect_workers", lambda home=None: {})
    monkeypatch.setattr(workers, "_read_custom_workers", lambda home=None: {})
    monkeypatch.setattr(workers, "_fleet_detected", lambda: None)


def _status_args(**overrides):
    defaults = dict(workers_action="status", json=False, all=False, limit=10)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _history_execution(execution_id, task="task", status=DONE, started_at=100.0):
    return WorkerExecution(
        execution_id=execution_id,
        worker_type="opencode",
        task=task,
        status=status,
        started_at=started_at,
        updated_at=started_at + 100.0,
        result="ok" if status == DONE else "",
        error="" if status == DONE else "boom",
    )


# ---------------------------------------------------------------------------
# Live fleet view (§28)
# ---------------------------------------------------------------------------


def test_status_shows_running_executions(monkeypatch, capsys):
    _patch_status_detection(monkeypatch)
    long_task = "Write a really long task " * 10

    inst, _proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task=long_task))
        rc = workers.run_workers_command(_status_args())

    out = capsys.readouterr().out
    assert rc == 0
    assert "Running executions" in out
    assert execution_id in out
    assert "opencode" in out
    assert "RUNNING" in out
    # Task is truncated to 60 chars in the table.
    assert "…" in out
    assert len(long_task) > 60


def test_status_shows_blocked_execution(monkeypatch, capsys):
    _patch_status_detection(monkeypatch)

    inst, _proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="blocked task"))
        inst._executions[execution_id].status = BLOCKED
        inst._persist_execution_state(execution_id)
        rc = workers.run_workers_command(_status_args())

    out = capsys.readouterr().out
    assert rc == 0
    assert execution_id in out
    assert "BLOCKED" in out


def test_status_all_shows_persisted_history(monkeypatch, capsys):
    home = get_hermes_home()
    backend.append_execution_history(
        _history_execution("hist-1", task="echo OBS_OK", status=DONE, started_at=100.0), home
    )
    backend.append_execution_history(
        _history_execution("hist-2", task="broken task", status=FAILED, started_at=300.0), home
    )
    _patch_status_detection(monkeypatch)

    rc = workers.run_workers_command(_status_args(all=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Recent executions" in out
    assert "hist-1" in out and "hist-2" in out
    assert "DONE" in out and "FAILED" in out
    assert "echo OBS_OK" in out


def test_status_without_all_hides_history(monkeypatch, capsys):
    home = get_hermes_home()
    backend.append_execution_history(_history_execution("hist-1", status=DONE), home)
    _patch_status_detection(monkeypatch)

    rc = workers.run_workers_command(_status_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Recent executions" not in out


def test_status_without_history_file_still_works(monkeypatch, capsys):
    """Backward compatibility: no store file yet → status still exits 0."""
    _patch_status_detection(monkeypatch)
    rc = workers.run_workers_command(_status_args())
    out = capsys.readouterr().out
    assert rc == 0
    assert "Running executions:" in out
    assert "none" in out


def test_status_json_shape(monkeypatch, capsys):
    home = get_hermes_home()
    backend.write_live_executions(
        {
            "live-1": {
                "execution_id": "live-1",
                "worker_type": "opencode",
                "task": "a live task",
                "status": RUNNING,
                "started_at": 100.0,
                "updated_at": 120.0,
            }
        },
        home,
    )
    backend.append_execution_history(_history_execution("hist-1", status=DONE), home)
    _patch_status_detection(monkeypatch)

    rc = workers.run_workers_command(_status_args(json=True, all=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["fleet"] is None
    assert payload["workers"] == []
    assert payload["hermes_herdr_integration"] is False
    assert [r["execution_id"] for r in payload["running"]] == ["live-1"]
    assert set(payload["running"][0]) == {
        "execution_id",
        "worker_type",
        "task",
        "status",
        "started_at",
        "updated_at",
    }
    assert [r["execution_id"] for r in payload["history"]] == ["hist-1"]
    assert set(payload["history"][0]) == {
        "execution_id",
        "worker_type",
        "task",
        "status",
        "started_at",
        "ended_at",
        "result_tail",
        "error",
    }


def test_live_executions_include_pid_when_process_running():
    inst, proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="t"))
        live = {r["execution_id"]: r for r in backend.load_live_executions()}
        assert live[execution_id]["pid"] == proc.pid


# ---------------------------------------------------------------------------
# Execution history persistence (§30)
# ---------------------------------------------------------------------------


def test_history_persisted_on_done():
    proc_out = b"OBS_OK line one\nline two\n"
    inst, _proc, stack = _backend_with_fake(out=proc_out, rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="echo OBS_OK"))
        final = inst.wait(execution_id)
    assert final["status"] == DONE

    history = backend.load_execution_history(10)
    assert len(history) == 1
    record = history[0]
    assert record["execution_id"] == execution_id
    assert record["worker_type"] == "opencode"
    assert record["task"] == "echo OBS_OK"
    assert record["status"] == DONE
    assert record["result_tail"] == "OBS_OK line one\nline two\n"
    assert record["error"] == ""
    assert record["ended_at"] >= record["started_at"]


def test_history_persisted_on_failure():
    inst, _proc, stack = _backend_with_fake(out=b"traceback here", rc=3, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="flaky"))
        inst.wait(execution_id)
    record = backend.load_execution_history(1)[0]
    assert record["execution_id"] == execution_id
    assert record["status"] == FAILED
    assert record["error"] == "exit code 3"
    assert record["result_tail"] == "traceback here"


def test_history_persisted_on_cancel():
    inst, _proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="long"))
        inst.stop(execution_id)
    record = backend.load_execution_history(1)[0]
    assert record["status"] == CANCELLED
    assert record["error"] == ""


def test_history_persisted_on_spawn_failure():
    inst = backend.get_backend("opencode")
    with patch.object(backend.subprocess, "Popen", side_effect=OSError("no binary")):
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="doomed"))
    record = backend.load_execution_history(1)[0]
    assert record["execution_id"] == execution_id
    assert record["status"] == FAILED
    assert "spawn failed" in record["error"]
    assert record["task"] == "doomed"


def test_history_result_tail_capped_at_200():
    inst, _proc, stack = _backend_with_fake(out=b"x" * 500, rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="big output"))
        inst.wait(execution_id)
    record = backend.load_execution_history(1)[0]
    assert backend.RESULT_TAIL_CHARS == 200
    assert record["result_tail"] == "x" * 200


def test_terminal_execution_leaves_live_registry():
    inst, _proc, stack = _backend_with_fake(out=b"ok", rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="t"))
        assert any(r["execution_id"] == execution_id for r in backend.load_live_executions())
        inst.wait(execution_id)
    assert backend.load_live_executions() == []


def test_load_execution_history_returns_most_recent_n():
    home = get_hermes_home()
    for i in range(15):
        backend.append_execution_history(
            WorkerExecution(
                execution_id=f"e-{i}",
                worker_type="opencode",
                task="t",
                status=DONE,
                started_at=float(i),
                updated_at=float(i + 1),
                result="ok",
                error="",
            ),
            home,
        )
    history = backend.load_execution_history(10, home)
    assert [r["execution_id"] for r in history] == [f"e-{i}" for i in range(14, 4, -1)]


def test_history_caps_at_100_oldest_dropped():
    home = get_hermes_home()
    for i in range(105):
        backend.append_execution_history(
            WorkerExecution(
                execution_id=f"exec-{i}",
                worker_type="opencode",
                task=f"task {i}",
                status=DONE,
                started_at=float(i),
                updated_at=float(i + 1),
                result="ok",
                error="",
            ),
            home,
        )
    history = backend.load_execution_history(1000, home)
    assert len(history) == backend.HISTORY_CAP == 100
    ids = [r["execution_id"] for r in history]
    assert "exec-0" not in ids  # oldest dropped
    assert "exec-5" in ids
    assert ids[0] == "exec-104"  # newest first
    # The on-disk file is capped too.
    raw = json.loads(backend.execution_history_path(home).read_text(encoding="utf-8"))
    assert len(raw) == 100


def test_history_round_trip_through_append_and_load():
    home = get_hermes_home()
    execution = _history_execution("rt-1", task="round trip", status=DONE)
    written = backend.append_execution_history(execution, home)
    assert written == backend.execution_history_path(home)
    assert backend.load_execution_history(10, home)[0] == {
        "execution_id": "rt-1",
        "worker_type": "opencode",
        "task": "round trip",
        "status": DONE,
        "started_at": 100.0,
        "ended_at": 200.0,
        "result_tail": "ok",
        "error": "",
    }


def test_history_survives_corrupt_store():
    home = get_hermes_home()
    backend.execution_history_path(home).write_text("not json{", encoding="utf-8")
    assert backend.load_execution_history(10, home) == []
    backend.append_execution_history(_history_execution("fresh", status=DONE), home)
    assert [r["execution_id"] for r in backend.load_execution_history(10, home)] == ["fresh"]


# ---------------------------------------------------------------------------
# Resume / recovery (§30)
# ---------------------------------------------------------------------------


def test_resume_no_flag_reissues_as_fresh_run(monkeypatch, capsys):
    home = get_hermes_home()
    backend.append_execution_history(
        WorkerExecution(
            execution_id="gone-1",
            worker_type="dsh",
            task="deep dive",
            status=DONE,
            started_at=100.0,
            updated_at=200.0,
            result="done",
            error="",
        ),
        home,
    )
    fake = CaptureBackend()
    monkeypatch.setattr(backend, "get_backend", lambda worker_type: fake)

    rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="gone-1"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "resumed gone-1 as new-id-1" in out
    assert "no resume flag for 'dsh'" in out
    spec, command = fake.started[0]
    assert spec.worker_type == "dsh"
    assert spec.task == "deep dive"
    assert command is None


def test_resume_gone_process_marks_interrupted(monkeypatch, capsys):
    home = get_hermes_home()
    backend.append_execution_history(
        WorkerExecution(
            execution_id="gone-2",
            worker_type="opencode",
            task="write tests",
            status=RUNNING,
            started_at=100.0,
            updated_at=100.0,
            result="",
            error="",
        ),
        home,
    )
    with patch.object(backend, "resolve_binary", return_value=Path("/fake/bin/opencode")):
        rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="gone-2"))
    captured = capsys.readouterr()
    assert rc == 1
    assert "marked FAILED (interrupted)" in captured.err
    assert "write tests" in captured.err
    assert "hermes workers run" in captured.err

    newest = backend.load_execution_history(5, home)[0]
    assert newest["execution_id"] == "gone-2"
    assert newest["status"] == FAILED
    assert newest["error"] == "interrupted"


def test_resume_stale_live_entry_removed_and_marked_interrupted(monkeypatch, capsys):
    home = get_hermes_home()
    backend.write_live_executions(
        {
            "stale-1": {
                "execution_id": "stale-1",
                "worker_type": "codex",
                "task": "fix the bug",
                "status": RUNNING,
                "started_at": 100.0,
                "updated_at": 100.0,
                "pid": 99999,
            }
        },
        home,
    )
    with patch.object(backend, "resolve_binary", return_value=Path("/fake/bin/codex")):
        rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="stale-1"))
    captured = capsys.readouterr()
    assert rc == 1
    assert "marked FAILED (interrupted)" in captured.err
    assert backend.load_live_executions() == []
    assert backend.load_execution_history(5, home)[0]["status"] == FAILED


def test_resume_live_process_reattaches_via_flag(monkeypatch, capsys):
    home = get_hermes_home()
    backend.write_live_executions(
        {
            "live-9": {
                "execution_id": "live-9",
                "worker_type": "codex",
                "task": "fix the bug",
                "status": BLOCKED,
                "started_at": 100.0,
                "updated_at": 100.0,
                "pid": 4242,
            }
        },
        home,
    )
    fake = CaptureBackend()
    monkeypatch.setattr(backend, "get_backend", lambda worker_type: fake)
    monkeypatch.setattr(backend, "_pid_alive", lambda pid: True)
    with patch.object(backend, "resolve_binary", return_value=Path("/fake/bin/codex")):
        rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="live-9"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "resumed live-9 as new-id-1" in out
    assert "--continue" in out
    spec, command = fake.started[0]
    assert command == backend.build_resume_command("codex", "fix the bug")
    assert spec.task == "fix the bug"


def test_resume_unknown_execution_fails(capsys):
    rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="nope"))
    assert rc == 1
    assert "unknown execution" in capsys.readouterr().err


def test_resume_empty_execution_id_fails(capsys):
    rc = backend.run_workers_resume_command(SimpleNamespace(execution_id=""))
    assert rc == 1
    assert "execution_id must not be empty" in capsys.readouterr().err


def test_resume_worker_not_installed_fails(monkeypatch, capsys):
    home = get_hermes_home()
    backend.append_execution_history(
        _history_execution("gone-3", task="t", status=DONE), home
    )
    with patch.object(backend, "resolve_binary", return_value=None):
        rc = backend.run_workers_resume_command(SimpleNamespace(execution_id="gone-3"))
    assert rc == 1
    assert "not installed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_status_and_resume_parser():
    root = __import__("argparse").ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    status_args = root.parse_args(["workers", "status", "--all", "--limit", "5", "--json"])
    assert status_args.workers_action == "status"
    assert status_args.all is True
    assert status_args.limit == 5
    assert status_args.json is True

    resume_args = root.parse_args(["workers", "resume", "abc123"])
    assert resume_args.workers_action == "resume"
    assert resume_args.execution_id == "abc123"
