"""Authorization Middleware.

Central authorization gate that every protected operation must pass
through.  Consults the Policy Engine and Capability Registry, emits
audit events, denies execution when appropriate, and returns
approval-required state when applicable.

No authorization logic should be duplicated across services — all
enforcement flows through this single middleware.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from .audit import AuditLogger, get_audit_logger
from .capabilities import CapabilityRegistry
from .exceptions import (
    ApprovalRequired,
    AuthorizationDenied,
    CapabilityNotFound,
)
from .policy import PolicyDecision, PolicyEngine

_log = logging.getLogger("hermes.plugins.workspace.security.authorization")


class AuthorizationMiddleware:
    """Authorize every protected operation through a single gate.

    Usage::

        authz = AuthorizationMiddleware(registry=...)
        decision = authz.authorize(
            "workspace.create",
            resource={"type": "workspace", "id": workspace_id},
            session_id="sess-1",
        )

        if not decision.allowed:
            raise AuthorizationDenied(decision.capability, decision.reason)

        if decision.requires_approval:
            return {"status": "approval_required", "decision": decision}

        # proceed with the operation
    """

    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
        policy_engine: Optional[PolicyEngine] = None,
        audit_logger: Optional[AuditLogger] = None,
        *,
        session_id: str = "",
    ):
        self._registry = registry or CapabilityRegistry()
        self._engine = policy_engine or PolicyEngine(registry=self._registry)
        self._audit = audit_logger or get_audit_logger()
        self._session_id = session_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authorize(
        self,
        capability: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        session_id: str = "",
        correlation_id: str = "",
    ) -> PolicyDecision:
        """Evaluate and record an authorization decision.

        Returns a ``PolicyDecision``.  The caller is responsible for
        acting on ``.allowed``, ``.requires_approval``, and ``.audited``.

        Always emits an audit event recording the decision and its
        reason — nothing silently bypasses auditing.
        """
        corr_id = correlation_id or uuid.uuid4().hex[:12]
        sid = session_id or self._session_id

        try:
            decision = self._engine.evaluate(capability, context={
                "session_id": sid,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "details": details or {},
                "correlation_id": corr_id,
            })
        except CapabilityNotFound as exc:
            # Audit the unknown-capability attempt, then re-raise
            self._audit.log(
                action=f"authorize.{capability}",
                status="DENY:capability_not_found",
                resource_type=resource_type,
                resource_id=resource_id,
                details={"capability": capability, "error": str(exc)},
                session_id=sid,
                correlation_id=corr_id,
            )
            raise

        decision.capability = capability

        # Determine audit status label
        if not decision.allowed:
            status = "DENY"
        elif decision.requires_approval:
            status = "APPROVAL_REQUIRED"
        else:
            status = "ALLOW"

        # Always audit
        self._audit.log(
            action=f"authorize.{capability}",
            status=status,
            resource_type=resource_type,
            resource_id=resource_id,
            details={
                "decision_id": decision.decision_id,
                "reason": decision.reason,
                "audited": decision.audited,
                "requires_approval": decision.requires_approval,
                **(details or {}),
            },
            session_id=sid,
            correlation_id=corr_id,
        )

        return decision

    def guard(
        self,
        capability: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        session_id: str = "",
        correlation_id: str = "",
        raise_on_deny: bool = True,
        raise_on_approval: bool = False,
    ) -> PolicyDecision:
        """Authorize and optionally raise on deny or approval-required.

        By default raises ``AuthorizationDenied`` when a capability is
        denied, and returns the ``PolicyDecision`` when approval is
        required (no raise) so the caller can handle the approval flow.

        Parameters
        ----------
        raise_on_deny:
            Raise ``AuthorizationDenied`` when ``decision.allowed`` is
            ``False``.  Default ``True``.
        raise_on_approval:
            Raise ``ApprovalRequired`` when ``decision.requires_approval``
            is ``True``.  Default ``False`` — callers must explicitly
            handle the approval state themselves.
        """
        decision = self.authorize(
            capability,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            session_id=session_id,
            correlation_id=correlation_id,
        )

        if not decision.allowed and raise_on_deny:
            raise AuthorizationDenied(
                capability=capability,
                reason=decision.reason,
                decision_id=decision.decision_id,
            )

        if decision.requires_approval and raise_on_approval:
            raise ApprovalRequired(
                capability=capability,
                decision_id=decision.decision_id,
            )

        return decision

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def engine(self) -> PolicyEngine:
        return self._engine

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    @property
    def audit(self) -> AuditLogger:
        return self._audit
