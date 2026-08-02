-- =====================================================================
-- Workspace Plugin — Migration 003: Engineering Journal
-- =====================================================================

CREATE TABLE IF NOT EXISTS journal_entries (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    repository_id   TEXT REFERENCES repositories(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    markdown        TEXT NOT NULL DEFAULT '',
    entry_date      TEXT NOT NULL DEFAULT (date('now')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_journal_workspace ON journal_entries(workspace_id);
CREATE INDEX IF NOT EXISTS idx_journal_date      ON journal_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_journal_repo      ON journal_entries(repository_id);

CREATE TABLE IF NOT EXISTS journal_tags (
    entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (entry_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_journal_tags_entry ON journal_tags(entry_id);
CREATE INDEX IF NOT EXISTS idx_journal_tags_tag   ON journal_tags(tag);
