"""Tests for ``hermes self-describe`` (hermes_cli/self_describe.py).

Covers the contract of the machine-readable self-description: valid JSON,
all required sections present, no raw secrets ever emitted (default output
has status-only; ``--show-keys`` reveals only the ``mask_secret``
head/tail form), and toolset/plugin/skill enumeration reflects the live
config rather than a parallel registry.
"""

import argparse
import json
from types import SimpleNamespace

from hermes_cli.config import get_hermes_home
from hermes_cli.self_describe import build_self_description, run_self_describe
from hermes_cli.subcommands.self_describe import build_self_describe_parser

_TOP_LEVEL_SECTIONS = ("hermes", "model", "tools", "plugins", "skills", "config", "api_keys")


def _args(**overrides) -> SimpleNamespace:
    base = {"platform": "cli", "json": True, "compact": False, "show_keys": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_run_self_describe_emits_valid_json(capsys):
    rc = run_self_describe(_args())
    assert rc == 0

    payload = json.loads(capsys.readouterr().out)
    for key in _TOP_LEVEL_SECTIONS:
        assert key in payload, f"missing top-level section: {key}"

    assert payload["hermes"]["name"] == "Hermes Agent"
    assert payload["hermes"]["version"]
    assert payload["hermes"]["git_commit"]

    tools = payload["tools"]
    assert isinstance(tools["enabled_toolsets"], list)
    assert isinstance(tools["toolsets"], dict)
    assert isinstance(tools["tool_count"], int)
    # Every enabled toolset key must be reported in the toolsets map.
    for ts in tools["enabled_toolsets"]:
        assert tools["toolsets"].get(ts, {}).get("enabled") is True, ts

    assert isinstance(payload["plugins"], list)
    assert isinstance(payload["skills"], list)
    assert isinstance(payload["api_keys"], dict)
    assert "overrides" in payload["config"]


def test_default_output_never_emits_secret_values(capsys):
    home = get_hermes_home()
    raw = "sk-or-raw-secret-value-1234567890"
    (home / ".env").write_text(f"OPENROUTER_API_KEY={raw}\n")

    rc = run_self_describe(_args(show_keys=False))
    assert rc == 0

    out = capsys.readouterr().out
    assert raw not in out
    payload = json.loads(out)
    assert payload["api_keys"]["openrouter"]["status"] == "set"
    # No value key at all by default — status only.
    assert "value" not in payload["api_keys"]["openrouter"]


def test_show_keys_emits_only_masked_value(capsys):
    home = get_hermes_home()
    raw = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890"
    (home / ".env").write_text(f"ANTHROPIC_API_KEY={raw}\n")

    rc = run_self_describe(_args(show_keys=True))
    assert rc == 0

    out = capsys.readouterr().out
    assert raw not in out
    payload = json.loads(out)
    masked = payload["api_keys"]["anthropic"]["value"]
    assert masked != raw
    # mask_secret preserves only the head/tail; the bulk stays hidden.
    assert "abcdefghijklmnopqrstuvwxyz" not in masked
    assert masked.startswith("sk-a") and masked.endswith("7890")


def test_compact_output_is_single_line_valid_json(capsys):
    rc = run_self_describe(_args(compact=True))
    assert rc == 0

    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    assert json.loads(out)["hermes"]["name"] == "Hermes Agent"


def test_enabled_toolsets_reflect_configured_platform(capsys):
    home = get_hermes_home()
    cfg = {"platform_toolsets": {"cli": ["terminal", "file", "memory"]}}
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")

    payload = build_self_description("cli")
    tools = payload["tools"]
    # The explicitly configured toolsets are enabled (superset — the resolver
    # also auto-enables recently-shipped toolsets and the kanban worker set).
    assert {"terminal", "file", "memory"} <= set(tools["enabled_toolsets"])
    assert tools["toolsets"]["terminal"]["enabled"] is True
    assert tools["toolsets"]["memory"]["enabled"] is True
    # Tools enumerated from the registry, not hardcoded.
    assert {"read_file", "write_file"} <= set(tools["toolsets"]["file"]["tools"])
    # A toolset outside the explicit list stays disabled.
    assert tools["toolsets"]["web"]["enabled"] is False


def test_skills_are_listed_with_source_and_status(capsys):
    home = get_hermes_home()
    skill_dir = home / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: A demo skill.\n---\n\n# Demo\n",
        encoding="utf-8",
    )

    payload = build_self_description("cli")
    entries = {s["name"]: s for s in payload["skills"]}
    assert "demo-skill" in entries
    assert entries["demo-skill"]["source"] == "local"
    assert entries["demo-skill"]["status"] == "enabled"


def test_config_overrides_are_redacted(capsys):
    home = get_hermes_home()
    cfg = {
        "platform_toolsets": {"cli": ["terminal"]},
        "custom_providers": {
            "myendpoint": {"api_key": "sk-raw-custom-provider-secret-98765"}
        },
    }
    (home / "config.yaml").write_text(json.dumps(cfg), encoding="utf-8")

    rc = run_self_describe(_args(show_keys=True))
    assert rc == 0
    out = capsys.readouterr().out
    assert "sk-raw-custom-provider-secret-98765" not in out


def test_parser_registers_subcommand():
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_self_describe_parser(subparsers, cmd_self_describe=lambda args: 0)

    args = parser.parse_args(
        ["self-describe", "--platform", "telegram", "--json", "--compact", "--show-keys"]
    )
    assert callable(args.func)
    assert args.platform == "telegram"
    assert args.json is True
    assert args.compact is True
    assert args.show_keys is True
