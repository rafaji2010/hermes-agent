"""Tests for Fleet model picker (§ per-harness model picker)."""
import json
import os
import time
import stat
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_constants import get_hermes_home

# Post-decomposition the fleet-models fetch state lives in
# hermes_cli.web_routers.fleet. Reads via ``ws.<name>`` resolve through the
# web_server lazy seam to the same objects; REBINDS must target the owner
# module directly (module-level rebinding cannot be forwarded).
from hermes_cli.web_routers import fleet as _fleet_owner


@pytest.fixture(autouse=True)
def _reset_fleet_models_cache():
    import hermes_cli.web_server as ws
    ws._FLEET_MODELS_CACHE.clear()
    _fleet_owner._FLEET_MODELS_AT = None
    _fleet_owner._FLEET_MODELS_TASK = None
    # also reset events store
    ws._fleet_events.clear()
    ws._fleet_seen.clear()
    ws._fleet_next_seq = 1
    yield
    ws._FLEET_MODELS_CACHE.clear()
    _fleet_owner._FLEET_MODELS_AT = None
    _fleet_owner._FLEET_MODELS_TASK = None


@pytest.fixture()
def _web_client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN
    import hermes_cli.web_server as ws
    ws._fleet_events.clear()
    ws._fleet_seen.clear()
    ws._fleet_next_seq = 1
    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


@pytest.fixture()
def _unauth_client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    from hermes_cli.web_server import app
    return TestClient(app)


def _fake_urlopen_factory(mapping, call_log=None):
    """Return a fake urlopen that uses per-URL mapping or raises."""
    import urllib.request
    import urllib.error
    import io

    class FakeResp:
        def __init__(self, data_obj):
            self._bytes = json.dumps(data_obj).encode("utf-8")
        def read(self):
            return self._bytes
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=5):
        # req is urllib.request.Request
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if call_log is not None:
            call_log.append(url)
        cfg = mapping.get(url, None)
        if cfg is None:
            raise urllib.error.URLError(f"unknown url {url}")
        if isinstance(cfg, Exception):
            raise cfg
        # cfg is dict payload
        return FakeResp(cfg)

    return fake_urlopen


class TestFleetModelsCatalog:
    def test_returns_three_providers(self, _web_client, monkeypatch):
        import urllib.request
        mapping = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "m1"}, {"id": "m2"}]},
            "https://api.commandcode.ai/provider/v1/models": {"data": [{"id": "c1"}]},
            "https://openrouter.ai/api/v1/models": {"data": [{"id": "anthropic/claude-3.7-sonnet"}]},
        }
        call_log = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping, call_log))
        resp = _web_client.get("/api/fleet/models")
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body
        assert "opencode-go" in body["providers"]
        assert "commandcode" in body["providers"]
        assert "openrouter" in body["providers"]
        assert body["providers"]["opencode-go"]["models"] == ["m1", "m2"]
        assert body["providers"]["commandcode"]["models"] == ["c1"]
        assert "errors" not in body or body["errors"] == {}
        # Cache-Control header
        assert "Cache-Control" in resp.headers
        assert "max-age=3600" in resp.headers["Cache-Control"]
        # No key leakage
        txt = json.dumps(body)
        assert "Bearer" not in txt
        assert "OPENCODE_GO_API_KEY" not in txt
        assert call_log  # at least one fetch

    def test_partial_failure_keeps_successes_plus_errors(self, _web_client, monkeypatch):
        import urllib.request, urllib.error
        mapping = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "ok-model"}]},
            "https://api.commandcode.ai/provider/v1/models": urllib.error.URLError("timeout"),
            "https://openrouter.ai/api/v1/models": {"data": [{"id": "or-model"}]},
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping))
        resp = _web_client.get("/api/fleet/models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["providers"]["opencode-go"]["models"] == ["ok-model"]
        assert body["providers"]["openrouter"]["models"] == ["or-model"]
        # failing provider still present with empty models + error entry
        assert "commandcode" in body["providers"]
        assert body["providers"]["commandcode"]["models"] == []
        assert "errors" in body
        assert "commandcode" in body["errors"]
        # Never 500 entire catalog
        # must-revalidate on partial error
        assert "must-revalidate" in resp.headers.get("Cache-Control", "")

    def test_never_500_even_all_fail(self, _web_client, monkeypatch):
        import urllib.request, urllib.error
        mapping = {
            "https://opencode.ai/zen/go/v1/models": urllib.error.URLError("down"),
            "https://api.commandcode.ai/provider/v1/models": urllib.error.URLError("down"),
            "https://openrouter.ai/api/v1/models": urllib.error.URLError("down"),
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping))
        resp = _web_client.get("/api/fleet/models")
        assert resp.status_code == 200
        body = resp.json()
        # all three keys present even on total failure
        for prov in ("opencode-go", "commandcode", "openrouter"):
            assert prov in body["providers"]
            assert body["providers"][prov]["models"] == []
        assert "errors" in body
        assert len(body["errors"]) == 3

    def test_no_key_in_response_or_logs(self, _web_client, monkeypatch, caplog):
        import urllib.request
        # Inject a fake key in env path via load_env monkeypatch
        import hermes_cli.web_server as ws
        orig_load = ws.load_env
        monkeypatch.setattr(ws, "load_env", lambda: {"OPENCODE_GO_API_KEY": "OPENC_SUPER_SECRET_123"})
        mapping = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "m"}]},
            "https://api.commandcode.ai/provider/v1/models": {"data": [{"id": "c"}]},
            "https://openrouter.ai/api/v1/models": {"data": [{"id": "o"}]},
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping))
        resp = _web_client.get("/api/fleet/models")
        body = resp.json()
        txt = json.dumps(body) + str(resp.headers) + caplog.text
        assert "OPENC_SUPER_SECRET_123" not in txt
        assert "Bearer" not in txt or "[REDACTED]" in txt or "Bearer" not in json.dumps(body)

    def test_401_without_token(self, _unauth_client, monkeypatch):
        import urllib.request
        mapping = {
            "https://opencode.ai/zen/go/v1/models": {"data": []},
            "https://api.commandcode.ai/provider/v1/models": {"data": []},
            "https://openrouter.ai/api/v1/models": {"data": []},
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping))
        resp = _unauth_client.get("/api/fleet/models")
        assert resp.status_code == 401

    def test_cache_ttl_no_refetch_second_call(self, _web_client, monkeypatch):
        import urllib.request
        mapping = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "x"}]},
            "https://api.commandcode.ai/provider/v1/models": {"data": [{"id": "y"}]},
            "https://openrouter.ai/api/v1/models": {"data": [{"id": "z"}]},
        }
        call_log: list[str] = []
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping, call_log))
        r1 = _web_client.get("/api/fleet/models")
        assert r1.status_code == 200
        first_count = len(call_log)
        assert first_count == 3
        # second call within TTL should not re-fetch
        call_log.clear()
        r2 = _web_client.get("/api/fleet/models")
        assert r2.status_code == 200
        assert len(call_log) == 0
        # body same
        assert r1.json()["providers"] == r2.json()["providers"]

    def test_stale_serve_on_failure_after_success(self, _web_client, monkeypatch):
        import urllib.request, urllib.error
        import hermes_cli.web_server as ws
        # First successful fetch
        mapping_ok = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "stale-ok"}]},
            "https://api.commandcode.ai/provider/v1/models": {"data": [{"id": "cc-ok"}]},
            "https://openrouter.ai/api/v1/models": {"data": [{"id": "or-ok"}]},
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping_ok))
        r1 = _web_client.get("/api/fleet/models")
        assert r1.status_code == 200
        # Expire TTL
        _fleet_owner._FLEET_MODELS_AT = time.time() - 4000
        # Now fail only openrouter
        mapping_partial = {
            "https://opencode.ai/zen/go/v1/models": {"data": [{"id": "new-ok"}]},
            "https://api.commandcode.ai/provider/v1/models": {"data": [{"id": "cc-ok"}]},
            "https://openrouter.ai/api/v1/models": urllib.error.URLError("timeout"),
        }
        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(mapping_partial))
        r2 = _web_client.get("/api/fleet/models")
        assert r2.status_code == 200
        body = r2.json()
        # opencode-go refreshed, openrouter served stale
        assert body["providers"]["opencode-go"]["models"] == ["new-ok"]
        assert body["providers"]["openrouter"]["models"] == ["or-ok"]
        assert "openrouter" in body["errors"]


class TestFleetModelsPersistence:
    def test_post_persists_atomically_0600(self, _web_client, monkeypatch):
        import urllib.request, stat as _stat
        # prime catalog so validation passes
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {
            "opencode-go": {"models": ["m1", "m2"]},
            "commandcode": {"models": ["c1"]},
            "openrouter": {"models": ["anthropic/claude-3.7-sonnet"]},
        }
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp = _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "m1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["worker"] == "opencode"
        assert body["provider"] == "opencode-go"
        assert body["model"] == "m1"
        # File exists, 0o600
        hm = get_hermes_home()
        p = hm / "worker_models.json"
        assert p.is_file()
        st = p.stat()
        # On POSIX, check permission bits include 0o600 (may have extra due to umask but should be 0o600)
        assert oct(_stat.S_IMODE(st.st_mode)) == "0o600"
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["opencode"]["provider"] == "opencode-go"
        assert data["opencode"]["model"] == "m1"

    def test_unknown_worker_404(self, _web_client, monkeypatch):
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": ["m1"]}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp = _web_client.post("/api/fleet/models/notaworker", json={"provider": "opencode-go", "model": "m1"})
        assert resp.status_code == 404

    def test_unknown_model_400_only_when_catalog_populated(self, _web_client, monkeypatch):
        import hermes_cli.web_server as ws
        # catalog populated for opencode-go
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": ["known"]}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp = _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "unknown-xyz"})
        assert resp.status_code == 400
        # When catalog empty for that provider, allow unknown
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": []}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp2 = _web_client.post("/api/fleet/models/opencode", json={"provider": "openrouter", "model": "any-model-123"})
        # openrouter catalog empty → allowed
        assert resp2.status_code == 200

    def test_get_worker_returns_current(self, _web_client, monkeypatch):
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": ["m1"]}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "m1"})
        r = _web_client.get("/api/fleet/models/opencode")
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "opencode-go"
        assert body["model"] == "m1"

    def test_corrupted_json_fallback(self, _web_client, monkeypatch):
        hm = get_hermes_home()
        p = hm / "worker_models.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{ not json", encoding="utf-8")
        from hermes_cli.worker_backend import load_worker_models
        data = load_worker_models()
        assert data == {}
        # backup exists
        backs = list(hm.glob("worker_models.json.corrupt.*"))
        assert len(backs) >= 1
        # GET should still work (returns default)
        r = _web_client.get("/api/fleet/models/opencode")
        assert r.status_code == 200
        assert r.json()["provider"] == ""
        # POST should repair file
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": ["m1"]}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp = _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "m1"})
        assert resp.status_code == 200
        assert p.is_file()
        repaired = json.loads(p.read_text(encoding="utf-8"))
        assert repaired["opencode"]["model"] == "m1"

    def test_delete_clears(self, _web_client, monkeypatch):
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": ["m1"]}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "m1"})
        r = _web_client.delete("/api/fleet/models/opencode")
        assert r.status_code == 200
        r2 = _web_client.get("/api/fleet/models/opencode")
        assert r2.json()["model"] == ""

    def test_401_without_token_on_post(self, _unauth_client):
        resp = _unauth_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "m1"})
        assert resp.status_code == 401

    def test_invalid_model_regex_rejected(self, _web_client, monkeypatch):
        import hermes_cli.web_server as ws
        _fleet_owner._FLEET_MODELS_CACHE = {"opencode-go": {"models": []}, "commandcode": {"models": []}, "openrouter": {"models": []}}
        _fleet_owner._FLEET_MODELS_AT = time.time()
        resp = _web_client.post("/api/fleet/models/opencode", json={"provider": "opencode-go", "model": "bad model with spaces!"})
        assert resp.status_code == 400

    def test_invalid_provider_rejected(self, _web_client, monkeypatch):
        resp = _web_client.post("/api/fleet/models/opencode", json={"provider": "unknown", "model": "m1"})
        assert resp.status_code == 400


class TestWorkerConfigPrecedence:
    def test_constraints_wins(self, tmp_path):
        from hermes_cli.worker_backend import _worker_config, set_worker_model
        home = tmp_path / "hm"
        home.mkdir()
        # worker_models has foo, config.yaml has bar, but constraints should win
        set_worker_model("opencode", "openrouter", "file-model", home)
        (home / "config.yaml").write_text("workers:\n  opencode:\n    model: yaml-model\n    provider: openrouter\n", encoding="utf-8")
        m, p = _worker_config("opencode", {"model": "constraint-model", "provider": "commandcode"}, home)
        assert m == "constraint-model"
        assert p == "commandcode"

    def test_file_wins_over_yaml(self, tmp_path):
        from hermes_cli.worker_backend import _worker_config, set_worker_model
        home = tmp_path / "hm"
        home.mkdir()
        set_worker_model("opencode", "opencode-go", "file-model", home)
        (home / "config.yaml").write_text("workers:\n  opencode:\n    model: yaml-model\n    provider: yaml-provider\n", encoding="utf-8")
        m, p = _worker_config("opencode", {}, home)
        assert m == "file-model"
        assert p == "opencode-go"

    def test_yaml_fallback(self, tmp_path):
        from hermes_cli.worker_backend import _worker_config
        home = tmp_path / "hm"
        home.mkdir()
        (home / "config.yaml").write_text("workers:\n  opencode:\n    model: yaml-model\n    provider: yaml-provider\n", encoding="utf-8")
        m, p = _worker_config("opencode", {}, home)
        assert m == "yaml-model"
        assert p == "yaml-provider"

    def test_empty_returns_empty(self, tmp_path):
        from hermes_cli.worker_backend import _worker_config
        home = tmp_path / "hm"
        home.mkdir()
        m, p = _worker_config("opencode", {}, home)
        assert m == ""
        assert p == ""


class TestPerHarnessWiring:
    def test_commandcode_passes_minus_m(self, monkeypatch):
        from hermes_cli.worker_backend import CommandCodeBackend, WorkerSpec, set_worker_model
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override, get_hermes_home
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        # Use home override so _worker_config sees it
        token = set_hermes_home_override(str(tmp))
        try:
            set_worker_model("commandcode", "commandcode", "my-model-123", tmp)
            backend = CommandCodeBackend()
            req = WorkerSpec(worker_type="commandcode", task="hello")
            cmd = backend._build_command(req)
            assert "-m" in cmd
            idx = cmd.index("-m")
            assert cmd[idx+1] == "my-model-123"
        finally:
            reset_hermes_home_override(token)

    def test_opencode_passes_model(self, monkeypatch):
        from hermes_cli.worker_backend import OpencodeBackend, WorkerSpec, set_worker_model
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        token = set_hermes_home_override(str(tmp))
        try:
            set_worker_model("opencode", "opencode-go", "opencode-model-xyz", tmp)
            backend = OpencodeBackend()
            req = WorkerSpec(worker_type="opencode", task="do it", workspace="/tmp/ws")
            cmd = backend._build_command(req)
            assert "--model" in cmd
            idx = cmd.index("--model")
            assert cmd[idx+1] == "opencode-model-xyz"
        finally:
            reset_hermes_home_override(token)

    def test_pi_supports_model_flag_or_skips(self, monkeypatch):
        from hermes_cli.worker_backend import PiBackend, WorkerSpec, set_worker_model
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        token = set_hermes_home_override(str(tmp))
        try:
            # Pi model pin
            set_worker_model("pi", "openrouter", "pi-model", tmp)
            backend = PiBackend()
            # Force pi to claim it supports --model
            import hermes_cli.worker_backend as wb
            orig = wb._pi_supports_model
            monkeypatch.setattr(wb, "_pi_supports_model", lambda: True)
            req = WorkerSpec(worker_type="pi", task="hello")
            cmd = backend._build_command(req)
            assert "--model" in cmd
            # When not supported, no --model
            monkeypatch.setattr(wb, "_pi_supports_model", lambda: False)
            cmd2 = backend._build_command(req)
            assert "--model" not in cmd2
        finally:
            reset_hermes_home_override(token)

    def test_atomic_write_and_malformed_fallback(self, tmp_path):
        from hermes_cli.worker_backend import set_worker_model, load_worker_models
        home = tmp_path / "hm"
        home.mkdir()
        set_worker_model("opencode", "opencode-go", "m1", home)
        p = home / "worker_models.json"
        assert p.is_file()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["opencode"]["model"] == "m1"
        # corrupt
        p.write_text("{ bad", encoding="utf-8")
        data2 = load_worker_models(home)
        assert data2 == {}
        # next write repairs
        set_worker_model("opencode", "opencode-go", "m2", home)
        data3 = json.loads(p.read_text(encoding="utf-8"))
        assert data3["opencode"]["model"] == "m2"

