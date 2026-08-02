# Workspace Plugin — Production Design

> Design document. Read-only. No source code was modified.

## Table of Contents

1. [Overall Architecture](#1-overall-architecture)
2. [Folder Structure](#2-folder-structure)
3. [Database Schema](#3-database-schema)
4. [Frontend Component Hierarchy](#4-frontend-component-hierarchy)
5. [Backend Services](#5-backend-services)
6. [IPC Interfaces](#6-ipc-interfaces)
7. [REST Endpoints](#7-rest-endpoints)
8. [State Management](#8-state-management)
9. [Navigation Integration](#9-navigation-integration)
10. [Integration with Hermes Subsystems](#10-integration-with-hermes-subsystems)
11. [Data Flow Diagrams](#11-data-flow-diagrams)
12. [Phased Implementation Plan](#12-phased-implementation-plan)

---

## 1. Overall Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     WORKSPACE PLUGIN                             │
│                                                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐ │
│  │  Desktop Plugin          │  │  Python Backend Plugin       │ │
│  │  (TypeScript / React)    │  │  (Python)                    │ │
│  │                          │  │                              │ │
│  │  • Contributed routes    │  │  • REST API server           │ │
│  │  • Contributed panes     │  │  • SQLite workspace.db       │ │
│  │  • Statusbar items       │  │  • Context engine provider   │ │
│  │  • Command palette       │  │  • Repo scanner service      │ │
│  │  • Nanostores            │  │  • Kanban bridge             │ │
│  │  • pluginRest() calls    │  │  • ADR file sync             │ │
│  │  • pluginSocket() events │  │  • Journal service           │ │
│  │                          │  │  • Skill registration        │ │
│  └────────┬─────────────────┘  └──────────────┬───────────────┘ │
│           │                                    │                  │
└───────────┼────────────────────────────────────┼──────────────────┘
            │ pluginRest('workspace', ...)        │
            │ pluginSocket('workspace', ...)      │
            │                    REST / WS        │
            ▼                    ▲                ▼
┌───────────┴────────────────────┴────────────────────────────────┐
│                    HERMES DESKTOP (unmodified)                  │
│                                                                 │
│  ┌──────┐  ┌───────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │ IPC  │  │ pluginRest│  │ Cron     │  │ ContributionReg   │ │
│  └──────┘  └───────────┘  └──────────┘  └───────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
            │                            │
            ▼                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    HERMES BACKEND (unmodified)                   │
│                                                                 │
│  /api/plugins/workspace/*  ←── plugin REST routes               │
│  /api/plugins/kanban/*     ←── consumed via bridge              │
│  /api/sessions/*           ←── consumed for context             │
│  ContextEngine pipeline    ←── workspace context provider       │
│  Skill loader              ←── workspace skills                 │
└─────────────────────────────────────────────────────────────────┘
```

### Design Principles

1. **The plugin owns `workspace.db`.** All structured workspace data (roadmaps,
   sprints, ADR indices, journal entries) lives in a dedicated SQLite database
   managed solely by the Python backend plugin. The renderer is a cache.

2. **Kanban tasks are the single source of truth for work items.** Sprints and
   roadmaps are grouping layers over kanban tasks. When a sprint item or roadmap
   milestone references a task, the reference is a kanban task ID — not a
   duplicated copy.

3. **ADRs are git-tracked files first, indexed records second.** The canonical
   ADR lives in `<repo>/docs/adr/NNNN-title.md`. The workspace database indexes
   them for search and browsing but is always reconcilable from the file system.

4. **Journal entries are structured records, not chat sessions.** They have
   their own schema, their own lifecycle, and are stored in the workspace
   database. The agent can create them via a skill, but they are not
   conversation history.

5. **The context engine provider is the injection point.** Workspace context
   (active sprint, open tasks, recent ADRs, journal context) is injected into
   agent turns through a registered context engine provider, never by modifying
   the system prompt directly.

6. **Desktop surface uses standard extension points.** Every UI component
   registers through the ContributionRegistry. No monkey-patching, no iframe,
   no separate window.

---

## 2. Folder Structure

```
~/.hermes/plugins/workspace/
│
├── plugin.yaml                         # Plugin manifest
├── __init__.py                         # register(ctx) entry point
├── pyproject.toml                      # Python dependencies
│
├── backend/                            # Python backend plugin
│   ├── __init__.py                     # Blueprint registration
│   ├── database.py                     # SQLite connection, schema migration
│   ├── models.py                       # SQLAlchemy / dataclass models
│   │
│   ├── api/                            # REST endpoint handlers
│   │   ├── __init__.py                 # Flask blueprint / FastAPI router
│   │   ├── workspace.py                # GET /workspace
│   │   ├── repos.py                    # GET/POST/PATCH /repos
│   │   ├── roadmaps.py                 # CRUD /roadmaps, /roadmaps/<id>/items
│   │   ├── sprints.py                  # CRUD /sprints, /sprints/<id>/items
│   │   ├── adrs.py                     # CRUD /adrs, /adrs/<id>
│   │   ├── journal.py                  # CRUD /journal
│   │   └── context.py                  # GET /context (snapshot for inspection)
│   │
│   ├── services/                       # Business logic
│   │   ├── __init__.py
│   │   ├── workspace_service.py        # Workspace CRUD, aggregation
│   │   ├── repo_scanner.py             # Cross-repo git metadata
│   │   ├── roadmap_service.py          # Roadmap + milestone management
│   │   ├── sprint_service.py           # Sprint management + kanban bridge
│   │   ├── adr_service.py              # ADR indexing, status transitions, file sync
│   │   ├── journal_service.py          # Journal CRUD, tagging, search
│   │   ├── context_provider.py         # Context engine integration
│   │   ├── kanban_bridge.py            # Read/write kanban tasks from workspace
│   │   └── health_scorer.py            # Repo health metrics
│   │
│   ├── migrations/                     # Schema versioning
│   │   ├── 001_initial.sql
│   │   └── migration.py                # Apply/detect migrations
│   │
│   └── tests/                          # Backend tests
│       ├── conftest.py
│       ├── test_workspace_api.py
│       ├── test_sprint_service.py
│       ├── test_adr_service.py
│       ├── test_context_provider.py
│       └── test_kanban_bridge.py
│
├── desktop/                            # Desktop renderer plugin
│   ├── index.ts                        # Entry: registers all contributions
│   │
│   ├── routes/                         # Full-page contributed routes
│   │   ├── workspace-dashboard.tsx     # /workspace
│   │   ├── roadmap-page.tsx            # /workspace/roadmap
│   │   ├── roadmap-detail.tsx          # /workspace/roadmap/:id
│   │   ├── sprints-page.tsx            # /workspace/sprints
│   │   ├── sprint-detail.tsx           # /workspace/sprints/:id
│   │   ├── adr-page.tsx                # /workspace/adrs
│   │   ├── adr-detail.tsx              # /workspace/adrs/:id
│   │   └── journal-page.tsx            # /workspace/journal
│   │
│   ├── panes/                          # Sidebar / rail panes
│   │   ├── workspace-nav.tsx           # Workspace in-app nav sidebar
│   │   └── sprint-rail.tsx             # Sprint detail in right rail
│   │
│   ├── components/                     # Reusable UI components
│   │   ├── repo-card.tsx               # Repository health card
│   │   ├── repo-list.tsx               # Repository list with filters
│   │   ├── health-badge.tsx            # Colored health indicator
│   │   ├── roadmap-timeline.tsx        # Horizontal timeline visualization
│   │   ├── milestone-card.tsx          # Milestone on timeline
│   │   ├── sprint-board.tsx            # Sprint kanban (read-only view of kanban)
│   │   ├── burndown-chart.tsx          # SVG burndown
│   │   ├── velocity-chart.tsx          # SVG velocity over time
│   │   ├── sprint-card.tsx             # Sprint summary card
│   │   ├── sprint-form.tsx             # Create/edit sprint dialog
│   │   ├── adr-list.tsx                # Filterable ADR list
│   │   ├── adr-status-badge.tsx        # Proposed/Accepted/Deprecated
│   │   ├── adr-form.tsx                # ADR create/edit dialog
│   │   ├── journal-calendar.tsx        # Date-picker calendar
│   │   ├── journal-entry-card.tsx      # Single journal entry
│   │   ├── journal-editor.tsx          # Full editor with tags
│   │   ├── journal-template-picker.tsx # Template selector
│   │   ├── tag-filter.tsx              # Tag-based filter chips
│   │   ├── workspace-activity-feed.tsx # Recent events across repos
│   │   └── quick-actions.tsx           # Floating action buttons
│   │
│   ├── stores/                         # Nanostores atoms
│   │   ├── workspace.ts               # Workspace config, repos
│   │   ├── roadmaps.ts                # Roadmap list, items
│   │   ├── sprints.ts                 # Sprint list, items, active sprint
│   │   ├── adrs.ts                    # ADR list, selected ADR
│   │   └── journal.ts                 # Journal entries, tags, filters
│   │
│   ├── lib/                            # Pure utilities
│   │   ├── api.ts                      # pluginRest() wrappers for all endpoints
│   │   ├── socket.ts                   # pluginSocket() event handler
│   │   ├── types.ts                    # TypeScript types matching backend models
│   │   ├── query-keys.ts              # @tanstack/react-query keys
│   │   └── formatting.ts              # Date, status, health formatting
│   │
│   ├── skills/                         # Bundled workspace skills
│   │   ├── adr/
│   │   │   └── SKILL.md               # ADR creation, review, status transitions
│   │   ├── journal/
│   │   │   └── SKILL.md               # Engineering journaling
│   │   ├── sprint-planning/
│   │   │   └── SKILL.md               # Sprint planning, standup, retro
│   │   ├── roadmap/
│   │   │   └── SKILL.md               # Roadmap planning, milestone tracking
│   │   └── workspace-context/
│   │       └── SKILL.md               # How the agent uses workspace context
│   │
│   └── tests/                          # Desktop-side tests
│       ├── setup.ts
│       ├── stores.test.ts
│       ├── api.test.ts
│       └── components.test.tsx
│
└── workspace.db                        # SQLite database (auto-created on first run)
```

### Plugin Manifest

**`plugin.yaml`**:
```yaml
name: workspace
version: "1.0.0"
description: "Workspace intelligence layer for multi-project engineering."
author: "Workspace Layer Team"
kind: plugin
entry: backend
```

The `entry: backend` directive tells the plugin manager to import `__init__.py`
and call `register(ctx)`. The desktop half is discovered separately by
`discoverBundledPlugins()` or `discoverRuntimePlugins()`.

---

## 3. Database Schema

All tables live in `workspace.db` (SQLite, WAL mode). The database sits at
`<hermes_home>/workspace.db` — at the Hermes home level, not inside a profile,
so all profiles share workspace data.

### Entity-Relationship Summary

```
workspace ──< workspace_repos >── repo
workspace ──< roadmaps
roadmap ──< roadmap_items
roadmap_item ── references ──> kanban task (by id, in kanban.db)
workspace ──< sprints
sprint ──< sprint_items
sprint_item ── references ──> kanban task (by id, in kanban.db)
repo ──< adrs
workspace ──< journal_entries
journal_entry ──< journal_tags
workspace ──< context_snapshots
```

### Tables

#### `workspaces`

```sql
CREATE TABLE workspaces (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    root_path   TEXT DEFAULT '',          -- optional root directory for repo scanning
    default_board TEXT DEFAULT 'default', -- which kanban board to associate
    config      TEXT DEFAULT '{}',        -- JSON: default skills, model prefs, etc.
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at  INTEGER NOT NULL DEFAULT (unixepoch())
);
```

#### `workspace_repos`

```sql
CREATE TABLE workspace_repos (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    repo_path    TEXT NOT NULL,            -- absolute path to the git repo
    repo_name    TEXT NOT NULL,            -- derived from path
    default_branch TEXT DEFAULT 'main',
    health_score INTEGER DEFAULT 0,        -- 0-100 composite score
    last_scanned_at INTEGER,
    added_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (workspace_id, repo_path)
);

CREATE INDEX idx_workspace_repos_workspace ON workspace_repos(workspace_id);
```

#### `repo_metadata` (cached scan results)

```sql
CREATE TABLE repo_metadata (
    repo_path        TEXT PRIMARY KEY,
    last_commit_hash TEXT,
    last_commit_date INTEGER,
    last_commit_author TEXT,
    open_branch_count INTEGER DEFAULT 0,
    uncommitted_files INTEGER DEFAULT 0,
    behind_remote    INTEGER DEFAULT 0,    -- commits behind default upstream
    ahead_remote     INTEGER DEFAULT 0,    -- commits ahead of default upstream
    ci_status        TEXT DEFAULT '',       -- last known CI status
    dependency_count INTEGER DEFAULT 0,
    outdated_deps    INTEGER DEFAULT 0,
    test_coverage    REAL DEFAULT 0.0,     -- 0.0-1.0 if detectable
    open_pr_count    INTEGER DEFAULT 0,
    scanned_at       INTEGER NOT NULL DEFAULT (unixepoch())
);
```

#### `roadmaps`

```sql
CREATE TABLE roadmaps (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    start_date  TEXT,                      -- ISO 8601 date
    end_date    TEXT,                      -- ISO 8601 date (or NULL for ongoing)
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('draft','active','completed','archived')),
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX idx_roadmaps_workspace ON roadmaps(workspace_id);
```

#### `roadmap_items`

```sql
CREATE TABLE roadmap_items (
    id           TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    roadmap_id   TEXT NOT NULL REFERENCES roadmaps(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    target_date  TEXT,                     -- ISO 8601 target milestone date
    status       TEXT NOT NULL DEFAULT 'planned'
                 CHECK (status IN ('planned','in_progress','completed','cancelled')),
    kanban_task_id TEXT DEFAULT NULL,      -- reference to kanban.db task
    sprint_id    TEXT DEFAULT NULL REFERENCES sprints(id) ON DELETE SET NULL,
    priority     INTEGER DEFAULT 0,
    sort_order   INTEGER DEFAULT 0,
    created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at   INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX idx_roadmap_items_roadmap ON roadmap_items(roadmap_id);
CREATE INDEX idx_roadmap_items_kanban  ON roadmap_items(kanban_task_id);
CREATE INDEX idx_roadmap_items_sprint  ON roadmap_items(sprint_id);
```

#### `sprints`

```sql
CREATE TABLE sprints (
    id           TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,            -- e.g. "Sprint 12"
    goal         TEXT DEFAULT '',
    start_date   TEXT NOT NULL,            -- ISO 8601
    end_date     TEXT NOT NULL,            -- ISO 8601
    status       TEXT NOT NULL DEFAULT 'planned'
                 CHECK (status IN ('planned','active','completed','cancelled')),
    board        TEXT DEFAULT 'default',   -- which kanban board houses tasks
    tenant       TEXT DEFAULT NULL,        -- optional kanban tenant filter
    created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at   INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX idx_sprints_workspace ON sprints(workspace_id);
CREATE INDEX idx_sprints_status    ON sprints(status);
```

#### `sprint_items`

```sql
CREATE TABLE sprint_items (
    sprint_id    TEXT NOT NULL REFERENCES sprints(id) ON DELETE CASCADE,
    kanban_task_id TEXT NOT NULL,          -- reference to kanban.db task
    story_points INTEGER DEFAULT 0,
    added_at     INTEGER NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (sprint_id, kanban_task_id)
);

CREATE INDEX idx_sprint_items_kanban ON sprint_items(kanban_task_id);
```

#### `adrs`

```sql
CREATE TABLE adrs (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    repo_path   TEXT NOT NULL,             -- which repo this ADR belongs to
    adr_number  INTEGER NOT NULL,          -- NNNN in docs/adr/NNNN-title.md
    title       TEXT NOT NULL,
    file_path   TEXT NOT NULL,             -- absolute path to the markdown file
    status      TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','accepted','deprecated','superseded')),
    superseded_by TEXT DEFAULT NULL,       -- adr_number of replacement
    tags        TEXT DEFAULT '[]',         -- JSON array of tag strings
    file_hash   TEXT DEFAULT '',           -- SHA-256 of file content, for sync
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    UNIQUE(repo_path, adr_number)
);

CREATE INDEX idx_adrs_repo   ON adrs(repo_path);
CREATE INDEX idx_adrs_status ON adrs(status);
```

#### `journal_entries`

```sql
CREATE TABLE journal_entries (
    id          TEXT PRIMARY KEY DEFAULT (hex(randomblob(8))),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    entry_date  TEXT NOT NULL,             -- ISO 8601 date (the day this is about)
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,             -- markdown
    mood        TEXT DEFAULT '',           -- optional: focused, blocked, productive, etc.
    session_id  TEXT DEFAULT NULL,         -- optional link to a Hermes session
    repo_path   TEXT DEFAULT NULL,         -- optional scope to a specific repo
    created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    updated_at  INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX idx_journal_workspace ON journal_entries(workspace_id);
CREATE INDEX idx_journal_date      ON journal_entries(entry_date);
CREATE INDEX idx_journal_session   ON journal_entries(session_id);
```

#### `journal_tags`

```sql
CREATE TABLE journal_tags (
    entry_id TEXT NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,               -- lowercase, trimmed
    PRIMARY KEY (entry_id, tag)
);

CREATE INDEX idx_journal_tags_tag ON journal_tags(tag);
```

#### `context_snapshots`

Captures what workspace context was injected into an agent turn, for audit and debugging.

```sql
CREATE TABLE context_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    snapshot    TEXT NOT NULL,             -- JSON: what was injected
    token_count INTEGER DEFAULT 0,
    captured_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX idx_context_snapshots_session ON context_snapshots(session_id);
```

#### Migration tracking

```sql
CREATE TABLE IF NOT EXISTS _migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  INTEGER NOT NULL DEFAULT (unixepoch()),
    description TEXT
);
```

---

## 4. Frontend Component Hierarchy

```
App Shell (Hermes Core)
│
├── Sidebar (Hermes Core)
│   └── WorkspaceNav (contributed sidebar.nav)
│       ├── WorkspaceDashboard link
│       ├── Roadmaps link
│       ├── Sprints link
│       ├── ADRs link
│       └── Journal link
│
├── Workspace Pane (contributed route — renders when at /workspace/*)
│   │
│   ├── /workspace → WorkspaceDashboard
│   │   ├── RepoList
│   │   │   └── RepoCard[]
│   │   │       ├── HealthBadge
│   │   │       ├── last commit, branch info
│   │   │       └── open tasks count (from kanban)
│   │   ├── ActiveSprintCard (if a sprint is active)
│   │   ├── WorkspaceActivityFeed
│   │   │   └── recent commits, ADR changes, sprint transitions
│   │   └── QuickActions
│   │       ├── "New ADR"
│   │       ├── "Journal Entry"
│   │       └── "Start Sprint"
│   │
│   ├── /workspace/roadmap → RoadmapPage
│   │   ├── RoadmapTimeline
│   │   │   ├── MilestoneCard[]
│   │   │   │   ├── title, date, status
│   │   │   │   └── linked kanban tasks count
│   │   │   └── "Now" line indicator
│   │   └── CreateMilestoneDialog (overlay)
│   │
│   ├── /workspace/roadmap/:id → RoadmapDetail
│   │   ├── RoadmapHeader (title, dates, status controls)
│   │   ├── RoadmapItemList
│   │   │   └── RoadmapItemRow[]
│   │   │       ├── drag-to-reorder
│   │   │       ├── kanban task link
│   │   │       └── sprint assignment
│   │   └── RelatedSprints list
│   │
│   ├── /workspace/sprints → SprintsPage
│   │   ├── SprintList
│   │   │   └── SprintCard[]
│   │   │       ├── name, dates, status
│   │   │       ├── item count / story points
│   │   │       └── progress bar
│   │   ├── SprintForm (create/edit dialog)
│   │   └── VelocityChart (across completed sprints)
│   │
│   ├── /workspace/sprints/:id → SprintDetail
│   │   ├── SprintHeader (name, goal, dates, status controls)
│   │   ├── BurndownChart (SVG)
│   │   ├── SprintBoard (read-only kanban view)
│   │   │   └── SprintTaskCard[] (simplified kanban cards)
│   │   ├── "Add to Sprint" — opens kanban task picker
│   │   └── SprintStats (completed, remaining, velocity)
│   │
│   ├── /workspace/adrs → ADRPage
│   │   ├── ADRList
│   │   │   └── ADRRow[]
│   │   │       ├── number, title
│   │   │       ├── ADRStatusBadge
│   │   │       └── repo name, date
│   │   ├── SearchBar (by title, tag)
│   │   ├── TagFilter (filter chips)
│   │   └── "New ADR" button → ADRForm
│   │
│   ├── /workspace/adrs/:id → ADRDetail
│   │   ├── ADRHeader (number, title, status, repo)
│   │   ├── MarkdownPreview (reuses Hermes preview pane)
│   │   ├── ADRStatusControls (accept, deprecate, supersede)
│   │   └── LinkedDecisions list (superseded/superseded_by)
│   │
│   └── /workspace/journal → JournalPage
│       ├── JournalCalendar
│       ├── JournalEntryList (for selected date/week)
│       │   └── JournalEntryCard[]
│       │       ├── title, mood, tags
│       │       └── snippet of body
│       ├── JournalEditor (date, title, body, mood, tags, repo scope)
│       │   └── JournalTemplatePicker
│       └── TagFilter
│
├── Statusbar (Hermes Core, contributed items)
│   ├── WorkspaceHealthIndicator
│   │   └── aggregate health + click → navigate to workspace
│   └── ActiveSprintIndicator
│       └── "Sprint 12 · 4d remaining · 7/12 tasks"
│
└── Command Palette (Hermes Core, contributed commands)
    ├── "Workspace: New ADR"
    ├── "Workspace: Journal Entry"
    ├── "Workspace: Start Sprint"
    ├── "Workspace: Scan Repos"
    └── "Workspace: Open Dashboard"
```

---

## 5. Backend Services

### 5.1 WorkspaceService

**File:** `backend/services/workspace_service.py`

| Method | Description |
|--------|-------------|
| `get_workspace(ws_id)` | Return workspace with repo count, sprint count |
| `create_workspace(name, root_path)` | Create workspace, trigger initial repo scan |
| `update_workspace(ws_id, fields)` | Update name, description, root_path, config |
| `list_workspaces()` | All workspaces for the current profile |
| `get_aggregate_health(ws_id)` | Compute overall health from all repo scores |
| `get_recent_activity(ws_id, limit)` | Union of recent commits, ADR changes, sprint events |

### 5.2 RepoScanner

**File:** `backend/services/repo_scanner.py`

| Method | Description |
|--------|-------------|
| `add_repo(workspace_id, path)` | Validate path is a git repo, add to workspace_repos |
| `scan_repo(repo_path)` | Run `git log -1`, `git status --porcelain`, `git branch -r`, check CI config, count deps. Populate repo_metadata. |
| `scan_all_repos(workspace_id)` | Scan every repo in the workspace |
| `compute_health(repo_path)` | Score 0-100 from: uncommitted files, behind remote, CI status, outdated deps, test coverage |
| `remove_repo(workspace_id, repo_path)` | Remove from workspace (does not delete the directory) |

Dependencies: `git` on PATH. Runs `subprocess.run` with timeouts. Caches results
in `repo_metadata` with `last_scanned_at` for freshness. Scheduled via cron.

### 5.3 RoadmapService

**File:** `backend/services/roadmap_service.py`

| Method | Description |
|--------|-------------|
| `create_roadmap(workspace_id, title, start, end)` | Create roadmap |
| `get_roadmap(roadmap_id)` | Roadmap with all items, ordered |
| `list_roadmaps(workspace_id)` | All roadmaps for workspace |
| `add_item(roadmap_id, title, target_date, kanban_task_id?)` | Add milestone |
| `update_item(item_id, fields)` | Update title, date, status, kanban ref |
| `reorder_items(roadmap_id, item_ids[])` | Set sort_order from array order |
| `delete_item(item_id)` | Remove milestone from roadmap |
| `link_to_kanban(item_id, kanban_task_id)` | Reference a kanban task (validates existence via KanbanBridge) |
| `link_to_sprint(item_id, sprint_id)` | Assign roadmap item to a sprint |
| `complete_roadmap(roadmap_id)` | Set status = completed |
| `archive_roadmap(roadmap_id)` | Set status = archived |

### 5.4 SprintService

**File:** `backend/services/sprint_service.py`

| Method | Description |
|--------|-------------|
| `create_sprint(workspace_id, name, goal, start, end)` | Create sprint |
| `get_sprint(sprint_id)` | Sprint with items, counts, burndown data |
| `list_sprints(workspace_id, status?)` | All sprints, optionally filtered |
| `add_item(sprint_id, kanban_task_id, story_points?)` | Link a kanban task to sprint |
| `remove_item(sprint_id, kanban_task_id)` | Unlink from sprint (task stays in kanban) |
| `update_story_points(sprint_id, kanban_task_id, points)` | Set story points |
| `start_sprint(sprint_id)` | Transition planned → active, record start |
| `complete_sprint(sprint_id)` | Transition active → completed, calculate velocity, move incomplete items |
| `cancel_sprint(sprint_id)` | Transition to cancelled, unlink all items |
| `get_burndown(sprint_id)` | Return [(date, ideal, actual)] series |
| `get_velocity(workspace_id, last_n)` | Average completed story points per sprint |
| `reconcile_from_kanban(sprint_id)` | Pull status updates from linked kanban tasks |

The burndown is computed by checking the completion date of each linked kanban
task (via `task_runs.ended_at` / `tasks.completed_at` in kanban.db) and
accumulating completed story points by date against the ideal line.

### 5.5 ADRService

**File:** `backend/services/adr_service.py`

| Method | Description |
|--------|-------------|
| `index_adr(repo_path, adr_number, title, file_path, status)` | Insert or update an ADR record |
| `scan_repo_adrs(repo_path)` | Scan `<repo>/docs/adr/*.md`, parse frontmatter, reconcile with DB |
| `sync_adr_from_file(repo_path, file_path)` | Read file, hash, update DB record if changed |
| `list_adrs(repo_path?, status?, tag?)` | Filtered ADR list |
| `get_adr(adr_id)` | Single ADR record + full file content (reads from disk) |
| `create_adr(repo_path, title, body, status, tags)` | Determine next ADR number, write markdown file, insert DB record |
| `update_adr_status(adr_id, new_status, superseded_by?)` | Update DB + rewrite file frontmatter |
| `update_adr_body(adr_id, body)` | Update file content + re-hash |
| `search_adrs(query)` | FTS5 search across title + body |

**ADR file format** (conventional):
```markdown
# ADR-0004: Use SQLite for workspace data

- **Status:** accepted
- **Date:** 2026-07-15
- **Tags:** storage, architecture

## Context
...

## Decision
...

## Consequences
...
```

The ADRService parses the status from the frontmatter (`**Status:** accepted`).
The canonical status values are: `proposed`, `accepted`, `deprecated`, `superseded`.

### 5.6 JournalService

**File:** `backend/services/journal_service.py`

| Method | Description |
|--------|-------------|
| `create_entry(workspace_id, date, title, body, mood?, session_id?, repo_path?, tags[])` | Create entry |
| `get_entry(entry_id)` | Single entry with tags |
| `update_entry(entry_id, fields)` | Update title, body, mood, tags |
| `delete_entry(entry_id)` | Hard delete |
| `list_entries(workspace_id, from_date?, to_date?, tag?, repo_path?, limit)` | Filtered list |
| `get_entries_for_date(workspace_id, date)` | All entries on a given day |
| `get_calendar_dates(workspace_id, year, month)` | Which days have entries (for calendar highlights) |
| `search_entries(workspace_id, query)` | FTS5 across title + body |
| `get_tag_cloud(workspace_id)` | [(tag, count)] sorted by frequency |
| `get_entry_templates()` | Return built-in templates (standup, retro, decision, freeform) |

**Built-in journal templates** (stored as JSON in code, not in DB):
- **Standup:** yesterday, today, blockers
- **Retrospective:** went well, went wrong, action items
- **Decision:** context, options, decision, rationale
- **Freeform:** blank markdown editor

### 5.7 ContextProvider

**File:** `backend/services/context_provider.py`

Registers as a context engine component that injects workspace metadata into
every agent turn.

```python
class WorkspaceContextProvider:
    def get_context(self, session_id, cwd) -> str:
        """
        Returns a markdown block to inject into the agent's context.

        Assembly logic:
        1. Determine which repo we're in from cwd
        2. Look up active sprint for that repo's workspace
        3. Look up assigned/open kanban tasks for the agent's profile
        4. Look up recent ADRs for the repo (last 5 accepted)
        5. Look up recent journal entries for the repo (last 3 entries)
        6. Look up the repo's health score
        7. Format as a structured markdown block

        Budget: capped at ~1500 tokens. Truncates oldest/least-relevant first.
        """
```

**Injected context format:**
```markdown
## Workspace Context

**Repository:** hermes-agent
**Active Sprint:** Sprint 12 (2026-07-14 → 2026-07-28, 4d remaining)
**Repo Health:** 72/100 (3 uncommitted files, 12 commits behind main)

### Your Open Tasks
| Task | Priority | Status | Sprint |
|------|----------|--------|--------|
| t_a1b2c3d4 — Fix session resume 404 | high | running | Sprint 12 |
| t_e5f6g7h8 — Add test coverage for gateway | medium | ready | Sprint 12 |

### Recent Decisions (ADRs)
- ADR-0004: Use SQLite for workspace data (accepted, 2026-07-15)
- ADR-0003: Adopt nanostores over Redux (accepted, 2026-07-10)

### Recent Journal
- 2026-07-15: "Sprint planning complete — 12 story points committed"
- 2026-07-14: "Investigated session 404 — race condition in resume path"
```

The context provider respects a **token budget** (`workspace.context_max_tokens`
in `config.yaml`, default 1500). It truncates sections in order: journal →
ADRs → health → tasks (tasks are always included if they fit).

### 5.8 KanbanBridge

**File:** `backend/services/kanban_bridge.py`

Thin wrapper that reads/writes the kanban database. Uses the same `hermes_cli.kanban_db`
module that the CLI and dashboard use — reuses, never reimplements.

| Method | Description |
|--------|-------------|
| `get_board(board?)` | Fetch kanban board via `kanban_db.get_board()` |
| `get_task(task_id)` | Fetch single task with full detail |
| `create_task(title, body, assignee?, priority?, tenant?)` | Create through `kanban_db.create_task()` |
| `update_task_status(task_id, status)` | Transition via `kanban_db.complete_task()` / `kanban_db.block_task()` |
| `list_tasks(board?, status?, assignee?, tenant?)` | Filtered task query |
| `list_tasks_for_sprint(sprint_id)` | All kanban tasks linked to a sprint |
| `link_tasks(parent_id, child_id)` | Via `kanban_db.link_tasks()` |
| `add_comment(task_id, body, author)` | Via `kanban_db.add_comment()` |
| `get_task_events(task_id)` | Event log for a task |
| `list_assignees()` | All known profiles who have tasks |

The bridge validates that referenced kanban tasks actually exist before
creating sprint/roadmap links. If a referenced task is deleted from kanban,
the sprint_item or roadmap_item reference becomes dangling — the UI shows it
as "deleted task" with an option to unlink.

### 5.9 HealthScorer

**File:** `backend/services/health_scorer.py`

| Metric | Weight | Source |
|--------|--------|--------|
| Uncommitted files | 20% | `git status --porcelain` count |
| Behind remote | 20% | `git rev-list --count HEAD..@{u}` |
| CI status | 20% | Detect CI config, run `gh run list --limit 1` |
| Outdated dependencies | 15% | Count deps with newer versions available |
| Stale branches | 10% | Merged branches not deleted |
| Open PRs age | 10% | Average age of open PRs |
| Test coverage | 5% | Parse from coverage report if available |

Score = weighted sum. Each metric is 0-100. Final score rounded to integer.

---

## 6. IPC Interfaces

The Workspace Plugin does **not** require new Electron IPC handlers. All
communication between the desktop and backend goes through existing channels.

### 6.1 REST (via pluginRest)

All REST calls use the existing `pluginRest()` function from `src/hermes.ts`,
which proxies through `hermesDesktop.api()` → Electron main → backend HTTP.

**No new IPC channel needed.** The `pluginRest` function already handles:
- Namespace scoping (`/api/plugins/workspace/...`)
- Profile-aware routing
- Token/auth header injection
- Timeout handling

### 6.2 WebSocket (via pluginSocket)

Live updates (sprint status changes, task completions, repo scan completion)
use `pluginSocket()` which opens a WebSocket to
`/api/plugins/workspace/events`.

**Event types emitted by the backend:**

| Event | Payload | When |
|-------|---------|------|
| `sprint.updated` | `{ sprint_id, status, ... }` | Sprint created, started, completed |
| `task.linked` | `{ sprint_id, kanban_task_id }` | Task added to/removed from sprint |
| `task.completed` | `{ kanban_task_id, sprint_id? }` | Kanban task completed (reconciles burndown) |
| `repo.scanned` | `{ repo_path, health_score }` | Repo scan finished |
| `adr.created` | `{ adr_id, repo_path, title, status }` | New ADR indexed |
| `adr.updated` | `{ adr_id, status }` | ADR status changed |
| `journal.created` | `{ entry_id, date }` | New journal entry |

### 6.3 Existing IPC Methods Used

The desktop half of the workspace plugin uses these existing IPC methods (no
new ones needed):

| IPC Method | Used For |
|------------|----------|
| `hermesDesktop.api()` | All REST calls (proxied through pluginRest) |
| `hermesDesktop.readDir()` | Browsing repo directories |
| `hermesDesktop.readFileText()` | Reading ADR content |
| `hermesDesktop.gitRoot()` | Detecting git repos when adding to workspace |
| `hermesDesktop.git.repoStatus()` | Quick repo status in dashboard |
| `hermesDesktop.revealPath()` | "Show in Finder/Explorer" for repos |
| `hermesDesktop.openExternal()` | Opening GitHub PRs, CI dashboards |

---

## 7. REST Endpoints

All endpoints are mounted at `/api/plugins/workspace/*` by the plugin system.

### 7.1 Workspace

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace` | List all workspaces | — | `Workspace[]` |
| `POST` | `/workspace` | Create workspace | `{ name, root_path?, description? }` | `Workspace` |
| `GET` | `/workspace/<id>` | Get workspace with stats | — | `WorkspaceDetail` |
| `PATCH` | `/workspace/<id>` | Update workspace | `{ name?, description?, root_path? }` | `Workspace` |
| `DELETE` | `/workspace/<id>` | Delete workspace | — | `{ ok: true }` |
| `GET` | `/workspace/<id>/health` | Aggregate health | — | `{ score, repos: [{path, score}] }` |
| `GET` | `/workspace/<id>/activity` | Recent activity feed | `?limit=20` | `ActivityEvent[]` |

### 7.2 Repos

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/repos` | List repos in workspace | — | `Repo[]` |
| `POST` | `/workspace/<id>/repos` | Add repo to workspace | `{ path }` | `Repo` |
| `DELETE` | `/workspace/<id>/repos` | Remove repo | `{ path }` | `{ ok: true }` |
| `POST` | `/workspace/<id>/repos/scan` | Scan all repos | — | `{ scanned: int }` |
| `POST` | `/workspace/<id>/repos/<path>/scan` | Scan single repo | — | `RepoMetadata` |
| `GET` | `/workspace/<id>/repos/<path>/meta` | Cached metadata | — | `RepoMetadata` |

### 7.3 Roadmaps

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/roadmaps` | List roadmaps | `?status=active` | `Roadmap[]` |
| `POST` | `/workspace/<id>/roadmaps` | Create roadmap | `{ title, description?, start_date?, end_date? }` | `Roadmap` |
| `GET` | `/roadmaps/<id>` | Get roadmap with items | — | `RoadmapDetail` |
| `PATCH` | `/roadmaps/<id>` | Update roadmap | `{ title?, status?, start_date?, end_date? }` | `Roadmap` |
| `DELETE` | `/roadmaps/<id>` | Delete roadmap | — | `{ ok: true }` |
| `POST` | `/roadmaps/<id>/items` | Add milestone | `{ title, target_date?, kanban_task_id?, sprint_id? }` | `RoadmapItem` |
| `PATCH` | `/roadmaps/<id>/items/<item_id>` | Update item | `{ title?, target_date?, status?, kanban_task_id?, sprint_id? }` | `RoadmapItem` |
| `DELETE` | `/roadmaps/<id>/items/<item_id>` | Remove item | — | `{ ok: true }` |
| `PUT` | `/roadmaps/<id>/items/order` | Reorder items | `{ item_ids: string[] }` | `{ ok: true }` |

### 7.4 Sprints

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/sprints` | List sprints | `?status=active` | `Sprint[]` |
| `POST` | `/workspace/<id>/sprints` | Create sprint | `{ name, goal?, start_date, end_date, board?, tenant? }` | `Sprint` |
| `GET` | `/sprints/<id>` | Get sprint detail | — | `SprintDetail` |
| `PATCH` | `/sprints/<id>` | Update sprint | `{ name?, goal?, start_date?, end_date?, status? }` | `Sprint` |
| `DELETE` | `/sprints/<id>` | Delete sprint | — | `{ ok: true }` |
| `POST` | `/sprints/<id>/items` | Add task to sprint | `{ kanban_task_id, story_points? }` | `SprintItem` |
| `DELETE` | `/sprints/<id>/items/<task_id>` | Remove task | — | `{ ok: true }` |
| `PATCH` | `/sprints/<id>/items/<task_id>` | Update story points | `{ story_points }` | `SprintItem` |
| `GET` | `/sprints/<id>/burndown` | Burndown data | — | `BurndownPoint[]` |
| `GET` | `/sprints/<id>/velocity` | Velocity data | — | `{ average, history: [{sprint, points}] }` |

### 7.5 ADRs

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/adrs` | List ADRs | `?repo_path=&status=&tag=` | `ADR[]` |
| `POST` | `/workspace/<id>/adrs` | Create ADR | `{ repo_path, title, body, status?, tags? }` | `ADR` |
| `GET` | `/adrs/<id>` | Get ADR with full content | — | `ADRDetail` |
| `PATCH` | `/adrs/<id>` | Update ADR | `{ status?, superseded_by?, body? }` | `ADR` |
| `GET` | `/adrs/<id>/content` | Get raw markdown content | — | `{ content: string }` |
| `POST` | `/workspace/<id>/adrs/scan` | Re-scan repo for ADRs | `{ repo_path }` | `{ indexed: int }` |
| `GET` | `/workspace/<id>/adrs/search` | Search ADRs | `?q=query` | `ADR[]` |

### 7.6 Journal

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/journal` | List entries | `?from=&to=&tag=&repo_path=&limit=50` | `JournalEntry[]` |
| `POST` | `/workspace/<id>/journal` | Create entry | `{ entry_date, title, body, mood?, session_id?, repo_path?, tags? }` | `JournalEntry` |
| `GET` | `/journal/<id>` | Get entry | — | `JournalEntry` |
| `PATCH` | `/journal/<id>` | Update entry | `{ title?, body?, mood?, tags? }` | `JournalEntry` |
| `DELETE` | `/journal/<id>` | Delete entry | — | `{ ok: true }` |
| `GET` | `/workspace/<id>/journal/calendar` | Calendar dates | `?year=&month=` | `string[]` (dates) |
| `GET` | `/workspace/<id>/journal/tags` | Tag cloud | — | `{tag: count}[]` |
| `GET` | `/workspace/<id>/journal/search` | Search entries | `?q=query` | `JournalEntry[]` |
| `GET` | `/journal/templates` | List templates | — | `Template[]` |

### 7.7 Context

| Method | Path | Description | Request Body | Response |
|--------|------|-------------|-------------|----------|
| `GET` | `/workspace/<id>/context` | Preview context that would be injected | `?session_id=&cwd=` | `{ text, token_count }` |
| `GET` | `/context/snapshots` | Recent context snapshots | `?session_id=&limit=10` | `ContextSnapshot[]` |

### 7.8 WebSocket Events

| Event | Direction | Payload |
|-------|-----------|---------|
| `sprint.updated` | Server → Client | `{ sprint_id, workspace_id, status, name }` |
| `sprint.item_added` | Server → Client | `{ sprint_id, kanban_task_id, story_points }` |
| `sprint.item_removed` | Server → Client | `{ sprint_id, kanban_task_id }` |
| `task.status_changed` | Server → Client | `{ kanban_task_id, new_status, sprint_id? }` |
| `repo.scan_complete` | Server → Client | `{ workspace_id, repo_path, health_score }` |
| `adr.created` | Server → Client | `{ workspace_id, adr_id, title, status }` |
| `adr.updated` | Server → Client | `{ adr_id, new_status }` |
| `journal.created` | Server → Client | `{ workspace_id, entry_id, entry_date }` |
| `journal.updated` | Server → Client | `{ entry_id }` |

---

## 8. State Management

### 8.1 Store Atoms

Each module has its own nanostore atoms. None are global — each is scoped to
the workspace plugin.

**`desktop/stores/workspace.ts`:**

```typescript
// Workspace list
export const $workspaces = atom<Workspace[]>([])
export const $selectedWorkspaceId = atom<string | null>(null)
export const $workspaceHealth = atom<WorkspaceHealth | null>(null)
export const $workspaceActivity = atom<ActivityEvent[]>([])

// Derived: active workspace object
export const $activeWorkspace = computed([$workspaces, $selectedWorkspaceId],
  (list, id) => list.find(w => w.id === id) ?? null
)

// Repos for active workspace
export const $workspaceRepos = atom<Repo[]>([])
export const $repoMetadata = atom<Record<string, RepoMetadata>>({})
```

**`desktop/stores/roadmaps.ts`:**

```typescript
export const $roadmaps = atom<Roadmap[]>([])
export const $selectedRoadmapId = atom<string | null>(null)
export const $roadmapItems = atom<RoadmapItem[]>([])
```

**`desktop/stores/sprints.ts`:**

```typescript
export const $sprints = atom<Sprint[]>([])
export const $selectedSprintId = atom<string | null>(null)
export const $sprintItems = atom<SprintItem[]>([])
export const $burndownData = atom<BurndownPoint[]>([])
export const $velocityData = atom<VelocityPoint[]>([])

// Derived: which sprint is currently active (status === 'active')
export const $activeSprint = computed($sprints,
  sprints => sprints.find(s => s.status === 'active') ?? null
)
```

**`desktop/stores/adrs.ts`:**

```typescript
export const $adrs = atom<ADR[]>([])
export const $selectedADRId = atom<string | null>(null)
export const $adrContent = atom<string>('')  // full markdown for preview
export const $adrTags = atom<string[]>([])
export const $adrFilter = atom<ADRFilter>({ status: undefined, tag: undefined, repo: undefined })
```

**`desktop/stores/journal.ts`:**

```typescript
export const $journalEntries = atom<JournalEntry[]>([])
export const $selectedEntryId = atom<string | null>(null)
export const $journalDate = atom<string>(todayISO())  // selected date
export const $journalTagFilter = atom<string | null>(null)
export const $journalTagCloud = atom<TagCount[]>([])
export const $calendarDates = atom<string[]>([])  // dates with entries
```

### 8.2 Query Cache (React Query)

Read-heavy data uses `@tanstack/react-query` for caching and invalidation:

```typescript
// Query key factory
export const workspaceKeys = {
  all:     ['workspace'] as const,
  list:    () => [...workspaceKeys.all, 'list'] as const,
  detail:  (id: string) => [...workspaceKeys.all, 'detail', id] as const,
  health:  (id: string) => [...workspaceKeys.all, 'health', id] as const,
  repos:   (id: string) => [...workspaceKeys.all, 'repos', id] as const,
  sprints: (id: string) => [...workspaceKeys.all, 'sprints', id] as const,
  adrs:    (id: string) => [...workspaceKeys.all, 'adrs', id] as const,
  journal: (id: string) => [...workspaceKeys.all, 'journal', id] as const,
}
```

Mutations update the query cache optimistically, then reconcile on server
response. Rolls back on error.

### 8.3 WebSocket-Driven Updates

The `pluginSocket()` event handler updates stores directly for real-time data:

```typescript
// In desktop/lib/socket.ts
const unsub = pluginSocket('workspace', '/events', (event) => {
  switch (event.type) {
    case 'task.status_changed':
      // Update sprint item status in $sprintItems
      break
    case 'repo.scan_complete':
      // Update $repoMetadata for the scanned repo
      // Invalidate workspace health query
      break
    case 'adr.created':
      // Prepend to $adrs
      break
    case 'sprint.updated':
      // Update sprint in $sprints, invalidate burndown
      break
  }
})
```

### 8.4 Persistence

Most workspace state is server-authoritative and fetched on navigation. The
only client-persisted state is:

| Item | Storage | Scope |
|------|---------|-------|
| `$selectedWorkspaceId` | `localStorage` via `persisted` helper | Global |
| `$adrFilter` prefs | `localStorage` | Global |
| `$journalDate` | Session-only (resets on reload) | — |
| Collapsed/expanded UI state | Component-level `useState` | — |

---

## 9. Navigation Integration

### 9.1 Route Registration

The desktop plugin registers routes through the ContributionRegistry:

```typescript
// In desktop/index.ts
import { registry } from '@/contrib/registry'

// Main dashboard route
registry.register({
  area: 'routes',
  id: 'workspace.dashboard',
  title: 'Workspace',
  data: { path: '/workspace' },
  render: () => <WorkspaceDashboard />
})

// Sub-routes render inside the dashboard via internal routing
// (react-router nested routes within WorkspaceDashboard)
```

### 9.2 Sidebar Navigation

```typescript
registry.register({
  area: 'sidebar.nav',
  id: 'workspace.nav',
  data: { codicon: 'organization', label: 'Workspace', path: '/workspace' }
})
```

### 9.3 Internal Routing

The `WorkspaceDashboard` component uses react-router nested routes for its
sub-pages:

```typescript
// Within WorkspaceDashboard
<Routes>
  <Route index element={<DashboardHome />} />
  <Route path="roadmap" element={<RoadmapPage />} />
  <Route path="roadmap/:id" element={<RoadmapDetail />} />
  <Route path="sprints" element={<SprintsPage />} />
  <Route path="sprints/:id" element={<SprintDetail />} />
  <Route path="adrs" element={<ADRPage />} />
  <Route path="adrs/:id" element={<ADRDetail />} />
  <Route path="journal" element={<JournalPage />} />
</Routes>
```

The outer Hermes route `/workspace` catches all sub-paths (`/workspace/*`)
and hands them to the workspace plugin's internal router.

### 9.4 Deep Linking

Hermes supports deep links via the `hermes://` protocol. The workspace plugin
can register handlers for:

- `hermes://workspace` → opens workspace dashboard
- `hermes://workspace/sprints/<id>` → opens specific sprint
- `hermes://workspace/adrs/<id>` → opens specific ADR

Deep link handling goes through `onDeepLink` in the preload bridge and the
existing deep-link dispatch in the app shell.

---

## 10. Integration with Hermes Subsystems

### 10.1 Kanban Integration

```
┌──────────────────────────────────────────────────────────────┐
│                     INTEGRATION MODEL                         │
│                                                              │
│  workspace.sprint_items.kanban_task_id ─────┐                │
│  workspace.roadmap_items.kanban_task_id ────┤                │
│                                              │                │
│                    FOREIGN KEY REFERENCE     │                │
│                                              ▼                │
│  ┌──────────────────────────────────────────────────────┐    │
│  │              kanban.db (UNMODIFIED)                  │    │
│  │                                                      │    │
│  │  tasks table                                         │    │
│  │  • t_a1b2c3d4 — Fix session resume 404               │    │
│  │  • t_e5f6g7h8 — Add test coverage for gateway        │    │
│  │  • ...                                               │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  workspace plugin reads kanban.db                            │
│  workspace plugin NEVER writes kanban.db task status         │
│  (the agent does that through kanban_* tools)                │
│                                                              │
│  workspace plugin DOES write:                                │
│  • sprint_items (link tasks to sprints)                     │
│  • roadmap_items (link tasks to roadmap milestones)         │
└──────────────────────────────────────────────────────────────┘
```

**Key rule:** The workspace plugin reads kanban task status and metadata for
display (burndown, sprint board, roadmap progress), but never modifies kanban
task status directly. The agent modifies tasks through the `kanban_*` tools;
the workspace plugin reconciles by reading the kanban database.

**Sprint-to-kanban mapping:**
- Each sprint has a `board` field pointing to a kanban board slug
- Each sprint has an optional `tenant` field for filtering tasks
- Sprint items are **references** to kanban tasks, not copies
- When a sprint completes, incomplete items are NOT deleted from the sprint —
  they are marked for carry-over review

**Roadmap-to-kanban mapping:**
- A roadmap item can optionally link to a kanban task
- The task serves as the "implementation" of the milestone
- Roadmap item status can be derived from the linked task status, or set
  independently for milestones that span multiple tasks

### 10.2 Session Integration

```
Agent Session
  │
  ├── Context Engine Pipeline
  │     │
  │     ├── [built-in compressor] → compresses old messages
  │     ├── [memory provider]     → injects long-term memories
  │     ├── [skill loader]        → injects skill instructions
  │     └── [WorkspaceContextProvider]  ← THIS IS THE INTEGRATION
  │           │
  │           ├── Reads workspace.db for:
  │           │   • Active sprint id → linked kanban tasks assigned to agent
  │           │   • Recent ADRs for the current repo
  │           │   • Recent journal entries
  │           │   • Repo health snapshot
  │           │
  │           └── Formats as markdown block → injected into context
  │
  └── Agent turn runs with workspace context
        │
        ├── Agent uses kanban_* tools → modifies kanban.db
        ├── Agent uses file tools → writes ADRs to repo
        ├── Agent creates journal entries via REST → workspace.db
        └── Session completes → context snapshot saved
```

**Session-to-journal linking:** When a session is productive (the agent
completed work), the workspace plugin can suggest creating a journal entry
linked to that session. The session_id in journal_entries allows "open this
session" deep links from the journal.

**Session search across workspace:** The existing `searchSessions()` function
can be called by the workspace dashboard to find sessions related to a specific
repo or task.

### 10.3 Context Engine Integration

The `WorkspaceContextProvider` registers with the context engine through the
plugin's `register(ctx)` function:

```python
# In backend/__init__.py
def register(ctx):
    provider = WorkspaceContextProvider(workspace_db_path)
    ctx.register_context_engine(provider)
```

The context engine calls `provider.get_context(session_id, cwd)` before each
agent turn. The provider:

1. Resolves the workspace from the CWD (matches against `workspace_repos`)
2. Looks up the active sprint, open tasks, recent ADRs, recent journal entries
3. Formats a compact markdown block
4. Respects the token budget (`workspace.context_max_tokens` in config)
5. Records a `context_snapshot` for later audit

The context is injected **after** the system prompt and skills, **before**
the conversation history — so it does not break prompt caching.

### 10.4 Memory Integration

Workspace-specific facts discovered by the agent (e.g., "the auth module is
fragile", "avoid using sqlalchemy 2.0 style queries until migration is done")
are stored in the existing memory system (honcho, mem0, etc.).

The context provider can query memory for workspace-related facts and include
them in the injected context:

```python
# Future: context provider queries memory
memories = memory_provider.search("workspace:hermes-agent:lessons")
# Include relevant memories in context block
```

No modification to the memory system is needed. The workspace plugin simply
reads from it.

### 10.5 Git Integration

The workspace plugin uses Hermes's existing git IPC methods through the
renderer bridge. No new git operations are needed.

| Operation | Method |
|-----------|--------|
| Detect git repos under a root | `hermesDesktop.git.scanRepos(roots, opts)` |
| Get git root for a path | `hermesDesktop.gitRoot(path)` |
| Get repo status (quick) | `hermesDesktop.git.repoStatus(path)` |
| Read ADR file | `hermesDesktop.readFileText(path)` |
| Write ADR file | `hermesDesktop.writeTextFile(path, content)` |
| Browse repo tree | `hermesDesktop.readDir(path)` |

The backend `RepoScanner` service runs `git` commands directly (via
`subprocess`) since it runs in the Python process with the same filesystem
access. This is intentional — the backend can batch-scan repos without IPC
overhead.

### 10.6 File API Integration

ADRs are stored as markdown files in the repository. The ADRService in the
backend writes directly to the file system (since it runs in the same process
as the Hermes backend, with direct file access). The desktop renderer reads
ADR content through the existing `readFileText` IPC method.

For the desktop UI:
- **Preview:** The existing `$previewTarget` store is used to preview ADR files
  in the right rail. The workspace ADR detail page sets the preview target when
  the user clicks "Preview".
- **Editor:** The ADR editor in the desktop uses a simple textarea or can
  delegate to the agent ("Edit this ADR") via the chat composer.

---

## 11. Data Flow Diagrams

### 11.1 Workspace Dashboard Load

```
User navigates to /workspace
  │
  ▼
WorkspaceDashboard mounts
  │
  ├── useQuery(['workspace', 'list'])
  │     │
  │     └── pluginRest('workspace', '/workspace')
  │           │
  │           ▼
  │         Backend: WorkspaceService.list_workspaces()
  │           │
  │           └── SELECT * FROM workspaces → return Workspace[]
  │
  ├── useQuery(['workspace', 'health', activeWsId])
  │     │
  │     └── pluginRest('workspace', `/workspace/${id}/health`)
  │           │
  │           ▼
  │         Backend: WorkspaceService.get_aggregate_health()
  │           │
  │           ├── For each repo in workspace:
  │           │     ├── Read repo_metadata (last_scanned_at check)
  │           │     └── HealthScorer.compute_health(repo_path)
  │           │
  │           └── Return { score, repos: [...] }
  │
  └── useQuery(['workspace', 'activity', activeWsId])
        │
        └── pluginRest('workspace', `/workspace/${id}/activity`)
              │
              ▼
            Backend: WorkspaceService.get_recent_activity()
              │
              └── UNION of:
                    • Recent git commits (from repo_metadata)
                    • Recent ADR status changes (from adrs table)
                    • Recent sprint events (from sprints table)
                    • Recent journal entries (from journal_entries)
```

### 11.2 Sprint Burndown Computation

```
User opens /workspace/sprints/:id
  │
  ▼
SprintDetail mounts
  │
  ├── useQuery(['sprints', id])
  │     │ pluginRest('workspace', `/sprints/${id}`)
  │     └── Backend: SprintService.get_sprint(id)
  │           │
  │           ├── SELECT * FROM sprints WHERE id = ?
  │           ├── SELECT si.*, t.title, t.status, t.assignee
  │           │   FROM sprint_items si
  │           │   LEFT JOIN kanban.db.tasks t ON si.kanban_task_id = t.id
  │           │   WHERE si.sprint_id = ?
  │           └── Return SprintDetail
  │
  └── useQuery(['sprints', id, 'burndown'])
        │ pluginRest('workspace', `/sprints/${id}/burndown`)
        └── Backend: SprintService.get_burndown(id)
              │
              ├── Read sprint start_date, end_date
              ├── Read sprint_items with story_points
              ├── For each linked kanban task:
              │     └── Check kanban.db.task_runs for completion date
              │
              ├── Calculate ideal line:
              │     total_points / sprint_days → linear decline
              │
              ├── Calculate actual line:
              │     For each day from start to end:
              │       completed = sum(points for tasks completed on or before day)
              │       remaining = total - completed
              │
              └── Return BurndownPoint[]
```

### 11.3 Agent Turn with Workspace Context

```
Agent session starts / user sends message
  │
  ▼
AIAgent.run_conversation()
  │
  ├── Context engine pipeline runs
  │     │
  │     ├── 1. Built-in compressor (ContextCompressor)
  │     │      └── Compresses old messages if over threshold
  │     │
  │     ├── 2. WorkspaceContextProvider.get_context(session_id, cwd)
  │     │      │
  │     │      ├── Resolve workspace from CWD
  │     │      │     SELECT workspace_id FROM workspace_repos
  │     │      │     WHERE ? LIKE repo_path || '%'  -- prefix match
  │     │      │
  │     │      ├── If workspace found:
  │     │      │     ├── Get active sprint
  │     │      │     │     SELECT * FROM sprints
  │     │      │     │     WHERE workspace_id = ? AND status = 'active'
  │     │      │     │
  │     │      │     ├── Get agent's open tasks (via KanbanBridge)
  │     │      │     │     SELECT t.* FROM kanban.db.tasks t
  │     │      │     │     JOIN sprint_items si ON t.id = si.kanban_task_id
  │     │      │     │     JOIN sprints s ON si.sprint_id = s.id
  │     │      │     │     WHERE s.id = ? AND t.assignee = ?
  │     │      │     │       AND t.status NOT IN ('done','archived')
  │     │      │     │
  │     │      │     ├── Get recent ADRs
  │     │      │     │     SELECT * FROM adrs
  │     │      │     │     WHERE repo_path = ? AND status = 'accepted'
  │     │      │     │     ORDER BY created_at DESC LIMIT 5
  │     │      │     │
  │     │      │     └── Get recent journal entries
  │     │      │           SELECT * FROM journal_entries
  │     │      │           WHERE workspace_id = ?
  │     │      │           ORDER BY created_at DESC LIMIT 3
  │     │      │
  │     │      ├── Format as markdown block
  │     │      │     (respecting token budget — truncate oldest/least relevant)
  │     │      │
  │     │      └── Record context_snapshot
  │     │            INSERT INTO context_snapshots (session_id, snapshot, token_count)
  │     │
  │     └── 3. Skill loader (injects skill instructions)
  │
  ├── System prompt + context block sent to model
  │
  ├── Model responds → agent turn proceeds
  │
  └── If agent uses kanban_* tools or writes ADRs:
        ├── kanban.db updated (by kanban tools)
        ├── workspace.db reconcilable on next scan
        └── Sprint burndown stale → subsequent query re-fetches
```

### 11.4 ADR Creation (Agent-Driven)

```
Agent decides to create an ADR
  │
  ├── Agent loads adr skill (SKILL.md)
  │     └── Teaches ADR format, status values, file conventions
  │
  ├── Agent uses write_file tool:
  │     write_file("<repo>/docs/adr/0005-use-grpc.md", markdown_content)
  │
  ├── File written to disk (by Hermes file tool)
  │
  ├── ADR index may be stale in workspace.db
  │     │
  │     ├── Option A: User clicks "Scan ADRs" in workspace UI
  │     │     → ADRService.scan_repo_adrs() → indexes new file
  │     │
  │     ├── Option B: Cron job runs periodic scan
  │     │     → Scheduled via Hermes cron, runs daily
  │     │
  │     └── Option C: File watcher (future enhancement)
  │           → watchdog/inotify on docs/adr/ directories
  │
  └── Once indexed: ADR appears in workspace UI
```

### 11.5 Sprint Creation Flow

```
User creates a sprint in workspace UI
  │
  ▼
SprintForm submits
  │
  ├── pluginRest('workspace', `/workspace/${wsId}/sprints`, {
  │     method: 'POST',
  │     body: { name, goal, start_date, end_date, board, tenant }
  │   })
  │
  ▼
Backend: SprintService.create_sprint()
  │
  ├── INSERT INTO sprints (...) VALUES (...)
  │
  └── Emit WebSocket event: sprint.created
        │
        ▼
      All connected workspace clients receive update
        │
        ▼
      $sprints atom updated → SprintList re-renders

── Later, user adds tasks to sprint ──

  │
  ├── Opens "Add to Sprint" dialog
  │     │
  │     ├── KanbanBridge.list_tasks(board, status='ready|todo')
  │     │     └── Reads kanban.db → returns eligible tasks
  │     │
  │     └── User selects tasks, sets story points
  │
  ├── pluginRest('workspace', `/sprints/${sprintId}/items`, {
  │     method: 'POST',
  │     body: { kanban_task_id, story_points }
  │   })
  │
  ▼
Backend: SprintService.add_item()
  │
  ├── Validates kanban task exists (KanbanBridge.get_task)
  ├── INSERT INTO sprint_items (...) VALUES (...)
  ├── Optionally updates roadmap_item.sprint_id if task is milestone-linked
  └── Emit WebSocket event: sprint.item_added
```

---

## 12. Phased Implementation Plan

### Milestone 0: Scaffold & Foundation (Week 1)

**Goal:** Plugin boots, database exists, one REST endpoint works end-to-end.

| Task | Description |
|------|-------------|
| M0.1 | Create `plugin.yaml`, `__init__.py`, `pyproject.toml` |
| M0.2 | Implement `backend/database.py` — SQLite connection, WAL mode, schema migration runner |
| M0.3 | Implement `backend/migrations/001_initial.sql` — all tables from Section 3 |
| M0.4 | Implement `backend/api/__init__.py` — Flask blueprint or FastAPI router, mount at `/api/plugins/workspace/` |
| M0.5 | Implement `backend/api/workspace.py` — `GET /workspace` (list) + `POST /workspace` (create) |
| M0.6 | Implement `backend/services/workspace_service.py` — basic CRUD |
| M0.7 | Create `desktop/index.ts` — register contributed route `/workspace` with placeholder component |
| M0.8 | Create `desktop/lib/api.ts` — `pluginRest('workspace', ...)` wrapper |
| M0.9 | End-to-end test: desktop → pluginRest → backend → workspace.db → response → render |

**Deliverable:** `/workspace` page loads in the desktop app and shows "Hello Workspace" with a list of workspaces from the backend.

---

### Milestone 1: Workspace Dashboard (Week 2-3)

**Goal:** Users can add repos, see health scores, view recent activity.

| Task | Description |
|------|-------------|
| M1.1 | Implement `backend/services/repo_scanner.py` — scan_repo() with git commands |
| M1.2 | Implement `backend/services/health_scorer.py` — composite health score |
| M1.3 | Implement `backend/api/repos.py` — CRUD + scan endpoints |
| M1.4 | Implement `backend/api/workspace.py` — health + activity endpoints |
| M1.5 | Create `desktop/stores/workspace.ts` — $workspaces, $workspaceRepos, $workspaceHealth |
| M1.6 | Create `desktop/components/repo-card.tsx` — health badge, stats, actions |
| M1.7 | Create `desktop/components/repo-list.tsx` — searchable, filterable repo grid |
| M1.8 | Create `desktop/components/workspace-activity-feed.tsx` |
| M1.9 | Create `desktop/routes/workspace-dashboard.tsx` — full dashboard page |
| M1.10 | Register `sidebar.nav` contribution — "Workspace" in sidebar |
| M1.11 | Register `statusbar` contribution — health indicator |

**Deliverable:** Workspace dashboard shows repos with health scores, can add/remove repos, scan triggers git metadata refresh.

---

### Milestone 2: ADRs (Week 3-4)

**Goal:** Full ADR lifecycle: create, browse, search, status transitions, file sync.

| Task | Description |
|------|-------------|
| M2.1 | Implement `backend/services/adr_service.py` — full CRUD, file read/write, frontmatter parsing |
| M2.2 | Implement `backend/api/adrs.py` — all ADR endpoints |
| M2.3 | Implement `backend/services/adr_service.py` — scan_repo_adrs(), reconcile with filesystem |
| M2.4 | Create `desktop/stores/adrs.ts` — $adrs, $selectedADRId, $adrFilter |
| M2.5 | Create `desktop/components/adr-list.tsx` — filterable list with status badges |
| M2.6 | Create `desktop/components/adr-detail.tsx` — markdown preview (reuse Hermes preview pane) |
| M2.7 | Create `desktop/components/adr-form.tsx` — template-based editor |
| M2.8 | Create `desktop/routes/adr-page.tsx` + `desktop/routes/adr-detail.tsx` |
| M2.9 | Write `desktop/skills/adr/SKILL.md` — teaches agent ADR format, conventions, status lifecycle |
| M2.10 | Register ADR-related command palette commands |

**Deliverable:** Users browse ADRs per repo, create new ones (fills template and writes to `docs/adr/`), change status (file + DB stay in sync).

---

### Milestone 3: Engineering Journal (Week 5-6)

**Goal:** Structured daily journal with entries, tags, templates, calendar view.

| Task | Description |
|------|-------------|
| M3.1 | Implement `backend/services/journal_service.py` — full CRUD, search, tag cloud |
| M3.2 | Implement `backend/api/journal.py` — all journal endpoints |
| M3.3 | Create `desktop/stores/journal.ts` — $journalEntries, $journalDate, $journalTagFilter |
| M3.4 | Create `desktop/components/journal-calendar.tsx` — month view with entry indicators |
| M3.5 | Create `desktop/components/journal-editor.tsx` — rich editor with tags, mood, repo scope |
| M3.6 | Create `desktop/components/journal-template-picker.tsx` — standup/retro/decision/freeform |
| M3.7 | Create `desktop/components/journal-entry-card.tsx` |
| M3.8 | Create `desktop/routes/journal-page.tsx` |
| M3.9 | Write `desktop/skills/journal/SKILL.md` — teaches agent daily journaling, standup format |
| M3.10 | Add composer middleware: "Journal Entry" quick action in ChatBar |

**Deliverable:** Users write daily journal entries, view by calendar date, filter by tags, use templates. Agent can create journal entries via skill.

---

### Milestone 4: Sprint Management (Week 6-8)

**Goal:** Sprint CRUD, kanban task linking, burndown charts, velocity tracking.

| Task | Description |
|------|-------------|
| M4.1 | Implement `backend/services/kanban_bridge.py` — read/write kanban.db |
| M4.2 | Implement `backend/services/sprint_service.py` — full CRUD, burndown, velocity |
| M4.3 | Implement `backend/api/sprints.py` — all sprint endpoints |
| M4.4 | Create `desktop/stores/sprints.ts` — $sprints, $sprintItems, $burndownData, $velocityData |
| M4.5 | Create `desktop/components/sprint-card.tsx` — summary card with progress bar |
| M4.6 | Create `desktop/components/sprint-form.tsx` — create/edit dialog |
| M4.7 | Create `desktop/components/sprint-board.tsx` — read-only kanban view of sprint tasks |
| M4.8 | Create `desktop/components/burndown-chart.tsx` — SVG chart |
| M4.9 | Create `desktop/components/velocity-chart.tsx` — SVG chart |
| M4.10 | Create `desktop/routes/sprints-page.tsx` + `desktop/routes/sprint-detail.tsx` |
| M4.11 | Implement WebSocket events for sprint updates (backend emits, desktop consumes) |
| M4.12 | Write `desktop/skills/sprint-planning/SKILL.md` — teaches agent sprint planning, standups, retros |

**Deliverable:** Full sprint lifecycle: create sprint, add kanban tasks, track burndown, view velocity across sprints. Real-time updates via WebSocket.

---

### Milestone 5: Roadmaps (Week 8-9)

**Goal:** Roadmap timeline, milestone tracking, kanban/ADT linking, sprint assignment.

| Task | Description |
|------|-------------|
| M5.1 | Implement `backend/services/roadmap_service.py` — full CRUD, reordering |
| M5.2 | Implement `backend/api/roadmaps.py` — all roadmap endpoints |
| M5.3 | Create `desktop/stores/roadmaps.ts` — $roadmaps, $roadmapItems |
| M5.4 | Create `desktop/components/roadmap-timeline.tsx` — horizontal timeline visualization |
| M5.5 | Create `desktop/components/milestone-card.tsx` — on-timeline milestone |
| M5.6 | Create `desktop/routes/roadmap-page.tsx` + `desktop/routes/roadmap-detail.tsx` |
| M5.7 | Implement drag-to-reorder on roadmap items |
| M5.8 | Write `desktop/skills/roadmap/SKILL.md` — teaches agent roadmap planning |

**Deliverable:** Create roadmaps with milestones on a timeline. Drag to reorder. Link milestones to kanban tasks and sprints.

---

### Milestone 6: Context Engine Integration (Week 9-10)

**Goal:** Agent sees workspace context on every turn. Context is inspectable.

| Task | Description |
|------|-------------|
| M6.1 | Implement `backend/services/context_provider.py` — WorkspaceContextProvider class |
| M6.2 | Implement token budget enforcement, truncation strategy |
| M6.3 | Implement `backend/api/context.py` — snapshot inspection endpoints |
| M6.4 | Register provider via `ctx.register_context_engine()` in `__init__.py` |
| M6.5 | Write `desktop/skills/workspace-context/SKILL.md` — teaches agent how to use context |
| M6.6 | Create context inspector UI — preview what the agent sees |
| M6.7 | Add config key `workspace.context_max_tokens` to config.yaml defaults |
| M6.8 | Write integration tests: agent turn receives context, context reflects sprint state |

**Deliverable:** Every agent turn in a workspace-scoped session receives a compact context block with active sprint, open tasks, recent ADRs, and journal context. Users can preview this context from the workspace UI.

---

### Milestone 7: Polish & Hardening (Week 10-12)

**Goal:** Production quality: i18n, keybinds, notifications, performance, tests.

| Task | Description |
|------|-------------|
| M7.1 | Add i18n strings for all workspace UI (en locale, structured for future translation) |
| M7.2 | Register keybinds: open workspace, new ADR, new journal entry, toggle sprint view |
| M7.3 | Add notification integration: sprint starting/ending, task completed, repo health dropped |
| M7.4 | Implement cron integration: daily repo scan, weekly journal prompt, sprint reminders |
| M7.5 | Add workspace picker to statusbar (quick-switch between workspaces) |
| M7.6 | Performance: lazy-load route components, paginate large lists, debounce search |
| M7.7 | Error handling: backend unavailable → cached state, kanban.db missing → degraded mode |
| M7.8 | Write comprehensive backend tests (pytest, 80%+ coverage on services) |
| M7.9 | Write desktop store and component tests (vitest) |
| M7.10 | End-to-end test: create workspace → add repo → create sprint → link tasks → run agent → verify context |
| M7.11 | Documentation: README, architecture overview, skill usage guides |
| M7.12 | Theme compliance audit: ensure all components use `--ui-*` tokens, no hardcoded colors |

**Deliverable:** Production-ready workspace plugin with full test coverage, i18n, keybinds, notifications, performance optimization, and documentation.

---

### Dependency Graph

```
M0 (Scaffold)
 │
 ├──► M1 (Dashboard)
 │     │
 │     ├──► M2 (ADRs) ── independent, can parallelize with M3
 │     │
 │     ├──► M3 (Journal) ── independent, can parallelize with M2
 │     │
 │     ├──► M4 (Sprints) ── depends on M1 (needs kanban bridge)
 │     │     │
 │     │     └──► M5 (Roadmaps) ── depends on M4 (links to sprints)
 │     │
 │     └──► M6 (Context Engine) ── depends on M1-M5 (needs data to inject)
 │
 └──► M7 (Polish) ── depends on everything
```

**Parallelizable work:**
- M2 (ADRs) and M3 (Journal) can be built simultaneously
- M4 (Sprints) and M5 (Roadmaps) can start once M1 and kanban bridge are done
- M6 (Context) can start its scaffolding early but needs at least M1 + one of {M2, M3, M4} for real data
- All milestones include their own skills, which can be written in parallel with the code

---

### Zero-Core-Change Guarantee

This design touches **zero** files in the Hermes Desktop core or Hermes agent
core. Every integration point is a documented, stable extension surface:

| Integration Point | Surface | Status |
|-------------------|---------|--------|
| Desktop UI pages | Contributed routes (#1 in architecture doc) | Stable |
| Desktop sidebar nav | Sidebar nav contribution (#1) | Stable |
| Desktop statusbar | Statusbar data contribution (#4) | Stable |
| Command palette | PALETTE_AREA contribution (#6) | Stable |
| Keybinds | KEYBINDS_AREA contribution (#7) | Stable |
| REST API | pluginRest() (#8) | Stable |
| WebSocket events | pluginSocket() (#8) | Stable |
| Context injection | Context engine provider (#11) | Stable |
| Agent skills | SKILL.md in plugin directory | Stable |
| Git/file operations | Existing IPC methods | Stable |
| Kanban task data | kanban.db read via KanbanBridge | Stable |
| Cron scheduling | Hermes cron via REST API | Stable |
| Notifications | notify() store | Stable |
