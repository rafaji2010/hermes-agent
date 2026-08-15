"""Tests for ``hermes workers`` — the worker capability registry.

Covers detection (mocked ``shutil.which`` / ``subprocess.run``), version
extraction, the builtin capability map, Herdr integration detection, and
custom-worker add/remove round-tripping through ``<HERMES_HOME>/workers.yaml``.
"""

import argparse
import contextlib
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_cli import workers
from hermes_cli.subcommands.workers import build_workers_parser
from hermes_constants import get_hermes_home

_FAKE_BIN = Path("/usr/local/bin")


class _FakeResult:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


def _hermetic_detection(tmp_path, versions):
    """Patch ``shutil.which`` + ``subprocess.run`` + the ``~/`` expansion so
    detection is fully isolated from the real machine.

    ``versions`` maps harness name -> --version stdout. Every harness resolves
    to ``<FAKE_BIN>/<name>`` and ``~/...`` paths land under ``tmp_path``.
    """
    stack = contextlib.ExitStack()

    def fake_run(cmd, **kwargs):
        return _FakeResult(stdout=versions.get(Path(cmd[0]).name, ""))

    stack.enter_context(
        patch.object(shutil, "which", side_effect=lambda n: str(_FAKE_BIN / n))
    )
    stack.enter_context(patch.object(subprocess, "run", side_effect=fake_run))
    stack.enter_context(
        patch.object(workers, "_expand", side_effect=lambda p: tmp_path / p.lstrip("~/"))
    )
    return stack


def _all_versions():
    return {
        "pi": "0.84.2",
        "codex": "codex-cli 0.147.0",
        "opencode": "1.18.18",
        "commandcode": "1.26.0",
        "dsh": "0.1.0-rc.6",
        "herdr": "herdr 0.8.0",
    }


# ---------------------------------------------------------------------------
# Capability map
# ---------------------------------------------------------------------------

def test_builtin_capability_map_matches_doc():
    expected = {
        "pi": ["coding", "exploration", "reasoning", "autonomous_tasks"],
        "codex": ["coding", "repository_reasoning", "implementation"],
        "opencode": ["coding", "testing", "implementation"],
        "commandcode": ["coding", "review"],
        "dsh": ["deep_reasoning", "long_horizon", "coding", "experimental"],
    }
    for name, caps in expected.items():
        assert name in workers.BUILTIN_WORKERS
        assert workers.BUILTIN_WORKERS[name]["capabilities"] == caps


def test_commandcode_and_dsh_have_no_herdr_state_file():
    assert workers.BUILTIN_WORKERS["commandcode"]["herdr"] is False
    assert workers.BUILTIN_WORKERS["dsh"]["herdr"] is False
    assert "commandcode" not in workers.HERDR_STATE_FILES
    assert "dsh" not in workers.HERDR_STATE_FILES


# ---------------------------------------------------------------------------
# Version extraction
# ---------------------------------------------------------------------------

def test_extract_version_handles_bare_prefixed_and_notice_lines():
    assert workers._extract_version("0.84.2\n") == "0.84.2"
    assert workers._extract_version("codex-cli 0.147.0") == "0.147.0"
    assert workers._extract_version("herdr 0.8.0") == "0.8.0"
    assert workers._extract_version("1.26.0") == "1.26.0"
    assert workers._extract_version("0.1.0-rc.6") == "0.1.0-rc.6"
    # An update notice line must not win over the real version line.
    assert workers._extract_version("Updated 1.22.0 -> 1.26.0\n1.26.0") == "1.26.0"


def test_extract_version_empty_garbage():
    assert workers._extract_version("") is None
    assert workers._extract_version("not a version") is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_detect_workers_versions_and_capabilities(tmp_path):
    with _hermetic_detection(tmp_path, _all_versions()):
        detected = workers.detect_workers(get_hermes_home())

    assert set(detected) == {"pi", "codex", "opencode", "commandcode", "dsh"}
    assert detected["pi"]["version"] == "0.84.2"
    assert detected["pi"]["capabilities"] == workers.BUILTIN_WORKERS["pi"]["capabilities"]
    assert detected["codex"]["version"] == "0.147.0"
    assert detected["opencode"]["version"] == "1.18.18"
    assert detected["commandcode"]["version"] == "1.26.0"
    assert detected["dsh"]["version"] == "0.1.0-rc.6"
    assert all(entry["source"] == "builtin" for entry in detected.values())


def test_detect_skips_missing_harness(tmp_path):
    with _hermetic_detection(tmp_path, {}):
        detected = workers.detect_workers(get_hermes_home())
    assert set(detected) == set(workers.BUILTIN_WORKERS)
    assert all(entry["version"] is None for entry in detected.values())


def test_detect_not_installed_returns_empty(tmp_path):
    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(shutil, "which", return_value=None))
    stack.enter_context(
        patch.object(workers, "_expand", side_effect=lambda p: tmp_path / p.lstrip("~/"))
    )
    with stack:
        detected = workers.detect_workers(get_hermes_home())
    assert detected == {}


def test_herdr_integration_flags(tmp_path):
    with _hermetic_detection(tmp_path, _all_versions()):
        # Create the pi + codex herdr-state files inside the isolated home.
        (tmp_path / ".pi/agent/extensions").mkdir(parents=True)
        (tmp_path / ".pi/agent/extensions/herdr-agent-state.ts").write_text("x")
        (tmp_path / ".codex").mkdir(parents=True)
        (tmp_path / ".codex/herdr-agent-state.sh").write_text("x")
        detected = workers.detect_workers(get_hermes_home())

    assert detected["pi"]["herdr"] is True
    assert detected["codex"]["herdr"] is True
    assert detected["opencode"]["herdr"] is False
    assert detected["commandcode"]["herdr"] is False
    assert detected["dsh"]["herdr"] is False


# ---------------------------------------------------------------------------
# Custom workers (workers.yaml)
# ---------------------------------------------------------------------------

def test_add_remove_custom_worker_roundtrip():
    home = get_hermes_home()
    name, caps = workers.add_custom_worker("myworker", ["coding", "testing"])
    assert (name, caps) == ("myworker", ["coding", "testing"])

    registry_path = home / "workers.yaml"
    assert registry_path.is_file()
    assert workers._read_custom_workers(home) == {"myworker": ["coding", "testing"]}

    # A second worker coexists; capabilities dedupe and preserve order.
    workers.add_custom_worker("helper", ["review", "review", "coding"])
    assert workers._read_custom_workers(home) == {
        "myworker": ["coding", "testing"],
        "helper": ["review", "coding"],
    }

    assert workers.remove_custom_worker("myworker", home) is True
    assert "myworker" not in workers._read_custom_workers(home)
    # Removing again reports nothing to remove.
    assert workers.remove_custom_worker("myworker", home) is False


def test_add_refuses_builtin_name():
    for name in ("pi", "codex", "opencode", "commandcode", "dsh", "herdr"):
        try:
            workers.add_custom_worker(name, ["coding"])
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for builtin name '{name}'")


def test_load_all_workers_merges_custom(tmp_path):
    home = get_hermes_home()
    workers.add_custom_worker("myworker", ["coding"], home)

    with _hermetic_detection(tmp_path, {"pi": "0.84.2"}):
        all_workers = workers.load_all_workers(home)

    by_name = {entry["name"]: entry for entry in all_workers}
    assert "pi" in by_name and by_name["pi"]["source"] == "builtin"
    assert by_name["myworker"]["source"] == "custom"
    assert by_name["myworker"]["version"] is None
    assert by_name["myworker"]["capabilities"] == ["coding"]
    assert by_name["myworker"]["herdr"] is False


# ---------------------------------------------------------------------------
# YAML emit/parse round-trip
# ---------------------------------------------------------------------------

def test_yaml_round_trip():
    original = {"alpha": ["coding", "testing"], "beta": ["review"]}
    text = workers._emit_workers_yaml(original)
    parsed = workers._parse_mini_yaml(text)
    assert parsed["workers"]["alpha"]["capabilities"] == ["coding", "testing"]
    assert parsed["workers"]["beta"]["capabilities"] == ["review"]


def test_parse_mini_yaml_tolerates_comments_and_inline_lists():
    text = (
        "# custom workers\n"
        "workers:\n"
        "  alpha:\n"
        "    capabilities: [coding, review]  # inline\n"
        "  beta:\n"
        "    capabilities:\n"
        "      - testing\n"
    )
    parsed = workers._parse_mini_yaml(text)
    assert parsed["workers"]["alpha"]["capabilities"] == ["coding", "review"]
    assert parsed["workers"]["beta"]["capabilities"] == ["testing"]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def test_table_output(capsys):
    rows = workers._worker_rows(
        [
            {"name": "pi", "version": "0.84.2", "capabilities": ["coding", "exploration"], "herdr": True},
            {"name": "myworker", "version": None, "capabilities": ["coding"], "herdr": False},
        ]
    )
    workers._print_table(rows)
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].startswith("WORKER")
    assert "VERSION" in lines[0] and "CAPABILITIES" in lines[0] and "HERDR" in lines[0]
    assert "pi" in lines[2] and "0.84.2" in lines[2] and "✓" in lines[2]
    assert "myworker" in lines[3] and "—" in lines[3]


def test_run_list_json(tmp_path, capsys):
    home = get_hermes_home()
    workers.add_custom_worker("myworker", ["coding"], home)

    with _hermetic_detection(tmp_path, {"pi": "0.84.2"}):
        rc = workers.run_workers_command(SimpleNamespace(workers_action="list", json=True))

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = {entry["name"] for entry in payload["workers"]}
    # Every harness resolves on PATH under the mock, so all builtins are
    # detected (only pi has a version); the custom worker must merge in too.
    assert names == set(workers.BUILTIN_WORKERS) | {"myworker"}
    by_name = {entry["name"]: entry for entry in payload["workers"]}
    assert by_name["pi"]["version"] == "0.84.2"
    assert by_name["myworker"]["source"] == "custom"
    assert payload["fleet"]["name"] == "herdr"
    assert "hermes_herdr_integration" in payload


def test_run_list_fleet_line(tmp_path, capsys):
    with _hermetic_detection(tmp_path, _all_versions()):
        rc = workers.run_workers_command(SimpleNamespace(workers_action="list", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Fleet layer: herdr 0.8.0" in out


def test_run_status_prints_hermes_integration(tmp_path, capsys):
    with _hermetic_detection(tmp_path, _all_versions()):
        rc = workers.run_workers_command(SimpleNamespace(workers_action="status", json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Hermes herdr integration" in out
    assert "not installed" in out


def test_run_add_remove_via_command(capsys):
    home = get_hermes_home()
    rc = workers.run_workers_command(
        SimpleNamespace(workers_action="add", name="myworker", capabilities=["coding"])
    )
    assert rc == 0
    assert workers._read_custom_workers(home) == {"myworker": ["coding"]}

    rc = workers.run_workers_command(
        SimpleNamespace(workers_action="remove", name="myworker")
    )
    assert rc == 0
    assert workers._read_custom_workers(home) == {}


def test_run_add_refuses_builtin(capsys):
    rc = workers.run_workers_command(
        SimpleNamespace(workers_action="add", name="pi", capabilities=["coding"])
    )
    assert rc == 1
    assert "error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------

def test_parser_registers_subcommands():
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_workers_parser(subparsers, cmd_workers=lambda args: 0)

    list_args = parser.parse_args(["workers", "list", "--json"])
    assert list_args.workers_action == "list"
    assert list_args.json is True
    assert callable(list_args.func)

    status_args = parser.parse_args(["workers", "status"])
    assert status_args.workers_action == "status"
    assert status_args.json is False

    add_args = parser.parse_args(["workers", "add", "myworker", "coding", "testing"])
    assert add_args.name == "myworker"
    assert add_args.capabilities == ["coding", "testing"]

    rm_args = parser.parse_args(["workers", "remove", "myworker"])
    assert rm_args.workers_action == "remove"
    assert rm_args.name == "myworker"

    ls_args = parser.parse_args(["workers", "ls"])
    assert ls_args.workers_action in ("list", "ls")
