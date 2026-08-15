"""Tests for ``hermes_cli/benchmark`` — worker benchmark suite (§24, §25).

Covers the category → task mapping, metric collection (completion /
correctness / tokens / latency / failure mode), pass-fail detection against
the expected marker, timeout and error handling, JSON / file output, worker
selection (default fleet skips dsh; explicit selection validated), and the
``hermes workers benchmark`` CLI parser + dispatch. ``worker_backend.run_task``
is mocked throughout so no harness ever runs.
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


def _patch_runner(monkeypatch, finals=None) -> FakeRunner:
    runner = FakeRunner(finals)
    monkeypatch.setattr(backend, "run_task", runner)
    return runner


def _patch_installed(monkeypatch, names):
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
    monkeypatch.setattr(workers, "_read_custom_workers", lambda home=None: {})


def _task(category_key: str, name: str) -> bench.BenchmarkTask:
    for cat in bench.CATEGORIES:
        if cat.key == category_key:
            for task in cat.tasks:
                if task.name == name:
                    return task
    raise AssertionError(f"no task {category_key}/{name}")


def _cli_args(**overrides):
    defaults = dict(worker="", category="", json=False, out="", timeout=120)
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Categories (§24)
# ---------------------------------------------------------------------------


def test_categories_cover_a_to_g():
    assert [cat.key for cat in bench.CATEGORIES] == list("ABCDEFG")
    for cat in bench.CATEGORIES:
        assert cat.name
        assert 1 <= len(cat.tasks) <= 3
        for task in cat.tasks:
            assert task.prompt
            assert task.markers
            assert task.values
            assert task.category == cat.key
            assert task.timeout > 0


def test_each_task_is_self_verifying_via_prompt():
    # The expected values must be derivable from the prompt + fixtures alone,
    # so tasks never depend on the internet or an external repo. Arithmetic
    # tasks compute their expected value from the prompt's own numbers.
    computed = {
        "C/sum_of_data": {str(sum(range(1, 11)))},
        "F/three_commands": {"4", "12", "7"},
        "G/add_and_mul": {str(2 + 3 + 4 * 5)},
    }
    for cat in bench.CATEGORIES:
        for task in cat.tasks:
            key = f"{cat.key}/{task.name}"
            for value in task.values:
                if value.lower() in task.prompt.lower():
                    continue
                if any(value.lower() in content.lower() for _, content in task.fixtures):
                    continue
                assert key in computed and value in computed[key], (
                    f"{key}: value {value!r} not derivable from prompt/fixtures"
                )


def test_context_task_marker_only_in_final_paragraph():
    task = _task("E", "last_paragraph")
    doc = task.prompt.split("Read the entire document above.")[0]
    sections, final = doc.split("Final paragraph.")
    assert "zephyros" not in sections.lower()
    assert "zephyros" in final.lower()


# ---------------------------------------------------------------------------
# Category → task mapping
# ---------------------------------------------------------------------------


def test_resolve_categories_all_when_empty():
    assert bench.resolve_categories("") == list(bench.CATEGORIES)
    assert bench.resolve_categories(None) == list(bench.CATEGORIES)


def test_resolve_categories_by_key_and_name():
    assert [c.key for c in bench.resolve_categories("B")] == ["B"]
    assert [c.key for c in bench.resolve_categories("coding")] == ["B"]
    assert [c.key for c in bench.resolve_categories("coding,recovery")] == ["B", "D"]
    assert [c.key for c in bench.resolve_categories("a,d,g")] == ["A", "D", "G"]


def test_resolve_categories_aliases_and_dedup():
    assert [c.key for c in bench.resolve_categories("context,tool")] == ["E", "F"]
    assert [c.key for c in bench.resolve_categories("coding,b")] == ["B"]


def test_resolve_categories_unknown_raises():
    with pytest.raises(ValueError):
        bench.resolve_categories("xyz")


# ---------------------------------------------------------------------------
# Metric collection (§25)
# ---------------------------------------------------------------------------


def test_metric_collection_records_all_fields(monkeypatch):
    task = _task("B", "is_prime")
    _patch_runner(
        monkeypatch,
        [
            {
                "status": backend.DONE,
                "result": "created solution.py\nran tests\n1 passed in 0.1s\n"
                "tokens used: 1,234",
            }
        ],
    )
    record = bench.run_task_benchmark("opencode", task)

    assert record["worker"] == "opencode"
    assert record["category"] == "B"
    assert record["task"] == "is_prime"
    assert record["status"] == backend.DONE
    assert record["completed"] is True
    assert record["correct"] is True
    assert record["tokens"] == 1234
    assert record["failure"] is None
    assert record["latency"] >= 0
    assert "1 passed" in record["output"]


def test_pass_detection_requires_marker(monkeypatch):
    task = _task("B", "is_prime")
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "all good"}])
    record = bench.run_task_benchmark("opencode", task)
    assert record["completed"] is False
    assert record["correct"] is False
    assert record["failure"] == "wrong-output"


def test_marker_match_is_case_insensitive(monkeypatch):
    task = _task("E", "last_paragraph")
    _patch_runner(
        monkeypatch,
        [{"status": backend.DONE, "result": "the observatory at ZEPHYROS hosts the chronometer"}],
    )
    record = bench.run_task_benchmark("opencode", task)
    assert record["completed"] is True
    assert record["correct"] is True


def test_timeout_handling(monkeypatch):
    task = _task("B", "is_prime")
    _patch_runner(monkeypatch, [{"status": backend.FAILED, "error": "timeout", "result": ""}])
    record = bench.run_task_benchmark("opencode", task)
    assert record["completed"] is False
    assert record["failure"] == "timeout"


def test_error_handling(monkeypatch):
    task = _task("B", "is_prime")
    _patch_runner(
        monkeypatch, [{"status": backend.FAILED, "error": "exit code 1", "result": "traceback"}]
    )
    record = bench.run_task_benchmark("opencode", task)
    assert record["completed"] is False
    assert record["failure"] == "error"
    assert record["status"] == backend.FAILED


def test_tokens_fallback_to_harness_field(monkeypatch):
    task = _task("B", "is_prime")
    _patch_runner(
        monkeypatch,
        [{"status": backend.DONE, "result": "1 passed", "tokens": 4321}],
    )
    record = bench.run_task_benchmark("opencode", task)
    assert record["tokens"] == 4321


def test_extract_tokens_parses_variants():
    assert bench._extract_tokens("tokens used: 1,234") == 1234
    assert bench._extract_tokens("Tokens consumed = 99") == 99
    assert bench._extract_tokens("tokens: 42") == 42
    assert bench._extract_tokens("nothing to see here") is None
    assert bench._extract_tokens("") is None


# ---------------------------------------------------------------------------
# Workspace + run_task wiring
# ---------------------------------------------------------------------------


def test_run_task_benchmark_builds_spec_and_workspace(monkeypatch, tmp_path):
    task = _task("A", "summarize_project")
    runner = _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "Acme Weather"}])
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Acme Weather\n", encoding="utf-8")

    record = bench.run_task_benchmark("opencode", task, workspace=workspace)

    assert runner.calls[0].worker_type == "opencode"
    assert runner.calls[0].timeout == task.timeout
    assert runner.calls[0].workspace == str(workspace)
    assert record["completed"] is True


def test_run_task_benchmark_writes_fixtures_into_temp_workspace(monkeypatch):
    task = _task("A", "summarize_project")
    runner = _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "Acme Weather"}])

    bench.run_task_benchmark("opencode", task)

    workspace = Path(runner.calls[0].workspace)
    assert workspace.is_dir()
    assert workspace.name.startswith("hermes-bench-")
    assert (workspace / "README.md").is_file()
    assert (workspace / "weather.py").is_file()
    assert (workspace / "forecast.py").is_file()
    assert "Acme Weather" in (workspace / "README.md").read_text(encoding="utf-8")


def test_run_task_benchmark_forwards_timeout_override(monkeypatch):
    task = _task("B", "is_prime")
    runner = _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "passed"}])
    bench.run_task_benchmark("opencode", task, timeout=30)
    assert runner.calls[0].timeout == 30


def test_opencode_backend_passes_workspace_via_dir():
    # opencode resolves its own working directory from the tty/session, not the
    # spawned process cwd — the workspace must be threaded through `--dir` or
    # benchmark tasks would run (and write files) in the parent's directory.
    harness = backend.OpencodeBackend()
    with_dir = harness._build_command(
        backend.WorkerSpec(worker_type="opencode", task="do it", workspace="/tmp/ws")
    )
    assert with_dir[0] == "opencode"
    assert "--dir" in with_dir
    assert with_dir[with_dir.index("--dir") + 1] == "/tmp/ws"
    assert with_dir[-1] == "do it"

    no_workspace = harness._build_command(
        backend.WorkerSpec(worker_type="opencode", task="do it")
    )
    assert "--dir" not in no_workspace


def test_run_benchmarks_covers_workers_x_categories(monkeypatch):
    _patch_runner(monkeypatch)
    results = bench.run_benchmarks(["pi", "opencode"], bench.resolve_categories("B,D"))
    pairs = {(r["worker"], r["category"]) for r in results}
    assert pairs == {("pi", "B"), ("pi", "D"), ("opencode", "B"), ("opencode", "D")}


# ---------------------------------------------------------------------------
# Worker selection
# ---------------------------------------------------------------------------


def test_default_workers_skip_dsh(monkeypatch):
    _patch_installed(monkeypatch, ["pi", "codex", "opencode", "commandcode", "dsh"])
    assert bench.resolve_workers("", get_hermes_home()) == [
        "pi",
        "codex",
        "opencode",
        "commandcode",
    ]


def test_default_workers_include_custom(monkeypatch):
    _patch_installed(monkeypatch, ["opencode"])
    monkeypatch.setattr(workers, "_read_custom_workers", lambda home=None: {"myworker": ["coding"]})
    assert bench.resolve_workers("", get_hermes_home()) == ["opencode", "myworker"]


def test_explicit_workers_preserve_order_and_allow_dsh(monkeypatch):
    _patch_installed(monkeypatch, [])
    with patch.object(backend, "resolve_binary", return_value=Path("/fake/bin/opencode")):
        assert bench.resolve_workers("opencode,dsh", get_hermes_home()) == ["opencode", "dsh"]


def test_explicit_unknown_worker_raises(monkeypatch):
    _patch_installed(monkeypatch, [])
    with pytest.raises(ValueError):
        bench.resolve_workers("not-a-worker", get_hermes_home())


def test_explicit_worker_not_installed_raises(monkeypatch):
    _patch_installed(monkeypatch, [])
    with patch.object(backend, "resolve_binary", return_value=None):
        with pytest.raises(ValueError):
            bench.resolve_workers("codex", get_hermes_home())


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def test_run_benchmark_command_table(monkeypatch, capsys):
    _patch_runner(
        monkeypatch,
        [{"status": backend.DONE, "result": "1 passed"}]
        * 2,  # coding has 2 tasks, worker opencode only
    )
    _patch_installed(monkeypatch, ["opencode"])

    rc = bench.run_benchmark_command(_cli_args(worker="opencode", category="coding"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "opencode" in out
    assert "CODING" in out
    assert "PASS" in out
    assert "Result: all 2 tasks passed" in out


def test_run_benchmark_command_reports_failure(monkeypatch, capsys):
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "no marker here"}])
    _patch_installed(monkeypatch, ["opencode"])

    rc = bench.run_benchmark_command(_cli_args(worker="opencode", category="F"))

    out = capsys.readouterr().out
    assert rc == 0
    assert "FAIL" in out
    assert "wrong-output" in out
    assert "Result: 0/1 tasks passed" in out


def test_run_benchmark_command_json(monkeypatch, capsys):
    _patch_runner(
        monkeypatch, [{"status": backend.DONE, "result": "2+2=4, 3*4=12, 10-3=7"}]
    )
    _patch_installed(monkeypatch, ["opencode"])

    rc = bench.run_benchmark_command(_cli_args(worker="opencode", category="F", json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "hermes workers benchmark"
    assert payload["workers"] == ["opencode"]
    assert payload["categories"][0]["key"] == "F"
    assert payload["summary"]["opencode"]["categories"]["F"]["pass"] is True
    assert payload["results"][0]["completed"] is True
    assert payload["results"][0]["tokens"] is None


def test_run_benchmark_command_out_writes_file(monkeypatch, tmp_path):
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "passed"}])
    _patch_installed(monkeypatch, ["opencode"])
    out_file = tmp_path / "bench.json"

    rc = bench.run_benchmark_command(
        _cli_args(worker="opencode", category="coding", out=str(out_file))
    )

    assert rc == 0
    assert out_file.is_file()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["results"][0]["worker"] == "opencode"


def test_run_benchmark_command_no_workers_installed(monkeypatch, capsys):
    _patch_installed(monkeypatch, [])
    rc = bench.run_benchmark_command(_cli_args(worker="", category=""))
    assert rc == 1
    assert "no worker harness installed" in capsys.readouterr().err


def test_run_benchmark_command_unknown_category(monkeypatch, capsys):
    _patch_installed(monkeypatch, ["opencode"])
    rc = bench.run_benchmark_command(_cli_args(worker="opencode", category="xyz"))
    assert rc == 1
    assert "unknown benchmark category" in capsys.readouterr().err


def test_run_benchmark_command_bad_out_path(monkeypatch, capsys, tmp_path):
    _patch_runner(monkeypatch, [{"status": backend.DONE, "result": "passed"}])
    _patch_installed(monkeypatch, ["opencode"])
    bad = tmp_path / "missing" / "bench.json"

    rc = bench.run_benchmark_command(_cli_args(worker="opencode", category="coding", out=str(bad)))

    assert rc == 1
    assert "cannot write" in capsys.readouterr().err


def test_aggregate_tracks_partial_failures():
    results = [
        {"worker": "opencode", "category": "B", "latency": 1.0, "tokens": 10,
         "completed": True, "correct": True},
        {"worker": "opencode", "category": "B", "latency": 2.0, "tokens": 5,
         "completed": False, "correct": False},
    ]
    agg = bench._aggregate(results)
    entry = agg["opencode"]["B"]
    assert entry["pass"] is False
    assert entry["passed"] == 1
    assert entry["tasks"] == 2
    assert entry["latency"] == 3.0
    assert entry["tokens"] == 15


# ---------------------------------------------------------------------------
# Parser registration + dispatch
# ---------------------------------------------------------------------------


def test_benchmark_subcommand_parser():
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    args = parser.parse_args(
        [
            "workers",
            "benchmark",
            "--worker",
            "pi,opencode",
            "--category",
            "coding,recovery",
            "--json",
            "--out",
            "bench.json",
            "--timeout",
            "30",
        ]
    )
    assert args.workers_action == "benchmark"
    assert args.worker == "pi,opencode"
    assert args.category == "coding,recovery"
    assert args.json is True
    assert args.out == "bench.json"
    assert args.timeout == 30
    assert callable(args.func)


def test_benchmark_subcommand_defaults():
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    args = parser.parse_args(["workers", "benchmark"])
    assert args.workers_action == "benchmark"
    assert args.worker == ""
    assert args.category == ""
    assert args.json is False
    assert args.out == ""
    assert args.timeout == 120


def test_workers_dispatch_routes_benchmark(monkeypatch, capsys):
    dispatched = {}

    def fake_command(args):
        dispatched["args"] = args
        return 7

    monkeypatch.setattr(bench, "run_benchmark_command", fake_command)
    rc = workers.run_workers_command(
        SimpleNamespace(workers_action="benchmark", worker="", category="", json=False,
                        out="", timeout=120)
    )
    assert rc == 7
    assert dispatched["args"].workers_action == "benchmark"
