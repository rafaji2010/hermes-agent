# M1 — Storage Foundation

> Milestone 1 of the Workspace Plugin.  Infrastructure only — no business
> features.

---

## Architecture

```
REST API (api/v1.py)
     ↓
WorkspaceService (services/workspace_service.py)   ← validation + git detection
     ↓
AbstractStorage (storage/__init__.py)              ← interface
     ↓
SQLiteStorage (storage/sqlite_storage.py)          ← implementation
     ↓
SQLite (workspace.db)                              ← file on disk
```

The rest of the plugin must **never** access SQLite directly.  All data
access goes through `AbstractStorage`, which is injected into
`WorkspaceService` at construction time.

---

## Storage Abstraction

**File:** `backend/storage/__init__.py`

```python
class AbstractStorage(ABC):
    # Workspaces
    def create_workspace(name, path) -> Workspace
    def list_workspaces() -> List[Workspace]
    def get_workspace(workspace_id) -> Optional[Workspace]
    def get_workspace_by_name(name) -> Optional[Workspace]

    # Repositories
    def register_repository(workspace_id, name, path,
                            git_root, default_branch) -> Repository
    def list_repositories(workspace_id) -> List[Repository]
    def get_repository(repo_id) -> Optional[Repository]
    def get_repository_by_path(workspace_id, path) -> Optional[Repository]

    # Settings
    def get_setting(key) -> Optional[str]
    def set_setting(key, value) -> None
```

Concrete implementations:

| Backend | Status |
|---------|--------|
| `SQLiteStorage` | Implemented (M1) |
| `PostgresStorage` | Future |
| `RemoteSyncStorage` | Future |

---

## Transaction Architecture

### Overview

The `AbstractStorage` interface exposes a transaction API that every
backend must implement.  Write operations in the service layer are
wrapped in transactions so that multi-step mutations are atomic.

### API

```python
class AbstractStorage(ABC):
    def begin_transaction() -> None      # start transaction or savepoint
    def commit() -> None                 # commit innermost unit
    def rollback() -> None               # roll back innermost unit

    @contextmanager
    def transaction():                   # with storage.transaction(): ...
```

The `transaction()` context manager calls `begin_transaction()` on
entry, `commit()` on clean exit, and `rollback()` on exception.

### Nesting: Savepoints

Nested `transaction()` blocks use **savepoints** rather than raising
errors.  This was chosen over flat-rejection because:

- **Composability.** An inner operation (e.g., `register_repository`)
  can be called both standalone and inside a larger orchestrating
  transaction without knowing the caller's depth.
- **Independent rollback.** An inner savepoint can roll back without
  losing the outer unit's work — critical for batch operations where
  one item failing should not undo previously-successful items.
- **Standard SQL.** `SAVEPOINT` / `RELEASE SAVEPOINT` / `ROLLBACK TO
  SAVEPOINT` are supported by SQLite, PostgreSQL, and MySQL.

```
depth 0:  no transaction active
depth 1:  BEGIN IMMEDIATE                ← outermost
depth 2:  SAVEPOINT sp_2                 ← nested
depth 3:  SAVEPOINT sp_3                 ← deeply nested
```

### Deferred Commit

Within a transaction block, individual write methods (`create_workspace`,
`register_repository`, `set_setting`) do **not** auto-commit.  They call
`_maybe_commit()` which is a no-op when `in_transaction` is `True`.  Only
the outermost `transaction()` context manager issues the final `COMMIT`.

This means:
- Calling a write method outside a `transaction()` block still auto-commits
  immediately (backward-compatible with M1 behaviour).
- Calling it inside a `transaction()` block defers the commit to the caller.

### Example Usage

```python
# Single atomic write (WorkspaceService pattern)
with storage.transaction():
    return storage.create_workspace("my-project", "/path")

# Multi-step atomic write (future: create workspace + register all repos)
with storage.transaction():
    ws = storage.create_workspace("my-workspace", "")
    storage.register_repository(ws.id, "repo-a", "/a", "/a", "main")
    storage.register_repository(ws.id, "repo-b", "/b", "/b", "main")
    # If repo-b fails (duplicate path), repo-a is also rolled back.

# Explicit API (for non-context-manager flows)
storage.begin_transaction()
try:
    storage.create_workspace("explicit", "")
    storage.commit()
except Exception:
    storage.rollback()
    raise
```

### Future Use Cases

The transaction system is designed for operations that will be added in
later milestones:

| Milestone | Operation | Why Transactions |
|-----------|-----------|-----------------|
| M3 — ADRs | Create ADR (write file + insert DB record) | Both must succeed or neither |
| M4 — Sprints | Create sprint + link kanban tasks | Sprint row + N sprint_items must be atomic |
| M4 — Sprint complete | Close sprint + carry-over incomplete tasks | Multiple status updates in one unit |
| M5 — Roadmaps | Reorder roadmap items | N rows updated in one swap |
| M3 — Journal | Create entry + update tag cloud | Entry + tags are one logical write |
| Future — Sync | Multi-table reconciliation | Repo metadata + ADR index + health score |

---

## Migration System

Migrations are plain SQL files in `backend/migrations/`, named `NNN_description.sql`.

The `MigrationRunner` (in `backend/migrations/__init__.py`):
1. Discovers files matching `\d{3}_\w+\.sql`
2. Sorts by version number
3. Checks `_migrations` table for already-applied versions
4. Applies un-applied migrations in order using `executescript()`
5. Records each applied migration in the `_migrations` table

Migrations are **idempotent** — running them twice is a no-op.

The `_migrations` table is created by `001_initial.sql` itself (with
`IF NOT EXISTS` guards) so the migration runner works from a completely
empty database.

### Current Migrations

| Version | Name | Description |
|---------|------|-------------|
| 001 | initial | Creates `workspaces`, `repositories`, `settings`, `_migrations` |

---

## Database Schema

### `workspaces`
| Column | Type | Constraints |
|--------|------|------------|
| `id` | TEXT | PRIMARY KEY |
| `name` | TEXT | NOT NULL, UNIQUE |
| `path` | TEXT | NOT NULL DEFAULT '' |
| `created_at` | TEXT | NOT NULL DEFAULT datetime('now') |
| `updated_at` | TEXT | NOT NULL DEFAULT datetime('now') |

### `repositories`
| Column | Type | Constraints |
|--------|------|------------|
| `id` | TEXT | PRIMARY KEY |
| `workspace_id` | TEXT | NOT NULL, FK → workspaces(id) ON DELETE CASCADE |
| `name` | TEXT | NOT NULL |
| `path` | TEXT | NOT NULL |
| `git_root` | TEXT | NOT NULL DEFAULT '' |
| `default_branch` | TEXT | NOT NULL DEFAULT 'main' |
| `created_at` | TEXT | NOT NULL DEFAULT datetime('now') |

Unique constraint on `(workspace_id, path)`.

### `settings`
| Column | Type | Constraints |
|--------|------|------------|
| `key` | TEXT | PRIMARY KEY |
| `value` | TEXT | NOT NULL DEFAULT '' |

### `_migrations`
| Column | Type | Constraints |
|--------|------|------------|
| `version` | INTEGER | PRIMARY KEY |
| `description` | TEXT | NOT NULL |
| `applied_at` | TEXT | NOT NULL DEFAULT datetime('now') |

---

## API Endpoints

All endpoints are mounted under `/api/plugins/workspace/`.

| Method | Path | Status | Description |
|--------|------|--------|-------------|
| `GET` | `/health` | 200 | M0 health check (backward compat) |
| `GET` | `/v1/health` | 200 | v1 health with database status |
| `GET` | `/v1/workspaces` | 200 | List all workspaces |
| `POST` | `/v1/workspaces` | 201 | Create workspace |
| `GET` | `/v1/repositories` | 200 | List repositories (query: `?workspace_id=`) |
| `POST` | `/v1/repositories` | 201 | Register repository |

### Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `WORKSPACE_NOT_FOUND` | 404 | Workspace ID does not exist |
| `DUPLICATE_WORKSPACE` | 409 | Workspace name already in use |
| `DUPLICATE_REPOSITORY` | 409 | Repository path already registered |
| `INVALID_PATH` | 400 | Path does not exist or is not a directory |
| `NOT_A_GIT_REPOSITORY` | 422 | Path is not a git repository |

### Request/Response Examples

**Create workspace:**
```json
POST /v1/workspaces
{"name": "hermes-agent", "path": "/home/user/hermes-agent"}

→ 201
{
  "workspaces": [{
    "id": "a1b2c3d4e5f6",
    "name": "hermes-agent",
    "path": "/home/user/hermes-agent",
    "created_at": "2026-07-19 12:00:00",
    "updated_at": "2026-07-19 12:00:00"
  }]
}
```

**Register repository:**
```json
POST /v1/repositories
{
  "workspace_id": "a1b2c3d4e5f6",
  "name": "hermes-agent",
  "path": "/home/user/hermes-agent"
}

→ 201
{
  "repositories": [{
    "id": "f6e5d4c3b2a1",
    "workspace_id": "a1b2c3d4e5f6",
    "name": "hermes-agent",
    "path": "/home/user/hermes-agent",
    "git_root": "/home/user/hermes-agent",
    "default_branch": "main",
    "created_at": "2026-07-19 12:01:00"
  }]
}
```

---

## Future Extension Strategy

### Adding a new domain table (e.g., `roadmaps`)

1. Create `backend/migrations/002_roadmaps.sql`
2. Add `Roadmap` model to `backend/models.py`
3. Add `AbstractStorage` abstract methods
4. Implement in `SQLiteStorage`
5. Add service layer in `backend/services/roadmap_service.py`
6. Add REST endpoint in `backend/api/v1.py`

No changes to existing code required.  The migration runner handles
ordering and idempotency automatically.

### Swapping storage backends

1. Implement `PostgresStorage(AbstractStorage)`
2. Change the injection in `_service()` (or via config)

The `WorkspaceService` and REST layer never need to know which backend
is in use.

### Adding a new API version (e.g., `v2`)

1. Create `backend/api/v2.py` with its own `APIRouter(prefix="/v2")`
2. Include it in `dashboard/plugin_api.py`

Existing v1 endpoints remain unchanged.  Clients can migrate at their
own pace.

---

## Test Results

```
50 passed in 0.51s

Storage tests:       12/12 ✓
Migration tests:      4/4  ✓
Service tests:       10/10 ✓
API tests:            9/9  ✓   ← +health_reflects_counts
Transaction tests:   15/15 ✓
```

---

## Workspace Status Page

The desktop plugin renders a professional administrator dashboard at
`/workspace`.  It consumes the enriched `GET /v1/health` endpoint to
display all system state in a single round-trip.

### Sections

| Section | Data Source | What It Shows |
|---------|-------------|---------------|
| **Plugin** | `StatusResponse` | Name, version, running status |
| **Backend** | `StatusResponse` | Connection state, API version, health |
| **Storage** | `StatusResponse` | Provider, connection, transaction/nesting support |
| **Database** | `StatusResponse` | Schema version, migration status, workspace/repository counts |
| **System** | `StatusResponse` | Hermes home directory, database path, plugin version |
| **Top Metrics** | `StatusResponse` | Workspace count, repository count, DB connection status |

### Behaviour

- **Auto-refresh.** The page refetches on every navigation to `/workspace`.
- **Manual refresh.** A "Refresh" button in the header bar re-queries the
  health endpoint and updates all sections.
- **Error state.** When the backend is unreachable, the page shows an
  `ErrorState` component with a "Retry" button.
- **Loading state.** While the query is in flight, a `Loader` is shown
  with "Loading status..." text.
- **Last refresh time.** Displayed in the header bar next to the refresh
  button.

### How It Verifies M1

The Status page exercises every M1 subsystem:

| Subsystem | Verified By |
|-----------|-------------|
| **Storage layer** | `storage_provider`, `database_connected` fields |
| **Migration system** | `schema_version`, `migration_status` fields |
| **Transaction support** | `transaction_support`, `nested_transactions` fields |
| **Database schema** | `workspace_count`, `repository_count` (queries tables) |
| **REST API** | Single `GET /v1/health` round-trip |
| **Plugin loading** | Page renders only if the desktop plugin registered |

### Component File

`apps/desktop/src/plugins/workspace/workspace-page.tsx`

---

## Extension Points Used

| Point | How |
|-------|-----|
| Dashboard manifest `"api"` field | `dashboard/manifest.json` → `plugin_api.py` imported and mounted at `/api/plugins/workspace/` by `_mount_plugin_api_routes()` |
| FastAPI `APIRouter` | `router` exported and mounted automatically |
| `get_hermes_home()` from `hermes_constants` | Resolves `workspace.db` path inside the profile-aware Hermes home |
| Python plugin `register(ctx)` | In `__init__.py` — logs startup message; future milestones will register hooks/tools/commands |

No Hermes core files were modified.
