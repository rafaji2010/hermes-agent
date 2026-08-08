"""Security Exceptions.

Dedicated exception hierarchy for security enforcement.  Services and
middleware raise these instead of generic RuntimeError / ValueError so
callers can distinguish security decisions from application errors.
"""

from __future__ import annotations


class SecurityError(Exception):
    """Base exception for all security-layer errors."""

    def __init__(self, message: str, code: str = "SECURITY_ERROR"):
        super().__init__(message)
        self.code = code


class AuthorizationDenied(SecurityError):
    """The requested capability is not allowed by policy."""

    def __init__(self, capability: str, reason: str = "", decision_id: str = ""):
        msg = f"Authorization denied for capability '{capability}'"
        if reason:
            msg += f": {reason}"
        if decision_id:
            msg += f" [decision_id={decision_id}]"
        super().__init__(msg, code="AUTHORIZATION_DENIED")
        self.capability = capability
        self.reason = reason
        self.decision_id = decision_id


class ApprovalRequired(SecurityError):
    """The requested capability requires explicit approval before execution."""

    def __init__(self, capability: str, decision_id: str = ""):
        msg = f"Approval required for capability '{capability}'"
        if decision_id:
            msg += f" [decision_id={decision_id}]"
        super().__init__(msg, code="APPROVAL_REQUIRED")
        self.capability = capability
        self.decision_id = decision_id


class CapabilityNotFound(SecurityError):
    """The requested capability is not registered."""

    def __init__(self, capability: str):
        super().__init__(
            f"Capability not found: '{capability}'",
            code="CAPABILITY_NOT_FOUND",
        )
        self.capability = capability


class PolicyViolation(SecurityError):
    """A policy rule was violated during authorization evaluation."""

    def __init__(self, message: str, policy_rule: str = ""):
        code = "POLICY_VIOLATION"
        if policy_rule:
            message += f" (rule: {policy_rule})"
        super().__init__(message, code=code)
        self.policy_rule = policy_rule
