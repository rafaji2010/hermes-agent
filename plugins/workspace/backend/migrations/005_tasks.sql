-- =====================================================================
-- Workspace Plugin — Migration 005: Tasks & Action Management
-- =====================================================================

CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    repository_id  TEXT REFERENCES repositories(id) ON DELETE SET NULL,
    roadmap_id     TEXT REFERENCES roadmaps(id) ON DELETE SET NULL,
    milestone_id   TEXT REFERENCES roadmap_milestones(id) ON DELETE SET NULL,
    adr_id         TEXT REFERENCES adrs(id) ON DELETE SET NULL,
    journal_id     TEXT REFERENCES journal_entries(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'todo'
                       CHECK(status IN ('todo','in_progress','blocked','review','done','cancelled')),
    priority       TEXT NOT NULL DEFAULT 'medium'
                       CHECK(priority IN ('critical','high','medium','low')),
    estimate_hours REAL,
    actual_hours   REAL,
    due_date       TEXT NOT NULL DEFAULT '',
    completed_at   TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_workspace ON tasks(workspace_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);
CREATE INDEX IF NOT EXISTS idx_tasks_roadmap ON tasks(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_tasks_milestone ON tasks(milestone_id);
CREATE INDEX IF NOT EXISTS idx_tasks_adr ON tasks(adr_id);
CREATE INDEX IF NOT EXISTS idx_tasks_journal ON tasks(journal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_repository ON tasks(repository_id);

CREATE TABLE IF NOT EXISTS task_labels (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    label   TEXT NOT NULL,
    PRIMARY KEY (task_id, label)
);

CREATE INDEX IF NOT EXISTS idx_task_labels_label ON task_labels(label);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id       TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS task_comments (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id);

-- =====================================================================
-- End of migration 005
-- =====================================================================
