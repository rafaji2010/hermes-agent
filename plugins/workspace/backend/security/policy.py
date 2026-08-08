"""Policy Engine.

Evaluates requested capabilities against the Capability Registry and
returns structured ``PolicyDecision`` objects — never booleans.

Design:
    The engine is configured with a set of rules.  Each rule is a
    callable that receives ``(capability_id, context)`` and returns
    ``PolicyDecision | None``.  The first rule that returns a non-None
    decision wins.  If no rule matches, a default-allow or default-deny
    fallback is used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .capabilities import CapabilityRegistry
from .exceptions import CapabilityNotFound

PolicyRule = Callable[..., Optional["PolicyDecision"]]


@dataclass
class PolicyDecision:
    """Structured decision from the policy engine.

    Never returned as a bare boolean — always includes reason, audit
    flag, and approval state.
    """

    allowed: bool
    requires_approval: bool
    audited: bool
    reason: str
    capability: str = ""
    decision_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.decision_id:
            self.decision_id = uuid.uuid4().hex[:12]

    @classmethod
    def allow(cls, capability: str = "", reason: str = "") -> "PolicyDecision":
        return cls(
            allowed=True,
            requires_approval=False,
            audited=False,
            reason=reason or "Capability permitted",
            capability=capability,
        )

    @classmethod
    def deny(cls, capability: str = "", reason: str = "") -> "PolicyDecision":
        return cls(
            allowed=False,
            requires_approval=False,
            audited=True,
            reason=reason or "Capability denied by policy",
            capability=capability,
        )

    @classmethod
    def require_approval(cls, capability: str = "", reason: str = "") -> "PolicyDecision":
        return cls(
            allowed=True,
            requires_approval=True,
            audited=True,
            reason=reason or "Approval required for this capability",
            capability=capability,
        )

    @classmethod
    def allow_audited(cls, capability: str = "", reason: str = "") -> "PolicyDecision":
        return cls(
            allowed=True,
            requires_approval=False,
            audited=True,
            reason=reason or "Capability permitted (audited)",
            capability=capability,
        )


class PolicyEngine:
    """Central policy evaluator for capability authorization.

    Consults the ``CapabilityRegistry`` for capability metadata (tier,
    approval_required, audit_required) and applies configurable rules.

    Usage::

        engine = PolicyEngine(registry=CapabilityRegistry())
        decision = engine.evaluate("fs.write", context={"session_id": "s1"})
        if not decision.allowed:
            raise AuthorizationDenied(...)
    """

    def __init__(
        self,
        registry: Optional[CapabilityRegistry] = None,
        *,
        default_allow: bool = True,
    ):
        self._registry = registry or CapabilityRegistry()
        self._rules: List[PolicyRule] = []
        self._default_allow = default_allow

    # ------------------------------------------------------------------
    # Rule registration
    # ------------------------------------------------------------------

    def add_rule(self, rule: PolicyRule) -> None:
        """Register a policy rule.

        Rules are evaluated in order.  The first rule returning a
        non-None decision wins.
        """
        self._rules.append(rule)

    def clear_rules(self) -> None:
        """Remove all custom rules (useful for testing)."""
        self._rules.clear()

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        capability_id: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PolicyDecision:
        """Evaluate whether a capability is allowed.

        Steps:
            1. Look up capability in the registry.
            2. Run custom rules (first-match wins).
            3. Apply registry defaults (approval / audit).
            4. Return default allow or deny.

        Raises ``CapabilityNotFound`` if the capability is unknown AND
        the registry has no matching entry.
        """
        ctx = context or {}
        cap = self._registry.get(capability_id)

        if cap is None:
            raise CapabilityNotFound(capability_id)

        # 1. Run custom rules
        for rule in self._rules:
            decision = rule(capability_id, ctx)
            if decision is not None:
                decision.capability = capability_id
                return decision

        # 2. Build decision from registry metadata
        if not self._default_allow:
            return PolicyDecision.deny(
                capability=capability_id,
                reason="Default-deny policy",
            )

        if cap.approval_required:
            return PolicyDecision.require_approval(
                capability=capability_id,
                reason=f"Capability '{capability_id}' requires approval (tier {cap.tier})",
            )

        if cap.audit_required:
            return PolicyDecision.allow_audited(
                capability=capability_id,
                reason=f"Capability '{capability_id}' permitted (audited)",
            )

        return PolicyDecision.allow(
            capability=capability_id,
            reason="Capability permitted",
        )

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry
