"""Authorization Middleware.

Central authorization gate that every protected operation must pass
through.  Consults the Policy Engine and Capability Registry, emits
audit events, denies execution when appropriate, and enforces approval
semantics (U1D-F):

Execution states
----------------
* ``allowed``          — decision.allowed and (not requires_approval or
                         approval granted) → operation may execute.
* ``approval_pending`` — decision.requires_approval and approval NOT
                         granted → ``ApprovalRequired`` raised; the
                         operation MUST NOT execute.
* ``denied``           — decision.allowed is False → ``AuthorizationDenied``
                         raised; the operation MUST NOT execute.

Approval-required operations NEVER execute without an actual grant, and
fail closed when no human approval channel is available (see
:mod:`.approval`).

Identity (U1D-F2)
-----------------
Audit events carry what identity is genuinely available: the durable
``session_id`` supplied by the caller, the host ``session_key`` namespace,
the profile home, and an ``actor`` ONLY when the transport provides one.
``session_key`` is never treated as the human actor.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from .approval import ApprovalOutcome, ApprovalProvider, HostApprovalProvider
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
        approval_provider: Optional[ApprovalProvider] = None,
        *,
        session_id: str = "",
    ):
        self._registry = registry or CapabilityRegistry()
        self._engine = policy_engine or PolicyEngine(registry=self._registry)
        self._audit = audit_logger or get_audit_logger()
        self._approval_provider = approval_provider or HostApprovalProvider()
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
        identity: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        """Evaluate and record an authorization decision.

        Returns a ``PolicyDecision``.  The caller is responsible for
        acting on ``.allowed``, ``.requires_approval``, and ``.audited``.

        Always emits an audit event recording the decision and its
        reason — nothing silently bypasses auditing.
        """
        corr_id = correlation_id or uuid.uuid4().hex[:12]
        sid = session_id or self._session_id
        ident = self._resolved_identity(identity, sid)

        try:
            decision = self._engine.evaluate(
                capability,
                context={
                    "session_id": sid,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "details": details or {},
                    "correlation_id": corr_id,
                },
            )
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
                **self._identity_kwargs(ident),
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
            **self._identity_kwargs(ident),
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
        approval_callback: Any = None,
        identity: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        """Authorize and enforce execution gating (U1D-F).

        Semantics:

        * ``decision.allowed is False`` → ``AuthorizationDenied``
          (unless ``raise_on_deny=False``).
        * ``decision.requires_approval`` and ``raise_on_approval=True`` →
          ``ApprovalRequired`` raised immediately (caller opted out of the
          approval flow).
        * ``decision.requires_approval`` otherwise → the approval provider
          is consulted.  Only an explicit grant lets execution proceed;
          denial, pending, and unavailable all raise
          ``ApprovalRequired`` (the operation MUST NOT execute).

        The approval outcome is audited separately so the decision and its
        resolution are both on the record.
        """
        corr_id = correlation_id or uuid.uuid4().hex[:12]
        sid = session_id or self._session_id
        ident = self._resolved_identity(identity, sid)

        decision = self.authorize(
            capability,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            session_id=sid,
            correlation_id=corr_id,
            identity=ident,
        )

        if not decision.allowed and raise_on_deny:
            raise AuthorizationDenied(
                capability=capability,
                reason=decision.reason,
                decision_id=decision.decision_id,
            )

        if decision.requires_approval:
            if raise_on_approval:
                raise ApprovalRequired(
                    capability=capability,
                    decision_id=decision.decision_id,
                )

            try:
                outcome = self._approval_provider.request(
                    capability,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    details=details,
                    session_id=sid,
                    approval_callback=approval_callback,
                )
            except Exception:
                _log.exception(
                    "Approval provider raised for capability %s — failing closed",
                    capability,
                )
                outcome = ApprovalOutcome.unavailable("approval provider raised")
            self._audit.log(
                action=f"approve.{capability}",
                status=self._approval_audit_status(outcome),
                resource_type=resource_type,
                resource_id=resource_id,
                details={
                    "decision_id": decision.decision_id,
                    "reason": decision.reason,
                    "approval_status": outcome.status,
                    "message": outcome.message,
                    **(details or {}),
                },
                session_id=sid,
                correlation_id=corr_id,
                **self._identity_kwargs(ident),
            )
            if not outcome.granted:
                raise ApprovalRequired(
                    capability=capability,
                    decision_id=decision.decision_id,
                )

        return decision

    # ------------------------------------------------------------------
    # Identity / audit helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _approval_audit_status(outcome: ApprovalOutcome) -> str:
        if outcome.status == "granted":
            return "ALLOW:approved"
        if outcome.status == "pending":
            return "APPROVAL_PENDING"
        if outcome.status == "unavailable":
            return "APPROVAL_UNAVAILABLE"
        return "DENY:approval_denied"

    @staticmethod
    def _resolved_identity(
        identity: Optional[Dict[str, Any]],
        session_id: str,
    ) -> Dict[str, Any]:
        """Return identity fields for the audit event.

        When the caller supplies identity, use it.  Otherwise collect what
        the host genuinely exposes (session key namespace + profile home) —
        never an inferred actor.
        """
        if identity:
            return identity
        from ..identity import collect_host_identity

        return collect_host_identity()

    @staticmethod
    def _identity_kwargs(ident: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the audit identity kwargs from an identity dict."""
        return {
            "session_key": str(ident.get("session_key") or ""),
            "profile_home": str(ident.get("profile_home") or ""),
            "turn_id": str(ident.get("turn_id") or ""),
            "tool_call_id": str(ident.get("tool_call_id") or ""),
            "actor": str(ident.get("actor") or ""),
        }

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

    @property
    def approval_provider(self) -> ApprovalProvider:
        return self._approval_provider

    @approval_provider.setter
    def approval_provider(self, provider: ApprovalProvider) -> None:
        """Swap the approval provider (runtime injection / tests)."""
        self._approval_provider = provider
