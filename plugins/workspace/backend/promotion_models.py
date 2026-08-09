"""S7.5.2 — Memory promotion contract models.

Strongly-typed contract for proposing a durable memory promotion from a
verified Workspace source.  These models are the **eligibility/provenance
envelope** only — they do NOT persist, do NOT write to ``MEMORY.md``, and
do NOT invoke the Hermes memory tool.  Persistence and the actual memory
write arrive in later S7.5 phases.

Design invariants (approved S7.5 plan):

* Workspace remains authoritative for source scope; Hermes ``MemoryStore``
  remains the authoritative durable memory target.
* This module stores NO claim content and NO raw source content — only
  hashes, identity references, and provenance metadata.
* Absolute paths, ``HERMES_HOME`` paths, credentials, transcripts, and
  prompt payloads are rejected at construction time.
* Provenance fields are bounded; nothing here duplicates Hermes memory
  scanning/redaction (that integration is a later phase).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AssertionType(str, Enum):
    """How the promoted claim was derived.

    Authority decreases down this list:

    * ``canonical_fact`` — derived from an authoritative, valid source
      (e.g. a synced ADR).  May be eligible without per-promotion user
      confirmation when the source remains valid.
    * ``user_confirmed_summary`` — a summary the user explicitly confirmed.
      Requires explicit confirmation semantics.
    * ``user_authored_fact`` — manually authored by the user with explicit
      provenance.  Requires manual provenance.
    * ``model_inference`` — produced by the model.  Must NEVER silently
      become authoritative; always proposal-only unless explicitly
      confirmed.
    """

    CANONICAL_FACT = "canonical_fact"
    USER_CONFIRMED_SUMMARY = "user_confirmed_summary"
    USER_AUTHORED_FACT = "user_authored_fact"
    MODEL_INFERENCE = "model_inference"


class SourceType(str, Enum):
    """What kind of Workspace artifact the promotion is derived from.

    Search results, aggregate context, and ``api_content`` are NOT
    evidence and have no entry here.
    """

    ADR = "adr"
    JOURNAL = "journal"
    TASK = "task"
    ROADMAP = "roadmap"
    SESSION_EVIDENCE = "session_evidence"


class SourceHashKind(str, Enum):
    """How ``source_hash`` was computed.

    * ``sha256_bytes`` — SHA-256 of canonical file bytes (ADR).
    * ``structured_snapshot`` — deterministic SHA-256 of selected
      serialized structured fields (journal/task/roadmap).  The source
      is mutable; a hash change marks the promotion stale.
    """

    SHA256_BYTES = "sha256_bytes"
    STRUCTURED_SNAPSHOT = "structured_snapshot"


class TargetKind(str, Enum):
    """Where the promoted claim will live.

    ``memory`` and ``user`` map to the existing Hermes ``MemoryStore``
    targets (``MEMORY.md`` / ``USER.md``).
    """

    MEMORY = "memory"
    USER = "user"


class EligibilityDecision(str, Enum):
    """Outcome of eligibility evaluation.

    * ``eligible`` — the candidate may be promoted.
    * ``rejected`` — the candidate is rejected for a specific reason.
    * ``manual_only`` — automatic promotion is not allowed; an explicit
      manual promotion with user-authored provenance is required.
    * ``proposed`` — the candidate is a proposal only (e.g. model
      inference); it must be explicitly confirmed before promotion.
    """

    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    MANUAL_ONLY = "manual_only"
    PROPOSED = "proposed"


# ---------------------------------------------------------------------------
# Rejection codes
# ---------------------------------------------------------------------------


class RejectionCode(str, Enum):
    """Machine-readable rejection reason (no vague exception messages)."""

    SCOPE_UNRESOLVED = "scope_unresolved"
    SCOPE_AMBIGUOUS = "scope_ambiguous"
    SCOPE_PARTIAL = "scope_partial"
    SCOPE_UNMAPPED = "scope_unmapped"
    PROJECT_ARCHIVED = "project_archived"
    WORKSPACE_MISMATCH = "workspace_mismatch"
    PROJECT_MISMATCH = "project_mismatch"
    PROFILE_MISMATCH = "profile_mismatch"
    SOURCE_MISSING = "source_missing"
    SOURCE_CONFLICTED = "source_conflicted"
    SOURCE_STALE = "source_stale"
    SOURCE_NOT_CANONICAL = "source_not_canonical"
    SOURCE_HASH_MISSING = "source_hash_missing"
    VOLATILE_SESSION_ID = "volatile_session_id"
    MISSING_PROVENANCE = "missing_provenance"
    UNSAFE_PATH = "unsafe_path"
    SECRET_DETECTED = "secret_detected"
    INVALID_PROVENANCE = "invalid_provenance"
    MODEL_INFERENCE_NOT_CONFIRMED = "model_inference_not_confirmed"
    DUPLICATE_CANDIDATE = "duplicate_candidate"


# ---------------------------------------------------------------------------
# Scope snapshot (read-only — no global fallback)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeSnapshot:
    """A frozen snapshot of the resolved scope at proposal time.

    All fields are identity references (IDs, slugs, profile label) — never
    raw ``HERMES_HOME`` paths, absolute repository paths, or session keys.
    """

    profile_label: str = ""
    workspace_id: str = ""
    workspace_name: str = ""
    project_id: str = ""
    project_slug: str = ""
    scope_state: str = "unresolved"  # mapped | partial | unmapped | ambiguous | unresolved
    match_source: str = "none"


# ---------------------------------------------------------------------------
# Provenance envelope
# ---------------------------------------------------------------------------


# Rejected path/secret patterns (defense-in-depth; Hermes scanner is the
# authoritative egress gate in a later phase).
_REJECTED_PATH_PATTERNS = (
    "/.hermes",
    "/home/",
    "/Users/",
    "/root/",
    "/etc/",
    "/usr/",
    "/var/",
    "/proc/",
    "/sys/",
    "/dev/",
    "/tmp/",
    "/opt/",
    "..",
    "~",
    "\\",
)
_SECRET_PATTERNS = (
    "sk-",
    "ghp_",
    "api_key",
    "apikey",
    "token=",
    "secret=",
    "password=",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
)


@dataclass(frozen=True)
class ProvenanceEnvelope:
    """Source evidence for a proposed promotion.

    Stores ONLY identity references, hashes, and project-relative paths.
    Never stores raw source content, claim content, transcripts,
    credentials, or absolute filesystem paths.
    """

    source_type: SourceType
    source_id: str
    source_canonical_id: str = ""
    source_relative_path: str = ""
    source_hash: str = ""
    source_hash_kind: SourceHashKind = SourceHashKind.SHA256_BYTES
    source_state: str = ""
    workspace_id: str = ""
    project_id: str = ""
    profile_label: str = ""
    session_id: str = ""
    turn_id: str = ""
    tool_call_id: str = ""
    correlation_id: str = ""

    def __post_init__(self):
        if not self.source_id:
            raise ValueError("source_id is required")
        if not self.source_hash:
            raise ValueError("source_hash is required")
        self._validate_paths()
        self._validate_no_secrets()

    def _validate_paths(self) -> None:
        """Reject absolute/traversal/home paths in source_relative_path."""
        p = self.source_relative_path or ""
        low = p.lower()
        for pat in _REJECTED_PATH_PATTERNS:
            if pat in low:
                raise ValueError(
                    f"Unsafe path in provenance: {pat!r} detected in "
                    f"source_relative_path (use project-relative paths only)"
                )
        if p.startswith("/"):
            raise ValueError(
                "Absolute paths are not allowed in source_relative_path"
            )

    def _validate_no_secrets(self) -> None:
        """Reject obvious secret tokens in any provenance string field."""
        for val in (
            self.source_id,
            self.source_canonical_id,
            self.source_relative_path,
            self.source_hash,
            self.session_id,
            self.turn_id,
            self.tool_call_id,
            self.correlation_id,
            self.profile_label,
        ):
            low = (val or "").lower()
            for pat in _SECRET_PATTERNS:
                if pat.lower() in low:
                    raise ValueError(
                        f"Potential secret detected in provenance field: "
                        f"{pat!r}"
                    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_type": self.source_type.value,
            "source_id": self.source_id,
            "source_canonical_id": self.source_canonical_id,
            "source_relative_path": self.source_relative_path,
            "source_hash": self.source_hash,
            "source_hash_kind": self.source_hash_kind.value,
            "source_state": self.source_state,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "profile_label": self.profile_label,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "tool_call_id": self.tool_call_id,
            "correlation_id": self.correlation_id,
        }


# ---------------------------------------------------------------------------
# Promotion candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromotionCandidate:
    """A proposed durable memory claim with provenance.

    The ``claim_text`` exists in memory during proposal evaluation only;
    it is NOT persisted by this contract layer.  The ``claim_hash`` is a
    deterministic SHA-256 of the canonicalized claim.
    """

    provenance: ProvenanceEnvelope
    assertion_type: AssertionType
    target_kind: TargetKind
    claim_text: str  # ephemeral — not persisted by S7.5.2
    claim_hash: str  # deterministic SHA-256 of canonicalized claim
    scope: ScopeSnapshot
    user_confirmed: bool = False

    def __post_init__(self):
        if not self.claim_text.strip():
            raise ValueError("claim_text must not be empty")
        if not self.claim_hash:
            raise ValueError("claim_hash is required")

    @property
    def candidate_identity(self) -> str:
        """Deterministic identity for duplicate detection.

        Two candidates with the same source, claim hash, target, and
        profile are considered duplicates.
        """
        return "|".join((
            self.provenance.profile_label,
            self.provenance.workspace_id,
            self.provenance.source_type.value,
            self.provenance.source_id,
            self.provenance.source_hash,
            self.claim_hash,
            self.target_kind.value,
        ))


# ---------------------------------------------------------------------------
# Eligibility result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of evaluating a :class:`PromotionCandidate`.

    Eligibility is pure: it performs NO side effects (no persistence, no
    memory writes, no audit, no network).  It only inspects the candidate
    and the scope snapshot.
    """

    decision: EligibilityDecision
    candidate: PromotionCandidate
    rejection_code: Optional[RejectionCode] = None
    reason: str = ""

    @property
    def is_eligible(self) -> bool:
        return self.decision == EligibilityDecision.ELIGIBLE

    @property
    def is_rejected(self) -> bool:
        return self.decision == EligibilityDecision.REJECTED

    @property
    def is_proposed(self) -> bool:
        return self.decision == EligibilityDecision.PROPOSED

    @property
    def is_manual_only(self) -> bool:
        return self.decision == EligibilityDecision.MANUAL_ONLY
