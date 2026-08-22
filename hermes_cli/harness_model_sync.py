"""Propagate Fleet model picks into each harness's native config.

When the user selects a model in the Fleet dashboard (or via
``hermes workers model``), the pick lands in ``worker_models.json`` — which
governs fleet dispatch only. Each harness also carries its own native model
config (``~/.opencode/opencode.jsonc``, ``~/.commandcode/config.json``,
``~/.codex/config.toml``, ``~/.dsh/settings.yaml``) that governs direct CLI
runs. This module keeps those in lockstep: one write path, called from
``worker_backend.set_worker_model()``, so "whatever I select in the fleet
dashboard" is the model everywhere.

Design rules:

- **Never destructive.** Every writer preserves all unrelated keys/comments.
  A failure to sync one harness never fails the pin itself (the pin is the
  source of truth for dispatch); failures are logged and collected in the
  result dict so callers can surface them.
- **Provider-aware.** Only sync a harness when the picked provider is one
  that harness can actually reach (e.g. dsh only has opencode-go wired).
  Skipping is reported, not silent.
- **pi gets no native write** — its model store is managed by ``pi auth``
  / interactive login; the fleet pin already reaches pi via ``--model`` at
  dispatch time.
"""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Callable

import logging

from hermes_constants import get_hermes_home

_log = logging.getLogger(__name__)

#: Model id charset mirror of web_server's validation regex.
_MODEL_RE = re.compile(r"^[A-Za-z0-9._/:-]{1,128}$")

#: provider slug -> harnesses that can reach it natively. Harnesses absent
#: from a provider's set are skipped with an explanatory result.
_PROVIDER_REACH: dict[str, frozenset[str]] = {
    # opencode-go: native zen gateway on opencode + dsh; codex/commandcode
    # reach it through the local Responses shim (:8899), not directly.
    "opencode-go": frozenset({"opencode", "dsh"}),
    # commandcode gateway: commandcode CLI natively; others via shim only.
    "commandcode": frozenset({"commandcode"}),
    # openrouter: direct key auth on opencode + codex TOML provider block;
    # commandcode/dsh have no OpenRouter wiring.
    "openrouter": frozenset({"opencode", "codex"}),
}

#: Fallback when the pin's provider field is empty (legacy pins).
_DEFAULT_REACH = frozenset({"opencode", "commandcode", "dsh", "codex"})


def _atomic_write(path: Path, data: str) -> None:
    """tmp+fsync+rename write; preserves mode where the file exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        import os

        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            try:
                os.chmod(tmp_path, path.stat().st_mode & 0o777)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# opencode — ~/.opencode/opencode.jsonc (JSONC: comments allowed, we only
# touch the top-level "model" line, preserving everything else byte-for-byte).
# ---------------------------------------------------------------------------


def _opencode_config_path(hermes_home: Path | None = None) -> Path:
    home = hermes_home or get_hermes_home()
    return Path.home() / ".opencode" / "opencode.jsonc"


def _strip_jsonc(text: str) -> str:
    """Remove // comments and trailing commas just enough for json.loads."""
    out = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _sync_opencode(
    provider: str, model: str, hermes_home: Path | None = None
) -> bool:
    path = _opencode_config_path(hermes_home)
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(_strip_jsonc(raw))

    old_model = str(parsed.get("model") or "")
    new_model = f"{provider}/{model}" if provider else model
    if old_model == new_model:
        return False  # nothing to change

    parsed["model"] = new_model
    # Rewrite preserving unrelated keys; keep JSONC comments by doing a
    # surgical replacement of the "model" line when one exists.
    model_line_re = re.compile(r"^(\s*)\"model\"\s*:\s*.*$", re.MULTILINE)
    if model_line_re.search(raw):
        new_raw = model_line_re.sub(
            lambda m: f'{m.group(1)}"model": {json.dumps(new_model)}', raw, count=1
        )
    else:
        # No explicit model line — append before final closing brace.
        stripped = raw.rstrip()
        if stripped.endswith("}"):
            body = stripped[:-1].rstrip().rstrip(",")
            new_raw = body + ",\n" + f'  "model": {json.dumps(new_model)}\n}}\n'
        else:
            new_raw = json.dumps(parsed, indent=2) + "\n"
    _atomic_write(path, new_raw)
    return True


# ---------------------------------------------------------------------------
# commandcode — ~/.commandcode/config.json ("provider"+"model", plain JSON).
# The gateway slugs differ from Hermes': openrouter→open-router, and the
# catalog uses vendor-qualified ids there (deepseek/deepseek-v4-flash,
# meta/muse-spark-1.2-contributor). Unknown pairs are left untouched.
# ---------------------------------------------------------------------------

_COMMANDCODE_GATEWAY_SLUGS = {"opencode-go": "command-code-go", "commandcode": "command-code", "openrouter": "open-router"}
_COMMANDCODE_VENDOR_PREFIX = {
    "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
}


def _commandcode_config_path(hermes_home: Path | None = None) -> Path:
    return Path.home() / ".commandcode" / "config.json"


def _sync_commandcode(
    provider: str, model: str, hermes_home: Path | None = None
) -> bool:
    if provider == "opencode-go":
        return False  # no verified mapping into the commandcode gateway
    path = _commandcode_config_path(hermes_home)
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)

    gateway_slug = _COMMANDCODE_GATEWAY_SLUGS.get(provider)
    if not gateway_slug:
        return False
    # Map known flat ids to vendor-qualified gateway ids; pass through ids
    # that are already vendor-qualified; unknown bare ids are unguessable.
    if model in _COMMANDCODE_VENDOR_PREFIX:
        remote_id = _COMMANDCODE_VENDOR_PREFIX[model]
    elif "/" in model:
        remote_id = model
    else:
        return False
    if not remote_id or not _MODEL_RE.match(remote_id):
        return False

    new_pair = (gateway_slug, remote_id)
    old_pair = (str(parsed.get("provider") or ""), str(parsed.get("model") or ""))
    if old_pair == new_pair:
        return False

    parsed["provider"] = gateway_slug
    parsed["model"] = remote_id
    _atomic_write(path, json.dumps(parsed, indent=2) + "\n")
    return True


# ---------------------------------------------------------------------------
# dsh — ~/.dsh/settings.yaml agent-default-model block (PyYAML round-trip of
# the whole file; comments in that file are informational only).
# ---------------------------------------------------------------------------


def _dsh_settings_path(hermes_home: Path | None = None) -> Path:
    return Path.home() / ".dsh" / "settings.yaml"


def _sync_dsh(provider: str, model: str, hermes_home: Path | None = None) -> bool:
    if provider != "opencode-go":
        return False  # dsh only has the opencode-go provider wired natively
    path = _dsh_settings_path(hermes_home)
    try:
        import yaml
    except ImportError:
        return False
    if not path.is_file():
        return False
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        return False

    block = parsed.get("agent-default-model")
    if not isinstance(block, dict):
        block = {}
    old_pair = (str(block.get("provider") or ""), str(block.get("model") or ""))
    new_pair = (provider, model)
    if old_pair == new_pair:
        return False

    block["provider"] = provider
    block["model"] = model
    parsed["agent-default-model"] = block
    _atomic_write(path, yaml.safe_dump(parsed, sort_keys=False, default_flow_style=False))
    return True


# ---------------------------------------------------------------------------
# codex — ~/.codex/config.toml top-level `model` + `model_provider`.
# tomllib read (3.11+) + surgical text rewrite of the two scalar lines so all
# [projects...] trust tables survive byte-for-byte (tomli_w would reorder /
# drop comments across the rest of the file).
# ---------------------------------------------------------------------------


def _codex_config_path(hermes_home: Path | None = None) -> Path:
    return Path.home() / ".codex" / "config.toml"


def _sync_codex(provider: str, model: str, hermes_path: Path | None = None) -> bool:
    del hermes_path  # signature parity; codex config lives under ~
    path = _codex_config_path()
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    try:
        import tomllib
    except ImportError:  # pragma: no cover - requires-python >= 3.11
        return False

    parsed = tomllib.loads(raw)
    # Codex needs both lines coherent: model id AND which provider block
    # resolves it. We map Hermes' provider slug → the user's existing TOML
    # provider table names (verified present in ~/.codex/config.toml).
    slug_to_codex_provider = {"opencode-go": "opencode-go", "openrouter": "openrouter"}
    codex_provider = slug_to_codex_provider.get(provider)
    if not codex_provider:
        return False

    old_pair = (
        str(parsed.get("model") or ""),
        str(parsed.get("model_provider") or ""),
    )
    new_pair = (model, codex_provider)
    if old_pair == new_pair:
        return False

    def _set_scalar(pattern: re.Pattern[str], replacement: str, raw_text: str) -> str:
        if pattern.search(raw_text):
            return pattern.sub(lambda m: m.expand(replacement), raw_text, count=1)
        # Append after the last top-level scalar, before first [table].
        lines = raw_text.splitlines(keepends=True)
        insert_at = 0
        for idx, line in enumerate(lines):
            if line.startswith("["):
                break
            if line.strip():
                insert_at = idx + 1
        lines.insert(insert_at, f"{replacement}\n")
        return "".join(lines)

    raw = _set_scalar(re.compile(r"(?m)^model\s*=\s*.*$"), f'model = "{model}"', raw)
    raw = _set_scalar(
        re.compile(r"(?m)^model_provider\s*=\s*.*$"),
        f'model_provider = "{codex_provider}"',
        raw,
    )
    _atomic_write(path, raw)
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

#: Workers with native-config writers (resolved as ``_sync_<worker>``).
_NATIVE_SYNC_WRITERS = frozenset({"opencode", "commandcode", "dsh", "codex"})


def sync_native_configs(
    worker: str, provider: str, model: str, hermes_home: Path | None = None
) -> dict:
    """Mirror a Fleet model pin into the harness's native config file(s).

    Called from ``worker_backend.set_worker_model()`` so every picker surface
    (Fleet dashboard POST, ``hermes workers model``, future ones) propagates
    automatically. Never raises; returns a report dict.
    """
    report: dict = {"worker": worker, "provider": provider, "model": model, "synced": [], "skipped": [], "errors": []}
    if worker not in _NATIVE_SYNC_WRITERS:
        report["skipped"].append(f"{worker}: no native config writer")
        return report
    # Late binding via globals() so monkeypatched writers are honored in tests.
    syncer = globals()[f"_sync_{worker}"]

    reach = _PROVIDER_REACH.get(provider, _DEFAULT_REACH)
    if worker not in reach:
        report["skipped"].append(
            f"{worker}: provider '{provider}' not reachable natively (fleet pin still applies at dispatch)"
        )
        return report

    if not _MODEL_RE.match(model):
        report["errors"].append(f"invalid model id: {model!r}")
        return report

    try:
        changed = syncer(provider, model, hermes_home)
    except Exception as exc:  # noqa: BLE001 — never break the pin
        report["errors"].append(str(exc))
        try:
            _log.warning("harness model sync failed for %s: %s", worker, exc)
        except Exception:
            pass
        return report

    if changed:
        report["synced"].append(worker)
    return report


def sync_all_from_pins(hermes_home: Path | None = None) -> dict:
    """Re-propagate every current worker_models.json pin (repair utility)."""
    from hermes_cli.worker_backend import load_worker_models

    pins = load_worker_models(hermes_home)
    results = {}
    for worker, entry in pins.items():
        if isinstance(entry, dict):
            results[worker] = sync_native_configs(
                worker, str(entry.get("provider") or ""), str(entry.get("model") or ""), hermes_home
            )
    return results
