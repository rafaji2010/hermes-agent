-- =====================================================================
-- Workspace Plugin — Migration 002: Architecture Decision Records
-- =====================================================================

CREATE TABLE IF NOT EXISTS adrs (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    repository_id   TEXT REFERENCES repositories(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','accepted','rejected','superseded','deprecated')),
    category        TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_adrs_workspace ON adrs(workspace_id);
CREATE INDEX IF NOT EXISTS idx_adrs_slug      ON adrs(slug);
CREATE INDEX IF NOT EXISTS idx_adrs_status    ON adrs(status);

-- Separate table so markdown blobs don't bloat list queries.
CREATE TABLE IF NOT EXISTS adr_content (
    adr_id   TEXT PRIMARY KEY REFERENCES adrs(id) ON DELETE CASCADE,
    markdown TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS adr_tags (
    adr_id TEXT NOT NULL REFERENCES adrs(id) ON DELETE CASCADE,
    tag    TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (adr_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_adr_tags_adr ON adr_tags(adr_id);
CREATE INDEX IF NOT EXISTS idx_adr_tags_tag ON adr_tags(tag);
