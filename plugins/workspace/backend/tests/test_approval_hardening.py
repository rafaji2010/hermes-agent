"""U1D-F — Workspace approval hardening & audit identity tests.

Covers:
- HostApprovalProvider mapping of the Hermes ``request_tool_approval``
  verdict onto ApprovalOutcome (granted / denied / pending / unavailable).
- The provider never consults ``hermes approvals test`` — that CLI is a
  read-only command *detector*, not the Workspace authorization engine.
- Approval-required operations never execute without a grant (service
  integration level).
- Audit identity: profile home, session key namespace, durable session id,
  and the invariant that session_key is never promoted to the actor slot.
- U1D-F3 boundary: canonical ADR writes can never target host-protected
  instruction-file names.
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

from plugins.workspace.backend.security.approval import (
    ApprovalOutcome,
    HostApprovalProvider,
)
from plugins.workspace.backend.security.authorization import (
    AuthorizationMiddleware,
)
from plugins.workspace.backend.security.capabilities import (
    CapabilityDef,
    CapabilityRegistry,
)
from plugins.workspace.backend.security.exceptions import ApprovalRequired
from plugins.workspace.backend.security.audit import AuditLogger


@pytest.fixture
def audit_logger() -> Generator[AuditLogger, None, None]:
    with tempfile.TemporaryDirectory() as td:
        yield AuditLogger(log_path=Path(td) / "audit.log")


@pytest.fixture
def registry() -> CapabilityRegistry:
    return CapabilityRegistry()


# ---------------------------------------------------------------------------
# HostApprovalProvider verdict mapping
# ---------------------------------------------------------------------------


class TestHostApprovalProviderMapping:
    def test_approved_verdict_grants(self, monkeypatch):
        provider = HostApprovalProvider()

        def fake_request_tool_approval(
            tool_name, reason, *, rule_key="", approval_callback=None
        ):
            assert tool_name.startswith("workspace.")
            assert rule_key.startswith("workspace:")
            return {"approved": True, "message": None}

        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            fake_request_tool_approval,
        )
        outcome = provider.request("adr.reconcile.write", resource_type="adr")
        assert outcome.granted is True
        assert outcome.status == "granted"

    def test_denied_verdict_denies(self, monkeypatch):
        provider = HostApprovalProvider()

        def fake_request_tool_approval(
            tool_name, reason, *, rule_key="", approval_callback=None
        ):
            return {
                "approved": False,
                "message": "BLOCKED: denied by user. Do NOT retry.",
                "user_consent": False,
            }

        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            fake_request_tool_approval,
        )
        outcome = provider.request("fs.write")
        assert outcome.granted is False
        assert outcome.status == "denied"

    def test_gateway_pending_verdict_is_not_a_grant(self, monkeypatch):
        """A gateway-queued approval_required verdict must never count as
        approval — the operation stays non-executable."""
        provider = HostApprovalProvider()

        def fake_request_tool_approval(
            tool_name, reason, *, rule_key="", approval_callback=None
        ):
            return {
                "approved": False,
                "status": "approval_required",
                "command": "<fs.write>",
                "message": "queued for /approve",
            }

        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            fake_request_tool_approval,
        )
        outcome = provider.request("fs.write")
        assert outcome.granted is False
        assert outcome.status == "pending"

    def test_no_channel_fails_closed(self, monkeypatch):
        """Host fail-closed (no interactive user / gateway) → unavailable."""
        provider = HostApprovalProvider()

        def fake_request_tool_approval(
            tool_name, reason, *, rule_key="", approval_callback=None
        ):
            return {
                "approved": False,
                "message": "BLOCKED: no interactive user or gateway is present.",
            }

        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            fake_request_tool_approval,
        )
        outcome = provider.request("fs.write")
        assert outcome.granted is False
        assert outcome.status in ("denied", "unavailable")

    def test_exception_fails_closed(self, monkeypatch):
        provider = HostApprovalProvider()

        def boom(tool_name, reason, *, rule_key="", approval_callback=None):
            raise RuntimeError("gate broke")

        monkeypatch.setattr("tools.approval.request_tool_approval", boom)
        outcome = provider.request("fs.write")
        assert outcome.granted is False
        assert outcome.status == "unavailable"

    def test_provider_never_consults_approvals_test(self, monkeypatch):
        """`hermes approvals test` is a read-only detector — it must never be
        used as the Workspace authorization engine.  The provider's only
        host dependency is ``tools.approval.request_tool_approval``: if it
        ever imported/consulted the approvals-test module (here poisoned to
        raise on any attribute access), the outcome could not be a grant."""
        import sys

        provider = HostApprovalProvider()

        class PoisonedModule:
            def __getattr__(self, _name):
                raise AssertionError("approvals test must not be consulted")

        monkeypatch.setitem(sys.modules, "hermes_cli.approvals_test", PoisonedModule())
        monkeypatch.setattr(
            "tools.approval.request_tool_approval",
            lambda *a, **k: {"approved": True},
        )
        outcome = provider.request("fs.write")
        assert outcome.granted is True


# ---------------------------------------------------------------------------
# Approval-required never executes without a grant (service integration)
# ---------------------------------------------------------------------------


class TestApprovalEnforcementIntegration:
    def test_approval_required_operation_has_no_side_effect(
        self, registry, audit_logger, tmp_path, monkeypatch
    ):
        """A capability registered approval_required MUST NOT execute when
        no approval channel grants it — verify at the service boundary."""
        import plugins.workspace.backend.database as db_mod
        from plugins.workspace.backend.database import DatabaseManager
        from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage
        from plugins.workspace.backend.services.workspace_service import (
            WorkspaceService,
        )

        registry.register(
            CapabilityDef(
                identifier="workspace.create",
                description="blocked by approval for this test",
                tier=2,
                approval_required=True,
                audit_required=True,
                scope="workspace",
            )
        )

        class NoChannelProvider:
            def request(self, *a, **k):
                return ApprovalOutcome.unavailable("no channel in test")

        authz = AuthorizationMiddleware(
            registry=registry,
            audit_logger=audit_logger,
            approval_provider=NoChannelProvider(),
        )
        db = DatabaseManager(db_path=tmp_path / "t.db")
        db.get_connection()
        db_mod._db = db
        store = SQLiteStorage()
        from plugins.workspace.backend.models import WorkspaceCreate

        svc = WorkspaceService(store, authz=authz)

        with pytest.raises(ApprovalRequired):
            svc.create_workspace(WorkspaceCreate(name="w-approval", path=str(tmp_path)))

        assert store.get_workspace_by_name("w-approval") is None

    def test_approval_grant_executes_exactly_once(
        self, registry, audit_logger, tmp_path
    ):
        import plugins.workspace.backend.database as db_mod
        from plugins.workspace.backend.database import DatabaseManager
        from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage
        from plugins.workspace.backend.services.workspace_service import (
            WorkspaceService,
        )

        registry.register(
            CapabilityDef(
                identifier="workspace.create",
                description="requires approval",
                tier=2,
                approval_required=True,
                audit_required=True,
                scope="workspace",
            )
        )
        requests = {"n": 0}

        class GrantingProvider:
            def request(self, *a, **k):
                requests["n"] += 1
                return ApprovalOutcome.granted()

        authz = AuthorizationMiddleware(
            registry=registry,
            audit_logger=audit_logger,
            approval_provider=GrantingProvider(),
        )
        db = DatabaseManager(db_path=tmp_path / "t.db")
        db.get_connection()
        db_mod._db = db
        store = SQLiteStorage()
        from plugins.workspace.backend.models import WorkspaceCreate

        svc = WorkspaceService(store, authz=authz)

        ws = svc.create_workspace(WorkspaceCreate(name="w-granted", path=str(tmp_path)))
        assert ws.id
        assert store.get_workspace(ws.id) is not None
        assert requests["n"] == 1

        statuses = [e["status"] for e in audit_logger.read(30)]
        assert "ALLOW:approved" in statuses


# ---------------------------------------------------------------------------
# Audit identity (U1D-F2)
# ---------------------------------------------------------------------------


class TestAuditIdentity:
    def test_identity_recorded_on_authorize_path(self, registry, audit_logger):
        authz = AuthorizationMiddleware(registry=registry, audit_logger=audit_logger)
        authz.authorize(
            "task.create",
            resource_type="task",
            resource_id="t-1",
            session_id="durable-sess-42",
            identity={
                "session_key": "cli:abc123",
                "profile_home": "/home/u/.hermes/profiles/coder",
                "turn_id": "",
                "tool_call_id": "",
            },
        )
        events = audit_logger.read(10)
        event = next(e for e in events if e["action"] == "authorize.task.create")
        assert event["session_id"] == "durable-sess-42"
        assert event["session_key"] == "cli:abc123"
        assert event["profile_home"] == "/home/u/.hermes/profiles/coder"

    def test_actor_never_inferred_from_session_key(self, registry, audit_logger):
        """The CRITICAL invariant: a session_key must never appear in the
        actor slot."""
        authz = AuthorizationMiddleware(registry=registry, audit_logger=audit_logger)
        authz.authorize(
            "task.create",
            identity={"session_key": "telegram:user_999"},
        )
        events = audit_logger.read(10)
        event = next(e for e in events if e["action"] == "authorize.task.create")
        assert event["session_key"] == "telegram:user_999"
        assert event["actor"] == ""

    def test_actor_only_when_supplied(self, registry, audit_logger):
        authz = AuthorizationMiddleware(registry=registry, audit_logger=audit_logger)
        authz.authorize(
            "task.create",
            identity={
                "session_key": "x",
                "actor": "transport-supplied-actor",
            },
        )
        events = audit_logger.read(10)
        event = next(e for e in events if e["action"] == "authorize.task.create")
        assert event["actor"] == "transport-supplied-actor"

    def test_host_identity_collected_when_not_supplied(
        self, registry, audit_logger, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        authz = AuthorizationMiddleware(registry=registry, audit_logger=audit_logger)
        authz.authorize("task.create", resource_type="task")
        events = audit_logger.read(10)
        event = next(e for e in events if e["action"] == "authorize.task.create")
        assert event["profile_home"] == str((tmp_path / ".hermes").resolve())
        assert event["session_key"] != ""
        assert event["actor"] == ""


# ---------------------------------------------------------------------------
# U1D-F3 — protected instruction-file write boundary
# ---------------------------------------------------------------------------


class TestProtectedInstructionBoundary:
    def test_canonical_adr_path_cannot_be_instruction_file(self):
        """ADR canonical paths are validated against the project
        docs/adr/ contract (U1D-E); a stored path named like a host
        instruction file must be rejected — ADR writes can never become
        AGENTS.md / SOUL.md / CLAUDE.md / .cursorrules writes."""
        from plugins.workspace.backend.services.adr_reconcile_service import (
            ADRReconcileError,
            ADRReconcileService,
        )

        service = ADRReconcileService.__new__(ADRReconcileService)
        service._adr_dirs = ("docs/adr", "adr")

        for name in ("AGENTS.md", "SOUL.md", "CLAUDE.md", ".cursorrules"):
            with pytest.raises(ADRReconcileError) as exc:
                service._validate_canonical_rel(name)
            assert exc.value.code == "ADR_UNSAFE_PATH"
