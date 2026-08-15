"""Tests for ``hermes_cli/worker_backend`` — AgentExecutionBackend + routing.

Covers the WorkerSpec/WorkerExecution contract, evidence-driven task→worker
routing (§13), the subprocess backend lifecycle (§20) with a mocked
``subprocess.Popen``, failure handling (§29: nonzero exit, timeout, retry,
switch-on-failure), and the ``hermes workers run`` CLI handler.
"""

import io
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import worker_backend as backend
from hermes_cli import workers
from hermes_cli.subcommands.workers import build_workers_parser
from hermes_cli.worker_backend import (
    CANCELLED,
    DISPATCHING,
    DONE,
    FAILED,
    PLANNED,
    RUNNING,
    WorkerExecution,
    WorkerSpec,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStdout:
    """File-like stand-in for ``Popen.stdout`` with no real fd.

    ``fileno()`` raising OSError means the non-blocking drain path is skipped
    while the process is "running"; ``read()`` returns everything on exit.
    """

    def __init__(self, data: bytes = b""):
        self._data = data

    def fileno(self):
        raise OSError("no real fd")

    def read(self, n=-1):
        data = self._data
        self._data = b""
        return data


class FakeProc:
    """Scripted ``subprocess.Popen``: returns None from ``poll()`` for
    ``delay`` calls, then ``rc``."""

    def __init__(self, out=b"", rc=0, delay=1, stdin_enabled=False):
        self._out = out
        self._rc = rc
        self._delay = delay
        self._polls = 0
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.stdin = io.BytesIO() if stdin_enabled else None
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


class ScriptedBackend:
    """CLI-test backend: ``start()`` pops the next final status; ``status()``
    returns it immediately (the CLI's wait loop sees a terminal status)."""

    def __init__(self, final_statuses):
        self.final_statuses = list(final_statuses)
        self.started: list[tuple[str, WorkerSpec]] = []
        self.status_map = {}

    def start(self, spec):
        execution_id = f"exec-{len(self.started) + 1}"
        self.started.append((execution_id, spec))
        status = self.final_statuses.pop(0) if self.final_statuses else DONE
        self.status_map[execution_id] = {
            "execution_id": execution_id,
            "worker_type": spec.worker_type,
            "status": status,
            "result": "some output" if status == DONE else "",
            "error": "" if status == DONE else "boom",
        }
        return execution_id

    def status(self, execution_id):
        return self.status_map[execution_id]


def _patch_installed(monkeypatch, names):
    """Pretend exactly ``names`` are the detected+registered workers."""
    installed = {
        name: {
            "name": name,
            "version": "1.0",
            "source": "builtin",
            "capabilities": list(workers.BUILTIN_WORKERS.get(name, {}).get("capabilities", [])),
            "herdr": False,
        }
        for name in names
    }
    monkeypatch.setattr(workers, "detect_workers", lambda home=None: installed)
    monkeypatch.setattr(workers, "_read_custom_workers", lambda home=None: {})


def _cli_args(**overrides):
    defaults = dict(
        task="do something",
        worker="",
        capabilities=None,
        workspace="",
        timeout=600,
        retry=0,
        switch_on_failure=False,
        wait=False,
        context="",
        model="",
        provider="",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Data contract (§19)
# ---------------------------------------------------------------------------


def test_worker_spec_defaults():
    spec = WorkerSpec(worker_type="opencode", task="do the thing")
    assert spec.workspace == ""
    assert spec.context == ""
    assert spec.constraints == {}
    assert spec.acceptance_criteria == ""
    assert spec.timeout == 600
    assert spec.parent_task_id == ""
    assert spec.environment_policy == "isolated"


def test_worker_execution_defaults():
    execution = WorkerExecution(execution_id="abc", worker_type="pi")
    assert execution.status == PLANNED
    assert execution.started_at == 0.0
    assert execution.updated_at == 0.0
    assert execution.result == ""
    assert execution.error == ""


# ---------------------------------------------------------------------------
# Routing (§13)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("Design the architecture for the payment service", "pi"),
        ("Implement the feature and add it to the codebase", "codex"),
        ("Write tests for the new endpoint", "opencode"),
        ("Review the PR and give feedback", "commandcode"),
        ("Long-horizon deep reasoning about the migration", "dsh"),
    ],
)
def test_route_task_picks_worker_for_task_type(monkeypatch, task, expected):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    assert backend.route_task(task) == expected


def test_route_skips_workers_not_installed(monkeypatch):
    _patch_installed(monkeypatch, ["pi"])
    # "implement" prefers codex/opencode, but only pi is installed.
    assert backend.route_task("Implement the feature") == "pi"


def test_route_default_order_when_no_hint(monkeypatch):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    # No keyword matches → DEFAULT_ORDER (codex first).
    assert backend.route_task("tidy up the workshop") == "codex"


def test_route_default_order_honors_availability(monkeypatch):
    _patch_installed(monkeypatch, ["opencode", "pi"])
    assert backend.route_task("tidy up the workshop") == "opencode"


def test_route_capabilities_filter(monkeypatch):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    # "implement" matches codex first, but codex lacks 'testing'.
    assert backend.route_task("Implement the feature", capabilities_required=["testing"]) == "opencode"


def test_route_capabilities_filter_lenient_when_none_qualify(monkeypatch):
    _patch_installed(monkeypatch, ["codex"])
    # codex doesn't cover 'testing', but it's the only installed worker.
    assert backend.route_task("Implement it", capabilities_required=["testing"]) == "codex"


def test_route_no_workers_raises(monkeypatch):
    _patch_installed(monkeypatch, [])
    with pytest.raises(backend.WorkerBackendError):
        backend.route_task("anything")


def test_rank_workers_returns_best_first(monkeypatch):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    assert backend.rank_workers("Write tests for it") == ["opencode", "codex"]
    assert backend.rank_workers("no hints here") == list(backend.DEFAULT_ORDER)


def test_next_best_worker_after_failure(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    assert backend.next_best_worker("codex", "no hints") == "opencode"
    # Last in the ranking wraps to the first.
    assert backend.next_best_worker("pi", "no hints") == "codex"


def test_custom_worker_can_be_routed(monkeypatch):
    monkeypatch.setattr(workers, "detect_workers", lambda home=None: {})
    monkeypatch.setattr(
        workers, "_read_custom_workers", lambda home=None: {"helper": ["coding", "testing"]}
    )
    assert backend.route_task("Implement and test it") == "helper"


# ---------------------------------------------------------------------------
# Launch commands per worker type
# ---------------------------------------------------------------------------


def test_pi_command_uses_print_flag():
    inst = backend.PiBackend()
    with patch.object(backend, "detect_pi_flag", return_value="-p"):
        assert inst._build_command(WorkerSpec(worker_type="pi", task="hello")) == ["pi", "-p", "hello"]
    with patch.object(backend, "detect_pi_flag", return_value="--print"):
        assert inst._build_command(WorkerSpec(worker_type="pi", task="hello")) == ["pi", "--print", "hello"]


def test_pi_command_falls_back_to_interactive_when_no_flag():
    inst = backend.PiBackend()
    with patch.object(backend, "detect_pi_flag", return_value=None):
        assert inst._build_command(WorkerSpec(worker_type="pi", task="hello")) == ["pi", "hello"]


def test_codex_command_uses_model_provider_and_danger_sandbox():
    inst = backend.CodexBackend()
    spec = WorkerSpec(
        worker_type="codex", task="fix it", constraints={"model": "gpt-x", "provider": "openai"}
    )
    command = inst._build_command(spec)
    assert command == [
        "codex",
        "exec",
        "-c",
        "model_provider=openai",
        "-m",
        "gpt-x",
        "--sandbox",
        "danger-full-access",
        "fix it",
    ]


def test_opencode_command_uses_auto_and_model():
    inst = backend.OpencodeBackend()
    with patch.object(backend, "_worker_config", return_value=("my-model", "")):
        command = inst._build_command(WorkerSpec(worker_type="opencode", task="write tests"))
    assert command == ["opencode", "run", "--auto", "--model", "my-model", "write tests"]


def test_commandcode_command_resolves_nvm_binary():
    inst = backend.CommandCodeBackend()
    resolved = Path("/home/u/.nvm/versions/node/v20/bin/commandcode")
    with patch.object(backend, "resolve_binary", return_value=resolved):
        command = inst._build_command(WorkerSpec(worker_type="commandcode", task="review"))
    assert command == [str(resolved), "-p", "--yolo", "review"]


def test_dsh_command_marks_profile_web():
    inst = backend.DshBackend()
    assert inst._build_command(WorkerSpec(worker_type="dsh", task="deep dive")) == [
        "dsh",
        "--profile",
        "web",
        "deep dive",
    ]


def test_detect_pi_flag_prefers_long_flag():
    backend.reset_pi_flag_cache()
    with patch.object(
        backend.subprocess,
        "run",
        return_value=type("R", (), {"stdout": "usage: pi [-p] [--print]\n", "stderr": ""})(),
    ):
        assert backend.detect_pi_flag() == "--print"
    backend.reset_pi_flag_cache()


def test_worker_config_prefers_constraints_over_config():
    home = workers.custom_workers_path().parent
    (home / "config.yaml").write_text(
        "workers:\n"
        "  opencode:\n"
        "    model: per-worker-model\n"
        "    provider: per-worker-provider\n",
        encoding="utf-8",
    )
    # Explicit CLI overrides win.
    assert backend._worker_config(
        "opencode", {"model": "explicit", "provider": "explicit-p"}, home
    ) == ("explicit", "explicit-p")
    # Per-worker config.yaml block is the default when no constraints.
    assert backend._worker_config("opencode", {}, home) == ("per-worker-model", "per-worker-provider")
    # Other workers fall back to empty (no global model forwarding).
    assert backend._worker_config("codex", {}, home) == ("", "")


def test_worker_config_does_not_forward_global_hermes_model(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n"
        "  model: fal-ai/flux-2/klein/9b\n"
        "  provider: fal-ai\n",
        encoding="utf-8",
    )
    with patch.object(backend.workers, "_parse_mini_yaml", return_value={"model": {"model": "flux", "provider": "fal-ai"}}):
        # The nested global model section must NOT leak into the worker command.
        assert backend._worker_config("opencode", {}, tmp_path) == ("", "")


# ---------------------------------------------------------------------------
# Backend lifecycle (§20) — mocked subprocess.Popen
# ---------------------------------------------------------------------------


def _backend_with_fake(worker_type="opencode", **proc_kwargs):
    proc = FakeProc(**proc_kwargs)
    stack = patch.object(backend.subprocess, "Popen", return_value=proc)
    stack.start()
    return backend.get_backend(worker_type), proc, stack


def test_start_returns_id_and_runs_process():
    inst, proc, stack = _backend_with_fake()
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        assert inst.status(execution_id)["status"] == RUNNING
        assert inst._processes[execution_id] is proc


def test_start_lifecycle_transitions():
    inst, _, stack = _backend_with_fake()
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it", timeout=10))
        execution = inst._executions[execution_id]
        assert execution.status == RUNNING
        assert execution.started_at > 0
        assert execution.updated_at >= execution.started_at


def test_start_spawn_error_fails_execution():
    inst = backend.get_backend("opencode")
    with patch.object(backend.subprocess, "Popen", side_effect=OSError("no binary")):
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        status = inst.status(execution_id)
    assert status["status"] == FAILED
    assert "spawn failed" in status["error"]


def test_start_wait_done_with_result():
    inst, _, stack = _backend_with_fake(out=b"WORKER_OK\n", rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        result = inst.wait(execution_id)
    assert result["status"] == DONE
    assert "WORKER_OK" in result["result"]


def test_read_returns_accumulated_output():
    inst, _, stack = _backend_with_fake(out=b"line one\nline two\n", rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        result = inst.wait(execution_id)
        assert inst.read(execution_id) == "line one\nline two\n"
        # All consumed; incremental read returns nothing new.
        assert inst.read(execution_id, {"mode": "incremental"}) == ""


def test_nonzero_exit_fails_with_error():
    inst, _, stack = _backend_with_fake(out=b"traceback here", rc=3, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        result = inst.wait(execution_id)
    assert result["status"] == FAILED
    assert result["error"] == "exit code 3"
    assert "traceback here" in result["result"]


def test_timeout_fails_and_kills_process():
    inst, proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it", timeout=600))
        result = inst.wait(execution_id, timeout=0.05)
    assert result["status"] == FAILED
    assert result["error"] == "timeout"
    assert proc.terminated is True


def test_stop_cancels_running_execution():
    inst, proc, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        inst.stop(execution_id)
        assert inst.status(execution_id)["status"] == CANCELLED
        assert proc.terminated is True


def test_resume_blocked_execution():
    inst, _, stack = _backend_with_fake(rc=0, delay=1_000_000)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        inst._executions[execution_id].status = "BLOCKED"
        inst.resume(execution_id)
        assert inst.status(execution_id)["status"] == RUNNING


def test_send_writes_to_process_stdin():
    inst, _, stack = _backend_with_fake(rc=0, delay=1_000_000, stdin_enabled=True)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="pi", task="interactive"))
        inst.send(execution_id, "continue")
        written = inst._processes[execution_id].stdin.getvalue()
    assert written == b"continue\n"


def test_unknown_execution_raises_keyerror():
    inst = backend.get_backend("opencode")
    for method in (inst.status, inst.wait, inst.stop, inst.resume):
        with pytest.raises(KeyError):
            method("does-not-exist")


def test_wait_custom_condition():
    inst, _, stack = _backend_with_fake(rc=0, delay=1)
    with stack:
        execution_id = inst.start(WorkerSpec(worker_type="opencode", task="do it"))
        # Wait for exactly RUNNING — returns before the process completes.
        running = inst.wait(execution_id, condition={RUNNING}, timeout=1.0)
    assert running["status"] == RUNNING


# ---------------------------------------------------------------------------
# CLI handler (`hermes workers run`)
# ---------------------------------------------------------------------------


def test_cli_run_routes_and_prints_execution_id(capsys):
    scripted = ScriptedBackend([DONE])
    with patch.object(backend, "route_task", return_value="opencode"), patch.object(
        backend, "rank_workers", return_value=["opencode", "codex"]
    ), patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write tests"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "Routed to worker 'opencode'" in out
    assert "execution_id: exec-1" in out
    assert len(scripted.started) == 1


def test_cli_run_wait_prints_result(capsys):
    scripted = ScriptedBackend([DONE])
    with patch.object(backend, "route_task", return_value="opencode"), patch.object(
        backend, "rank_workers", return_value=["opencode"]
    ), patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write tests", wait=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "[opencode] DONE" in out
    assert "--- result ---" in out
    assert "some output" in out


def test_cli_run_retry_on_failure(capsys):
    scripted = ScriptedBackend([FAILED, DONE])
    with patch.object(backend, "route_task", return_value="opencode"), patch.object(
        backend, "rank_workers", return_value=["opencode"]
    ), patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write tests", wait=True, retry=1))

    captured = capsys.readouterr()
    assert rc == 0
    assert len(scripted.started) == 2
    assert "[retry 2/2]" in captured.out
    assert "boom" in captured.err


def test_cli_run_switch_on_failure_routes_next_best(capsys, monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    scripted = ScriptedBackend([FAILED, DONE])
    with patch.object(backend, "get_backend", return_value=scripted), patch.object(
        backend, "resolve_binary", return_value=Path("/fake/bin/codex")
    ):
        rc = backend.run_workers_cli_command(
            _cli_args(task="no hints", worker="codex", wait=True, retry=1, switch_on_failure=True)
        )

    captured = capsys.readouterr()
    assert rc == 0
    assert len(scripted.started) == 2
    assert scripted.started[0][1].worker_type == "codex"
    assert scripted.started[1][1].worker_type == "opencode"
    assert "switched to worker 'opencode'" in captured.out


def test_cli_run_exhausts_retries_and_fails(capsys):
    scripted = ScriptedBackend([FAILED, FAILED])
    with patch.object(backend, "route_task", return_value="opencode"), patch.object(
        backend, "rank_workers", return_value=["opencode"]
    ), patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write tests", wait=True, retry=1))

    captured = capsys.readouterr()
    assert rc == 1
    assert len(scripted.started) == 2
    assert "FAILED" in captured.err


def test_cli_run_empty_task_rejected(capsys):
    assert backend.run_workers_cli_command(_cli_args(task="")) == 1
    assert "task must not be empty" in capsys.readouterr().err


def test_cli_run_unknown_worker_rejected(capsys):
    rc = backend.run_workers_cli_command(_cli_args(worker="not-a-worker"))
    assert rc == 1
    assert "unknown worker type" in capsys.readouterr().err


def test_cli_run_worker_not_installed_rejected(capsys):
    with patch.object(backend, "resolve_binary", return_value=None):
        rc = backend.run_workers_cli_command(_cli_args(worker="codex"))
    assert rc == 1
    assert "not installed" in capsys.readouterr().err


def test_cli_run_no_workers_routed(capsys, monkeypatch):
    _patch_installed(monkeypatch, [])
    rc = backend.run_workers_cli_command(_cli_args(task="write tests"))
    assert rc == 1
    assert "no worker harness installed" in capsys.readouterr().err


def test_workers_run_dispatch_empty_task(capsys):
    rc = workers.run_workers_command(SimpleNamespace(workers_action="run", task=""))
    assert rc == 1
    assert "task must not be empty" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


def test_run_subcommand_parser():
    root = __import__("argparse").ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    args = root.parse_args(
        [
            "workers",
            "run",
            "Implement the feature",
            "--worker",
            "opencode",
            "--capabilities",
            "coding",
            "testing",
            "--workspace",
            "/tmp/ws",
            "--wait",
            "--timeout",
            "30",
            "--retry",
            "2",
            "--switch-on-failure",
            "--context",
            "handoff",
            "--model",
            "m1",
            "--provider",
            "p1",
        ]
    )
    assert args.workers_action == "run"
    assert args.task == "Implement the feature"
    assert args.worker == "opencode"
    assert args.capabilities == ["coding", "testing"]
    assert args.workspace == "/tmp/ws"
    assert args.wait is True
    assert args.timeout == 30
    assert args.retry == 2
    assert args.switch_on_failure is True
    assert args.context == "handoff"
    assert args.model == "m1"
    assert args.provider == "p1"
    assert callable(args.func)


def test_run_subcommand_defaults():
    root = __import__("argparse").ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)
    args = root.parse_args(["workers", "run", "a task"])
    assert args.wait is False
    assert args.timeout == 600
    assert args.retry == 0
    assert args.switch_on_failure is False
    assert args.workspace == ""
