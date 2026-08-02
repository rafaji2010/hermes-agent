# Project Scope & Authority Alignment — Design Record (S7.2)

**Status:** IMPLEMENTED (S7.2 BUILD complete)
**Owner:** Hermes Workspace plugin (`plugins/workspace`)
**Related:** `docs/Hermes_Project_Handbook.md`, `plugins/workspace/README.md`,
`hermes_cli/projects_db.py`, `hermes_state.py`

---

## 1. Problem

The Workspace plugin (`workspace.db`) held engineering data scoped only by a
free-form, caller-supplied workspace id. The Hermes Project authority
(per-profile `projects.db`, owned by Hermes Core) was never connected:
nothing resolved "which project am I in" → "which workspace data may I see",
and every previously-global endpoint (`/tasks`, `/search`, `/graph`,
`/analytics`, `/assistant/*`) silently served **all** workspaces when the
caller sent no scope.

## 2. Authority model (unchanged, from S7.1)

- **Hermes Project** (`projects.db`, `p_<hex>` ids, unique slug, project
  folders, `project_for_path` longest-prefix abspath matching) is the
  canonical project identity. Owned by Hermes Core. Archived projects are
  excluded from resolution.
- **Workspace** (`workspace.db`, `uuid4().hex[:12]`) stays the storage
  identity for engineering data.
- The only coupling introduced: a soft, nullable
  `workspaces.hermes_project_id` column. No identity merge, no FK into
  `projects.db`, no core change.

## 3. Migration 006 (`006_project_scope.sql`)

- `ALTER TABLE workspaces ADD COLUMN hermes_project_id TEXT;` (nullable)
- `CREATE INDEX idx_workspaces_hermes_project ON workspaces(hermes_project_id);`
- Forward-only, tracked in `_migrations`, existing workspaces stay unmapped,
  no auto-association at migration time.

## 4. `ProjectScopeResolver` (`backend/services/scope_resolver.py`)

Resolution precedence (implemented exactly):

1. **Explicit mapping** — a known workspace whose `hermes_project_id` is
   set wins immediately (state `mapped`, source `mapping`).
2. **Session cwd** — `SessionDB.get_session(session_id)` → `cwd` →
   `projects_db.project_for_path(cwd)`.
3. **Session git root** — fallback to `git_repo_root` when cwd misses.
4. **Reverse mapping** — a project identified from path evidence resolves
   to its linked workspace via `get_workspace_by_project_id`.
5. **Fallback states** — `unmapped` (workspace known, no mapping, no path
   evidence), `partial` (project XOR workspace identified, link missing),
   `unresolved` (nothing identified).

Invariants:

- **Never unresolved → global.** Enforcement rejects (403
  `SCOPE_UNRESOLVED`) when no scope can be established.
- Fail-safe on ambiguity: duplicate mappings are refused at write time
  (`DuplicateProjectMappingError`), and backfill never mutates when a
  project maps to >1 workspace.
- Session-less callers (messaging) supply `cwd` explicitly or stay
  unresolved — no silent global widening.
- `projects.db` and `state.db` are accessed through injectable callables
  (defaults lazy-import Hermes Core), so the plugin stays importable and
  unit-testable without Core at module load.

## 5. Storage mapping operations (`AbstractStorage` + `SQLiteStorage`)

`link_project(workspace_id, project_id)` (rejects empty ids, unknown
workspaces, and duplicate mappings), `unlink_project(workspace_id)`,
`get_project_link(workspace_id)`, `get_workspace_by_project_id(project_id)`,
`list_workspaces_by_project_id(project_id)`. `Workspace` model gained
`hermes_project_id`.

## 6. API surface (`api/v1.py`)

Mapping:

- `GET /v1/workspaces/{id}/project` → `ProjectLink`
- `PUT /v1/workspaces/{id}/project` `{project_id}` — validates the project
  exists in `projects.db` (404 `PROJECT_NOT_FOUND`), 409 on duplicate
- `DELETE /v1/workspaces/{id}/project`

Resolution + backfill:

- `POST /v1/scope/resolve` `{session_id?, workspace_id?, cwd?}` →
  `ResolvedProjectScope` (diagnostic; returns `partial`/`unresolved` states)
- `POST /v1/scope/backfill` `{project_id, workspace_id?, dry_run=true}` —
  inspection-first: `proposed` (dry run), `applied` (explicit),
  `already_linked`, `ambiguous` (never mutates), `not_found`

Scope enforcement (previously-global endpoints now take `session_id` and
reject unresolvable scopes with 403):

- `GET /tasks`, `GET /tasks/search`, `GET /search`, `GET /graph`
- `GET /analytics`, `GET /analytics/trends`, `GET /analytics/insights`,
  `POST /analytics/export` (analytics are now computed per workspace)
- `POST /assistant/chat`, `POST /assistant/context`,
  `GET /assistant/suggestions`

Membership checks (Amendment 2 — no IDOR-shaped legacy exceptions):

- `GET /adrs/{id}`, `GET /journal/{id}`, `GET /roadmaps/{id}`,
  `GET /tasks/{id}` accept an optional `workspace_id`; cross-workspace
  lookups return 404 (no existence leak).

Task reassignment guard:

- `PUT /tasks/{id}` with a different `workspace_id` is rejected
  (`CROSS_PROJECT_REASSIGNMENT`) when both workspaces are mapped to
  different projects. Unmapped sides are allowed (nothing to verify).

## 7. Security integration

- New capabilities registered in the Capability Registry:
  `workspace.scope.read` (tier 1, audited) and `workspace.scope.link`
  (tier 2, approval + audit).
- All mapping/resolution endpoints gate through
  `AuthorizationMiddleware.guard()` with `resource_type="workspace"` +
  `resource_id`, and audit events carry project/workspace ids in `details`.
- Per Amendment 1 (approved in PLAN), no `scope=all` /
  `workspace.admin.global_read` capability was built: caller inspection
  showed the only consumers of the global-capable endpoints are desktop
  plugin pages, which now receive resolved scopes. Global admin reads stay
  deferred.

## 8. Desktop integration

- `apps/desktop/src/plugins/workspace/stores/scope.ts` — renderer cache of
  backend resolution, keyed on the active session id + shared
  `$projectScope`; pure mappers (`workspaceScopeFromResolution`,
  `scopeQueryParams`, `scopeReady`) are unit-tested (vitest).
- `scope-notice.tsx` — safe unmapped state with an explicit, confirmation-
  gated backfill link button.
- All 7 pages (ADRs, journal, roadmaps, tasks, search, assistant, analytics)
  thread the resolved `workspace_id`; queries are gated (`enabled: !!ws`),
  so an unscoped page never issues a global request. Manual workspace-id
  inputs remain as explicit overrides for tooling/debug use.

## 9. Test coverage

Backend (all under `plugins/workspace/backend/tests/`):

- `test_migration_006.py` (7) — column/index/nullability/legacy unmapped
- `test_project_scope_storage.py` (12) — link/unlink/get-by-project/dup
- `test_scope_resolver.py` (14) — precedence, path sources, states, overrides
- `test_project_scope_api.py` (29) — mapping CRUD, resolve, backfill
  (incl. ambiguous/already-linked), 403 enforcement, membership 404s,
  cross-project reassignment
- Updated `test_analytics_api.py` / `test_assistant_api.py` for scoped calls
  + 403-on-unscoped assertions

Frontend: `apps/desktop/src/plugins/workspace/stores/scope.test.ts` (8).

Baseline `uv run pytest plugins/workspace` was 389; now **451 passed**.
Desktop `npm run build` green; new-file typecheck/lint clean except two
plugin-fence import errors (session/project atoms are not in the SDK —
same class as the pre-existing plugin-wide debt documented in S7.1).

## 10. Runtime verification

Smoke test with real Core imports (`hermes_cli.projects_db`,
`project_for_path`, `SessionDB`) against a temp `HERMES_HOME`:
project creation → `project_for_path` hit/miss → unmapped → partial via
real path resolution → mapped → reverse mapping via cwd → unresolved →
workspace isolation → duplicate-mapping guard. All passed.

## 11. Deliberately deferred (see handbook roadmap)

- `scope=all` / global admin reads — deferred (Amendment 1).
- Analytics/graph global aggregates (`/graph/stats`, `/graph/shortest-path`)
  remain as-is (documented global surfaces; not consumer-critical).
- Desktop SDK fence compliance for `@/store` atom access — requires SDK
  widening, tracked with the existing plugin debt.
- Session↔project auto-linking — explicitly NOT built (S7.3 territory).
