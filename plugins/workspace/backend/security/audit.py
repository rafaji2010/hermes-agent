"""Audit Logging Framework.

Implements ADR-SEC-008 — structured JSON Lines audit logging for all
security-sensitive operations.  Initially writes to local files only.

Usage::

    logger = AuditLogger()
    logger.log(
        action="tool.invoke",
        status="success",
        resource_type="shell",
        resource_id="terminal_1",
        details={"command": "ls -la"},
        session_id="sess-123",
    )
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import AuditEvent

_log = logging.getLogger("hermes.plugins.workspace.security.audit")


class AuditLogger:
    """Structured JSON Lines audit logger.

    Thread-safe via internal lock.  Writes to a file in the Hermes home
    directory by default.
    """

    def __init__(self, log_path: Optional[Path] = None):
        if log_path:
            self._path = Path(log_path)
        else:
            from hermes_constants import get_hermes_home  # type: ignore[import-untyped]
            self._path = Path(get_hermes_home()) / "logs" / "audit.log"
        self._lock = threading.Lock()
        self._ensure_dir()

    def _ensure_dir(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        action: str,
        status: str = "success",
        *,
        actor: str = "",
        resource_type: str = "",
        resource_id: str = "",
        details: dict | None = None,
        session_id: str = "",
        correlation_id: str = "",
        session_key: str = "",
        profile_home: str = "",
        turn_id: str = "",
        tool_call_id: str = "",
    ) -> AuditEvent:
        """Write an audit event to the log.

        Returns the ``AuditEvent`` that was logged.  ``session_key`` is the
        host approval namespace (never a human identity); ``actor`` is only
        ever supplied by the transport.
        """
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            status=status,
            actor=actor,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            session_id=session_id,
            correlation_id=correlation_id or str(uuid.uuid4())[:8],
            session_key=session_key,
            profile_home=profile_home,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
        )
        self._write_jsonl(event)
        return event

    def log_event(self, event: AuditEvent) -> AuditEvent:
        """Write a pre-built ``AuditEvent`` to the log."""
        event.event_id = event.event_id or str(uuid.uuid4())
        event.timestamp = event.timestamp or datetime.now(timezone.utc).isoformat()
        event.correlation_id = event.correlation_id or str(uuid.uuid4())[:8]
        self._write_jsonl(event)
        return event

    def _write_jsonl(self, event: AuditEvent) -> None:
        line = json.dumps({
            "event_id": event.event_id,
            "timestamp": event.timestamp,
            "action": event.action,
            "status": event.status,
            "actor": event.actor,
            "resource_type": event.resource_type,
            "resource_id": event.resource_id,
            "details": event.details,
            "session_id": event.session_id,
            "correlation_id": event.correlation_id,
            "session_key": event.session_key,
            "profile_home": event.profile_home,
            "turn_id": event.turn_id,
            "tool_call_id": event.tool_call_id,
        }, default=str, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read(self, limit: int = 100) -> list[dict]:
        """Read the last N audit events."""
        events: list[dict] = []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for line in lines[-limit:]:
                try:
                    events.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    pass
        except FileNotFoundError:
            pass
        return events

    @property
    def path(self) -> Path:
        return self._path


# ── Module-level default logger ─────────────────────────────────────────────

_default_logger: Optional[AuditLogger] = None
_default_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    """Return the module-level audit logger singleton."""
    global _default_logger
    if _default_logger is None:
        with _default_lock:
            if _default_logger is None:
                _default_logger = AuditLogger()
    return _default_logger
