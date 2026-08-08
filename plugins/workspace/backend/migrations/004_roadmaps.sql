-- =====================================================================
-- Workspace Plugin — Migration 004: Roadmaps & Milestones
-- =====================================================================

CREATE TABLE IF NOT EXISTS roadmaps (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_roadmaps_workspace ON roadmaps(workspace_id);

CREATE TABLE IF NOT EXISTS roadmap_milestones (
    id          TEXT PRIMARY KEY,
    roadmap_id  TEXT NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'planned'
                    CHECK(status IN ('planned','in_progress','blocked','completed')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    target_date TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_milestones_roadmap ON roadmap_milestones(roadmap_id);

-- =====================================================================
-- End of migration 004
-- =====================================================================
