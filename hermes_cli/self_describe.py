"""``hermes self-describe`` — machine-readable description of the running instance.

Emits a JSON description of the active Hermes installation: version/build
identity, the configured model/provider, per-platform enabled toolsets and
their tools, installed plugins, skills, and a redacted config summary.

All enumeration reuses the existing code paths — ``hermes dump`` helpers
(git/build identity, model/provider, config overrides, credential
inventory), ``hermes_cli.tools_config`` toolset resolution, the tool
registry, the plugin discovery/status helpers, and the skills scanner. No
parallel registry is built. Secrets are redacted via
:func:`agent.redact.mask_secret` / :func:`hermes_cli.config.redact_config_value`
— the same machinery ``hermes config`` / ``hermes status`` / ``hermes dump``
use.
"""

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from hermes_cli import dump as _dump
from hermes_constants import display_hermes_home, get_hermes_home


def _load_env() -> None:
    """Load credentials from ``~/.hermes/.env`` so key status reflects the
    environment the managed backends actually see (same as ``hermes dump``)."""
    try:
        from hermes_cli.env_loader import load_hermes_dotenv
        from hermes_cli.config import get_project_root

        env_path = _dump.get_env_path()
        load_hermes_dotenv(
            hermes_home=env_path.parent,
            project_env=get_project_root() / ".env",
        )
    except Exception:
        pass


def _version_block() -> dict:
    """Build identity: package version, release date, git commit, runtime."""
    try:
        from hermes_cli import __release_date__, __version__
    except ImportError:
        __version__ = "(unknown)"
        __release_date__ = ""
    try:
        from hermes_cli.config import detect_install_method, get_project_root

        project_root = get_project_root()
        install_method = detect_install_method(project_root)
    except Exception:
        project_root = None
        install_method = "(unknown)"

    commit = _dump._get_git_commit(project_root) if project_root else "(unknown)"
    commit_date = _dump._get_git_commit_date(project_root) if project_root else ""

    try:
        from hermes_cli.profiles import get_active_profile_name

        profile = get_active_profile_name() or "(default)"
    except Exception:
        profile = "(default)"

    return {
        "name": "Hermes Agent",
        "version": __version__,
        "release_date": __release_date__,
        "git_commit": commit,
        "git_commit_date": commit_date,
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
        "install_method": install_method,
        "install_dir": str(project_root) if project_root else "(unknown)",
        "profile": profile,
        "hermes_home": display_hermes_home(),
    }


def _model_block(config: dict) -> dict:
    """Active model/provider from config (mirrors ``hermes dump``)."""
    model, provider = _dump._get_model_and_provider(config)
    return {"default": model, "provider": provider}


def _tools_block(config: dict, platform: str) -> dict:
    """Enabled toolsets + the tools each toolset ships.

    Toolset resolution goes through ``hermes_cli.tools_config._get_platform_tools``
    — the same resolver ``hermes tools`` and the platform adapters use. Tool
    names come from the tool registry's ``get_available_toolsets``, the same
    enumeration the tools UI renders.
    """
    from hermes_cli.tools_config import _get_platform_tools

    enabled: set = set()
    try:
        enabled = _get_platform_tools(config, platform)
    except Exception:
        pass
    enabled = set(enabled)

    # Populate the registry via the same discovery path the agent uses.
    try:
        from tools.registry import discover_builtin_tools, registry

        discover_builtin_tools()
        per_toolset = registry.get_available_toolsets()
    except Exception:
        per_toolset = {}

    toolsets: dict = {}
    total_tools = 0
    for ts_key in sorted(per_toolset):
        info = per_toolset[ts_key]
        tools = sorted(info.get("tools") or [])
        is_enabled = ts_key in enabled
        if is_enabled:
            total_tools += len(tools)
        toolsets[ts_key] = {
            "enabled": is_enabled,
            "available": bool(info.get("available")),
            "requirements": sorted(info.get("requirements") or []),
            "tools": tools,
        }
    # Toolsets enabled in config but with no registry entry yet (e.g. an
    # MCP-only or plugin toolset) still appear as enabled with an empty list.
    for ts_key in enabled - set(per_toolset):
        toolsets[ts_key] = {"enabled": True, "available": False, "requirements": [], "tools": []}

    return {
        "platform": platform,
        "enabled_toolsets": sorted(enabled),
        "toolsets": toolsets,
        "tool_count": total_tools,
    }


def _plugins_block() -> list:
    """Installed plugins with activation status (reuses ``hermes plugins``)."""
    try:
        from hermes_cli.plugins_cmd import (
            _discover_all_plugins,
            _get_disabled_set,
            _get_enabled_set,
            _plugin_status,
        )

        entries = _discover_all_plugins()
        enabled = _get_enabled_set()
        disabled = _get_disabled_set()
        payload = [
            {
                "name": name,
                "version": str(version),
                "description": description,
                "source": source,
                "status": _plugin_status(name, enabled, disabled, key=key),
            }
            for name, version, description, source, _dir, key in entries
        ]
        payload.sort(key=lambda p: (p["name"], p["source"]))
        return payload
    except Exception:
        return []


def _skills_block() -> list:
    """Installed skills with source + enabled status (reuses the skills scanner)."""
    try:
        from tools.skills_hub import HubLockFile, ensure_hub_dirs
        from tools.skills_sync import _read_manifest
        from tools.skills_tool import _find_all_skills
        from agent.skill_utils import get_disabled_skill_names

        ensure_hub_dirs()
        hub_installed = {e["name"]: e for e in HubLockFile().list_installed()}
        builtin_names = set(_read_manifest())
        disabled_names = get_disabled_skill_names()

        skills = []
        for skill in sorted(
            _find_all_skills(skip_disabled=True),
            key=lambda s: (s.get("category") or "", s["name"]),
        ):
            name = skill["name"]
            if name in hub_installed:
                source = "hub"
            elif name in builtin_names:
                source = "builtin"
            else:
                source = "local"
            skills.append(
                {
                    "name": name,
                    "category": skill.get("category", ""),
                    "description": skill.get("description", ""),
                    "source": source,
                    "status": "disabled" if name in disabled_names else "enabled",
                }
            )
        return skills
    except Exception:
        return []


def _config_block(config: dict, *, redacted: bool = True) -> dict:
    """Compact config summary: paths, memory provider, gateway, platforms,
    cron, MCP servers, and non-default overrides."""
    hermes_home = get_hermes_home()

    from hermes_cli.config import get_config_path

    overrides = _dump._config_overrides(config)

    mcp_cfg = config.get("mcp", {}) if isinstance(config.get("mcp"), dict) else {}
    servers = mcp_cfg.get("servers", {}) if isinstance(mcp_cfg.get("servers"), dict) else {}
    mcp_servers = sorted(servers.keys())

    return {
        "config_path": str(get_config_path()),
        "memory_provider": _dump._memory_provider(config),
        "gateway": _dump._gateway_status(),
        "platforms": _dump._configured_platforms(),
        "cron_jobs": _dump._cron_summary(hermes_home),
        "mcp_servers": mcp_servers,
        "overrides": _redact_config_overrides(overrides) if redacted else overrides,
    }


def _redact_config_overrides(overrides: dict) -> dict:
    """Redact credential-shaped values inside the overrides summary."""
    from hermes_cli.config import redact_config_value

    return redact_config_value(overrides)


def _api_keys_block(*, show_keys: bool) -> dict:
    """Credential inventory: status per key, optionally the masked value.

    Mirrors ``hermes dump``: status is ``"set"`` / ``"not set"`` (plus the
    auth-pool and shell-only annotations), and the raw value is NEVER
    emitted — ``--show-keys`` shows only the ``mask_secret`` head/tail form.
    """
    from agent.redact import mask_secret

    dotenv_keys = _dump._dotenv_key_names()
    result: dict = {}
    for env_var, label in _dump.API_KEY_LABELS:
        val = os.getenv(env_var, "")
        entry: dict = {"status": "set" if val else "not set"}
        if val:
            if env_var not in dotenv_keys:
                entry["note"] = "shell only — not in .env; managed/desktop backend may not see it"
            if show_keys:
                entry["value"] = mask_secret(val)
        if not val and label == "openrouter":
            try:
                from agent.credential_pool import load_pool as _load_pool

                if _load_pool("openrouter").has_credentials():
                    entry["status"] = "set (auth pool)"
            except Exception:
                pass
        result[label] = entry
    return result


def build_self_description(platform: str = "cli", *, show_keys: bool = False) -> dict:
    """Assemble the full self-description payload for ``platform``."""
    _load_env()

    try:
        from hermes_cli.config import load_config

        config = load_config()
    except Exception:
        config = {}

    return {
        "hermes": _version_block(),
        "model": _model_block(config),
        "tools": _tools_block(config, platform),
        "plugins": _plugins_block(),
        "skills": _skills_block(),
        "config": _config_block(config, redacted=True),
        "api_keys": _api_keys_block(show_keys=show_keys),
    }


def run_self_describe(args: Any) -> int:
    """Entry point for ``hermes self-describe``. Prints JSON to stdout."""
    platform_name = getattr(args, "platform", None) or "cli"
    show_keys = bool(getattr(args, "show_keys", False))
    compact = bool(getattr(args, "compact", False))

    payload = build_self_description(platform_name, show_keys=show_keys)
    if compact:
        print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    else:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0
