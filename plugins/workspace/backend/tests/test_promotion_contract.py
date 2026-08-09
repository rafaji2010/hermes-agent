"""S7.5.2 — Promotion contract and eligibility tests.

Tests are deterministic and hermetic: no database, no network, no file
I/O, no Hermes memory tool, no Workspace API calls.  Only the contract
models and the pure eligibility evaluator are exercised.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.promotion_models import (  # type: ignore[import-untyped]
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
from plugins.workspace.backend.promotion_contract import (  # type: ignore[import-untyped]
    canonicalize_claim,
    evaluate_eligibility,
    hash_claim,
    hash_structured_snapshot,
    make_candidate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mapped_scope(**kw) -> ScopeSnapshot:
    defaults = dict(
        profile_label="profile-a",
        workspace_id="ws-1",
        workspace_name="ws-a",
        project_id="proj-1",
        project_slug="proj-a",
        scope_state="mapped",
        match_source="session_cwd",
    )
    defaults.update(kw)
    return ScopeSnapshot(**defaults)


def _adr_provenance(**kw) -> ProvenanceEnvelope:
    defaults = dict(
        source_type=SourceType.ADR,
        source_id="adr-001",
        source_canonical_id="0001-auth-design",
        source_relative_path="docs/adr/0001-auth-design.md",
        source_hash="a" * 64,
        source_hash_kind=SourceHashKind.SHA256_BYTES,
        source_state="synced",
        workspace_id="ws-1",
        project_id="proj-1",
        profile_label="profile-a",
    )
    defaults.update(kw)
    return ProvenanceEnvelope(**defaults)


def _journal_provenance(**kw) -> ProvenanceEnvelope:
    defaults = dict(
        source_type=SourceType.JOURNAL,
        source_id="je-001",
        source_hash="b" * 64,
        source_hash_kind=SourceHashKind.STRUCTURED_SNAPSHOT,
        source_state="mutable",
        workspace_id="ws-1",
        project_id="proj-1",
        profile_label="profile-a",
    )
    defaults.update(kw)
    return ProvenanceEnvelope(**defaults)


def _session_provenance(**kw) -> ProvenanceEnvelope:
    defaults = dict(
        source_type=SourceType.SESSION_EVIDENCE,
        source_id="sess-evidence-1",
        source_hash="c" * 64,
        source_hash_kind=SourceHashKind.STRUCTURED_SNAPSHOT,
        source_state="durable",
        workspace_id="ws-1",
        project_id="proj-1",
        profile_label="profile-a",
        session_id="durable-sess-1234567890",
    )
    defaults.update(kw)
    return ProvenanceEnvelope(**defaults)


def _eligible_candidate(**kw) -> PromotionCandidate:
    """Build a canonical_fact ADR candidate with sensible defaults.

    Override any field: provenance, scope, assertion_type, target_kind,
    claim_text, user_confirmed.
    """
    fields = dict(
        claim_text="Use JWT for authentication in the Hermes backend.",
        assertion_type=AssertionType.CANONICAL_FACT,
        target_kind=TargetKind.MEMORY,
        provenance=_adr_provenance(),
        scope=_mapped_scope(),
        user_confirmed=False,
    )
    fields.update(kw)
    return make_candidate(**fields)


# ---------------------------------------------------------------------------
# A. Deterministic hashing
# ---------------------------------------------------------------------------


class TestHashing:
    def test_claim_hash_deterministic(self):
        h1 = hash_claim("  Use JWT  for auth.  \n")
        h2 = hash_claim("Use JWT for auth.")
        assert h1 == h2

    def test_different_claims_different_hash(self):
        assert hash_claim("Use JWT.") != hash_claim("Use OAuth2.")

    def test_canonicalize_collapses_whitespace(self):
        assert canonicalize_claim("a\n b\t  c") == "a b c"

    def test_structured_snapshot_deterministic(self):
        fields = {"title": "X", "status": "open", "id": "t-1"}
        h1 = hash_structured_snapshot(fields)
        # Different insertion order, same logical content.
        h2 = hash_structured_snapshot({"id": "t-1", "status": "open", "title": "X"})
        assert h1 == h2

    def test_structured_snapshot_different_fields(self):
        assert hash_structured_snapshot({"a": 1}) != hash_structured_snapshot({"a": 2})


# ---------------------------------------------------------------------------
# B. Assertion types
# ---------------------------------------------------------------------------


class TestAssertionTypes:
    def test_canonical_fact_eligible(self):
        result = evaluate_eligibility(_eligible_candidate())
        assert result.is_eligible

    def test_user_confirmed_summary_requires_confirmation(self):
        cand = make_candidate(
            claim_text="Summary of decision",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=False,
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.MISSING_PROVENANCE

    def test_user_confirmed_summary_eligible_when_confirmed(self):
        cand = make_candidate(
            claim_text="Summary of decision",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_eligible

    def test_user_authored_fact_manual_only_without_confirmation(self):
        cand = make_candidate(
            claim_text="Manual fact",
            assertion_type=AssertionType.USER_AUTHORED_FACT,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=False,
        )
        result = evaluate_eligibility(cand)
        assert result.is_manual_only

    def test_user_authored_fact_eligible_when_confirmed(self):
        cand = make_candidate(
            claim_text="Manual fact",
            assertion_type=AssertionType.USER_AUTHORED_FACT,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_eligible

    def test_model_inference_proposed_only(self):
        cand = make_candidate(
            claim_text="I think we should use OAuth2",
            assertion_type=AssertionType.MODEL_INFERENCE,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=False,
        )
        result = evaluate_eligibility(cand)
        assert result.is_proposed
        assert result.rejection_code == RejectionCode.MODEL_INFERENCE_NOT_CONFIRMED

    def test_model_inference_cannot_silently_become_authoritative(self):
        """Unconfirmed model inference is NEVER eligible."""
        for _ in range(10):
            result = evaluate_eligibility(_eligible_candidate(
                assertion_type=AssertionType.MODEL_INFERENCE,
                user_confirmed=False,
            ))
            assert not result.is_eligible

    def test_model_inference_eligible_after_explicit_confirmation(self):
        cand = make_candidate(
            claim_text="Confirmed inference",
            assertion_type=AssertionType.MODEL_INFERENCE,
            target_kind=TargetKind.MEMORY,
            provenance=_adr_provenance(),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_eligible


# ---------------------------------------------------------------------------
# C. ADR source eligibility
# ---------------------------------------------------------------------------


class TestADRSourceEligibility:
    def test_synced_adr_eligible(self):
        result = evaluate_eligibility(_eligible_candidate())
        assert result.is_eligible

    def test_conflicted_adr_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(source_state="conflict"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SOURCE_CONFLICTED

    def test_stale_adr_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(source_state="file_changed"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SOURCE_NOT_CANONICAL

    def test_missing_file_adr_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(source_state="missing_file"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SOURCE_MISSING

    def test_invalid_adr_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(source_state="invalid"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SOURCE_NOT_CANONICAL

    def test_missing_source_hash_rejected(self):
        with pytest.raises(ValueError, match="source_hash is required"):
            _adr_provenance(source_hash="")


# ---------------------------------------------------------------------------
# D. Scope rejection
# ---------------------------------------------------------------------------


class TestScopeRejection:
    def test_unresolved_scope_rejected(self):
        cand = _eligible_candidate(scope=_mapped_scope(scope_state="unresolved"))
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SCOPE_UNRESOLVED

    def test_ambiguous_scope_rejected(self):
        cand = _eligible_candidate(scope=_mapped_scope(scope_state="ambiguous"))
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SCOPE_AMBIGUOUS

    def test_partial_scope_rejected(self):
        cand = _eligible_candidate(scope=_mapped_scope(scope_state="partial"))
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SCOPE_PARTIAL

    def test_unmapped_scope_rejected(self):
        cand = _eligible_candidate(scope=_mapped_scope(scope_state="unmapped"))
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SCOPE_UNMAPPED

    def test_workspace_mismatch_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(workspace_id="other-ws"),
            scope=_mapped_scope(workspace_id="ws-1"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.WORKSPACE_MISMATCH

    def test_project_mismatch_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(project_id="other-proj"),
            scope=_mapped_scope(project_id="proj-1"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.PROJECT_MISMATCH

    def test_profile_mismatch_rejected(self):
        cand = _eligible_candidate(
            provenance=_adr_provenance(profile_label="profile-b"),
            scope=_mapped_scope(profile_label="profile-a"),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.PROFILE_MISMATCH


# ---------------------------------------------------------------------------
# E. Structured source hashing (journal/task/roadmap)
# ---------------------------------------------------------------------------


class TestStructuredSourceHashing:
    def test_journal_structured_snapshot(self):
        fields = {"title": "Journal Entry", "entry_date": "2026-08-01", "id": "je-1"}
        h = hash_structured_snapshot(fields)
        assert len(h) == 64
        assert h == hash_structured_snapshot(fields)

    def test_task_structured_snapshot(self):
        fields = {"title": "Review auth", "status": "in_progress", "id": "t-1"}
        h = hash_structured_snapshot(fields)
        assert len(h) == 64

    def test_roadmap_structured_snapshot(self):
        fields = {"name": "Q3 Roadmap", "progress": 0.5, "id": "r-1"}
        h = hash_structured_snapshot(fields)
        assert len(h) == 64

    def test_journal_eligible_with_structured_hash(self):
        cand = make_candidate(
            claim_text="We decided on structured logging.",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=_journal_provenance(),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_eligible

    def test_journal_wrong_hash_kind_rejected(self):
        cand = make_candidate(
            claim_text="We decided on structured logging.",
            assertion_type=AssertionType.CANONICAL_FACT,
            target_kind=TargetKind.MEMORY,
            provenance=_journal_provenance(source_hash_kind=SourceHashKind.SHA256_BYTES),
            scope=_mapped_scope(),
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.SOURCE_HASH_MISSING


# ---------------------------------------------------------------------------
# F. Session evidence
# ---------------------------------------------------------------------------


class TestSessionEvidence:
    def test_durable_session_accepted(self):
        cand = make_candidate(
            claim_text="User confirmed a preference in this session.",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=_session_provenance(),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_eligible

    def test_volatile_session_id_rejected(self):
        cand = make_candidate(
            claim_text="Inference from session",
            assertion_type=AssertionType.MODEL_INFERENCE,
            target_kind=TargetKind.MEMORY,
            provenance=_session_provenance(session_id="volatile-runtime-abc"),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.VOLATILE_SESSION_ID

    def test_missing_session_id_rejected(self):
        cand = make_candidate(
            claim_text="Session evidence",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=_session_provenance(session_id=""),
            scope=_mapped_scope(),
            user_confirmed=True,
        )
        result = evaluate_eligibility(cand)
        assert result.is_rejected
        assert result.rejection_code == RejectionCode.MISSING_PROVENANCE


# ---------------------------------------------------------------------------
# G. Duplicate identity
# ---------------------------------------------------------------------------


class TestDuplicateIdentity:
    def test_same_candidate_same_identity(self):
        c1 = _eligible_candidate()
        c2 = _eligible_candidate()
        assert c1.candidate_identity == c2.candidate_identity

    def test_different_claim_different_identity(self):
        c1 = _eligible_candidate(claim_text="Use JWT.")
        c2 = _eligible_candidate(claim_text="Use OAuth2.")
        assert c1.candidate_identity != c2.candidate_identity

    def test_different_workspace_different_identity(self):
        c1 = _eligible_candidate()
        c2 = _eligible_candidate(
            provenance=_adr_provenance(workspace_id="other-ws"),
            scope=_mapped_scope(workspace_id="other-ws"),
        )
        assert c1.candidate_identity != c2.candidate_identity


# ---------------------------------------------------------------------------
# H. Security — path/secret rejection
# ---------------------------------------------------------------------------


class TestProvenanceSecurity:
    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="Unsafe path|Absolute paths"):
            _adr_provenance(source_relative_path="/etc/passwd")

    def test_hermes_home_path_rejected(self):
        with pytest.raises(ValueError, match="Unsafe path"):
            _adr_provenance(source_relative_path="~/.hermes/config")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Unsafe path"):
            _adr_provenance(source_relative_path="../../etc/passwd")

    def test_home_path_rejected(self):
        with pytest.raises(ValueError, match="Unsafe path"):
            _adr_provenance(source_relative_path="/home/user/secret")

    def test_secret_in_provenance_rejected(self):
        with pytest.raises(ValueError, match="Potential secret"):
            _adr_provenance(source_id="sk-abc123secret")

    def test_api_key_in_provenance_rejected(self):
        with pytest.raises(ValueError, match="Potential secret"):
            _adr_provenance(source_canonical_id="api_key=xyz")

    def test_no_side_effects_during_evaluation(self):
        """Eligibility evaluation must be pure — no mutation of inputs."""
        cand = _eligible_candidate()
        original_hash = cand.claim_hash
        original_text = cand.claim_text
        original_scope = cand.scope.scope_state
        result = evaluate_eligibility(cand)
        assert result.is_eligible
        assert cand.claim_hash == original_hash
        assert cand.claim_text == original_text
        assert cand.scope.scope_state == original_scope


# ---------------------------------------------------------------------------
# I. Eligibility result properties
# ---------------------------------------------------------------------------


class TestEligibilityResult:
    def test_eligible_properties(self):
        result = evaluate_eligibility(_eligible_candidate())
        assert result.is_eligible
        assert not result.is_rejected
        assert not result.is_proposed
        assert not result.is_manual_only
        assert result.rejection_code is None

    def test_rejected_properties(self):
        result = evaluate_eligibility(
            _eligible_candidate(scope=_mapped_scope(scope_state="unresolved"))
        )
        assert result.is_rejected
        assert not result.is_eligible
        assert result.rejection_code == RejectionCode.SCOPE_UNRESOLVED

    def test_proposed_properties(self):
        result = evaluate_eligibility(_eligible_candidate(
            assertion_type=AssertionType.MODEL_INFERENCE,
            user_confirmed=False,
        ))
        assert result.is_proposed
        assert not result.is_eligible
        assert result.rejection_code == RejectionCode.MODEL_INFERENCE_NOT_CONFIRMED
