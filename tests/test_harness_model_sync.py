"""Tests for harness_model_sync — Fleet model picks propagate to native configs.

Behavior contracts (no snapshot tests):
- Each writer preserves unrelated keys/comments in the native config.
- Idempotent: syncing the same pick twice is a no-op.
- Provider-reachability: unreachable pairs are skipped, never mis-written.
- set_worker_model() triggers the sync for both Fleet POST and CLI paths.
"""

import json
import os
import re
from pathlib import Path

import pytest

from hermes_cli import harness_model_sync as hms
from hermes_cli import worker_backend as wb


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Isolate HERMES_HOME and point every native-config path at tmp_path."""
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))

    real_home = tmp_path / "native"
    real_home.mkdir()

    monkeypatch.setattr(hms, "_opencode_config_path", lambda *_: real_home / ".opencode" / "opencode.jsonc")
    monkeypatch.setattr(hms, "_commandcode_config_path", lambda *_: real_home / ".commandcode" / "config.json")
    monkeypatch.setattr(hms, "_dsh_settings_path", lambda *_: real_home / ".dsh" / "settings.yaml")
    monkeypatch.setattr(hms, "_codex_config_path", lambda *_: real_home / ".codex" / "config.toml")
    return home, real_home


# ---------------------------------------------------------------------------
# opencode (JSONC — comment + structure preservation is the contract)
# ---------------------------------------------------------------------------


def _write_opencode(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """{
  // opencode global config
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "openrouter": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "OpenRouter",
      // key comes from env
      "options": { "baseURL": "https://openrouter.ai/api/v1" }
    }
  },
  "model": "opencode-go/deepseek-v4-flash"
}
""",
        encoding="utf-8",
    )


def test_opencode_sync_updates_model_preserving_comments(fake_home):
    _, native = fake_home
    cfg = native / ".opencode" / "opencode.jsonc"
    _write_opencode(cfg)

    changed = hms._sync_opencode("opencode-go", "muse-spark-1.2-contributor")
    assert changed is True

    raw = cfg.read_text(encoding="utf-8")
    assert '"model": "opencode-go/muse-spark-1.2-contributor"' in raw
    # Comments preserved byte-for-byte
    assert "// opencode global config" in raw
    assert "// key comes from env" in raw
    # Unrelated keys intact
    assert '"$schema"' in raw
    assert '"npm": "@ai-sdk/openai-compatible"' in raw


def test_opencode_sync_is_idempotent(fake_home):
    _, native = fake_home
    cfg = native / ".opencode" / "opencode.jsonc"
    _write_opencode(cfg)
    hms._sync_opencode("opencode-go", "muse-spark-1.2-contributor")
    first = cfg.read_text(encoding="utf-8")

    changed = hms._sync_opencode("opencode-go", "muse-spark-1.2-contributor")
    assert changed is False
    assert cfg.read_text(encoding="utf-8") == first


def test_opencode_sync_missing_file_is_noop(fake_home):
    _, native = fake_home
    assert hms._sync_opencode("opencode-go", "x") is False
    assert not (native / ".opencode").exists()


# ---------------------------------------------------------------------------
# commandcode (plain JSON; gateway slug + vendor-prefix mapping contract)
# ---------------------------------------------------------------------------


def _write_commandcode(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"installed": True, "firstMessageSent": True, "provider": "command-code", "model": "meta/muse-spark-1.2-contributor"}
        ),
        encoding="utf-8",
    )


def test_commandcode_sync_maps_vendor_prefix(fake_home):
    _, native = fake_home
    cfg = native / ".commandcode" / "config.json"
    _write_commandcode(cfg)

    assert hms._sync_commandcode("commandcode", "deepseek-v4-flash") is True
    parsed = json.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["provider"] == "command-code"
    assert parsed["model"] == "deepseek/deepseek-v4-flash"
    # Unrelated keys preserved
    assert parsed["installed"] is True
    assert parsed["firstMessageSent"] is True


def test_commandcode_skips_unreachable_provider(fake_home):
    _, native = fake_home
    cfg = native / ".commandcode" / "config.json"
    _write_commandcode(cfg)
    before = cfg.read_text(encoding="utf-8")

    # dsh-only provider must not be written into commandcode's config
    assert hms._sync_commandcode("opencode-go", "deepseek-v4-flash") is False
    assert cfg.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# dsh (YAML round-trip of agent-default-model block)
# ---------------------------------------------------------------------------


def _write_dsh(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """# DeepSeek Harness settings
agent-default-model:
  provider: opencode-go
  model: deepseek-v4-flash

llm-pi-ai:
  providers:
    opencode-go:
      apiKeyEnv: OPENCODE_GO_API_KEY
      baseURL: https://opencode.ai/zen/go/v1
""",
        encoding="utf-8",
    )


def test_dsh_sync_updates_block_preserving_rest(fake_home):
    _, native = fake_home
    cfg = native / ".dsh" / "settings.yaml"
    _write_dsh(cfg)

    assert hms._sync_dsh("opencode-go", "muse-spark-1.2-contributor") is True
    text = cfg.read_text(encoding="utf-8")
    import yaml

    parsed = yaml.safe_load(text)
    assert parsed["agent-default-model"]["model"] == "muse-spark-1.2-contributor"
    assert parsed["agent-default-model"]["provider"] == "opencode-go"
    assert parsed["llm-pi-ai"]["providers"]["opencode-go"]["apiKeyEnv"] == "OPENCODE_GO_API_KEY"


def test_dsh_rejects_non_opencodego_provider(fake_home):
    _, native = fake_home
    cfg = native / ".dsh" / "settings.yaml"
    _write_dsh(cfg)
    before = cfg.read_text(encoding="utf-8")

    assert hms._sync_dsh("openrouter", "stealth/ox-alpha") is False
    assert cfg.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# codex (TOML — surgical scalar rewrite, [projects] tables untouched)
# ---------------------------------------------------------------------------


def _write_codex(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '''model = "gpt-5.6-luna"
model_reasoning_effort = "medium"

[projects."/tmp/codex-test"]
trust_level = "trusted"

[model_providers.opencode-go]
name = "OpenCode-Go"
base_url = "http://127.0.0.1:8899/v1"
env_key = "OPENCODE_GO_API_KEY"

[model_providers.openrouter]
name = "OpenRouter"
''',
        encoding="utf-8",
    )


def test_codex_sync_rewrites_scalars_keeps_tables(fake_home):
    _, native = fake_home
    cfg = native / ".codex" / "config.toml"
    _write_codex(cfg)

    assert hms._sync_codex("opencode-go", "deepseek-v4-pro") is True
    raw = cfg.read_text(encoding="utf-8")
    assert 'model = "deepseek-v4-pro"' in raw
    assert 'model_provider = "opencode-go"' in raw
    # Tables survive verbatim
    assert '[projects."/tmp/codex-test"]' in raw
    assert 'trust_level = "trusted"' in raw
    assert "[model_providers.openrouter]" in raw

    import tomllib

    parsed = tomllib.loads(raw)
    assert parsed["model"] == "deepseek-v4-pro"
    assert parsed["model_provider"] == "opencode-go"


def test_codex_rejects_commandcode_provider(fake_home):
    _, native = fake_home
    cfg = native / ".codex" / "config.toml"
    _write_codex(cfg)
    before = cfg.read_text(encoding="utf-8")

    assert hms._sync_codex("commandcode", "deepseek/deepseek-v4-flash") is False
    assert cfg.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Orchestration: sync_native_configs report shape + reachability
# ---------------------------------------------------------------------------


def test_orchestrator_reports_skip_for_unknown_worker():
    report = hms.sync_native_configs("pi", "openrouter", "stealth/ox-alpha")
    assert report["synced"] == []
    assert any("pi" in s for s in report["skipped"])


def test_orchestrator_rejects_invalid_model_id():
    report = hms.sync_native_configs("opencode", "opencode-go", "has spaces")
    assert report["synced"] == []
    assert report["errors"]


def test_orchestrator_never_raises_on_writer_crash(fake_home, monkeypatch):
    _, native = fake_home
    (native / ".opencode").mkdir(parents=True)

    def boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(hms, "_sync_opencode", boom)
    report = hms.sync_native_configs("opencode", "opencode-go", "deepseek-v4-flash")
    assert report["synced"] == []
    assert any("disk on fire" in e for e in report["errors"])


# ---------------------------------------------------------------------------
# Integration: set_worker_model triggers sync (covers BOTH surfaces —
# Fleet POST endpoint and `hermes workers model` both call this function).
# ---------------------------------------------------------------------------


def test_set_worker_model_propagates_to_native_config(fake_home):
    home, native = fake_home
    cfg = native / ".opencode" / "opencode.jsonc"
    _write_opencode(cfg)

    entry = wb.set_worker_model("opencode", "opencode-go", "muse-spark-1.2-contributor", home)
    assert entry == {"provider": "opencode-go", "model": "muse-spark-1.2-contributor"}

    # Pin persisted...
    pins = wb.load_worker_models(home)
    assert pins["opencode"]["model"] == "muse-spark-1.2-contributor"
    # ...and native config updated
    raw = cfg.read_text(encoding="utf-8")
    assert '"model": "opencode-go/muse-spark-1.2-contributor"' in raw


def test_set_worker_model_pin_survives_sync_failure(fake_home, monkeypatch):
    home, native = fake_home
    (native / ".opencode").mkdir(parents=True)
    monkeypatch.setattr(
        hms, "_sync_opencode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    entry = wb.set_worker_model("opencode", "opencode-go", "deepseek-v4-flash", home)
    assert entry == {"provider": "opencode-go", "model": "deepseek-v4-flash"}
    assert wb.load_worker_models(home)["opencode"]["provider"] == "opencode-go"


def test_fleet_post_endpoint_end_to_end(fake_home):
    """The actual surface the user asked about: dashboard picker → everywhere."""
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    home, native = fake_home
    cfg = native / ".opencode" / "opencode.jsonc"
    _write_opencode(cfg)

    with TestClient(app) as client:
        resp = client.post(
            "/api/fleet/models/opencode",
            headers={_SESSION_HEADER_NAME: _SESSION_TOKEN},
            json={"provider": "opencode-go", "model": "muse-spark-1.2-contributor"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["worker"] == "opencode"
        assert body["provider"] == "opencode-go"
        assert body["model"] == "muse-spark-1.2-contributor"

    # Pin landed...
    assert wb.load_worker_models(home)["opencode"]["model"] == "muse-spark-1.2-contributor"
    # ...and propagated into the native opencode config
    assert '"model": "opencode-go/muse-spark-1.2-contributor"' in cfg.read_text(encoding="utf-8")


def test_clear_worker_model_does_not_touch_native_config(fake_home):
    """Clearing the pin restores harness-default behavior; no native write."""
    home, native = fake_home
    cfg = native / ".opencode" / "opencode.jsonc"
    _write_opencode(cfg)

    assert wb.clear_worker_model("opencode", home) is False
    # No sync attempted on clear → file untouched
    assert '"model": "opencode-go/deepseek-v4-flash"' in cfg.read_text(encoding="utf-8")
