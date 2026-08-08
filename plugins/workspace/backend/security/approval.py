"""Workspace approval provider (U1D-F).

Semantic contract
-----------------
Workspace distinguishes three execution states for a protected operation:

* ``allowed``         — policy permits execution; no approval needed.
* ``approval_pending`` — policy requires approval; execution MUST NOT
                        start until the human grants it.
* ``denied``          — policy (or the human) refuses; execution MUST NOT
                        start.

Approval-required operations NEVER execute without an actual approval
grant.  When no human approval channel is available, the operation fails
closed (``unavailable`` → treated as not granted).

Host adoption
-------------
The default provider adapts the Hermes host approval primitive
``tools.approval.request_tool_approval()`` — the same gate Tier-2
dangerous shell patterns and plugin ``pre_tool_call`` escalations use.
Workspace does NOT implement a second approval engine.

* ``hermes approvals test`` is a read-only command *detector*; it is never
  used as the Workspace authorization engine and is never called here.
* ``session_key`` identifies the approval state namespace, never the human
  actor.  Workspace never infers an actor from ``session_key``.
* YOLO / ``approvals.mode: off`` does not bypass this provider: the host
  gate is bypass-free for protected paths, and Workspace grants here only
  on an explicit host verdict.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("hermes.plugins.workspace.security.approval")


class ApprovalOutcome:
    """Result of an approval request.

    ``granted`` is the ONLY value that authorizes execution.  ``status``
    distinguishes how the request ended so callers can audit precisely:
    ``granted`` | ``denied`` | ``pending`` | ``unavailable``.
    """

    def __init__(
        self,
        granted: bool,
        status: str,
        message: str = "",
        decision_id: str = "",
    ):
        self.granted = granted
        self.status = status
        self.message = message
        self.decision_id = decision_id

    @classmethod
    def granted(cls, message: str = "", decision_id: str = "") -> "ApprovalOutcome":
        return cls(True, "granted", message, decision_id)

    @classmethod
    def denied(cls, message: str = "", decision_id: str = "") -> "ApprovalOutcome":
        return cls(False, "denied", message, decision_id)

    @classmethod
    def pending(cls, message: str = "", decision_id: str = "") -> "ApprovalOutcome":
        return cls(False, "pending", message, decision_id)

    @classmethod
    def unavailable(cls, message: str = "") -> "ApprovalOutcome":
        return cls(False, "unavailable", message)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ApprovalOutcome(granted={self.granted}, status={self.status})"


class ApprovalProvider(ABC):
    """Request human approval for a Workspace operation."""

    @abstractmethod
    def request(
        self,
        capability: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        session_id: str = "",
        approval_callback: Any = None,
    ) -> ApprovalOutcome:
        """Return an :class:`ApprovalOutcome`.  Never raises for a denial."""


class HostApprovalProvider(ApprovalProvider):
    """Adopt the Hermes host approval gate via ``request_tool_approval``.

    The host gate already:

    * escalates to the same human prompt/gateway button flow used for
      dangerous commands;
    * honors session/permanent allowlists for the rule key;
    * FAILS CLOSED when no interactive user or gateway is present;
    * returns a definitive ``{"approved": bool}`` verdict to the caller.

    This provider maps that verdict onto :class:`ApprovalOutcome`.
    """

    def request(
        self,
        capability: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: Optional[Dict[str, Any]] = None,
        session_id: str = "",
        approval_callback: Any = None,
    ) -> ApprovalOutcome:
        try:
            from tools.approval import (  # type: ignore[import-untyped]
                request_tool_approval,
            )
        except Exception as exc:  # pragma: no cover - host import failure
            _log.exception("Host approval primitive unavailable")
            return ApprovalOutcome.unavailable(f"host approval unavailable: {exc}")

        description = (
            f"Workspace operation '{capability}' on {resource_type} "
            f"{resource_id or '(unspecified)'} requires human approval."
        )
        try:
            result = request_tool_approval(
                f"workspace.{capability}",
                description,
                rule_key=f"workspace:{capability}",
                approval_callback=approval_callback,
            )
        except Exception as exc:  # pragma: no cover - fail closed
            _log.exception("Host approval gate raised")
            return ApprovalOutcome.unavailable(str(exc))

        if not isinstance(result, dict):
            return ApprovalOutcome.unavailable(
                "host approval returned an invalid verdict"
            )

        if result.get("approved") is True:
            return ApprovalOutcome.granted(
                message=result.get("message") or "",
                decision_id=str(result.get("decision_id") or ""),
            )

        status = str(result.get("status") or "denied")
        if status == "approval_required":
            # Gateway queued the request for /approve review — the operation
            # is NOT authorized to execute yet.
            return ApprovalOutcome.pending(
                message=str(result.get("message") or ""),
                decision_id=str(result.get("decision_id") or ""),
            )
        return ApprovalOutcome.denied(
            message=str(result.get("message") or ""),
            decision_id=str(result.get("decision_id") or ""),
        )
