-- =====================================================================
-- Workspace Plugin — Migration 007: Canonical ADR Reconciliation
-- =====================================================================
-- Evolves the ADR projection to support Git/file-canonical ADRs.
--
-- The canonical authority for ADR CONTENT is the Markdown file inside
-- the resolved Hermes Project repository (default ``docs/adr/``).
-- ``workspace.db`` keeps an INDEX/PROJECTION: metadata + a derived
-- markdown cache (search/API performance) + reconciliation bookkeeping.
--
-- New columns on ``adrs``:
--   * canonical_path  — project-relative file path (e.g. docs/adr/0001-x.md)
--   * content_hash    — SHA-256 of the canonical file bytes (drift detection)
--   * reconcile_state — synced | db_legacy | missing_file | conflict | invalid
--   * source          — workspace_db (DB-only / legacy) | git_file (canonical)
--   * last_indexed    — when the projection was last refreshed from the file
--   * last_error      — machine-readable reason (malformed / duplicate / …)
--
-- SAFETY: existing rows are NOT touched by the migration.  They keep
-- ``reconcile_state = 'db_legacy'`` and ``source = 'workspace_db'`` so
-- every pre-S7.3A ADR stays visible and recoverable until explicitly
-- materialized/reconciled.  No destructive change.
--
-- Forward-only, tracked via the existing ``_migrations`` table.

ALTER TABLE adrs ADD COLUMN canonical_path TEXT;
ALTER TABLE adrs ADD COLUMN content_hash TEXT;
ALTER TABLE adrs ADD COLUMN reconcile_state TEXT NOT NULL DEFAULT 'db_legacy';
ALTER TABLE adrs ADD COLUMN source TEXT NOT NULL DEFAULT 'workspace_db';
ALTER TABLE adrs ADD COLUMN last_indexed TEXT;
ALTER TABLE adrs ADD COLUMN last_error TEXT;

CREATE INDEX IF NOT EXISTS idx_adrs_reconcile_state
    ON adrs(workspace_id, reconcile_state);
CREATE INDEX IF NOT EXISTS idx_adrs_canonical_path
    ON adrs(workspace_id, canonical_path);

-- =====================================================================
-- End of migration 007
-- =====================================================================
