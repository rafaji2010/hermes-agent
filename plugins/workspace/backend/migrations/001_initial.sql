-- =====================================================================
-- Workspace Plugin — Migration 001: Initial Schema
-- =====================================================================
-- Creates the infrastructure tables for the workspace storage layer.
-- All other domain tables (roadmaps, sprints, ADRs, journal) belong
-- to later milestones.

-- -------------------------------------------------------------------
-- Migration tracking
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS _migrations (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- -------------------------------------------------------------------
-- Workspaces
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_workspaces_name ON workspaces(name);

-- -------------------------------------------------------------------
-- Repositories
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repositories (
    id              TEXT PRIMARY KEY,
    workspace_id    TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    path            TEXT NOT NULL,
    git_root        TEXT NOT NULL DEFAULT '',
    default_branch  TEXT NOT NULL DEFAULT 'main',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(workspace_id, path)
);

CREATE INDEX IF NOT EXISTS idx_repositories_workspace
    ON repositories(workspace_id);

-- -------------------------------------------------------------------
-- Key-value settings store
-- -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL DEFAULT ''
);

-- =====================================================================
-- End of migration 001
-- =====================================================================
