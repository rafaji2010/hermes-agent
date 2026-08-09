"""S7.5.3 — Workspace memory promotion ledger service.

A focused service abstraction over the metadata-only promotion ledger.
Persistence stays in ``SQLiteStorage``; this service owns:

* lifecycle/status transitions (deterministic, no invented states)
* scope/profile isolation (workspace-scoped access, no global fallback)
* deterministic dedup (repeated equivalent candidate → existing record)
* audit of lifecycle mutations (metadata only — never claim/transcript/
  secret content)

This milestone does NOT perform promotion: no Hermes MemoryStore writes,
no provider calls, no automatic extraction.  It only records WHAT happened
to a candidate and WHY.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..models import (
    PromotionRecord,
    PromotionRecordError,
    PromotionRecordNotFoundError,
    ScopeAmbiguousError,
    ScopeResolutionError,
    Workspace,
)
from ..promotion_contract import evaluate_eligibility
from ..promotion_models import EligibilityDecision, PromotionCandidate
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.promotion")


# ---------------------------------------------------------------------------
# Lifecycle status model (deterministic, minimal)
# ---------------------------------------------------------------------------

# Contract distinction states — the initial status reflects the eligibility
# decision of the candidate at proposal time.
_CONTRACT_STATUS_BY_DECISION = {
    EligibilityDecision.ELIGIBLE: "eligible",
    EligibilityDecision.REJECTED: "rejected",
    EligibilityDecision.MANUAL_ONLY: "manual_only",
    EligibilityDecision.PROPOSED: "proposed",
}

# Ledger lifecycle states used by later promotion phases.
LEDGER_STATUSES = {
    "proposed",
    "eligible",
    "manual_only",
    "rejected",
    "pending",
    "approved",
    "promoted",
    "failed",
    "superseded",
}

# Allowed transitions (deterministic; anything else is rejected).
_ALLOWED_TRANSITIONS = {
    # proposal/eligibility -> pending (awaiting promotion execution)
    "proposed": {"pending", "rejected"},
    "eligible": {"pending", "rejected"},
    "manual_only": {"pending", "rejected"},
    # pending -> approved / failed
    "pending": {"approved", "failed", "superseded", "rejected"},
    # approved -> promoted / failed
    "approved": {"promoted", "failed", "superseded"},
    # promoted -> superseded (a newer promotion replaced this one)
    "promoted": {"superseded"},
    # terminal-ish states
    "rejected": set(),
    "failed": {"pending"},
    "superseded": set(),
}


class PromotionService:
    """Metadata-only promotion ledger service."""

    def __init__(
        self,
        storage: AbstractStorage,
        authz=None,
        audit=None,
    ):
        self._storage = storage
        self._authz = authz
        self._audit = audit if audit is not None else (
            getattr(authz, "audit", None) if authz else None
        )

    # ------------------------------------------------------------------
    # Propose (record an eligibility decision — no promotion executed)
    # ------------------------------------------------------------------

    def propose(
        self,
        candidate: PromotionCandidate,
        *,
        scope_state: str = "mapped",
        session_id: str = "",
        correlation_id: str = "",
    ) -> PromotionRecord:
        """Record a promotion candidate in the ledger.

        Scope must be exactly ``mapped`` — unresolved/ambiguous/partial/
        unmapped are rejected (no global fallback).  The candidate's
        eligibility decision maps to the initial ledger status.

        Deterministic dedup: a candidate identity already present in the
        same profile returns the existing record (no duplicate active
        records).
        """
        if scope_state != "mapped":
            if scope_state == "ambiguous":
                raise ScopeAmbiguousError(
                    "Promotion requires a mapped workspace (ambiguous scope)"
                )
            raise ScopeResolutionError(
                f"Promotion requires a mapped workspace, got '{scope_state}'"
            )

        ws = self._require_workspace(candidate.provenance.workspace_id)
        self._require_scope_match(candidate, ws)

        result = evaluate_eligibility(candidate)
        initial_status = _CONTRACT_STATUS_BY_DECISION.get(
            result.decision, "proposed"
        )

        existing = self._storage.get_promotion_by_candidate_identity(
            candidate.provenance.profile_label,
            candidate.candidate_identity,
        )
        if existing is not None:
            self._audit_lifecycle(
                "promotion.propose.duplicate",
                existing,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            return existing

        fields = {
            "profile_label": candidate.provenance.profile_label,
            "workspace_id": candidate.provenance.workspace_id,
            "project_id": candidate.provenance.project_id,
            "source_type": candidate.provenance.source_type.value,
            "source_id": candidate.provenance.source_id,
            "source_canonical_id": candidate.provenance.source_canonical_id,
            "source_relative_path": candidate.provenance.source_relative_path,
            "source_hash": candidate.provenance.source_hash,
            "source_hash_kind": candidate.provenance.source_hash_kind.value,
            "source_state": candidate.provenance.source_state,
            "assertion_type": candidate.assertion_type.value,
            "claim_hash": candidate.claim_hash,
            "target_kind": candidate.target_kind.value,
            "candidate_identity": candidate.candidate_identity,
            "status": initial_status,
            "eligibility_decision": result.decision.value,
            "rejection_code": result.rejection_code.value if result.rejection_code else "",
        }
        with self._storage.transaction():
            record = self._storage.create_promotion_record(fields)
        self._audit_lifecycle(
            "promotion.propose",
            record,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return record

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, promotion_id: str, *, workspace_id: str = "") -> PromotionRecord:
        """Return a promotion record, workspace-scoped."""
        record = self._storage.get_promotion_record(promotion_id)
        if record is None:
            raise PromotionRecordNotFoundError(promotion_id)
        if workspace_id and record.workspace_id != workspace_id:
            raise PromotionRecordNotFoundError(promotion_id)
        return record

    def list_for_workspace(self, workspace_id: str, *, limit: int = 100) -> list:
        """Return promotion records for a workspace (never global)."""
        if not workspace_id:
            raise ScopeResolutionError("workspace_id is required for ledger lookup")
        return self._storage.list_promotion_records(workspace_id, limit=limit)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        promotion_id: str,
        to_status: str,
        *,
        workspace_id: str = "",
        failure_code: str = "",
        session_id: str = "",
        correlation_id: str = "",
    ) -> PromotionRecord:
        """Transition a promotion record to a new status.

        Deterministic: only the allowed transitions in
        ``_ALLOWED_TRANSITIONS`` are permitted.  Unknown targets raise.
        """
        if to_status not in LEDGER_STATUSES:
            raise PromotionRecordError(
                f"Unknown promotion status '{to_status}'",
                code="PROMOTION_INVALID_STATUS",
            )
        record = self.get(promotion_id, workspace_id=workspace_id)
        allowed = _ALLOWED_TRANSITIONS.get(record.status, set())
        if to_status not in allowed:
            raise PromotionRecordError(
                f"Illegal promotion transition '{record.status}' -> '{to_status}'",
                code="PROMOTION_ILLEGAL_TRANSITION",
            )
        fields: dict = {"status": to_status}
        if to_status == "approved":
            fields["approved_at"] = _now_iso()
        elif to_status == "promoted":
            fields["promoted_at"] = _now_iso()
        if failure_code:
            fields["failure_code"] = failure_code
        with self._storage.transaction():
            updated = self._storage.update_promotion_record(
                promotion_id, fields
            )
        if updated is None:
            raise PromotionRecordNotFoundError(promotion_id)
        self._audit_lifecycle(
            f"promotion.{to_status}",
            updated,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return updated

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_workspace(self, workspace_id: str) -> Workspace:
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise ScopeResolutionError(
                f"Workspace '{workspace_id}' does not exist — no global fallback"
            )
        return ws

    def _require_scope_match(self, candidate: PromotionCandidate, ws: Workspace) -> None:
        """Candidate workspace must match the resolved workspace."""
        if candidate.provenance.workspace_id and \
                candidate.provenance.workspace_id != ws.id:
            raise ScopeResolutionError(
                f"Candidate workspace '{candidate.provenance.workspace_id}' "
                f"does not match resolved workspace '{ws.id}'"
            )

    def _audit_lifecycle(
        self,
        action: str,
        record: PromotionRecord,
        *,
        session_id: str = "",
        correlation_id: str = "",
    ) -> None:
        """Audit a lifecycle mutation — metadata ONLY, never content."""
        if self._audit is None:
            return
        try:
            self._audit.log(
                action=action,
                status=record.status.upper(),
                resource_type="promotion",
                resource_id=record.promotion_id,
                details={
                    "profile_label": record.profile_label,
                    "workspace_id": record.workspace_id,
                    "project_id": record.project_id,
                    "source_type": record.source_type,
                    "source_id": record.source_id,
                    "source_hash": record.source_hash,
                    "claim_hash": record.claim_hash,
                    "target_kind": record.target_kind,
                    "candidate_identity": record.candidate_identity,
                    "eligibility_decision": record.eligibility_decision,
                    "rejection_code": record.rejection_code,
                },
                session_id=session_id,
                correlation_id=correlation_id,
            )
        except Exception:
            _log.exception("promotion audit failed")


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()
