"""``hermes workers`` — worker capability registry.

Discovers the installed external coding-agent harnesses (the "fleet": pi,
codex, opencode, commandcode, dsh, herdr), reports their version and
capabilities, and manages a small registry of user-defined custom workers
persisted to ``<HERMES_HOME>/workers.yaml``.

Hermes uses this registry to route tasks to the best worker: each detected
harness declares the capabilities it is good at (from the capability map in
the architecture doc §12), and the herdr column reports whether that worker
has the Herdr agent-state integration installed. Custom workers are declared
by the user and stored in YAML so they survive restarts.

Stdlib only by design — no new dependencies.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from hermes_constants import get_hermes_home

# ---------------------------------------------------------------------------
# Worker definitions
# ---------------------------------------------------------------------------

#: Built-in capability registry (architecture doc §12). Used when a worker is
#: detected on this machine; custom workers carry their own capabilities.
BUILTIN_WORKERS: dict[str, dict] = {
    "pi": {
        "capabilities": ["coding", "exploration", "reasoning", "autonomous_tasks"],
        "herdr": True,
    },
    "codex": {
        "capabilities": ["coding", "repository_reasoning", "implementation"],
        "herdr": True,
    },
    "opencode": {
        "capabilities": ["coding", "testing", "implementation"],
        "herdr": True,
    },
    "commandcode": {
        "capabilities": ["coding", "review"],
        "herdr": False,
    },
    "dsh": {
        "capabilities": ["deep_reasoning", "long_horizon", "coding", "experimental"],
        "herdr": False,
    },
}

#: Fleet-layer tool (not a worker itself, but reported alongside the fleet).
FLEET_NAME = "herdr"

#: Per-worker Herdr agent-state files that mark an installed integration.
#: Keys are Path.home()-relative (the external harnesses are not
#: profile-aware); the Hermes plugin check uses get_hermes_home() instead.
HERDR_STATE_FILES: dict[str, str] = {
    "pi": ".pi/agent/extensions/herdr-agent-state.ts",
    "codex": ".codex/herdr-agent-state.sh",
    "opencode": ".config/opencode/plugins/herdr-agent-state.js",
}

#: ``~/.hermes/plugins/herdr-agent-state/__init__.py`` — the Hermes-side
#: Herdr integration (profile-aware, resolved via get_hermes_home()).
HERMES_HERDR_PLUGIN = Path("plugins") / "herdr-agent-state" / "__init__.py"

#: Well-known extra locations for harnesses that are frequently installed off
#: PATH (checked after shutil.which misses).
EXTRA_LOCATIONS: dict[str, list[str]] = {
    "dsh": ["~/.local/bin/dsh"],
    "commandcode": ["~/.nvm/versions/node/*/bin/commandcode"],
}

_VERSION_RE = re.compile(r"v?\d+\.\d+[\w.\-]*")
_TIMEOUT_SECONDS = 5

_CUSTOM_FILE = "workers.yaml"
_CUSTOM_KEY = "workers"


def _expand(path: str) -> Path:
    """Expand a ``~``-leading path to an absolute Path (glob patterns kept)."""
    return Path(os.path.expanduser(path))


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _resolve_command(name: str) -> Path | None:
    """Resolve a harness binary to a path, or None if not installed.

    Checks PATH first, then well-known extra locations (e.g. commandcode
    under ``~/.nvm/versions/node/*/bin``, dsh under ``~/.local/bin``).
    """
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    for pattern in EXTRA_LOCATIONS.get(name, []):
        expanded = _expand(pattern)
        matches = sorted(expanded.parent.glob(expanded.name)) if "*" in expanded.name else [expanded]
        for candidate in matches:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _extract_version(output: str) -> str | None:
    """Pull the version out of a harness's ``--version`` output.

    Handles bare versions ("0.84.2"), prefixed ones ("codex-cli 0.147.0",
    "herdr 0.8.0"), and the last line winning when a tool prints an update
    notice ahead of the version.
    """
    for line in reversed(output.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        for token in reversed(line.split()):
            if _VERSION_RE.fullmatch(token):
                return token
    return None


def _detect_version(command: Path) -> str | None:
    """Run ``<command> --version`` and return a clean version string.

    Never blocks on a missing/broken harness — any failure yields None.
    """
    try:
        result = subprocess.run(
            [str(command), "--version"],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    combined = result.stdout or ""
    if result.stderr:
        combined = f"{combined}\n{result.stderr}"
    return _extract_version(combined)


def _herdr_installed(name: str, hermes_home: Path | None = None) -> bool:
    """True when the Herdr agent-state integration file for a worker exists."""
    rel = HERDR_STATE_FILES.get(name)
    if not rel:
        return False
    return _expand(f"~/{rel}").is_file()


def _fleet_detected() -> dict | None:
    """Detect the herdr fleet layer (reported, not a worker)."""
    command = _resolve_command(FLEET_NAME)
    if command is None:
        return None
    return {"name": FLEET_NAME, "version": _detect_version(command)}


def detect_workers(hermes_home: Path | None = None) -> dict[str, dict]:
    """Detect installed builtin harnesses -> {name: worker_info}.

    A worker is reported only when its binary is actually installed.
    """
    hermes_home = hermes_home or get_hermes_home()
    detected: dict[str, dict] = {}
    for name, definition in BUILTIN_WORKERS.items():
        command = _resolve_command(name)
        if command is None:
            continue
        detected[name] = {
            "name": name,
            "version": _detect_version(command),
            "source": "builtin",
            "capabilities": list(definition["capabilities"]),
            "herdr": _herdr_installed(name, hermes_home),
        }
    return detected


# ---------------------------------------------------------------------------
# Custom worker registry (persisted to <HERMES_HOME>/workers.yaml)
# ---------------------------------------------------------------------------

def custom_workers_path(hermes_home: Path | None = None) -> Path:
    """Path to the custom-worker registry file (profile-aware)."""
    return (hermes_home or get_hermes_home()) / _CUSTOM_FILE


def _emit_workers_yaml(workers: dict[str, list[str]]) -> str:
    lines = ["workers:"]
    for name in sorted(workers):
        lines.append(f"  {name}:")
        lines.append("    capabilities:")
        for capability in workers[name]:
            lines.append(f"      - {capability}")
    return "\n".join(lines) + "\n"


def _strip_yaml_comment(s: str) -> str:
    """Remove a trailing ``# comment`` outside quotes."""
    in_single = in_double = False
    for i, ch in enumerate(s):
        if ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = not in_single
        elif ch == "#" and not in_single and not in_double and (i == 0 or s[i - 1].isspace()):
            return s[:i].rstrip()
    return s.rstrip()


def _parse_flow_value(value: str):
    """Parse an inline scalar or flow list (``[a, b]``)."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip('"').strip("'") for item in inner.split(",")]
    return value.strip().strip('"').strip("'")


def _parse_mini_yaml(text: str) -> dict:
    """Parse a minimal YAML subset: nested mappings, block lists, inline flow
    lists and scalars (comments tolerated). Used only to read back the
    workers.yaml we write (plus small hand edits); unknown structures are
    ignored gracefully."""
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_yaml_comment(raw).strip()
        if not stripped:
            continue
        lines.append((len(raw) - len(raw.lstrip(" ")), stripped))
    if not lines:
        return {}

    def parse_block(index: int, indent: int):
        if index >= len(lines):
            return {}, index
        if lines[index][1].startswith("- "):
            items = []
            while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
                items.append(_parse_flow_value(lines[index][1][2:]))
                index += 1
            return items, index

        node: dict = {}
        while index < len(lines) and lines[index][0] == indent:
            line = lines[index][1]
            if line.startswith("- "):
                break
            if ":" not in line:
                index += 1
                continue
            key, _, rest = line.partition(":")
            key = key.strip().strip('"').strip("'")
            rest = rest.strip()
            if rest:
                node[key] = _parse_flow_value(rest)
                index += 1
            else:
                if index + 1 < len(lines) and lines[index + 1][0] > indent:
                    value, index = parse_block(index + 1, lines[index + 1][0])
                else:
                    value, index = None, index + 1
                node[key] = value
        return node, index

    parsed, _ = parse_block(0, lines[0][0])
    return parsed


def _read_custom_workers(hermes_home: Path | None = None) -> dict[str, list[str]]:
    """Load custom workers from workers.yaml -> {name: [capabilities, ...]}."""
    path = custom_workers_path(hermes_home)
    if not path.is_file():
        return {}
    try:
        raw = _parse_mini_yaml(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    registry = raw.get(_CUSTOM_KEY) or {}
    if not isinstance(registry, dict):
        return {}
    workers: dict[str, list[str]] = {}
    for name, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        caps = entry.get("capabilities")
        if isinstance(caps, str):
            caps = [caps]
        if isinstance(caps, list):
            workers[str(name)] = [str(c) for c in caps]
    return workers


def _write_custom_workers(workers: dict[str, list[str]], hermes_home: Path | None = None) -> Path:
    path = custom_workers_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_emit_workers_yaml(workers), encoding="utf-8")
    return path


def add_custom_worker(
    name: str, capabilities: list[str], hermes_home: Path | None = None
) -> tuple[str, list[str]]:
    """Register (or update) a custom worker. Returns the stored name+caps.

    Refuses names that collide with a builtin harness — those are detected,
    not declared, and shadowing them would confuse routing.
    """
    name = name.strip()
    if not name:
        raise ValueError("worker name must not be empty")
    if name in BUILTIN_WORKERS or name == FLEET_NAME:
        raise ValueError(f"'{name}' is a detected builtin — it cannot be overridden as a custom worker")
    capabilities = list(dict.fromkeys(c.strip() for c in capabilities if c.strip()))
    workers = _read_custom_workers(hermes_home)
    workers[name] = capabilities
    _write_custom_workers(workers, hermes_home)
    return name, capabilities


def remove_custom_worker(name: str, hermes_home: Path | None = None) -> bool:
    """Remove a custom worker. Returns True when it existed and was removed."""
    name = name.strip()
    workers = _read_custom_workers(hermes_home)
    if name not in workers:
        return False
    del workers[name]
    _write_custom_workers(workers, hermes_home)
    return True


def load_all_workers(hermes_home: Path | None = None) -> list[dict]:
    """Detected builtins + registered custom workers, one dict per worker."""
    hermes_home = hermes_home or get_hermes_home()
    workers = detect_workers(hermes_home)
    for name, capabilities in _read_custom_workers(hermes_home).items():
        workers[name] = {
            "name": name,
            "version": None,
            "source": "custom",
            "capabilities": list(capabilities),
            "herdr": False,
        }
    return [workers[name] for name in sorted(workers)]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _worker_rows(workers: list[dict]) -> list[tuple[str, str, str, str]]:
    rows = []
    for worker in workers:
        version = worker.get("version") or "—"
        capabilities = ", ".join(worker.get("capabilities") or [])
        herdr = "✓" if worker.get("herdr") else "—"
        rows.append((worker["name"], version, capabilities, herdr))
    return rows


def _print_table(rows: list[tuple[str, str, str, str]]) -> None:
    headers = ("WORKER", "VERSION", "CAPABILITIES", "HERDR")
    all_rows = [headers, *rows]
    widths = [
        max(len(str(row[i])) for row in all_rows) for i in range(len(headers))
    ]
    for i, row in enumerate(all_rows):
        cells = [
            str(row[j]).ljust(widths[j]) if j < 3 else str(row[j])
            for j in range(len(headers))
        ]
        line = "  ".join(cells).rstrip()
        print(line)
        if i == 0:
            print("  ".join("-" * w for w in widths))


def _json_payload(workers: list[dict], fleet: dict | None, hermes_herdr: bool) -> dict:
    return {
        "fleet": fleet,
        "workers": workers,
        "hermes_herdr_integration": hermes_herdr,
    }


def _print_json(workers: list[dict], fleet: dict | None, hermes_herdr: bool) -> None:
    print(json.dumps(_json_payload(workers, fleet, hermes_herdr), indent=2, default=str))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def run_workers_command(args) -> int:
    """Dispatch ``hermes workers <action>``. Returns process exit code."""
    hermes_home = get_hermes_home()
    action = getattr(args, "workers_action", None)
    as_json = bool(getattr(args, "json", False))

    if action in ("add",):
        try:
            name, capabilities = add_custom_worker(
                args.name, list(args.capabilities), hermes_home
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Registered custom worker '{name}' with capabilities: {', '.join(capabilities)}")
        return 0

    if action in ("remove", "rm"):
        if remove_custom_worker(args.name, hermes_home):
            print(f"Removed custom worker '{args.name}'")
            return 0
        print(f"error: custom worker '{args.name}' is not registered", file=sys.stderr)
        return 1

    if action in ("run",):
        from hermes_cli.worker_backend import run_workers_cli_command

        return run_workers_cli_command(args)

    if action in ("benchmark",):
        from hermes_cli.benchmark import run_benchmark_command

        return run_benchmark_command(args)

    if action in ("list", "ls", "status", None):
        workers = load_all_workers(hermes_home)
        fleet = _fleet_detected()
        hermes_herdr = (hermes_home / HERMES_HERDR_PLUGIN).is_file()
        if as_json:
            _print_json(workers, fleet, hermes_herdr)
        else:
            _print_table(_worker_rows(workers))
            if fleet:
                print()
                print(f"Fleet layer: {fleet['name']} {fleet.get('version') or '(version unknown)'}")
            if action == "status":
                state = "installed" if hermes_herdr else "not installed"
                print(f"Hermes herdr integration: {state}")
        return 0

    print(f"error: unknown workers action '{action}'", file=sys.stderr)
    return 1
