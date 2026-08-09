-- =====================================================================
-- Workspace Plugin — Migration 008: Memory Promotion Metadata Ledger
-- =====================================================================
-- Metadata-only ledger tracking the lifecycle and provenance of memory
-- promotion candidates (S7.5.3).
--
-- The ledger records WHAT happened to a promotion candidate and WHY.
-- It is NOT a memory store: the actual durable memory remains owned by
-- Hermes MemoryStore / a proven provider target.  This table stores
-- identity references, hashes, and lifecycle state ONLY.
--
-- NEVER stored here:
--   * full claim text / transcript text / prompts
--   * credentials / secrets
--   * arbitrary raw source content
--   * vector embeddings
--   * raw HERMES_HOME paths or absolute filesystem paths
--
-- Candidate identity is a deterministic hash-derived key (S7.5.2
-- ``PromotionCandidate.candidate_identity``) — never timestamps,
-- insertion order, Python repr(), or random values.  One row per
-- (profile_label, candidate_identity) prevents accidental duplicate
-- active records; a repeated equivalent candidate deterministically
-- returns the existing record.
--
-- SAFETY: additive only.  No existing table is modified.  Forward-only,
-- tracked via the existing ``_migrations`` table.

CREATE TABLE IF NOT EXISTS workspace_memory_promotions (
    promotion_id        TEXT PRIMARY KEY,
    -- Identity boundary (safe profile identifier — NEVER raw HERMES_HOME)
    profile_label       TEXT NOT NULL,
    workspace_id        TEXT NOT NULL,
    project_id          TEXT NOT NULL DEFAULT '',
    -- Source provenance
    source_type         TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    source_canonical_id TEXT NOT NULL DEFAULT '',
    source_relative_path TEXT NOT NULL DEFAULT '',
    source_hash         TEXT NOT NULL,
    source_hash_kind    TEXT NOT NULL,
    source_state        TEXT NOT NULL DEFAULT '',
    -- Claim identity
    assertion_type      TEXT NOT NULL,
    claim_hash          TEXT NOT NULL,
    target_kind         TEXT NOT NULL,
    candidate_identity  TEXT NOT NULL,
    -- Lifecycle
    status              TEXT NOT NULL,
    eligibility_decision TEXT NOT NULL,
    rejection_code      TEXT NOT NULL DEFAULT '',
    failure_code        TEXT NOT NULL DEFAULT '',
    superseded_by       TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    approved_at         TEXT,
    promoted_at         TEXT
);

-- Deterministic dedup: one active record per candidate identity per profile.
CREATE UNIQUE INDEX IF NOT EXISTS idx_promotions_candidate_identity
    ON workspace_memory_promotions(profile_label, candidate_identity);

-- Workspace-scoped lookup + status transitions.
CREATE INDEX IF NOT EXISTS idx_promotions_workspace_status
    ON workspace_memory_promotions(workspace_id, status);

CREATE INDEX IF NOT EXISTS idx_promotions_profile
    ON workspace_memory_promotions(profile_label);

-- =====================================================================
-- End of migration 008
-- =====================================================================
