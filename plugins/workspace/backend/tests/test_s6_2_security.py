"""S6.2 — Security Enforcement & Policy Engine Tests.

Comprehensive tests covering:
- Policy Engine (allow, deny, approval required)
- Authorization Middleware (allowed, denied, audit generation, approval)
- Capability Enforcement (unknown, audited, approval)
- Trust Labels (propagation, preservation)
- Security Exceptions
- Service Integration (authorized write ops via middleware)
- Regression (existing behaviour is unchanged when no authz is wired)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Generator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.security.exceptions import (
    ApprovalRequired,
    AuthorizationDenied,
    CapabilityNotFound,
    PolicyViolation,
    SecurityError,
)
from plugins.workspace.backend.security.policy import (
    PolicyDecision,
    PolicyEngine,
)
from plugins.workspace.backend.security.authorization import (
    AuthorizationMiddleware,
)
from plugins.workspace.backend.security.capabilities import (
    CAPABILITIES,
    CapabilityDef,
    CapabilityRegistry,
)
from plugins.workspace.backend.security.audit import AuditLogger, get_audit_logger
from plugins.workspace.backend.security.labels import (
    ContentLabel,
    label_content,
    get_label_prefix,
    format_safe_boundary,
    LABEL_WEB_CONTENT,
)
from plugins.workspace.backend.services.workspace_service import WorkspaceService
from plugins.workspace.backend.services.adr_service import ADRService
from plugins.workspace.backend.services.task_service import TaskService
from plugins.workspace.backend.models import (
    WorkspaceCreate,
    ADRCreate,
    TaskCreate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def registry():
    return CapabilityRegistry()


@pytest.fixture
def audit_logger():
    with tempfile.TemporaryDirectory() as td:
        yield AuditLogger(log_path=Path(td) / "audit.log")


@pytest.fixture
def engine(registry):
    return PolicyEngine(registry=registry)


@pytest.fixture
def authz(registry, audit_logger):
    return AuthorizationMiddleware(registry=registry, audit_logger=audit_logger)


# ---------------------------------------------------------------------------
# PolicyDecision
# ---------------------------------------------------------------------------

class TestPolicyDecision:
    def test_allow_factory(self):
        d = PolicyDecision.allow(capability="fs.read")
        assert d.allowed is True
        assert d.requires_approval is False
        assert d.audited is False
        assert d.capability == "fs.read"
        assert d.decision_id

    def test_deny_factory(self):
        d = PolicyDecision.deny(capability="shell.sudo", reason="Not admin")
        assert d.allowed is False
        assert d.requires_approval is False
        assert d.audited is True
        assert "Not admin" in d.reason

    def test_require_approval_factory(self):
        d = PolicyDecision.require_approval(capability="fs.write")
        assert d.allowed is True
        assert d.requires_approval is True
        assert d.audited is True

    def test_allow_audited_factory(self):
        d = PolicyDecision.allow_audited(capability="adr.create")
        assert d.allowed is True
        assert d.requires_approval is False
        assert d.audited is True

    def test_unique_decision_ids(self):
        d1 = PolicyDecision.allow("a")
        d2 = PolicyDecision.allow("b")
        assert d1.decision_id != d2.decision_id


# ---------------------------------------------------------------------------
# Policy Engine — allow, deny, approval
# ---------------------------------------------------------------------------

class TestPolicyEngine:
    def test_allow_tier1_no_approval(self, engine):
        decision = engine.evaluate("fs.read")
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_approval_required_tier2(self, engine):
        decision = engine.evaluate("fs.write")
        assert decision.allowed is True
        assert decision.requires_approval is True
        assert decision.audited is True

    def test_audited_capability(self, engine):
        decision = engine.evaluate("workspace.create")
        assert decision.allowed is True
        assert decision.requires_approval is False
        assert decision.audited is True

    def test_unknown_capability_raises(self, engine):
        with pytest.raises(CapabilityNotFound) as exc:
            engine.evaluate("nonexistent.cap")
        assert "nonexistent.cap" in str(exc.value)
        assert exc.value.capability == "nonexistent.cap"

    def test_custom_rule_allows(self, registry):
        engine = PolicyEngine(registry=registry)

        def block_fs_write(cap_id, ctx):
            if cap_id == "fs.write":
                return PolicyDecision.deny(cap_id, "Blocked by custom rule")
            return None

        engine.add_rule(block_fs_write)
        decision = engine.evaluate("fs.write")
        assert decision.allowed is False
        assert "Blocked by custom rule" in decision.reason

    def test_custom_rule_takes_precedence(self, registry):
        engine = PolicyEngine(registry=registry)

        def force_allow(cap_id, ctx):
            if cap_id == "shell.sudo":
                return PolicyDecision.allow(cap_id, "Admin override")
            return None

        engine.add_rule(force_allow)
        decision = engine.evaluate("shell.sudo")
        assert decision.allowed is True
        assert decision.requires_approval is False  # overrode default

    def test_default_deny_policy(self, registry):
        engine = PolicyEngine(registry=registry, default_allow=False)
        decision = engine.evaluate("fs.read")  # tier 1, auto-approve normally
        assert decision.allowed is False
        assert "Default-deny" in decision.reason

    def test_clear_rules_restores_default(self, registry):
        engine = PolicyEngine(registry=registry)

        def block_all(cap_id, ctx):
            return PolicyDecision.deny(cap_id, "Blocked")
        engine.add_rule(block_all)
        assert engine.evaluate("fs.read").allowed is False

        engine.clear_rules()
        assert engine.evaluate("fs.read").allowed is True


# ---------------------------------------------------------------------------
# Authorization Middleware — allowed / denied / audit
# ---------------------------------------------------------------------------

class TestAuthorizationMiddleware:
    def test_authorize_allowed(self, authz):
        decision = authz.authorize("fs.read")
        assert decision.allowed is True
        assert decision.requires_approval is False

    def test_authorize_approval_required(self, authz):
        decision = authz.authorize("fs.write")
        assert decision.allowed is True
        assert decision.requires_approval is True

    def test_authorize_emits_audit(self, authz, audit_logger):
        authz.authorize("workspace.create", resource_type="workspace",
                        resource_id="ws-1", session_id="sess-test")
        events = audit_logger.read(10)
        assert len(events) >= 1
        ev = events[-1]
        assert ev["action"] == "authorize.workspace.create"
        assert ev["status"] == "ALLOW"
        assert ev["resource_type"] == "workspace"
        assert ev["resource_id"] == "ws-1"
        assert ev["session_id"] == "sess-test"

    def test_authorize_denied_emits_audit(self, authz, audit_logger):
        # Add a blocking rule to the engine
        authz.engine.add_rule(
            lambda cap_id, ctx: PolicyDecision.deny(cap_id, "Test deny")
        )
        decision = authz.authorize("fs.read", session_id="s1")
        assert decision.allowed is False
        events = audit_logger.read(10)
        denials = [e for e in events if e["status"] == "DENY"]
        assert len(denials) >= 1
        assert denials[-1]["action"] == "authorize.fs.read"

    def test_guard_allowed(self, authz):
        decision = authz.guard("workspace.create")
        assert decision.allowed is True

    def test_guard_denied_raises(self, authz):
        authz.engine.add_rule(
            lambda cap_id, ctx: PolicyDecision.deny(cap_id, "Blocked")
        )
        with pytest.raises(AuthorizationDenied) as exc:
            authz.guard("workspace.create", raise_on_deny=True)
        assert exc.value.capability == "workspace.create"
        assert "Blocked" in str(exc.value)

    def test_guard_approval_required_fails_closed_without_grant(self, authz):
        """U1D-F: approval-required operations MUST NOT execute without a
        grant.  When no approval channel yields a grant, guard() raises
        ApprovalRequired instead of returning an executable decision."""
        from plugins.workspace.backend.security.approval import (
            ApprovalOutcome,
        )

        class NoChannelProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                return ApprovalOutcome.unavailable("no human channel")

        authz.approval_provider = NoChannelProvider()
        with pytest.raises(ApprovalRequired) as exc:
            authz.guard("fs.write")
        assert exc.value.capability == "fs.write"

    def test_guard_approval_required_raises_when_configured(self, authz):
        with pytest.raises(ApprovalRequired) as exc:
            authz.guard("fs.write", raise_on_approval=True)
        assert exc.value.capability == "fs.write"

    def test_guard_approval_granted_executes(self, authz, audit_logger):
        """U1D-F: an explicit approval grant lets exactly one operation
        proceed."""
        from plugins.workspace.backend.security.approval import (
            ApprovalOutcome,
        )

        calls = {"n": 0}

        class GrantingProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                calls["n"] += 1
                return ApprovalOutcome.granted()

        authz.approval_provider = GrantingProvider()
        decision = authz.guard("fs.write")
        assert decision.requires_approval is True
        assert calls["n"] == 1
        statuses = [e["status"] for e in audit_logger.read(20)]
        assert "ALLOW:approved" in statuses

    def test_guard_approval_denied_blocks_execution(self, authz, audit_logger):
        """U1D-F: a denial prevents execution."""
        from plugins.workspace.backend.security.approval import (
            ApprovalOutcome,
        )

        class DenyingProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                return ApprovalOutcome.denied("user said no")

        authz.approval_provider = DenyingProvider()
        with pytest.raises(ApprovalRequired):
            authz.guard("fs.write")
        statuses = [e["status"] for e in audit_logger.read(20)]
        assert "DENY:approval_denied" in statuses

    def test_guard_approval_pending_blocks_execution(self, authz):
        """U1D-F: a gateway-queued (pending) approval is NOT a grant."""
        from plugins.workspace.backend.security.approval import (
            ApprovalOutcome,
        )

        class PendingProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                return ApprovalOutcome.pending("queued for /approve")

        authz.approval_provider = PendingProvider()
        with pytest.raises(ApprovalRequired):
            authz.guard("fs.write")

    def test_guard_approval_unavailable_fails_closed(self, authz):
        """U1D-F: no human approval channel → fail closed."""
        from plugins.workspace.backend.security.approval import (
            ApprovalOutcome,
        )

        class UnavailableProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                return ApprovalOutcome.unavailable("no channel")

        authz.approval_provider = UnavailableProvider()
        with pytest.raises(ApprovalRequired):
            authz.guard("fs.write")

    def test_guard_approval_provider_raising_fails_closed(self, authz):
        """U1D-F: a provider failure is a denial, never an allow."""
        class BrokenProvider:
            def request(self, capability, *, resource_type="", resource_id="",
                        details=None, session_id="", approval_callback=None):
                raise RuntimeError("approval machinery broken")

        authz.approval_provider = BrokenProvider()
        with pytest.raises(ApprovalRequired):
            authz.guard("fs.write")

    def test_unknown_capability_emits_audit_then_raises(self, authz, audit_logger):
        with pytest.raises(CapabilityNotFound):
            authz.authorize("no.such.capability", resource_type="test")
        events = audit_logger.read(10)
        denials = [e for e in events if "capability_not_found" in e["status"]]
        assert len(denials) >= 1

    def test_correlation_id_propagated(self, authz, audit_logger):
        authz.authorize("fs.read", correlation_id="my-corr-123")
        events = audit_logger.read(10)
        corr_ids = {e["correlation_id"] for e in events}
        assert "my-corr-123" in corr_ids


# ---------------------------------------------------------------------------
# Capability Enforcement
# ---------------------------------------------------------------------------

class TestCapabilityEnforcement:
    def test_workspace_domain_capabilities_registered(self):
        registry = CapabilityRegistry()
        assert registry.get("workspace.create") is not None
        assert registry.get("task.delete") is not None
        assert registry.get("adr.update") is not None

    def test_workspace_delete_requires_approval(self):
        registry = CapabilityRegistry()
        cap = registry.get("workspace.delete")
        assert cap is not None
        assert cap.approval_required is True
        assert cap.tier == 2

    def test_roadmap_delete_audited_routine(self):
        """U1D-F: roadmap.delete is routine desktop CRUD behind an explicit
        user action — tier 1, audited, no approval gate."""
        registry = CapabilityRegistry()
        cap = registry.get("roadmap.delete")
        assert cap is not None
        assert cap.approval_required is False
        assert cap.tier == 1
        assert cap.audit_required is True

    def test_all_domain_caps_are_audited(self):
        registry = CapabilityRegistry()
        domain_caps = registry.list_by_scope("workspace")
        for cap in domain_caps:
            assert cap.audit_required, f"{cap.identifier} should be audited"

    def test_approval_capability_returns_approval_required(self, engine):
        decision = engine.evaluate("workspace.delete")
        assert decision.requires_approval is True
        assert decision.allowed is True

    def test_audited_capability_logs_audit(self, authz, audit_logger):
        authz.authorize("task.create", resource_type="task",
                        resource_id="t-1", session_id="s1")
        events = audit_logger.read(10)
        task_events = [e for e in events if "task" in e["action"]]
        assert len(task_events) >= 1
        assert task_events[-1]["status"] == "ALLOW"

    def test_audit_record_includes_all_fields(self, authz, audit_logger):
        authz.authorize("journal.create", resource_type="journal",
                        resource_id="j-1", session_id="sess-xyz",
                        correlation_id="corr-abc")
        events = audit_logger.read(10)
        ev = events[-1]
        for field in ("event_id", "timestamp", "action", "status",
                       "session_id", "correlation_id", "resource_type",
                       "resource_id"):
            assert field in ev, f"Missing field: {field}"
        assert ev["action"] == "authorize.journal.create"
        assert ev["status"] == "ALLOW"


# ---------------------------------------------------------------------------
# Trust Labels — propagation & preservation
# ---------------------------------------------------------------------------

class TestTrustLabelPropagation:
    def test_label_web_content_is_untrusted(self):
        label = label_content("web", origin_url="https://evil.com")
        assert label.trust_level == "untrusted"
        assert label.source == LABEL_WEB_CONTENT

    def test_label_preservation_through_re_labeling(self):
        """Labels should persist the original trust level."""
        original = label_content("web", origin_url="https://x.com",
                                 size_bytes=500)
        assert original.trust_level == "untrusted"
        prefix = get_label_prefix(original)
        assert "WEB" in prefix

    def test_boundary_format_preserves_label_info(self):
        label = label_content("web", origin_url="https://x.com",
                              size_bytes=200)
        formatted = format_safe_boundary(label, "some content")
        assert "BEGIN" in formatted
        assert "END" in formatted
        assert "untrusted" in formatted
        assert "200" in formatted
        assert "some content" in formatted

    def test_label_metadata_preserved(self):
        label = label_content("web", origin_url="https://src.example",
                              mime_type="text/html",
                              size_bytes=4096)
        assert label.mime_type == "text/html"
        assert label.origin_url == "https://src.example"
        assert label.size_bytes == 4096

    def test_multiple_sources_have_distinct_labels(self):
        user_label = label_content("user")
        web_label = label_content("web")
        llm_label = label_content("llm")

        assert user_label.trust_level == "trusted"
        assert web_label.trust_level == "untrusted"
        assert llm_label.trust_level == "untrusted"

        assert user_label.source != web_label.source
        assert web_label.source != llm_label.source


# ---------------------------------------------------------------------------
# Security Exceptions
# ---------------------------------------------------------------------------

class TestSecurityExceptions:
    def test_security_error_base(self):
        err = SecurityError("test", code="TEST_CODE")
        assert err.code == "TEST_CODE"
        assert "test" in str(err)

    def test_authorization_denied(self):
        err = AuthorizationDenied("fs.delete", reason="Not allowed",
                                  decision_id="d123")
        assert "fs.delete" in str(err)
        assert "Not allowed" in str(err)
        assert "d123" in str(err)
        assert err.capability == "fs.delete"
        assert err.decision_id == "d123"

    def test_approval_required(self):
        err = ApprovalRequired("workspace.delete", decision_id="d456")
        assert err.capability == "workspace.delete"
        assert err.decision_id == "d456"
        assert "workspace.delete" in str(err)

    def test_capability_not_found(self):
        err = CapabilityNotFound("unknown.op")
        assert err.capability == "unknown.op"
        assert "unknown.op" in str(err)

    def test_policy_violation(self):
        err = PolicyViolation("Invalid state", policy_rule="rule_x")
        assert err.policy_rule == "rule_x"
        assert "rule_x" in str(err)

    def test_exception_hierarchy(self):
        assert issubclass(AuthorizationDenied, SecurityError)
        assert issubclass(ApprovalRequired, SecurityError)
        assert issubclass(CapabilityNotFound, SecurityError)
        assert issubclass(PolicyViolation, SecurityError)


# ---------------------------------------------------------------------------
# Service Integration — authorized operations
# ---------------------------------------------------------------------------

class TestServiceIntegrationAuthz:
    """Verify that services correctly enforce authorization when
    middleware is wired in, and do NOT enforce when it is absent
    (regression)."""

    def test_workspace_service_authorized_create(self, storage, audit_logger):
        authz = AuthorizationMiddleware(audit_logger=audit_logger)
        svc = WorkspaceService(storage=storage, authz=authz)
        ws = svc.create_workspace(WorkspaceCreate(name="test-auth-ws", path="/tmp"))
        assert ws is not None
        assert ws.name == "test-auth-ws"

    def test_workspace_service_allows_when_no_authz(self, storage):
        svc = WorkspaceService(storage=storage)  # no authz
        ws = svc.create_workspace(WorkspaceCreate(name="no-authz-ws", path="/tmp"))
        assert ws.name == "no-authz-ws"

    def test_workspace_service_denied_by_rule(self, storage, audit_logger):
        registry = CapabilityRegistry()
        engine = PolicyEngine(registry=registry)

        def deny_all_workspace(cap_id, ctx):
            if cap_id.startswith("workspace."):
                return PolicyDecision.deny(cap_id, "Test deny rule")
            return None

        engine.add_rule(deny_all_workspace)
        authz = AuthorizationMiddleware(registry=registry, policy_engine=engine,
                                        audit_logger=audit_logger)
        svc = WorkspaceService(storage=storage, authz=authz)

        with pytest.raises(AuthorizationDenied):
            svc.create_workspace(WorkspaceCreate(name="blocked", path="/tmp"))

    def test_adr_service_authorized(self, storage, audit_logger):
        authz = AuthorizationMiddleware(audit_logger=audit_logger)
        svc = ADRService(storage=storage, authz=authz)
        ws = storage.create_workspace("adr-authz-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(
            workspace_id=ws.id, title="Test ADR",
        ))
        assert adr is not None
        assert adr.title == "Test ADR"

    def test_adr_service_allows_when_no_authz(self, storage):
        svc = ADRService(storage=storage)
        ws = storage.create_workspace("adr-no-authz-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(
            workspace_id=ws.id, title="No Authz ADR",
        ))
        assert adr.title == "No Authz ADR"

    def test_adr_service_denied_by_rule(self, storage, audit_logger):
        registry = CapabilityRegistry()
        engine = PolicyEngine(registry=registry)
        engine.add_rule(
            lambda cap_id, ctx: PolicyDecision.deny(cap_id, "Block")
            if cap_id == "adr.create" else None
        )
        authz = AuthorizationMiddleware(registry=registry, policy_engine=engine,
                                        audit_logger=audit_logger)
        svc = ADRService(storage=storage, authz=authz)
        ws = storage.create_workspace("adr-blocked-ws", "/tmp")

        with pytest.raises(AuthorizationDenied):
            svc.create_adr(ADRCreate(workspace_id=ws.id, title="Blocked"))

    def test_task_service_authorized(self, storage, audit_logger):
        authz = AuthorizationMiddleware(audit_logger=audit_logger)
        svc = TaskService(storage=storage, authz=authz)
        task = svc.create_task(TaskCreate(title="Authz Task"))
        assert task is not None
        assert task.title == "Authz Task"

    def test_task_service_allows_when_no_authz(self, storage):
        svc = TaskService(storage=storage)
        task = svc.create_task(TaskCreate(title="No Authz Task"))
        assert task.title == "No Authz Task"


# ---------------------------------------------------------------------------
# Regression — existing behaviour unchanged
# ---------------------------------------------------------------------------

class TestRegressionExistingBehaviours:
    """Ensure existing behaviour works identically when no authz is wired."""

    def test_workspace_crud_unchanged(self, storage):
        svc = WorkspaceService(storage=storage)
        ws = svc.create_workspace(WorkspaceCreate(name="reg-ws", path="/tmp"))
        assert ws is not None
        listed = svc.list_workspaces()
        assert any(w.id == ws.id for w in listed)
        got = svc.get_workspace(ws.id)
        assert got.name == "reg-ws"

    def test_adr_workflow_unchanged(self, storage):
        svc = ADRService(storage=storage)
        ws = storage.create_workspace("reg-adr-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(workspace_id=ws.id, title="Reg ADR"))
        assert adr is not None
        got = svc.get_adr(adr.id)
        assert got.title == "Reg ADR"

        updated = svc.update_adr(adr.id, type("U", (), {"title": "Updated", "status": None, "category": None, "markdown": None, "tags": None})())
        assert updated.title == "Updated"

        svc.delete_adr(adr.id)
        with pytest.raises(Exception):
            svc.get_adr(adr.id)

    def test_task_workflow_unchanged(self, storage):
        svc = TaskService(storage=storage)
        t = svc.create_task(TaskCreate(title="Reg Task"))
        assert t.title == "Reg Task"
        got = svc.get_task(t.id)
        assert got.id == t.id
        svc.delete_task(t.id)


# ---------------------------------------------------------------------------
# Capability Registry — domain additions
# ---------------------------------------------------------------------------

class TestDomainCapabilities:
    def test_workspace_capabilities_count(self):
        registry = CapabilityRegistry()
        domain = registry.list_by_scope("workspace")
        # At least the 18+ domain capabilities defined in S6.2
        assert len(domain) >= 18

    def test_every_domain_cap_has_audit(self):
        registry = CapabilityRegistry()
        domain = registry.list_by_scope("workspace")
        for cap in domain:
            assert cap.audit_required, f"{cap.identifier} should require audit"

    def test_domain_caps_are_tier1_or_tier2(self):
        registry = CapabilityRegistry()
        domain = registry.list_by_scope("workspace")
        for cap in domain:
            assert cap.tier in (1, 2), (
                f"{cap.identifier} has unexpected tier {cap.tier}"
            )

    def test_list_by_scope_workspace(self):
        registry = CapabilityRegistry()
        domain = registry.list_by_scope("workspace")
        identifiers = {c.identifier for c in domain}
        expected = {
            "workspace.create", "workspace.update", "workspace.delete",
            "repository.register",
            "adr.create", "adr.update", "adr.delete",
            "journal.create", "journal.update", "journal.delete",
            "roadmap.create", "roadmap.update", "roadmap.delete",
            "milestone.create", "milestone.update", "milestone.delete",
            "task.create", "task.update", "task.delete",
        }
        assert identifiers.issuperset(expected), f"Missing: {expected - identifiers}"
