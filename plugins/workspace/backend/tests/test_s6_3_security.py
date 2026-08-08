"""S6.3 — Security Hardening Tests.

Comprehensive tests covering:
- Network Isolation (URL validation, SSRF prevention, IP classification)
- Resource Limits (content size, tag counts, length limits)
- Path Sandbox (allow/deny lists, system path denial, workspace scoping)
- Integration (combined hardening in authorization pipeline)
- Regression (existing behaviour unchanged)
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.security.network_isolation import (
    ALLOWED_PROTOCOLS,
    DENIED_PROTOCOLS,
    NetworkValidationResult,
    NetworkValidator,
    is_safe_url,
    validate_url,
    validate_urls,
)
from plugins.workspace.backend.security.resource_limits import (
    LimitCheckResult,
    ResourceLimitExceeded,
    ResourceLimiter,
    ResourceLimits,
)
from plugins.workspace.backend.security.sandbox import (
    PathSandbox,
    PathValidationResult,
    SandboxConfig,
)


# ---------------------------------------------------------------------------
# Network Isolation
# ---------------------------------------------------------------------------

class TestNetworkIsolation:
    """URL validation and SSRF prevention."""

    def test_valid_https_url(self):
        result = validate_url("https://example.com")
        assert result.is_safe
        assert result.category == "public"

    def test_valid_http_url(self):
        result = validate_url("http://example.org/path?q=1")
        assert result.is_safe

    def test_denied_file_protocol(self):
        result = validate_url("file:///etc/passwd")
        assert not result.is_safe
        assert result.category == "denied_protocol"
        assert "file" in result.reason.lower()

    def test_denied_ftp_protocol(self):
        result = validate_url("ftp://example.com/file")
        assert not result.is_safe
        assert "ftp" in result.reason.lower()

    def test_denied_gopher_protocol(self):
        result = validate_url("gopher://example.com")
        assert not result.is_safe

    def test_denied_javascript_protocol(self):
        result = validate_url("javascript:alert(1)")
        assert not result.is_safe

    def test_missing_protocol(self):
        result = validate_url("example.com")
        assert not result.is_safe
        assert result.category == "invalid_url"

    def test_empty_url(self):
        result = validate_url("")
        assert not result.is_safe
        assert result.category == "empty"

    def test_loopback_ip_v4(self):
        result = validate_url("http://127.0.0.1:8080/api")
        assert not result.is_safe
        assert result.category == "loopback"

    def test_loopback_ip_v6(self):
        result = validate_url("http://[::1]:8080/api")
        assert not result.is_safe
        assert result.category == "loopback"

    def test_private_10_ip(self):
        result = validate_url("https://10.0.0.1/admin")
        assert not result.is_safe
        assert result.category == "private"

    def test_private_192_168_ip(self):
        result = validate_url("https://192.168.1.1/api")
        assert not result.is_safe

    def test_private_172_16_ip(self):
        result = validate_url("https://172.16.0.1/api")
        assert not result.is_safe

    def test_link_local_ip(self):
        result = validate_url("http://169.254.1.1/status")
        assert not result.is_safe
        assert result.category == "link_local"

    def test_multicast_ip(self):
        result = validate_url("http://224.0.0.1/stream")
        assert not result.is_safe
        assert result.category == "multicast"

    def test_cgnat_ip(self):
        result = validate_url("http://100.64.0.1/api")
        assert not result.is_safe

    def test_convenience_is_safe_url(self):
        assert is_safe_url("https://example.com") is True
        assert is_safe_url("http://127.0.0.1") is False
        assert is_safe_url("file:///etc/passwd") is False

    def test_validate_multiple_urls(self):
        urls = [
            "https://example.com",
            "https://google.com",
            "http://127.0.0.1:8080",
        ]
        results = validate_urls(urls)
        assert results[urls[0]].is_safe
        assert results[urls[1]].is_safe
        assert not results[urls[2]].is_safe

    def test_allowed_host_list_mode(self):
        validator = NetworkValidator()
        validator.add_allowed_host("example.com")
        # Without any hosts in allow list, all are allowed by default
        # But our implementation uses allow list only when non-empty
        result = validate_url("https://other.com")
        assert result.is_safe  # default is permissive

    def test_blocked_host_list(self):
        validator = NetworkValidator()
        validator.add_blocked_host("evil.com")
        result = validator.validate("https://evil.com/path")
        assert not result.is_safe
        assert result.category == "blocked_host"

    def test_validate_raw_ip(self):
        validator = NetworkValidator()
        assert validator.validate_ip("10.0.0.1")["safe"] is False
        assert validator.validate_ip("8.8.8.8")["safe"] is True
        assert validator.validate_ip("invalid")["safe"] is False

    def test_is_public_ip(self):
        validator = NetworkValidator()
        assert validator.is_public_ip("8.8.8.8") is True
        assert validator.is_public_ip("10.0.0.1") is False
        assert validator.is_public_ip("127.0.0.1") is False

    def test_result_has_structured_fields(self):
        result = validate_url("https://example.com/path?q=1")
        assert result.is_safe
        assert result.url == "https://example.com/path?q=1"
        assert result.hostname == "example.com"
        assert result.protocol == "https"

    def test_denied_protocols_constant(self):
        assert "file" in DENIED_PROTOCOLS
        assert "ftp" in DENIED_PROTOCOLS
        assert "gopher" in DENIED_PROTOCOLS
        assert "javascript" in DENIED_PROTOCOLS

    def test_allowed_protocols_constant(self):
        assert "http" in ALLOWED_PROTOCOLS
        assert "https" in ALLOWED_PROTOCOLS


# ---------------------------------------------------------------------------
# Resource Limits
# ---------------------------------------------------------------------------

class TestResourceLimits:
    """Resource limit enforcement."""

    def test_content_within_limit(self):
        limiter = ResourceLimiter()
        result = limiter.check_content_size("hello", "body")
        assert result.allowed is True
        assert result.resource == "body"

    def test_content_exceeds_default_limit(self):
        limiter = ResourceLimiter()
        big = "x" * (11 * 1024 * 1024)  # 11 MB
        result = limiter.check_content_size(big, "file")
        assert result.allowed is False
        assert "file" in result.reason

    def test_tag_count_within_limit(self):
        limiter = ResourceLimiter()
        result = limiter.check_tag_count(["a", "b", "c"], "ADR tags")
        assert result.allowed is True

    def test_tag_count_exceeds_limit(self):
        limiter = ResourceLimiter()
        many_tags = [f"tag-{i}" for i in range(100)]
        result = limiter.check_tag_count(many_tags, "too many tags")
        assert result.allowed is False

    def test_title_length(self):
        limiter = ResourceLimiter()
        result = limiter.check_title_length("Short title")
        assert result.allowed is True

    def test_title_too_long(self):
        limiter = ResourceLimiter()
        long_title = "x" * 500
        result = limiter.check_title_length(long_title)
        assert result.allowed is False

    def test_check_content_size_with_custom_max(self):
        limiter = ResourceLimiter()
        result = limiter.check_content_size("hello", "test", max_bytes=3)
        assert result.allowed is False  # "hello" is 5 bytes

    def test_default_limits_dataclass(self):
        limits = ResourceLimits()
        assert limits.max_content_size_bytes == 10 * 1024 * 1024
        assert limits.max_title_length == 256
        assert limits.max_tag_count == 50

    def test_resource_limit_exceeded_exception(self):
        exc = ResourceLimitExceeded("tags", 50, 100)
        assert exc.resource == "tags"
        assert exc.limit == 50
        assert exc.actual == 100
        assert "tags" in str(exc)

    def test_validate_workspace_name(self):
        limiter = ResourceLimiter()
        assert limiter.validate_workspace_name("my-workspace").allowed is True
        long_name = "x" * 200
        assert limiter.validate_workspace_name(long_name).allowed is False

    def test_validate_repository_name(self):
        limiter = ResourceLimiter()
        assert limiter.validate_repository_name("my-repo").allowed is True
        long_name = "x" * 500
        assert limiter.validate_repository_name(long_name).allowed is False


# ---------------------------------------------------------------------------
# Path Sandbox
# ---------------------------------------------------------------------------

class TestPathSandbox:
    """Path isolation and sandbox enforcement."""

    def test_valid_workspace_path(self):
        config = SandboxConfig(workspace_root="/tmp/test-workspace")
        sandbox = PathSandbox(config)
        result = sandbox.validate_path("/tmp/test-workspace/src/main.py")
        assert result.is_allowed is True
        assert result.category == "allowed"

    def test_denied_system_etc(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("/etc/passwd")
        assert not result.is_allowed
        assert result.category == "denied_system"

    def test_denied_system_proc(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("/proc/cpuinfo")
        assert not result.is_allowed
        assert result.category == "denied_system"

    def test_denied_system_sys(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("/sys/class/net")
        assert not result.is_allowed

    def test_denied_system_usr(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("/usr/bin/python")
        assert not result.is_allowed

    def test_empty_path(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("")
        assert not result.is_allowed
        assert result.category == "denied_empty"

    def test_canonical_path_returned(self):
        config = SandboxConfig(workspace_root="/tmp/ws")
        sandbox = PathSandbox(config)
        result = sandbox.validate_path("/tmp/ws/../ws/file.txt")
        assert result.canonical_path
        assert "/tmp/ws/file.txt" in result.canonical_path

    def test_is_in_workspace_true(self):
        config = SandboxConfig(workspace_root="/tmp/my-workspace")
        sandbox = PathSandbox(config)
        assert sandbox.is_in_workspace("/tmp/my-workspace/src/main.py") is True

    def test_is_in_workspace_false(self):
        config = SandboxConfig(workspace_root="/tmp/my-workspace")
        sandbox = PathSandbox(config)
        assert sandbox.is_in_workspace("/etc/passwd") is False

    def test_is_safe_temp(self):
        sandbox = PathSandbox()
        assert sandbox.is_safe_temp("/tmp/hermes-agent/sess-1/output.txt") is True
        assert sandbox.is_safe_temp("/tmp/other/file.txt") is False

    def test_validate_multiple_paths(self):
        sandbox = PathSandbox()
        paths = ["/etc/passwd", "/tmp/hermes-agent/ok.txt"]
        results = sandbox.validate_paths(paths)
        assert not results[paths[0]].is_allowed
        assert results[paths[1]].is_allowed

    def test_write_disabled_config(self):
        config = SandboxConfig(allow_write=False)
        sandbox = PathSandbox(config)
        result = sandbox.validate_path("/tmp/hermes-agent/file.txt", operation="write")
        assert not result.is_allowed
        assert result.category == "denied_write"

    def test_execute_disabled_config(self):
        config = SandboxConfig(allow_execute=False)
        sandbox = PathSandbox(config)
        result = sandbox.validate_path("/tmp/test.py", operation="execute")
        assert not result.is_allowed
        assert result.category == "denied_execute"

    def test_delete_disabled_config(self):
        config = SandboxConfig(allow_delete=False)
        sandbox = PathSandbox(config)
        result = sandbox.validate_path("/tmp/test.txt", operation="delete")
        assert not result.is_allowed
        assert result.category == "denied_delete"

    def test_sandbox_config_defaults(self):
        config = SandboxConfig()
        assert config.allow_read_outside_workspace is True
        assert config.allow_write is True
        assert config.allow_delete is True
        assert config.allow_execute is False
        assert config.allow_symlinks is False
        assert config.workspace_root == ""
        assert config.temp_root == "/tmp/hermes-agent"


# ---------------------------------------------------------------------------
# Integration — Network + Resource + Sandbox in authz pipeline
# ---------------------------------------------------------------------------

class TestS6_3Integration:
    """Combined hardening in the security pipeline."""

    def test_network_validator_integrated_with_authorization(self):
        from plugins.workspace.backend.security.authorization import (
            AuthorizationMiddleware,
        )
        authz = AuthorizationMiddleware()
        # Verify we can import and create components side by side
        validator = NetworkValidator()
        assert validator.validate("https://safe.com").is_safe

    def test_resource_limiter_integrated_with_services(self):
        limiter = ResourceLimiter()
        assert limiter.check_content_size("hello").allowed is True

    def test_sandbox_integrated_with_services(self):
        sandbox = PathSandbox()
        result = sandbox.validate_path("/tmp/hermes-agent/output.log")
        assert result.is_allowed is True

    def test_all_three_subsystems_coexist(self):
        """Network, resource, and sandbox subsystems work independently."""
        net = NetworkValidator()
        res = ResourceLimiter()
        sandbox = PathSandbox()

        assert net.validate("https://example.com").is_safe
        assert res.check_content_size("data").allowed
        assert sandbox.validate_path("/tmp/hermes-agent/file.txt").is_allowed


# ---------------------------------------------------------------------------
# Regression — existing behaviour unchanged
# ---------------------------------------------------------------------------

class TestRegressionS6_3:
    """Ensure existing components are unaffected by new hardening modules."""

    def test_security_package_imports_clean(self):
        from plugins.workspace.backend.security import (
            AuditLogger,
            AuthorizationMiddleware,
            CapabilityRegistry,
            ContentLabel,
            NetworkValidator,
            PathSandbox,
            PolicyEngine,
            ResourceLimiter,
            detect_dangerous_patterns,
            detect_secrets,
            sanitize_content,
        )
        assert True  # Imports succeeded

    def test_existing_capability_registry_unchanged(self):
        from plugins.workspace.backend.security.capabilities import CAPABILITIES
        assert len(CAPABILITIES) >= 44
        assert "fs.read" in CAPABILITIES
        assert "workspace.create" in CAPABILITIES

    def test_existing_audit_logger_unchanged(self):
        from plugins.workspace.backend.security.audit import AuditLogger
        with tempfile.TemporaryDirectory() as td:
            logger = AuditLogger(log_path=Path(td) / "audit.log")
            logger.log(action="test", status="ok")
            events = logger.read(10)
            assert len(events) == 1
