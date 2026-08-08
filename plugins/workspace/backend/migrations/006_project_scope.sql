-- =====================================================================
-- Workspace Plugin — Migration 006: Hermes Project Scope
-- =====================================================================
-- Adds a soft, nullable mapping from a Workspace to a Hermes Project
-- (``projects.db`` in the active profile).  The mapping is intentionally
-- thin:
--
--   * No foreign key — projects.db lives in a different database and is
--     per-profile; the Workspace plugin never owns Project rows.
--   * Forward-only — existing workspaces stay unmapped; nothing is
--     auto-associated by this migration.
--   * ``hermes_project_id`` stores the Hermes Project's canonical ID
--     (``p_<hex>``); lookup by slug happens on the Hermes side.
--
-- The index only covers mapped workspaces so unmapped rows cost nothing.

ALTER TABLE workspaces ADD COLUMN hermes_project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_workspaces_hermes_project
    ON workspaces(hermes_project_id);

-- =====================================================================
-- End of migration 006
-- =====================================================================
