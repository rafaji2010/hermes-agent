# M3 — Engineering Journal

> Milestone 3 of the Workspace Plugin. Engineering journal for software projects.

---

## Architecture

```
Desktop UI (journal-page.tsx, journal-editor.tsx)
     ↓
REST API (api/v1.py — journal routes)
     ↓
JournalService (services/journal_service.py)
     ↓
AbstractStorage (storage/__init__.py)
     ↓
SQLiteStorage (storage/sqlite_storage.py)
```

---

## Database Schema

Migration `003_engineering_journal.sql`.

### `journal_entries`

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | TEXT | PRIMARY KEY |
| `workspace_id` | TEXT | NOT NULL, FK → workspaces(id) ON DELETE CASCADE |
| `repository_id` | TEXT | FK → repositories(id) ON DELETE SET NULL |
| `title` | TEXT | NOT NULL |
| `summary` | TEXT | NOT NULL DEFAULT '' |
| `markdown` | TEXT | NOT NULL DEFAULT '' |
| `entry_date` | TEXT | NOT NULL DEFAULT date('now') |
| `created_at` | TEXT | NOT NULL DEFAULT datetime('now') |
| `updated_at` | TEXT | NOT NULL DEFAULT datetime('now') |

### `journal_tags`

| Column | Type | Constraints |
|--------|------|-------------|
| `entry_id` | TEXT | NOT NULL, FK → journal_entries(id) ON DELETE CASCADE |
| `tag` | TEXT | NOT NULL, COLLATE NOCASE |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/journal?workspace_id=&tag=&date=&q=&limit=` | List entries, newest first |
| `POST` | `/v1/journal` | Create entry |
| `GET` | `/v1/journal/{id}` | Get single entry |
| `PUT` | `/v1/journal/{id}` | Update entry (all fields optional) |
| `DELETE` | `/v1/journal/{id}` | Delete entry and tags |

Query parameters support filtering by tag, date (YYYY-MM-DD), and full-text search across title, summary, and markdown body.

---

## Desktop UI

Route: `/workspace/journal`

- **List panel** — newest-first, shows title, summary, date, tags
- **Search** — full-text across title + summary + markdown
- **Date filter** — date picker input (YYYY-MM-DD)
- **Tag filter** — dropdown from existing tags
- **Detail panel** — full markdown body, summary, edit/delete buttons
- **Editor dialog** — title, date, summary, tags, markdown textarea
- **Delete confirmation** — modal before deletion

---

## Workspace Status

The status dashboard (`/workspace`) now shows a **Journal Entries** metric card alongside Workspaces, Repositories, and Database status.

---

## Future Compatibility

The schema is designed for extension without migration. Future milestones can attach:

- **Git commits** — add a `git_commit` column or join table
- **Sprint references** — add a `sprint_id` column
- **ADR references** — add a `journal_adr_links` join table
- **File attachments** — add a `journal_attachments` table

These require only ALTER TABLE migrations and new join tables — no schema redesign.

---

## Test Results

```
99 passed in 0.96s

Storage tests:         12/12 ✓
Migration tests:        4/4  ✓
Service (workspace):   10/10 ✓
API tests:              9/9  ✓
Transaction tests:     15/15 ✓
ADR storage:           12/12 ✓
ADR service:            8/8  ✓
ADR API:                7/7  ✓
Journal storage:        8/8  ✓   ← new
Journal service:        8/8  ✓   ← new
Journal API:            6/6  ✓   ← new
```

---

## Extension Points Used

| Point | How |
|-------|-----|
| Migration system | `003_engineering_journal.sql` auto-discovered |
| `AbstractStorage` | Extended with 7 journal methods |
| Transaction layer | All writes use `with storage.transaction()` |
| Contributed routes | `/workspace/journal` registered |
| Status endpoint | `journal_count` added to `StatusResponse` |

No Hermes core files modified.
