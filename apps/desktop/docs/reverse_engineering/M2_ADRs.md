# M2 — Architecture Decision Records (ADRs)

> Milestone 2 of the Workspace Plugin.  Full ADR lifecycle management.

---

## Architecture

```
Desktop UI (adr-page.tsx, adr-detail.tsx, adr-editor.tsx)
     ↓
REST API (api/v1.py — ADR routes)
     ↓
ADRService (services/adr_service.py)   ← slug gen, validation, transactions
     ↓
AbstractStorage (storage/__init__.py)  ← interface
     ↓
SQLiteStorage (storage/sqlite_storage.py)  ← implementation
     ↓
SQLite (workspace.db)
```

---

## Database Schema

Migration `002_adrs.sql` adds three tables:

### `adrs`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | TEXT | PRIMARY KEY |
| `workspace_id` | TEXT | NOT NULL, FK → workspaces(id) ON DELETE CASCADE |
| `repository_id` | TEXT | FK → repositories(id) ON DELETE SET NULL |
| `title` | TEXT | NOT NULL |
| `slug` | TEXT | NOT NULL, UNIQUE |
| `status` | TEXT | NOT NULL, CHECK (proposed|accepted|rejected|superseded|deprecated) |
| `category` | TEXT | NOT NULL DEFAULT '' |
| `created_at` | TEXT | NOT NULL DEFAULT datetime('now') |
| `updated_at` | TEXT | NOT NULL DEFAULT datetime('now') |

### `adr_content`

| Column | Type | Constraints |
|--------|------|-------------|
| `adr_id` | TEXT | PRIMARY KEY, FK → adrs(id) ON DELETE CASCADE |
| `markdown` | TEXT | NOT NULL DEFAULT '' |

### `adr_tags`

| Column | Type | Constraints |
|--------|------|-------------|
| `adr_id` | TEXT | NOT NULL, FK → adrs(id) ON DELETE CASCADE |
| `tag` | TEXT | NOT NULL, COLLATE NOCASE |

---

## ADR Statuses

| Status | Meaning |
|--------|---------|
| `proposed` | Under discussion, not yet accepted |
| `accepted` | Approved and active |
| `rejected` | Considered and declined |
| `superseded` | Replaced by a newer ADR |
| `deprecated` | No longer applicable |

---

## Slug Generation

Slugs are auto-generated from titles:
1. Lowercase
2. Strip special characters
3. Replace whitespace/hyphens with single hyphens
4. Strip leading/trailing hyphens
5. If duplicate, append `-2`, `-3`, etc.

Examples:
- `"Use SQLite for Storage"` → `use-sqlite-for-storage`
- `"ADR: Decision #1"` → `adr-decision-1`

---

## API Endpoints

All under `/api/plugins/workspace/v1/adrs`.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/adrs?workspace_id=&status=&category=&tag=&q=` | List ADRs with optional filters |
| `POST` | `/adrs` | Create ADR (slug auto-generated) |
| `GET` | `/adrs/{id}` | Get single ADR |
| `PUT` | `/adrs/{id}` | Update ADR (all fields optional) |
| `DELETE` | `/adrs/{id}` | Delete ADR and cascaded content/tags |

---

## Transactions

All write operations (`create_adr`, `update_adr`, `delete_adr`) use
`with self._storage.transaction():`.  The ADR row, content row, and tag
rows are all written or rolled back as a single atomic unit.

---

## Desktop UI

The ADR page (`adr-page.tsx`) is a contributed route at `/workspace/adrs`.

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `ADRPage` | `adr-page.tsx` | Main page: filter bar, ADR list, detail panel, editor dialog |
| `ADRDetail` | `adr-detail.tsx` | Read-only ADR view with markdown body |
| `ADREditor` | `adr-editor.tsx` | Create/edit dialog with fields for title, status, category, tags, markdown |
| `$adrs` store | `stores/adrs.ts` | Nanostores atoms for ADR state, filters, editor open/close |

### Features

- **Search** — full-text across title and markdown body
- **Filter by status** — dropdown: Proposed, Accepted, Rejected, Superseded, Deprecated
- **Filter by category** — dropdown populated from existing categories
- **Filter by tag** — dropdown populated from existing tags
- **List** — scrollable left panel with status badges and tag chips
- **Detail** — right panel with markdown body, metadata, edit/delete buttons
- **Editor** — dialog with title, status select, category input, tags input, markdown textarea
- **Delete confirmation** — modal dialog before permanent deletion

---

## Test Results

```
77 passed in 0.68s

Storage tests:       12/12 ✓
Migration tests:      4/4  ✓
Service tests:       10/10 ✓
API tests:            9/9  ✓
Transaction tests:   15/15 ✓
ADR storage tests:   12/12 ✓   ← new
ADR service tests:    8/8  ✓   ← new
ADR API tests:        7/7  ✓   ← new
```

---

## Extension Points Used

| Point | How |
|-------|-----|
| Dashboard manifest `"api"` field | v1 router included in `plugin_api.py` |
| Migration system | `002_adrs.sql` auto-discovered by `MigrationRunner` |
| `AbstractStorage` | Extended with 8 ADR methods |
| `WorkspaceService` | ADRs reference `workspace_id` (validated by existing workspace lookup) |
| Transaction layer | `create_adr`, `update_adr`, `delete_adr` use `with storage.transaction()` |
| Contributed routes | `/workspace/adrs` registered via `ContributionRegistry` |

No Hermes core files modified.
