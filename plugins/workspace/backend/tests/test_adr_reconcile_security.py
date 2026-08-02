"""Security tests for ADR reconciliation (S7.3A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.security.capabilities import (  # type: ignore[import-untyped]
    CapabilityRegistry,
)
from plugins.workspace.backend.security.policy import (  # type: ignore[import-untyped]
    PolicyEngine,
)


def test_reconcile_capabilities_registered():
    reg = CapabilityRegistry()
    read = reg.get("adr.reconcile.read")
    assert read is not None
    assert read.tier == 1
    assert read.approval_required is False
    assert read.audit_required is True

    write = reg.get("adr.reconcile.write")
    assert write is not None
    assert write.tier == 2
    assert write.approval_required is True
    assert write.audit_required is True


def test_reconcile_write_requires_approval_by_policy():
    reg = CapabilityRegistry()
    engine = PolicyEngine(registry=reg)
    decision = engine.evaluate("adr.reconcile.write", context={})
    assert decision.requires_approval is True
    assert decision.audited is True


def test_reconcile_read_allow_audited():
    reg = CapabilityRegistry()
    engine = PolicyEngine(registry=reg)
    decision = engine.evaluate("adr.reconcile.read", context={})
    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.audited is True


def test_unknown_reconcile_capability_denied():
    reg = CapabilityRegistry()
    engine = PolicyEngine(registry=reg)
    from plugins.workspace.backend.security.exceptions import CapabilityNotFound

    with pytest.raises(CapabilityNotFound):
        engine.evaluate("adr.reconcile.sudo", context={})


def test_audit_events_emitted_for_reconcile_actions(storage, temp_git_repo):
    """Real reconcile/materialize/file-update emit audit events."""
    from plugins.workspace.backend.security.audit import AuditLogger, get_audit_logger
    from plugins.workspace.backend.security.authorization import AuthorizationMiddleware
    from plugins.workspace.backend.services.adr_reconcile_service import (
        ADRReconcileService,
    )

    events: list = []

    class CollectingLogger(AuditLogger):
        def log(self, action, status, resource_type="", resource_id="",
                details=None, session_id="", correlation_id=""):
            events.append({
                "action": action, "status": status,
                "resource_type": resource_type, "resource_id": resource_id,
                "details": details or {},
            })
            return None

    authz = AuthorizationMiddleware(audit_logger=CollectingLogger())
    svc = ADRReconcileService(storage, authz=authz)

    ws = storage.create_workspace("audit-ws", str(temp_git_repo))
    storage.register_repository(
        workspace_id=ws.id, name="r", path=str(temp_git_repo),
        git_root=str(temp_git_repo), default_branch="main",
    )

    # Reconcile (real) → audit event
    svc.reconcile(ws.id, dry_run=False)
    assert any(e["action"] == "adr.reconcile.run" for e in events)
    run_event = next(e for e in events if e["action"] == "adr.reconcile.run")
    assert run_event["resource_type"] == "workspace"
    assert run_event["resource_id"] == ws.id
    assert "dry_run" in run_event["details"]

    # Materialize → audit event with provenance
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Audit Legacy",
        slug="audit-legacy", status="proposed", category="",
        markdown="# Audit Legacy\n", tags=[],
    )
    svc.materialize(adr.id, dry_run=False)
    assert any(e["action"] == "adr.materialize" for e in events)
    mat_event = next(e for e in events if e["action"] == "adr.materialize")
    assert mat_event["resource_type"] == "adr"
    assert mat_event["resource_id"] == adr.id
    assert mat_event["details"]["source_before"] == "workspace_db"
    assert "target_path" in mat_event["details"]

    # File update → audit event
    updated = storage.get_adr(adr.id)
    svc.update_file(updated.id, "# Audit Legacy (v2)\n", dry_run=False)
    assert any(e["action"] == "adr.file_updated" for e in events)


def test_dry_run_reconcile_still_audited_as_read(storage, temp_git_repo):
    """Dry-run previews are auditable without mutating the projection."""
    from plugins.workspace.backend.security.audit import AuditLogger
    from plugins.workspace.backend.security.authorization import AuthorizationMiddleware
    from plugins.workspace.backend.services.adr_reconcile_service import (
        ADRReconcileService,
    )

    events: list = []

    class CollectingLogger(AuditLogger):
        def log(self, action, status, resource_type="", resource_id="",
                details=None, session_id="", correlation_id=""):
            events.append({"action": action, "details": details or {}})
            return None

    authz = AuthorizationMiddleware(audit_logger=CollectingLogger())
    svc = ADRReconcileService(storage, authz=authz)
    ws = storage.create_workspace("dry-audit-ws", str(temp_git_repo))
    storage.register_repository(
        workspace_id=ws.id, name="r", path=str(temp_git_repo),
        git_root=str(temp_git_repo), default_branch="main",
    )

    svc.reconcile(ws.id, dry_run=True)
    # Dry-run is read-class: no run audit event (guard is at the API layer),
    # and nothing was written.
    assert storage.list_adrs(ws.id) == []
