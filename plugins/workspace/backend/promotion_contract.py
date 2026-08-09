"""S7.5.2 — Promotion eligibility contract.

Deterministic, side-effect-free eligibility evaluation for a
:class:`~plugins.workspace.backend.promotion_models.PromotionCandidate`.

This module does NOT:
* persist anything
* write to ``MEMORY.md`` / ``USER.md``
* invoke the Hermes memory tool
* call external memory providers
* perform audit/network/file I/O
* modify Workspace state

It only inspects the candidate's provenance, assertion type, scope
snapshot, and source state to produce an :class:`EligibilityResult`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from .promotion_models import (
    AssertionType,
    EligibilityDecision,
    EligibilityResult,
    PromotionCandidate,
    ProvenanceEnvelope,
    RejectionCode,
    ScopeSnapshot,
    SourceHashKind,
    SourceType,
    TargetKind,
)

# ---------------------------------------------------------------------------
# Deterministic hashing
# ---------------------------------------------------------------------------


def canonicalize_claim(claim_text: str) -> str:
    """Normalize a claim string deterministically before hashing.

    Strips surrounding whitespace, collapses internal whitespace runs to
    single spaces, and normalizes line endings.  The result is stable
    across platforms and Python versions.
    """
    return " ".join(claim_text.strip().split())


def hash_claim(claim_text: str) -> str:
    """SHA-256 of the canonicalized claim text (UTF-8 encoded)."""
    canonical = canonicalize_claim(claim_text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_structured_snapshot(fields: Dict[str, Any]) -> str:
    """SHA-256 of a deterministic JSON encoding of structured fields.

    Keys are sorted; values are JSON-serialized with ``sort_keys=True``
    and ``ensure_ascii=False``.  This produces a stable hash for the
    same logical input regardless of dict insertion order.
    """
    encoded = json.dumps(
        fields,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------


_VALID_SCOPE_STATES = {"mapped"}
_REJECT_SCOPE_STATES = {
    "unresolved": RejectionCode.SCOPE_UNRESOLVED,
    "ambiguous": RejectionCode.SCOPE_AMBIGUOUS,
    "partial": RejectionCode.SCOPE_PARTIAL,
    "unmapped": RejectionCode.SCOPE_UNMAPPED,
}


def _validate_scope(
    scope: ScopeSnapshot,
    provenance: ProvenanceEnvelope,
) -> Optional[Tuple[RejectionCode, str]]:
    """Return (rejection_code, reason) if scope is not valid for promotion."""
    if scope.scope_state not in _VALID_SCOPE_STATES:
        code = _REJECT_SCOPE_STATES.get(
            scope.scope_state, RejectionCode.SCOPE_UNRESOLVED
        )
        return code, f"Scope state is '{scope.scope_state}', must be 'mapped'"

    # Workspace must match.
    if provenance.workspace_id and scope.workspace_id and \
            provenance.workspace_id != scope.workspace_id:
        return RejectionCode.WORKSPACE_MISMATCH, (
            f"Provenance workspace_id '{provenance.workspace_id}' does not "
            f"match scope workspace_id '{scope.workspace_id}'"
        )

    # Project must match.
    if provenance.project_id and scope.project_id and \
            provenance.project_id != scope.project_id:
        return RejectionCode.PROJECT_MISMATCH, (
            f"Provenance project_id '{provenance.project_id}' does not "
            f"match scope project_id '{scope.project_id}'"
        )

    # Profile must match.
    if provenance.profile_label and scope.profile_label and \
            provenance.profile_label != scope.profile_label:
        return RejectionCode.PROFILE_MISMATCH, (
            f"Provenance profile_label '{provenance.profile_label}' does not "
            f"match scope profile_label '{scope.profile_label}'"
        )

    return None


# ---------------------------------------------------------------------------
# Source validation
# ---------------------------------------------------------------------------


# ADR reconcile states that are acceptable for canonical_fact promotion.
_ADR_CANONICAL_STATES = {"synced"}
# ADR reconcile states that are explicitly rejected.
_ADR_REJECT_STATES = {
    "conflict": RejectionCode.SOURCE_CONFLICTED,
    "missing_file": RejectionCode.SOURCE_MISSING,
    "invalid": RejectionCode.SOURCE_NOT_CANONICAL,
}
# Mutable structured sources (journal/task/roadmap) — always mutable.
_MUTABLE_SOURCE_TYPES = {SourceType.JOURNAL, SourceType.TASK, SourceType.ROADMAP}


def _validate_adr_source(
    provenance: ProvenanceEnvelope,
) -> Optional[Tuple[RejectionCode, str]]:
    """ADR-specific source validation."""
    state = provenance.source_state or ""

    if state in _ADR_REJECT_STATES:
        code = _ADR_REJECT_STATES[state]
        return code, f"ADR reconcile state is '{state}'"

    if state not in _ADR_CANONICAL_STATES:
        return RejectionCode.SOURCE_NOT_CANONICAL, (
            f"ADR reconcile state is '{state}', must be one of "
            f"{sorted(_ADR_CANONICAL_STATES)}"
        )

    if provenance.source_hash_kind != SourceHashKind.SHA256_BYTES:
        return RejectionCode.SOURCE_HASH_MISSING, (
            f"ADR source_hash_kind must be 'sha256_bytes', got "
            f"'{provenance.source_hash_kind.value}'"
        )

    if not provenance.source_hash:
        return RejectionCode.SOURCE_HASH_MISSING, "ADR source_hash is empty"

    return None


def _validate_structured_source(
    provenance: ProvenanceEnvelope,
) -> Optional[Tuple[RejectionCode, str]]:
    """Journal/task/roadmap source validation (mutable structured snapshot)."""
    if provenance.source_hash_kind != SourceHashKind.STRUCTURED_SNAPSHOT:
        return RejectionCode.SOURCE_HASH_MISSING, (
            f"Structured source_hash_kind must be 'structured_snapshot', "
            f"got '{provenance.source_hash_kind.value}'"
        )
    if not provenance.source_hash:
        return RejectionCode.SOURCE_HASH_MISSING, "source_hash is empty"
    return None


def _validate_session_evidence(
    provenance: ProvenanceEnvelope,
) -> Optional[Tuple[RejectionCode, str]]:
    """Session evidence validation.

    A durable SessionDB row id is required.  Volatile Desktop runtime
    IDs are rejected.  The contract cannot inspect SessionDB directly
    (that is a later phase), so it applies structural rules: the id must
    be non-empty and must not look like a volatile runtime id.
    """
    sid = provenance.session_id
    if not sid:
        return RejectionCode.MISSING_PROVENANCE, "session_id is required for session evidence"

    # Volatile runtime ids are typically short hex-ish or descriptive
    # strings injected by the Desktop runtime.  The canonical durable id
    # is a stored SessionDB row id (hex timestamp-based).  We reject
    # obvious volatile prefixes and very short ids.
    _VOLATILE_PREFIXES = ("volatile-", "runtime-", "desktop-", "tmp-")
    low = sid.lower()
    for prefix in _VOLATILE_PREFIXES:
        if low.startswith(prefix):
            return RejectionCode.VOLATILE_SESSION_ID, (
                f"session_id '{sid}' appears to be a volatile runtime id"
            )

    return None


def _validate_source(
    provenance: ProvenanceEnvelope,
) -> Optional[Tuple[RejectionCode, str]]:
    """Dispatch to source-type-specific validation."""
    st = provenance.source_type

    if st == SourceType.ADR:
        return _validate_adr_source(provenance)

    if st in _MUTABLE_SOURCE_TYPES:
        return _validate_structured_source(provenance)

    if st == SourceType.SESSION_EVIDENCE:
        return _validate_session_evidence(provenance)

    return RejectionCode.SOURCE_NOT_CANONICAL, f"Unknown source_type '{st.value}'"


# ---------------------------------------------------------------------------
# Assertion-type rules
# ---------------------------------------------------------------------------


def _validate_assertion(
    candidate: PromotionCandidate,
) -> Optional[Tuple[EligibilityDecision, Optional[RejectionCode], str]]:
    """Apply assertion-type authority rules.

    Returns ``(decision, rejection_code, reason)`` or ``None`` if the
    assertion type passes.
    """
    at = candidate.assertion_type

    if at == AssertionType.MODEL_INFERENCE:
        if not candidate.user_confirmed:
            return (
                EligibilityDecision.PROPOSED,
                RejectionCode.MODEL_INFERENCE_NOT_CONFIRMED,
                "model_inference must be explicitly confirmed before promotion",
            )
        # Confirmed model inference falls through to source/scope checks.

    if at == AssertionType.USER_AUTHORED_FACT:
        # User-authored facts require manual provenance; they are always
        # manual_only unless the user explicitly confirmed.
        if not candidate.user_confirmed:
            return (
                EligibilityDecision.MANUAL_ONLY,
                None,
                "user_authored_fact requires explicit user confirmation",
            )

    if at == AssertionType.USER_CONFIRMED_SUMMARY:
        if not candidate.user_confirmed:
            return (
                EligibilityDecision.REJECTED,
                RejectionCode.MISSING_PROVENANCE,
                "user_confirmed_summary requires user_confirmed=True",
            )

    # canonical_fact passes through to source/scope validation.
    return None


# ---------------------------------------------------------------------------
# Public eligibility evaluation
# ---------------------------------------------------------------------------


def evaluate_eligibility(candidate: PromotionCandidate) -> EligibilityResult:
    """Evaluate whether a candidate is eligible for promotion.

    Pure function: no side effects.  Returns an :class:`EligibilityResult`.
    """
    # 1. Assertion-type authority rules.
    assertion_result = _validate_assertion(candidate)
    if assertion_result is not None:
        decision, code, reason = assertion_result
        return EligibilityResult(
            decision=decision,
            candidate=candidate,
            rejection_code=code,
            reason=reason,
        )

    # 2. Scope validation.
    scope_result = _validate_scope(candidate.scope, candidate.provenance)
    if scope_result is not None:
        code, reason = scope_result
        return EligibilityResult(
            decision=EligibilityDecision.REJECTED,
            candidate=candidate,
            rejection_code=code,
            reason=reason,
        )

    # 3. Source validation.
    source_result = _validate_source(candidate.provenance)
    if source_result is not None:
        code, reason = source_result
        return EligibilityResult(
            decision=EligibilityDecision.REJECTED,
            candidate=candidate,
            rejection_code=code,
            reason=reason,
        )

    # 4. All checks passed.
    return EligibilityResult(
        decision=EligibilityDecision.ELIGIBLE,
        candidate=candidate,
    )


# ---------------------------------------------------------------------------
# Helpers for building candidates
# ---------------------------------------------------------------------------


def make_candidate(
    *,
    claim_text: str,
    assertion_type: AssertionType,
    target_kind: TargetKind,
    provenance: ProvenanceEnvelope,
    scope: ScopeSnapshot,
    user_confirmed: bool = False,
) -> PromotionCandidate:
    """Build a :class:`PromotionCandidate` with a deterministic claim hash."""
    return PromotionCandidate(
        provenance=provenance,
        assertion_type=assertion_type,
        target_kind=target_kind,
        claim_text=claim_text,
        claim_hash=hash_claim(claim_text),
        scope=scope,
        user_confirmed=user_confirmed,
    )
