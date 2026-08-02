# Canonical ADR Reconciliation — Design Record (S7.3A)

**Status:** IMPLEMENTED (S7.3A BUILD complete)
**Owner:** Hermes Workspace plugin (`plugins/workspace`)
**Related:** `docs/Hermes_Project_Handbook.md`, `plugins/workspace/README.md`,
`docs/reverse_engineering/ProjectScopeAuthorityDesign.md` (S7.2),
`docs/reverse_engineering/MemoryArchitectureGapAnalysis.md` (S7.1)

---

## 1. Why Git/file ADRs are canonical

S7.1 established that Workspace must not become a second generic memory or
artifact system; S7.2 established Hermes Project identity as canonical.
S7.3A resolves the ADR authority conflict documented since S7.1
("design doc says Git files canonical; implementation stores markdown rows").

Git-tracked Markdown files are the right substrate for engineering records:
git history = provenance, mergeability, external editing, review, backup.
`workspace.db` keeps an **index/projection** — metadata, a derived markdown
cache for search/API performance, and reconciliation bookkeeping — never a
competing copy of truth.

## 2. Canonical ADR location / convention discovered

- **Repository inspection found NO existing `docs/adr/` convention** (no
  `docs/adr/`, `docs/adrs/`, or `docs/architecture/` anywhere in the repo;
  no ADR skill or tool). The plugin's own `plugins/workspace/docs/security/
  adr-sec-*.md` files are Nygard-format design docs, but they live in the
  plugin tree, not a resolved project repository, and are not part of the
  runtime ADR store.
- **Convention established (per approved S7.3 PLAN):** canonical ADR files
  live under `<repo>/docs/adr/` (project-relative), named
  `NNNN-slug.md` (the `NNNN` prefix is an ordering hint, NOT identity).
  Optional YAML frontmatter carries `status` / `category` / `tags`.
  Required body element: an `# H1` title heading.
- **Identity:** the stable canonical identity is derived from the file stem
  minus the ordering prefix (`0001-use-sqlite.md` → `use-sqlite`). Identity
  survives renames within the ADR directory (ordering prefix changes are
  re-links, not new entities).

## 3. Authority model

```
Git repository
    ↓  (canonical content)
docs/adr/*.md
    ↓  (discovery + parse + hash)
ADRReconcileService
    ↓  (transactional projection writes)
workspace.db  (adrs + adr_content[derived cache] + adr_tags + reconcile fields)
    ↓
REST API / Desktop UI / Search / Graph
```

- The **file** is authoritative for content, title, status, category, tags.
- The **DB row** is a projection: identity/slug, relations, derived markdown
  cache (marked derived; used for search/API without re-parsing the
  filesystem), and reconciliation bookkeeping (`canonical_path`,
  `content_hash`, `reconcile_state`, `source`, `last_indexed`, `last_error`).
- No bidirectional sync. Conflicts are visible, never "latest wins".

## 4. Migration 007 (`007_adr_reconciliation.sql`)

Forward-only, additive, tracked by `_migrations`:

- `adrs` gains: `canonical_path TEXT`, `content_hash TEXT`,
  `reconcile_state TEXT NOT NULL DEFAULT 'db_legacy'`,
  `source TEXT NOT NULL DEFAULT 'workspace_db'`, `last_indexed TEXT`,
  `last_error TEXT`.
- Indexes: `idx_adrs_reconcile_state (workspace_id, reconcile_state)`,
  `idx_adrs_canonical_path (workspace_id, canonical_path)`.
- **Existing rows are untouched** — they default to `db_legacy` /
  `workspace_db` and remain visible/recoverable until explicitly
  materialized or reconciled. No destructive change.

## 5. Projection schema (ADR model additions)

`ADR` gains `canonical_path`, `content_hash`, `reconcile_state`, `source`,
`last_indexed`, `last_error`. The markdown in `adr_content` for canonical
rows is the **raw file content** (frontmatter included) so the desktop
editor round-trips faithfully; it is a derived cache, not the authority.

## 6. Identity / provenance model

- DB row `id` remains the internal reference (relations, graph edges).
- Canonical identity (`canonical_id` in status payloads / search results) is
  the stem-derived slug — stable across renames.
- Provenance: `source` (`workspace_db` | `git_file`), `canonical_path`
  (project-relative), `content_hash` (SHA-256 of file bytes),
  `last_indexed`, and audit events (`adr.materialize` records
  `source_before: workspace_db` + `target_path`; materialized files carry a
  `source: workspace_db` frontmatter marker).

## 7. Reconciliation states

| State | Meaning |
|---|---|
| `synced` | projection hash == canonical file hash |
| `file_new` | canonical file discovered, no projection row (dry-run report) |
| `file_changed` | canonical file changed since last index (dry-run report; real mode refreshes) |
| `db_legacy` | DB-only record, no canonical file — visible, recoverable |
| `missing_file` | projection references a file that no longer exists (row kept) |
| `conflict` | DB record and canonical file both exist with differing content |
| `invalid` | malformed file (no H1 / bad frontmatter / unreadable / duplicate identity) |

## 8. Reconciliation algorithm

Per workspace (S7.2 scope enforced at the API layer; service fails closed):

1. Resolve the workspace; no registered repositories → skip discovery
   (legacy rows stay `db_legacy`).
2. For each registered repository (sandbox-validated root): discover
   `docs/adr/**/*.md`; out-of-root / symlink escapes are **skipped**, never
   followed.
3. Parse each file (frontmatter + H1). Malformed → `invalid` (reported with
   the path; no projection row created unless one already exists).
4. Classify against the projection:
   - Same `canonical_path`, hash equal → `synced` (no write).
   - Same path, hash differs, DB untouched since last index → `file_changed`
     → refresh projection from the file (title/status/category/markdown/hash/
     last_indexed; slug stays the stable identity).
   - Same path, hash differs, DB edited since last index → `conflict`
     (visible, no auto-write).
   - Legacy row (slug match, no `canonical_path`): content agrees → promote
     to `git_file`/`synced` (link); content differs → `conflict`.
   - Row linked to a different path whose old file is gone → **rename**:
     re-link projection to the new location (file is authority).
   - Row linked to a different path whose old file still exists → duplicate
     identity → `invalid`.
   - No row → index: create projection row (`file_new` in dry-run; `synced`
     after real indexing). Slug collisions (legacy global UNIQUE) → `invalid`.
5. Rows with `source='git_file'` whose file is missing → `missing_file`
   (never auto-deleted).
6. Idempotent: an unchanged second run writes nothing.

## 9. Legacy ADR strategy

- Every pre-S7.3A DB-only ADR keeps `reconcile_state='db_legacy'`,
  `source='workspace_db'` — visible, searchable, fully recoverable.
- **Materialization** (`POST /v1/adrs/{id}/materialize`) is the explicit,
  previewable path: `dry_run` (default) previews the target
  (`docs/adr/NNNN-<slug>.md`, next free sequence); real mode writes the file
  atomically (temp + `os.replace`) with frontmatter (status/category/tags +
  `source: workspace_db` provenance marker when none exists), then promotes
  the row to `git_file`/`synced`. The file becomes authority only after the
  write succeeds; a failed write leaves the row untouched (recoverable via
  re-run or reconcile).
- Target-exists / identity-collision → 409 `MATERIALIZE_TARGET_EXISTS`, no
  write, no DB change.

## 10. CRUD behavior after S7.3A

- `POST /v1/adrs` (create): creates a DB-only row (`db_legacy`,
  `workspace_db`) — new ADRs are DB-only until explicitly materialized. No
  silent file creation.
- `PUT /v1/adrs/{id}`: **409 `ADR_CANONICAL_UPDATE`** for canonical rows —
  the file is the write path (`PUT /v1/adrs/{id}/file`). Legacy rows keep
  full CRUD.
- `DELETE /v1/adrs/{id}`: **409 `ADR_CANONICAL_DELETE`** for canonical rows
  — delete the file in git, then reconcile. Legacy rows delete as before.
- `PUT /v1/adrs/{id}/file`: atomic canonical-file write + projection refresh
  (git_file rows only; legacy → 409 with guidance to materialize first).

## 11. Security / path rules

- New capabilities: `adr.reconcile.read` (tier 1, audited) and
  `adr.reconcile.write` (tier 2, approval + audit). Dry-run/preview
  operations gate on `read`; real writes gate on `write` (approval semantics
  follow the existing codebase convention — tier-2 proceeds, audited, until
  the broader approval-flow milestone).
- All endpoints resolve the S7.2 scope first (`_require_scope` — 403
  `SCOPE_UNRESOLVED`, never global) and enforce cross-workspace membership
  (404, no existence leak).
- Path safety: every discovered path and write target is validated against a
  per-repository root (prefix + `Path.resolve()`) and the S6 `PathSandbox`
  (system-path deny list, hidden-dir protection, symlink rejection).
  Client-supplied canonical paths with `..`/absolute escapes → rejected.
- Audit events: `adr.reconcile.run` (project id, counts, dry_run),
  `adr.materialize` (source_before, target_path), `adr.file_updated`
  (canonical_path).

## 12. API changes

- `POST /v1/adrs/reconcile` `{workspace_id, session_id?, dry_run}` →
  `ADRReconcileSummary`
- `GET /v1/adrs/reconcile/status?workspace_id=&session_id=` →
  `ADRReconcileStatusList`
- `POST /v1/adrs/{id}/materialize?workspace_id=&session_id=`
  `{dry_run}` → `ADRMaterializeResult` (409 on target-exists / already
  canonical)
- `PUT /v1/adrs/{id}/file?workspace_id=&session_id=`
  `{markdown, dry_run}` → `ADRMaterializeResult` (409 on legacy row)
- `PUT /v1/adrs/{id}` / `DELETE /v1/adrs/{id}`: 409 for canonical rows.
- `GET /v1/graph/stats` and `GET /v1/graph/shortest-path` are now
  **workspace-scoped** (403 when unscoped) — closing the S7.2 carry-forward
  global aggregates.
- Search results carry `source_type` (`git_adr` | `workspace_adr`) and
  `canonical_id`.

## 13. Desktop behavior

- ADR rows show a reconciliation-state badge (synced / changed / legacy /
  missing / conflict / invalid).
- Detail view shows the state badge, the project-relative canonical path,
  and a "Materialize to file" action for legacy ADRs (preview → explicit
  apply).
- Header "Reconcile" button runs preview → apply and reports a summary.
- The ADR editor saves canonical ADRs through the file endpoint (markdown
  body incl. frontmatter) and shows a canonical-edit notice.
- Unresolved workspace scope still fails closed; no global requests.

## 14. Tests

- `test_migration_007.py` (7): clean migration, upgrade from 006, legacy
  preservation, defaults, indexes, idempotent tracking, round-trip.
- `test_adr_reconcile_storage.py` (7): projection meta updates, canonical
  path lookup, workspace isolation, CRUD preservation.
- `test_adr_reconcile_service.py` (35): parsing, discovery, indexing,
  idempotence, drift refresh, missing files, malformed, duplicates, legacy
  promotion/conflict, rename re-link, materialization (preview/apply/
  target-exists/sequence/no-repo/already-canonical), file updates, sandbox.
- `test_adr_reconcile_api.py` (15): reconcile/status/materialize/file
  endpoints, 403 unscoped, 409 canonical guards, membership 404, isolation.
- `test_adr_reconcile_security.py` (6): capability registry, policy
  decisions, audit events.
- Updated `test_search_graph_api.py`: scoped graph stats/shortest-path.
- Desktop: `stores/adrs.test.ts` (7) — state labels/tones/summary helpers.
- Total: **522 backend** (451 baseline + 71) + **15 frontend** (8 scope + 7 adr).

## 15. Runtime verification

Bounded smoke test with real `hermes_cli.projects_db` + temp git
repositories (no user data): project scope resolves; discovery + indexing;
projection reads; external-edit drift + refresh; legacy classification;
materialize preview + apply; cross-project isolation; out-of-sandbox path
rejection; missing-file + rename self-healing. All passed.

## 16. Limitations / S7.3B boundary

- ADR graph nodes still use the DB row id (canonical-id node identity
  deferred — the S7.3B split owns graph canonical-entity changes).
- `adr_dirs` is a service constant (`docs/adr`); a config.yaml section for
  the Workspace plugin does not exist yet (config plumbing deferred).
- Cross-workspace slug collisions on indexing are surfaced as `invalid`
  (legacy global UNIQUE constraint; disambiguation deferred).
- Approval-required capabilities proceed + audit (existing codebase
  convention; approval-flow UI is a separate milestone).
- Kanban task authority (S7.3B), context injection (S7.4), memory promotion
  (S7.5), and network-egress enforcement (S7.6) are NOT started.
