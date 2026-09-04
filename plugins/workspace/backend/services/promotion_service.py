"""S7.5.3 — Workspace memory promotion ledger service.

Ledger phase is metadata-only: lifecycle/status transitions, scope/profile
isolation, deterministic dedup, and audit of mutations without storing
claim, transcript, or secret content.

Execution phase lives in this same module below: execute() invokes the
Hermes memory_tool dispatcher, the ONLY write path to MEMORY.md and USER.md.
See execute() plus _parse_memory_tool_result(). The ledger-only note above
covers candidate recording only, not execution.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

from ..models import (
    PromotionRecord,
    PromotionRecordError,
    PromotionRecordNotFoundError,
    ScopeAmbiguousError,
    ScopeResolutionError,
    Workspace,
)
from ..promotion_contract import (
    canonicalize_claim,
    evaluate_eligibility,
    is_promotable_status,
    revalidate_for_execution,
    structured_snapshot_fields,
)
from ..promotion_models import (
    AssertionType,
    EligibilityDecision,
    PromotionCandidate,
    ProvenanceEnvelope,
    ScopeSnapshot,
    SourceHashKind,
    SourceType,
    TargetKind,
)
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

        Atomic: the current-status read AND the allowed-transition check
        happen INSIDE the ``BEGIN IMMEDIATE`` transaction, so concurrent
        transitions serialize and exactly one caller wins a given
        transition — losers deterministically receive
        ``PROMOTION_ILLEGAL_TRANSITION``.
        """
        if to_status not in LEDGER_STATUSES:
            raise PromotionRecordError(
                f"Unknown promotion status '{to_status}'",
                code="PROMOTION_INVALID_STATUS",
            )
        with self._storage.transaction():
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
    # Execution (S7.5.4a)
    # ------------------------------------------------------------------

    def execute_promotion(
        self,
        promotion_id: str,
        claim_text: str,
        *,
        workspace_id: str,
        user_confirmed: bool,
        session_id: str = "",
        correlation_id: str = "",
    ) -> PromotionRecord:
        """Execute an explicit, caller-confirmed promotion.

        Flow (fail-closed at every step):

        * load the ledger record
        * verify workspace / profile / source authority
        * reconstruct the candidate from the ledger + caller claim
        * re-run eligibility + live source freshness
        * verify claim hash == recorded claim_hash
        * transition promotable status -> pending -> approved
        * invoke the Hermes ``memory_tool`` dispatcher (the ONLY write path)
        * ``success and not staged`` -> promoted
        * ``success and staged`` -> remain approved (Hermes pending file is
          the source of truth for the staged write)
        * failure / malformed result -> failed

        The caller cannot change the claim represented by the record: the
        supplied ``claim_text`` must hash to the recorded ``claim_hash``.
        """
        record = self._storage.get_promotion_record(promotion_id)
        if record is None:
            raise PromotionRecordNotFoundError(promotion_id)

        # --- authority guards (fail closed, no global fallback) -----------
        if workspace_id and record.workspace_id != workspace_id:
            raise PromotionRecordNotFoundError(promotion_id)
        if self._effective_profile_label() != record.profile_label:
            raise PromotionRecordNotFoundError(promotion_id)
        self._require_workspace(record.workspace_id)

        # --- terminal / idempotent states ---------------------------------
        if record.status == "promoted":
            if self._claim_hash(claim_text) != record.claim_hash:
                raise PromotionRecordError(
                    "claim_text does not match the promoted record's claim_hash",
                    code="PROMOTION_CLAIM_MISMATCH",
                )
            self._audit_lifecycle(
                "promotion.promoted.idempotent",
                record,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            return record
        if record.status == "approved":
            # A previous execution staged the write; the Hermes pending file
            # owns the outcome.  Do NOT re-invoke memory_tool.
            self._audit_lifecycle(
                "promotion.approved.idempotent",
                record,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            return record
        if record.status in ("rejected", "superseded", "failed"):
            raise PromotionRecordError(
                f"Cannot execute promotion in terminal state '{record.status}'",
                code="PROMOTION_ILLEGAL_TRANSITION",
            )

        # --- reconstruct the candidate from the ledger ---------------------
        ws = self._require_workspace(record.workspace_id)
        candidate = self._reconstruct_candidate(record, ws, claim_text, user_confirmed)

        # claim_text must match the recorded claim hash.
        if candidate.claim_hash != record.claim_hash:
            raise PromotionRecordError(
                "claim_text does not match the record's claim_hash — "
                "the caller cannot change the promoted claim",
                code="PROMOTION_CLAIM_MISMATCH",
            )

        # --- live source freshness -----------------------------------------
        live_hash = self._live_source_hash(record)
        revalidated = revalidate_for_execution(candidate, live_hash)
        if revalidated.decision != EligibilityDecision.ELIGIBLE:
            code = (
                revalidated.rejection_code.value
                if revalidated.rejection_code else "rejected"
            )
            if revalidated.decision == EligibilityDecision.PROPOSED:
                # Unconfirmed model inference stays proposed — never writes,
                # never transitions to rejected on its own.
                raise PromotionRecordError(
                    f"Promotion requires explicit confirmation: {revalidated.reason}",
                    code="PROMOTION_NOT_CONFIRMED",
                )
            if revalidated.decision == EligibilityDecision.MANUAL_ONLY:
                # User-authored facts without explicit confirmation stay
                # manual_only — never writes.
                raise PromotionRecordError(
                    f"Promotion requires explicit confirmation: {revalidated.reason}",
                    code="PROMOTION_NOT_CONFIRMED",
                )
            self._transition_or_raise(
                record,
                "rejected",
                failure_code=code,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            raise PromotionRecordError(
                f"Promotion revalidation failed: {revalidated.reason}",
                code=code.upper(),
            )

        # --- approval transitions -------------------------------------------
        if is_promotable_status(record.status):
            record = self.transition(
                record.promotion_id,
                "pending",
                workspace_id=workspace_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )
        if record.status == "pending":
            record = self.transition(
                record.promotion_id,
                "approved",
                workspace_id=workspace_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )
        # Re-fetch: concurrent executions may have advanced the record.
        record = self.get(record.promotion_id, workspace_id=workspace_id)
        if record.status != "approved":
            raise PromotionRecordError(
                f"Cannot execute promotion in state '{record.status}'",
                code="PROMOTION_ILLEGAL_TRANSITION",
            )

        # --- Hermes memory write (the ONLY write path) ----------------------
        try:
            from tools.memory_tool import (  # type: ignore[import-untyped]
                load_on_disk_store,
                memory_tool,
            )

            store = load_on_disk_store()
            raw = memory_tool(
                action="add",
                target=record.target_kind,
                content=claim_text,
                store=store,
            )
        except Exception:
            # Outcome is UNKNOWN: the write may have landed before the
            # exception.  Never guess — leave the ledger conservatively at
            # approved (with an explicit marker) rather than marking
            # promoted or failed.
            _log.exception("memory_tool invocation raised (outcome unknown)")
            self._mark_unknown_outcome(
                record,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            return self.get(record.promotion_id, workspace_id=workspace_id)

        parsed = self._parse_memory_tool_result(raw)
        if parsed is None:
            return self.transition(
                record.promotion_id,
                "failed",
                workspace_id=workspace_id,
                failure_code="malformed_memory_tool_response",
                session_id=session_id,
                correlation_id=correlation_id,
            )

        success = parsed.get("success") is True
        staged = parsed.get("staged") is True

        if success and not staged:
            return self.transition(
                record.promotion_id,
                "promoted",
                workspace_id=workspace_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )

        if success and staged:
            # Remains approved; the Hermes pending file is authoritative.
            self._audit_lifecycle(
                "promotion.approved_staged",
                record,
                session_id=session_id,
                correlation_id=correlation_id,
                extra_details={
                    "pending_id": str(parsed.get("pending_id") or ""),
                },
            )
            return self.get(record.promotion_id, workspace_id=workspace_id)

        failure_code = str(parsed.get("error") or "memory_write_error")[:200]
        return self.transition(
            record.promotion_id,
            "failed",
            workspace_id=workspace_id,
            failure_code=failure_code,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    def _mark_unknown_outcome(
        self,
        record: PromotionRecord,
        *,
        session_id: str = "",
        correlation_id: str = "",
    ) -> None:
        """Record an UNKNOWN memory-write outcome without changing status.

        Used when ``memory_tool`` raises: the write may or may not have
        landed, so the ledger stays at ``approved`` with an explicit
        ``failure_code`` marker for later reconciliation.  Never guesses
        promoted or failed.
        """
        try:
            with self._storage.transaction():
                self._storage.update_promotion_record(
                    record.promotion_id,
                    {"failure_code": "memory_write_outcome_unknown"},
                )
        except Exception:
            _log.exception("failed to mark unknown outcome for %s", record.promotion_id)
        self._audit_lifecycle(
            "promotion.approved_unknown_outcome",
            record,
            session_id=session_id,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # Reconciliation (S7.5.4b)
    # ------------------------------------------------------------------

    def reconcile_promotion(
        self,
        promotion_id: str,
        *,
        workspace_id: str,
        user_confirmed: bool,
        claim_text: str,
        session_id: str = "",
        correlation_id: str = "",
    ) -> PromotionRecord:
        """Reconcile an already-approved memory promotion (outcome detection
        ONLY — never grants authorization).

        ``execute_promotion`` already performed authorization, eligibility,
        source validation, claim validation, and write initiation.  This
        method determines only whether that previously authorized operation
        COMPLETED, by inspecting the Hermes staged pending file and the exact
        ``MEMORY.md``/``USER.md`` entry.

        Never calls ``evaluate_eligibility`` / ``revalidate_for_execution`` —
        those are PRE-WRITE gates.  A source becoming stale AFTER a successful
        write does NOT revoke the completed promotion.
        """
        record = self._storage.get_promotion_record(promotion_id)
        if record is None:
            raise PromotionRecordNotFoundError(promotion_id)

        # --- authority guards (identity checks only, not authorization) ----
        if workspace_id and record.workspace_id != workspace_id:
            raise PromotionRecordNotFoundError(promotion_id)
        if self._effective_profile_label() != record.profile_label:
            raise PromotionRecordNotFoundError(promotion_id)
        self._require_workspace(record.workspace_id)

        # --- claim hash: caller cannot change the promoted claim ------------
        if self._claim_hash(claim_text) != record.claim_hash:
            raise PromotionRecordError(
                "claim_text does not match the record's claim_hash",
                code="PROMOTION_CLAIM_MISMATCH",
            )

        # --- status behavior ------------------------------------------------
        if record.status == "promoted":
            self._audit_lifecycle(
                "promotion.reconciled_idempotent",
                record,
                session_id=session_id,
                correlation_id=correlation_id,
            )
            return record
        if record.status != "approved":
            raise PromotionRecordError(
                f"Cannot reconcile promotion in state '{record.status}'",
                code="PROMOTION_ILLEGAL_TRANSITION",
            )

        # --- resolve the staged pending file (Hermes-owned) -----------------
        pending_id = self._resolve_pending_id(record, claim_text)

        if pending_id is not None:
            pending = self._read_pending_file(pending_id)
            if pending is not None:
                # The write is still staged and not yet decided.
                self._audit_lifecycle(
                    "promotion.reconciled_pending_still",
                    record,
                    session_id=session_id,
                    correlation_id=correlation_id,
                    extra_details={"pending_id": pending_id},
                )
                return self.get(record.promotion_id, workspace_id=workspace_id)

        # pending_id resolved but file absent/malformed, OR no pending found.
        # Determine completion by EXACT memory-entry membership.
        if self._exact_memory_entry_present(record, claim_text):
            return self._promote_if_possible(
                record,
                workspace_id=workspace_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )

        # No pending file, no exact memory entry, and Hermes exposes no
        # definitive failure record -> conservative UNKNOWN (never failed).
        self._mark_unknown_outcome(
            record,
            session_id=session_id,
            correlation_id=correlation_id,
        )
        return self.get(record.promotion_id, workspace_id=workspace_id)

    # ------------------------------------------------------------------
    # Reconciliation helpers (S7.5.4b)
    # ------------------------------------------------------------------

    def _promote_if_possible(
        self,
        record: PromotionRecord,
        *,
        workspace_id: str,
        session_id: str = "",
        correlation_id: str = "",
    ) -> PromotionRecord:
        """Transition approved -> promoted, retry-safe under concurrency.

        If another reconciler already promoted the record, re-read and return
        it idempotently instead of surfacing an illegal-transition error.
        """
        try:
            return self.transition(
                record.promotion_id,
                "promoted",
                workspace_id=workspace_id,
                session_id=session_id,
                correlation_id=correlation_id,
            )
        except PromotionRecordError as exc:
            if getattr(exc, "code", "") == "PROMOTION_ILLEGAL_TRANSITION":
                # Re-read: the record may have advanced to promoted by a
                # concurrent reconciler (retry-safe).
                fresh = self.get(record.promotion_id, workspace_id=workspace_id)
                if fresh.status == "promoted":
                    self._audit_lifecycle(
                        "promotion.reconciled_idempotent",
                        fresh,
                        session_id=session_id,
                        correlation_id=correlation_id,
                    )
                    return fresh
                if fresh.status == "approved":
                    # Still approved -> outcome unknown, not failed.
                    self._mark_unknown_outcome(
                        fresh,
                        session_id=session_id,
                        correlation_id=correlation_id,
                    )
                    return fresh
            raise

    def _resolve_pending_id(self, record: PromotionRecord, claim_text: str) -> Optional[str]:
        """Resolve the authoritative Hermes pending_id.

        Primary: the recorded audit ``details.pending_id`` is authoritative —
        but the ledger does not persist it as a column, so we recover it from
        the workspace audit log for this promotion.  Fallback: scan
        ``list_pending("memory")`` matching target + canonical claim — exactly
        one match acceptable; zero/multiple -> unresolved (None).
        """
        # 1. Audit-details primary association.
        recorded = self._pending_id_from_audit(record)
        if recorded:
            return recorded

        # 2. Fallback scan (target + canonical claim).
        try:
            from tools.write_approval import list_pending  # type: ignore[import-untyped]

            pending = list_pending("memory")
        except Exception:
            return None

        canonical = canonicalize_claim(claim_text)
        matches = [
            p for p in pending
            if self._pending_matches(p, record, canonical)
        ]
        if len(matches) == 1:
            return str(matches[0].get("id") or "")
        # zero or multiple -> unresolved/ambiguous; never arbitrary selection.
        return None

    def _pending_id_from_audit(self, record: PromotionRecord) -> str:
        """Recover the authoritative pending_id from the promotion's audit
        trail (the ``details.pending_id`` written by S7.5.4a)."""
        if self._audit is None:
            return ""
        try:
            events = self._audit.read(200)
        except Exception:
            return ""
        for event in events:
            if event.get("resource_id") != record.promotion_id:
                continue
            details = event.get("details") or {}
            pid = str(details.get("pending_id") or "")
            if pid:
                return pid
        return ""

    def _pending_matches(self, pending: dict, record: PromotionRecord, canonical: str) -> bool:
        """A pending record matches when target and canonical claim agree."""
        try:
            payload = pending.get("payload") or {}
            if str(payload.get("target") or "memory") != record.target_kind:
                return False
            content = str(payload.get("content") or "")
            return canonicalize_claim(content) == canonical
        except Exception:
            return False

    def _read_pending_file(self, pending_id: str) -> Optional[dict]:
        """Read a Hermes pending memory record; None when missing/malformed."""
        try:
            from tools.write_approval import get_pending  # type: ignore[import-untyped]

            return get_pending("memory", pending_id)
        except Exception:
            return None

    def _exact_memory_entry_present(self, record: PromotionRecord, claim_text: str) -> bool:
        """True when the EXACT canonical claim is a member of the target memory
        file, using Hermes' own entry parsing (``\n§\n`` delimiter + strip).

        Never substring matching.  Profile-scoped via ``get_hermes_home()``.
        """
        try:
            from tools.memory_tool import (  # type: ignore[import-untyped]
                load_on_disk_store,
            )

            store = load_on_disk_store()
            entries = store._entries_for(record.target_kind)
        except Exception:
            _log.exception("exact memory-entry check failed — conservative unknown")
            return False
        canonical = canonicalize_claim(claim_text)
        return any(canonicalize_claim(e) == canonical for e in entries)

    # ------------------------------------------------------------------
    # Execution helpers (S7.5.4a)
    # ------------------------------------------------------------------

    @staticmethod
    def _claim_hash(claim_text: str) -> str:
        from ..promotion_contract import hash_claim

        return hash_claim(claim_text)

    @staticmethod
    def _effective_profile_label() -> str:
        """Safe profile identifier — the effective home basename, never a raw
        path."""
        try:
            return Path(get_hermes_home()).resolve().name
        except Exception:
            return ""

    def _reconstruct_candidate(
        self,
        record: PromotionRecord,
        ws: Workspace,
        claim_text: str,
        user_confirmed: bool,
    ) -> PromotionCandidate:
        """Rebuild the candidate from ledger metadata + the caller claim."""
        provenance = ProvenanceEnvelope(
            source_type=SourceType(record.source_type),
            source_id=record.source_id,
            source_canonical_id=record.source_canonical_id,
            source_relative_path=record.source_relative_path,
            source_hash=record.source_hash,
            source_hash_kind=SourceHashKind(record.source_hash_kind),
            source_state=record.source_state,
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            profile_label=record.profile_label,
        )
        scope = ScopeSnapshot(
            profile_label=record.profile_label,
            workspace_id=record.workspace_id,
            workspace_name=ws.name,
            project_id=record.project_id,
            scope_state="mapped",
            match_source="ledger",
        )
        from ..promotion_contract import make_candidate

        return make_candidate(
            claim_text=claim_text,
            assertion_type=AssertionType(record.assertion_type),
            target_kind=TargetKind(record.target_kind),
            provenance=provenance,
            scope=scope,
            user_confirmed=user_confirmed,
        )

    def _live_source_hash(self, record: PromotionRecord) -> Optional[str]:
        """Compute the live source hash for freshness verification.

        * ADR: SHA-256 of the canonical file bytes (``content_hash``).
        * journal/task/roadmap: deterministic structured snapshot.
        * session evidence: not executable in S7.5.4a -> None (fail closed).
        """
        source_type = SourceType(record.source_type)
        try:
            if source_type == SourceType.ADR:
                adr = self._storage.get_adr(record.source_id)
                if adr is None or adr.workspace_id != record.workspace_id:
                    return None
                if adr.reconcile_state != "synced":
                    return None
                if not adr.content_hash:
                    return None
                return adr.content_hash

            if source_type == SourceType.JOURNAL:
                row = self._storage.get_journal_entry(record.source_id)
            elif source_type == SourceType.TASK:
                row = self._storage.get_task(record.source_id)
            elif source_type == SourceType.ROADMAP:
                row = self._storage.get_roadmap(record.source_id)
            else:
                # session evidence has no live workspace artifact to verify.
                return None

            if row is None or (getattr(row, "workspace_id", "") or "") != record.workspace_id:
                return None
            from ..promotion_contract import hash_structured_snapshot

            return hash_structured_snapshot(
                structured_snapshot_fields(source_type, row)
            )
        except Exception:
            _log.exception("live source hash computation failed — fail closed")
            return None

    @staticmethod
    def _parse_memory_tool_result(raw) -> Optional[dict]:
        """Safely parse the memory_tool JSON result.  Malformed -> None."""
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            return None
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _transition_or_raise(
        self,
        record: PromotionRecord,
        to_status: str,
        *,
        failure_code: str = "",
        session_id: str = "",
        correlation_id: str = "",
    ) -> None:
        """Best-effort transition when revalidation rejects a candidate.

        Only transitions allowed by the existing state machine are applied
        (e.g. eligible -> rejected); if the transition is illegal (e.g. an
        already-rejected record) the state is left untouched and the caller
        still fails closed.
        """
        try:
            self.transition(
                record.promotion_id,
                to_status,
                workspace_id=record.workspace_id,
                failure_code=failure_code,
                session_id=session_id,
                correlation_id=correlation_id,
            )
        except PromotionRecordError:
            _log.debug(
                "promotion %s could not transition to %s (left unchanged)",
                record.promotion_id,
                to_status,
            )

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
        extra_details: Optional[dict] = None,
    ) -> None:
        """Audit a lifecycle mutation — metadata ONLY, never content."""
        if self._audit is None:
            return
        try:
            details = {
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
            }
            if extra_details:
                details.update(extra_details)
            self._audit.log(
                action=action,
                status=record.status.upper(),
                resource_type="promotion",
                resource_id=record.promotion_id,
                details=details,
                session_id=session_id,
                correlation_id=correlation_id,
            )
        except Exception:
            _log.exception("promotion audit failed")


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()
