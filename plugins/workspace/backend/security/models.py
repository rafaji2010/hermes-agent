"""Security data models — labels, events, capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ContentLabel:
    """Metadata label attached to content entering the system."""

    source: str
    trust_level: str          # trusted, untrusted, unknown
    mime_type: str = ""
    origin_url: str = ""
    origin_path: str = ""
    fetch_time: str = ""
    size_bytes: int = 0
    truncated: bool = False
    suspicious: bool = False
    sanitized: bool = False
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class SanitizationResult:
    """Result of content sanitization."""

    content: str
    original_size: int
    sanitized_size: int
    truncated: bool = False
    control_chars_removed: int = 0
    null_bytes_removed: int = 0
    dangerous_patterns: List[str] = field(default_factory=list)
    label: Optional[ContentLabel] = None


@dataclass
class CapabilityDef:
    """Definition of a single capability."""

    identifier: str
    description: str
    tier: int = 1               # 1=auto, 2=approval, 3=admin
    approval_required: bool = True
    audit_required: bool = False
    scope: str = "tool"


@dataclass
class AuditEvent:
    """Structured audit event for security-sensitive operations."""

    event_id: str
    timestamp: str
    action: str
    status: str      # success, denied, error, approved, denied_by_user
    actor: str = ""
    resource_type: str = ""
    resource_id: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    correlation_id: str = ""
    # U1D-F2 — identity/correlation fields.  ``session_key`` is the host
    # approval namespace, never a human identity; ``actor`` is only ever
    # populated by the transport (never inferred from session_key).
    session_key: str = ""
    profile_home: str = ""
    turn_id: str = ""
    tool_call_id: str = ""
