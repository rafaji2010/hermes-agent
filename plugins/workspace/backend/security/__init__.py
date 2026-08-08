"""Workspace Security Package.

Provides foundational security infrastructure:
- Content labeling and sanitization
- Secret detection and redaction
- Capability registry
- Audit logging
- Policy engine
- Authorization middleware
- Security exceptions
- Network isolation (URL validation, SSRF prevention)
- Resource limits (timeouts, sizes, counts)
- Path sandbox (allow/deny lists, workspace isolation)
"""

from .audit import AuditLogger, AuditEvent, get_audit_logger
from .authorization import AuthorizationMiddleware
from .capabilities import CapabilityRegistry, CAPABILITIES
from .exceptions import (
    ApprovalRequired,
    AuthorizationDenied,
    CapabilityNotFound,
    PolicyViolation,
    SecurityError,
)
from .labels import ContentLabel, label_content
from .models import (
    AuditEvent as AuditEventModel,
    CapabilityDef,
    ContentLabel as ContentLabelModel,
    SanitizationResult,
)
from .network_isolation import (
    NetworkValidationResult,
    NetworkValidator,
    ALLOWED_PROTOCOLS,
    DENIED_PROTOCOLS,
    validate_url,
    validate_urls,
    is_safe_url,
)
from .policy import PolicyDecision, PolicyEngine
from .resource_limits import (
    LimitCheckResult,
    ResourceLimitExceeded,
    ResourceLimiter,
    ResourceLimits,
)
from .sandbox import (
    PathSandbox,
    PathValidationResult,
    SandboxConfig,
)
from .sanitization import sanitize_content, detect_dangerous_patterns
from .secrets import redact_secrets, detect_secrets

__all__ = [
    "AuditLogger",
    "AuditEvent",
    "get_audit_logger",
    "AuthorizationMiddleware",
    "CapabilityRegistry",
    "CAPABILITIES",
    "ContentLabel",
    "label_content",
    "ContentLabelModel",
    "CapabilityDef",
    "SanitizationResult",
    "sanitize_content",
    "detect_dangerous_patterns",
    "redact_secrets",
    "detect_secrets",
    "PolicyDecision",
    "PolicyEngine",
    "SecurityError",
    "AuthorizationDenied",
    "ApprovalRequired",
    "CapabilityNotFound",
    "PolicyViolation",
    # Network isolation
    "NetworkValidationResult",
    "NetworkValidator",
    "ALLOWED_PROTOCOLS",
    "DENIED_PROTOCOLS",
    "validate_url",
    "validate_urls",
    "is_safe_url",
    # Resource limits
    "LimitCheckResult",
    "ResourceLimitExceeded",
    "ResourceLimiter",
    "ResourceLimits",
    # Path sandbox
    "PathSandbox",
    "PathValidationResult",
    "SandboxConfig",
]
