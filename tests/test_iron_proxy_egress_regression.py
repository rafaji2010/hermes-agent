"""M13.4 — iron-proxy egress regression tests.

Two invariants, mirroring the structure of ``test_iron_proxy_e2e.py``:

1. **The sandbox never sees a real credential.** ``build_proxy_config`` and
   ``write_mappings`` reference upstream secrets by *env-var name* and hand the
   sandbox only minted proxy tokens.  This is hermetic — it asserts on the
   serialized ``proxy.yaml`` / ``mappings.json`` and never needs the binary.

2. **Non-allowlisted hosts are blocked.** With the real binary running and
   ``allowed_hosts=[127.0.0.1]``, a request to any other host is rejected by
   the allowlist transform (403) before it ever reaches upstream.

The E2E half skips gracefully when the ``iron-proxy`` binary (and its curl /
openssl prerequisites) are absent, rather than failing the suite.  Install it
with ``hermes egress install`` to exercise it for real.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

import pytest

from agent.proxy_sources import iron_proxy as ip


# ---------------------------------------------------------------------------
# Availability flags (computed once at import for `skipif`).
# `find_iron_proxy()` resolves the managed binary under HERMES_HOME first, then
# falls back to the system PATH — with no network / auto-install involved.
# ---------------------------------------------------------------------------
_HAVE_BINARY = ip.find_iron_proxy() is not None
_HAVE_CURL = shutil.which("curl") is not None
_HAVE_OPENSSL = shutil.which("openssl") is not None

_E2E_SKIP = (
    not _HAVE_BINARY or not _HAVE_CURL or not _HAVE_OPENSSL
)


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at a temp dir so the proxy state dir, CA, and config
    never touch the real ``~/.hermes``.  Also blank any provider-shaped env
    vars so discovery can't read a real developer key."""
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in list(os.environ):
        if key.endswith("_API_KEY") or key in ("BWS_ACCESS_TOKEN",):
            monkeypatch.delenv(key, raising=False)
    return home


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# 1. Sandbox holds no real keys (hermetic — always runs)
# ---------------------------------------------------------------------------


def test_sandbox_config_holds_no_real_secrets(hermes_home, monkeypatch):
    """The serialized egress config must never embed a real credential.

    The proxy swaps a sandbox-visible token for a secret it reads from its OWN
    environment at egress time.  The secret's *name* may appear (``source.var``
    / ``env_name``), but its *value* must never be written to ``proxy.yaml``
    or ``mappings.json`` — those are the files a sandbox could read back.
    """
    real_secret = "sk-egress-real-secret-9f8e7d6c5b4a"
    monkeypatch.setenv("TEST_EGRESS_REAL_KEY", real_secret)

    proxy_token = ip.mint_proxy_token("egress")
    mapping = ip.TokenMapping(
        proxy_token=proxy_token,
        real_env_name="TEST_EGRESS_REAL_KEY",
        upstream_hosts=("api.example.com",),
    )

    cfg = ip.build_proxy_config(
        mappings=[mapping],
        ca_cert=hermes_home / "ca.crt",
        ca_key=hermes_home / "ca.key",
        allowed_hosts=["api.example.com"],
    )
    config_path = ip.write_proxy_config(cfg)
    mappings_path = ip.write_mappings([mapping])

    config_text = config_path.read_text(encoding="utf-8")
    mappings_text = mappings_path.read_text(encoding="utf-8")

    # The real secret value must not leak into either file.
    assert real_secret not in config_text, (
        "real secret value leaked into proxy.yaml"
    )
    assert real_secret not in mappings_text, (
        "real secret value leaked into mappings.json"
    )

    # The config references the secret by NAME, and only hands out the token.
    assert "TEST_EGRESS_REAL_KEY" in config_text
    assert proxy_token in config_text

    # mappings.json carries the minted token + the env NAME, never the value.
    assert proxy_token in mappings_text
    assert "TEST_EGRESS_REAL_KEY" in mappings_text

    # Round-trip sanity: the token the sandbox would receive is the minted one,
    # not the real secret (they must differ, which is the whole point).
    loaded = ip.load_mappings()
    assert loaded and loaded[0].proxy_token == proxy_token
    assert loaded[0].real_env_name == "TEST_EGRESS_REAL_KEY"
    assert loaded[0].proxy_token != real_secret


def test_sandbox_config_references_no_other_env_secret(hermes_home, monkeypatch):
    """The sandbox-facing config must not point at any *unrelated* host secret.

    Only the env var named in the mapping may be referenced by the secrets
    transform — a stray host credential name in the config would mean the
    sandbox could steer the proxy into swapping a secret it shouldn't have.
    """
    monkeypatch.setenv("TEST_EGRESS_REAL_KEY", "sk-real")
    mapping = ip.TokenMapping(
        proxy_token=ip.mint_proxy_token("egress"),
        real_env_name="TEST_EGRESS_REAL_KEY",
        upstream_hosts=("api.example.com",),
    )
    cfg = ip.build_proxy_config(
        mappings=[mapping],
        ca_cert=hermes_home / "ca.crt",
        ca_key=hermes_home / "ca.key",
        allowed_hosts=["api.example.com"],
    )

    referenced_vars = [
        rule["source"]["var"]
        for transform in cfg["transforms"]
        if transform["name"] == "secrets"
        for rule in transform["config"]["secrets"]
    ]
    assert referenced_vars == ["TEST_EGRESS_REAL_KEY"]


# ---------------------------------------------------------------------------
# 2. Non-allowlisted hosts are blocked (E2E — skips if the binary is absent)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    _E2E_SKIP,
    reason=(
        "E2E iron-proxy regression requires the iron-proxy binary, curl, and "
        "openssl — install with `hermes egress install`"
    ),
)
def test_non_allowlisted_host_is_blocked(hermes_home, monkeypatch):
    """The allowlist transform must 403 any host outside ``allowed_hosts``.

    The proxy is configured to allow only ``127.0.0.1``; a request to a
    different host must be rejected (HTTP 403) by the proxy itself, before any
    connection to that host is attempted.
    """
    ca_crt, ca_key = ip.ensure_ca_cert()
    assert ca_crt.exists()

    monkeypatch.setenv("TEST_EGRESS_KEY", "sk-real")
    mapping = ip.TokenMapping(
        proxy_token=ip.mint_proxy_token("egress"),
        real_env_name="TEST_EGRESS_KEY",
        upstream_hosts=("127.0.0.1",),
    )

    tunnel_port = _free_port()
    cfg = ip.build_proxy_config(
        mappings=[mapping],
        ca_cert=ca_crt,
        ca_key=ca_key,
        tunnel_port=tunnel_port,
        allowed_hosts=["127.0.0.1"],
        # Hermetic: clear the default IMDS/RFC1918 deny list so the test is
        # exercising the ALLOWLIST transform, not the CIDR deny list.
        upstream_deny_cidrs=[],
        http_listen=[f"127.0.0.1:{tunnel_port}"],
    )
    ip.write_proxy_config(cfg)
    ip.write_mappings([mapping])

    try:
        status = ip.start_proxy()
    except RuntimeError as exc:
        pytest.skip(f"iron-proxy could not start in this environment: {exc}")
    assert status.pid is not None

    try:
        for _ in range(50):
            if ip._port_listening("127.0.0.1", tunnel_port):
                break
            time.sleep(0.2)
        else:
            pytest.fail("iron-proxy never started listening on the tunnel port")

        # A host OUTSIDE the allowlist, requested through the plain-HTTP
        # forward listener (tunnel_port + 1).  The allowlist transform rejects
        # it with 403 before dialing — so no fake upstream is needed and the
        # external host is never contacted.
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--max-time", "10",
                "-o", "/dev/null",
                "-w", "%{http_code}",
                "-x", f"http://127.0.0.1:{tunnel_port + 1}",
                "http://example.com/",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"curl failed: {result.stderr}"
        code = result.stdout.strip()
        assert code == "403", (
            f"non-allowlisted host returned HTTP {code}; expected 403 block "
            "from the allowlist transform"
        )
    finally:
        try:
            ip.stop_proxy()
        except Exception:
            pass
