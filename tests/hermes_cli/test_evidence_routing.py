"""Tests for evidence-driven worker routing (§13).

Covers the benchmark results store (``save_results`` / ``load_results`` /
merge semantics), the evidence-aware ``route_task`` scorer (category
inference, +2 PASS / −1 FAIL / +0.5 other-category PASS / capability bonus,
latency tie-break), the capability-hint fallback when no evidence exists, the
``hermes workers route`` CLI handler (+ ``--json`` shape), and the fact that
``hermes workers benchmark`` / ``hermes workers run`` wire the store in.
``worker_backend.run_task`` is mocked throughout so no harness ever runs.
"""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli import benchmark as bench
from hermes_cli import worker_backend as backend
from hermes_cli import workers
from hermes_cli.subcommands.workers import build_workers_parser
from hermes_constants import get_hermes_home

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRunner:
    """Scripted ``worker_backend.run_task``: pops the next final dict."""

    def __init__(self, finals=None):
        self.finals = list(finals or [])
        self.calls: list[backend.WorkerSpec] = []

    def __call__(self, spec):
        self.calls.append(spec)
        if not self.finals:
            return {"status": backend.DONE, "result": ""}
        final = dict(self.finals.pop(0))
        final.setdefault("result", "")
        return final


class ScriptedBackend:
    """CLI-test backend: ``start()`` returns an immediate terminal status."""

    def __init__(self, final_statuses):
        self.final_statuses = list(final_statuses)
        self.started: list[tuple[str, backend.WorkerSpec]] = []

    def start(self, spec):
        execution_id = f"exec-{len(self.started) + 1}"
        self.started.append((execution_id, spec))
        status = self.final_statuses.pop(0) if self.final_statuses else backend.DONE
        return execution_id

    def status(self, execution_id):
        status = self.final_statuses.pop(0) if self.final_statuses else backend.DONE
        return {
            "execution_id": execution_id,
            "worker_type": self.started[-1][1].worker_type,
            "status": status,
            "result": "",
            "error": "" if status == backend.DONE else "boom",
        }


def _patch_runner(monkeypatch, finals=None) -> FakeRunner:
    runner = FakeRunner(finals)
    monkeypatch.setattr(backend, "run_task", runner)
    return runner


def _patch_installed(monkeypatch, names, custom=None):
    """Pretend exactly ``names`` are detected+registered workers."""
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
    monkeypatch.setattr(
        workers, "_read_custom_workers", lambda home=None: dict(custom or {})
    )


def _evidence(
    worker: str,
    category: str,
    task: str,
    passed: bool,
    latency: float = 10.0,
) -> dict:
    return {
        "worker": worker,
        "category": category,
        "task": task,
        "pass": passed,
        "latency_s": latency,
        "tokens": None,
        "failure_mode": None if passed else "error",
    }


def _cli_args(**overrides):
    defaults = dict(task="do something", capabilities=None, json=False)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Category inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("write a function and test it", "coding"),
        ("add unit tests for the endpoint", "coding"),
        ("review this PR and give feedback", "review"),
        ("explain the architecture of this project", "repository_understanding"),
        ("understand how the module fits together", "repository_understanding"),
        ("fix the bug so the test passes", "recovery"),
        ("long-horizon migration in multiple steps", "long_horizon"),
        ("drive several shell tool calls", "tool_heavy"),
        ("create three cooperating modules", "multi_file"),
        ("read the long document and answer", "context_heavy"),
        ("tidy up the workshop", "coding"),
    ],
)
def test_infer_evidence_category(task, expected):
    assert backend.infer_evidence_category(task) == expected


def test_infer_evidence_category_is_empty_safe():
    assert backend.infer_evidence_category("") == "coding"
    assert backend.infer_evidence_category(None) == "coding"


# ---------------------------------------------------------------------------
# Evidence store (§13)
# ---------------------------------------------------------------------------


def test_save_load_results_roundtrip(tmp_path):
    path = tmp_path / "store.json"
    results = [
        _evidence("opencode", "coding", "B/is_prime", True, 13.0),
        _evidence("codex", "recovery", "D/fix_is_even", False, 9.0),
    ]

    written = bench.save_results(results, path=path)

    assert written == path
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "generated_at" in payload
    assert payload["results"] == results
    assert bench.load_results(path=path) == results


def test_load_results_missing_file_returns_empty(tmp_path):
    assert bench.load_results(path=tmp_path / "nope.json") == []


def test_load_results_tolerates_garbage(tmp_path):
    path = tmp_path / "store.json"
    path.write_text("not json {", encoding="utf-8")
    assert bench.load_results(path=path) == []


def test_save_results_merges_per_worker_task(tmp_path):
    path = tmp_path / "store.json"
    bench.save_results([_evidence("opencode", "coding", "B/is_prime", True, 10.0)], path=path)
    bench.save_results(
        [
            _evidence("opencode", "coding", "B/is_prime", False, 20.0),
            _evidence("codex", "coding", "B/is_prime", True, 5.0),
        ],
        path=path,
    )

    loaded = bench.load_results(path=path)
    assert len(loaded) == 2
    opencode = [r for r in loaded if r["worker"] == "opencode"][0]
    assert opencode["pass"] is False  # the newer record wins


def test_evidence_record_normalizes_benchmark_result():
    record = bench._evidence_record(
        {
            "worker": "opencode",
            "category": "B",
            "task": "is_prime",
            "completed": True,
            "correct": True,
            "tokens": 1234,
            "latency": 13.0,
            "failure": None,
        }
    )
    assert record == {
        "worker": "opencode",
        "category": "coding",
        "task": "B/is_prime",
        "pass": True,
        "latency_s": 13.0,
        "tokens": 1234,
        "failure_mode": None,
    }


def test_evidence_record_is_idempotent():
    record = _evidence("opencode", "coding", "B/is_prime", True, 13.0)
    assert bench._evidence_record(record) == record


# ---------------------------------------------------------------------------
# Evidence-aware routing
# ---------------------------------------------------------------------------


def test_pass_evidence_outranks_no_evidence(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    evidence = [_evidence("opencode", "coding", "B/is_prime", True, 12.0)]

    assert backend.route_task("write a function and test it", evidence=evidence) == "opencode"


def test_fail_penalizes(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    evidence = [
        _evidence("opencode", "coding", "B/is_prime", False, 12.0),
        _evidence("codex", "coding", "B/is_prime", True, 15.0),
    ]

    assert backend.route_task("write a function and test it", evidence=evidence) == "codex"


def test_latency_breaks_score_ties(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode"])
    evidence = [
        _evidence("opencode", "coding", "B/is_prime", True, 20.0),
        _evidence("codex", "coding", "B/is_prime", True, 5.0),
    ]

    # Both score +2 (pass) +1 (coding capability) = 3.0; codex wins on latency.
    assert backend.route_task("write a function and test it", evidence=evidence) == "codex"


def test_other_category_pass_counts_as_reliability(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    evidence = [
        # opencode has no category evidence, but a pass elsewhere (+0.5).
        _evidence("opencode", "recovery", "D/fix_is_even", True, 8.0),
    ]

    scored = backend.score_workers("write a function and test it", evidence=evidence)
    opencode = next(e for e in scored if e["worker"] == "opencode")
    # 0 (category) + 0.5 (other pass) + 1 (coding capability) = 1.5
    assert opencode["score"] == 1.5
    assert opencode["evidence"]["other_passes"] == 1


def test_capability_bonus_breaks_flat_no_evidence_ties(monkeypatch):
    # No evidence at all → route_task falls back to capability routing; but the
    # scorer still surfaces the capability bonus for display.
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    scored = backend.score_workers("write a function and test it", evidence=[])
    assert scored[0]["worker"] == "opencode"  # capability-hint order (test rule)
    assert all(e["score"] == 1.0 for e in scored)  # every worker has "coding"


def test_no_evidence_falls_back_to_capability_routing(monkeypatch):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    # evidence=None and evidence=[] both preserve the pre-evidence behavior.
    assert backend.route_task("Design the architecture for the payment service") == "pi"
    assert backend.route_task("Design the architecture", evidence=[]) == "pi"
    assert backend.route_task("Write tests for the endpoint", evidence=[]) == "opencode"


def test_evidence_only_for_other_worker_ignored_for_decision(monkeypatch):
    _patch_installed(monkeypatch, ["codex", "opencode"])
    # Evidence only names a worker that is not installed → no evidence applies,
    # so routing falls back to capability hints (test rule → opencode).
    evidence = [_evidence("ghost", "coding", "B/is_prime", True, 1.0)]
    assert backend.route_task("write tests", evidence=evidence) == "opencode"


def test_route_task_no_workers_raises(monkeypatch):
    _patch_installed(monkeypatch, [])
    with pytest.raises(backend.WorkerBackendError):
        backend.route_task("anything", evidence=[])


def test_score_workers_empty_with_no_installed(monkeypatch):
    _patch_installed(monkeypatch, [])
    assert backend.score_workers("anything", evidence=[]) == []


# ---------------------------------------------------------------------------
# `hermes workers route` CLI
# ---------------------------------------------------------------------------


def test_route_cli_json_shape(monkeypatch, capsys):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    store = bench.benchmark_results_path()
    store.write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "results": [_evidence("opencode", "coding", "B/is_prime", True, 13.0)],
            }
        ),
        encoding="utf-8",
    )

    rc = backend.run_workers_route_command(
        _cli_args(task="write a python function and test it", json=True)
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"] == "write a python function and test it"
    assert payload["category"] == "coding"
    assert payload["chosen"] == "opencode"
    assert payload["evidence_used"] is True
    assert payload["evidence_count"] == 1
    assert payload["evidence_file"] == str(store)
    top = payload["workers"][0]
    assert top["worker"] == "opencode"
    assert top["score"] == 3.0
    assert top["latency"] == 13.0
    assert top["evidence"] == {"passes": 1, "fails": 0, "other_passes": 0}


def test_route_cli_table_output(monkeypatch, capsys):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    rc = backend.run_workers_route_command(_cli_args(task="write tests"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "category:    coding" in out
    assert "evidence:    none" in out
    assert "opencode" in out
    assert "chosen: opencode" in out


def test_route_cli_empty_task_rejected(capsys):
    assert backend.run_workers_route_command(_cli_args(task="")) == 1
    assert "task must not be empty" in capsys.readouterr().err


def test_route_cli_no_workers_installed(monkeypatch, capsys):
    _patch_installed(monkeypatch, [])
    assert backend.run_workers_route_command(_cli_args(task="do it")) == 1
    assert "no worker harness installed" in capsys.readouterr().err


def test_workers_dispatch_routes_route_action(monkeypatch):
    dispatched = {}

    def fake_command(args):
        dispatched["args"] = args
        return 7

    monkeypatch.setattr(backend, "run_workers_route_command", fake_command)
    rc = workers.run_workers_command(
        SimpleNamespace(workers_action="route", task="do it", capabilities=None, json=False)
    )
    assert rc == 7
    assert dispatched["args"].workers_action == "route"


def test_route_subcommand_parser():
    root = argparse.ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    args = root.parse_args(
        ["workers", "route", "write tests", "--capabilities", "coding", "--json"]
    )
    assert args.workers_action == "route"
    assert args.task == "write tests"
    assert args.capabilities == ["coding"]
    assert args.json is True
    assert callable(args.func)

    defaults = root.parse_args(["workers", "route", "a task"])
    assert defaults.capabilities is None
    assert defaults.json is False


# ---------------------------------------------------------------------------
# Benchmark / run wiring (§13)
# ---------------------------------------------------------------------------


def _benchmark_cli_args(**overrides):
    defaults = dict(worker="", category="", json=False, out="", timeout=120)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_run_benchmark_command_writes_evidence_store(monkeypatch, capsys):
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "passed"}] * 2)
    _patch_installed(monkeypatch, ["opencode"])

    rc = bench.run_benchmark_command(
        _benchmark_cli_args(worker="opencode", category="coding")
    )

    assert rc == 0
    store = bench.benchmark_results_path()
    assert store.is_file()
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert len(payload["results"]) == 2
    assert payload["results"][0]["worker"] == "opencode"
    assert payload["results"][0]["category"] == "coding"
    assert payload["results"][0]["task"] == "B/is_prime"
    assert payload["results"][0]["pass"] is True


def test_run_benchmark_command_out_file_also_updates_store(monkeypatch, tmp_path):
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "passed"}] * 2)
    _patch_installed(monkeypatch, ["opencode"])
    out_file = tmp_path / "bench.json"

    rc = bench.run_benchmark_command(
        _benchmark_cli_args(worker="opencode", category="coding", out=str(out_file))
    )

    assert rc == 0
    assert out_file.is_file()
    assert bench.benchmark_results_path().is_file()


def test_cli_run_uses_evidence_store_when_present(monkeypatch, capsys):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    store = bench.benchmark_results_path()
    store.write_text(
        json.dumps(
            {
                "generated_at": "2026-01-01T00:00:00Z",
                "results": [_evidence("opencode", "coding", "B/is_prime", True, 13.0)],
            }
        ),
        encoding="utf-8",
    )
    scripted = ScriptedBackend([backend.DONE])

    with patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write a function and test it"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "[evidence] routing" in out
    assert "Routed to worker 'opencode'" in out
    assert len(scripted.started) == 1
    assert scripted.started[0][1].worker_type == "opencode"


def test_cli_run_falls_back_without_evidence_store(monkeypatch, capsys):
    _patch_installed(monkeypatch, ["codex", "opencode", "pi"])
    scripted = ScriptedBackend([backend.DONE])

    with patch.object(backend, "get_backend", return_value=scripted):
        rc = backend.run_workers_cli_command(_cli_args(task="write tests"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "[evidence] routing" not in out
    assert "Routed to worker 'opencode'" in out


def test_cli_run_routing_path_does_not_require_resolve_binary(monkeypatch, capsys):
    # Routing resolves the worker from evidence/capability hints; the
    # explicit `--worker` path's resolve_binary check must not run here.
    _patch_installed(monkeypatch, ["opencode"])
    scripted = ScriptedBackend([backend.DONE])

    with patch.object(backend, "get_backend", return_value=scripted), patch.object(
        backend, "resolve_binary", return_value=None
    ):
        rc = backend.run_workers_cli_command(_cli_args(task="write a function and test it"))

    assert rc == 0
