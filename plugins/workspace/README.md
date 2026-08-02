# Workspace Plugin

Workspace intelligence layer for multi-project engineering.

## Folder Structure

```
plugins/workspace/
├── plugin.yaml                  # Plugin manifest (v0.1.0)
├── __init__.py                  # register(ctx) entry point
├── README.md                    # This file
├── CONFIGURATION.md             # Required config to activate
├── docs/
│   └── security/                # Security architecture ADRs (sec-001 to sec-010)
├── dashboard/
│   ├── manifest.json            # Dashboard API discovery
│   └── plugin_api.py            # FastAPI router — 48 REST endpoints
├── backend/
│   ├── database.py              # DatabaseManager (SQLite connection pool)
│   ├── models.py                # Pydantic domain models
│   ├── storage/
│   │   ├── __init__.py          # AbstractStorage ABC
│   │   └── sqlite_storage.py    # SQLite implementation
│   ├── services/
│   │   ├── workspace_service.py # Workspace + repository management
│   │   ├── adr_service.py       # Architecture Decision Records
│   │   ├── journal_service.py   # Engineering journal
│   │   ├── roadmap_service.py   # Roadmaps + milestones
│   │   ├── task_service.py      # Task management
│   │   ├── analytics_service.py # Analytics & dashboards
│   │   ├── assistant_service.py # AI workspace assistant
│   │   ├── graph_service.py     # Knowledge graph
│   │   └── search_service.py    # Global search
│   ├── api/
│   │   └── v1.py                # v1 REST endpoints
│   ├── migrations/              # SQL migrations (001-007)
│   ├── security/
│   │   ├── exceptions.py        # Security exception hierarchy
│   │   ├── models.py            # ContentLabel, CapabilityDef, AuditEvent
│   │   ├── labels.py            # Content labeling (ADR-SEC-005 Layer 2)
│   │   ├── sanitization.py      # Content sanitization (ADR-SEC-005 Layer 3)
│   │   ├── secrets.py           # Secret detection & redaction (ADR-SEC-006)
│   │   ├── capabilities.py      # 44-capability registry (ADR-SEC-003)
│   │   ├── audit.py             # Audit logging framework (ADR-SEC-008)
│   │   ├── policy.py            # PolicyEngine + PolicyDecision
│   │   ├── authorization.py     # AuthorizationMiddleware
│   │   ├── network_isolation.py # URL validation, SSRF prevention (ADR-SEC-007 L5)
│   │   ├── resource_limits.py   # Resource limit enforcement (ADR-SEC-007 L6)
│   │   └── sandbox.py           # Path sandbox, workspace isolation (ADR-SEC-007 L1-2)
│   └── tests/                   # 389 pytest tests
```

## Security Architecture

### Enforcement Flow

```
REST API (v1.py)
  → AuthorizationMiddleware (single gate)
      → PolicyEngine.evaluate(capability, context)
          → CapabilityRegistry → CapabilityDef (tier, approval, audit)
          → Custom policy rules (first-match wins)
      → AuditLogger.log(decision) → audit.log
  → Workspace Services (guarded write operations)
```

### Components

| Component | Role |
|-----------|------|
| **PolicyDecision** | Structured result: `{allowed, requires_approval, audited, reason}` — never a bare boolean |
| **PolicyEngine** | Evaluates capabilities against the registry + custom rules; returns PolicyDecision |
| **AuthorizationMiddleware** | Single authorization gate; consults PolicyEngine + emits audit events |
| **CapabilityRegistry** | 44 capabilities across 10 scopes (filesystem, shell, git, network, browser, search, vision, delegation, plugins, memory, config, cron, workspace) |
| **AuditLogger** | Thread-safe JSON Lines logging; records ALLOW/DENY/APPROVAL_REQUIRED decisions |
| **Content Labels** | Origin metadata + trust levels (trusted/untrusted/unknown) attached to content |
| **Sanitization Pipeline** | Null-byte stripping, control char removal, ANSI strip, Unicode normalization, size truncation |
| **Secret Redaction** | Pattern-based + entropy-based detection; redacts API keys, tokens, PEM keys |
| **Network Validator** | SSRF prevention; validates URLs, blocks private IPs, enforces protocol allow-lists |
| **Path Sandbox** | Filesystem isolation; denies system paths, hidden dirs; enforces workspace scoping |
| **Resource Limiter** | Enforces content size, tag count, title/path length limits |

### Approval Workflow

Capabilities with `approval_required=True` (tier 2/3) never execute automatically. The middleware returns an `APPROVAL_REQUIRED` status. Callers must handle the approval flow explicitly. Backend only — no frontend dialogs.

### Capability Tiers

| Tier | Description | Examples |
|------|-------------|----------|
| 1 | Auto-approve, audited | `workspace.create`, `adr.update`, `task.create` |
| 2 | User approval required | `workspace.delete`, `roadmap.delete`, `fs.write` |
| 3 | Admin only | `shell.sudo`, `git.force_push`, `config.write` |

## Milestones

| Milestone | Status | Tests |
|-----------|--------|-------|
| M0 — Scaffold | ✓ | — |
| M1 — Workspace Foundation | ✓ | — |
| M2 — Storage Layer | ✓ | — |
| M3 — Transactions | ✓ | — |
| M4 — Workspace Status Dashboard | ✓ | — |
| M5 — ADR Management | ✓ | — |
| M6 — Engineering Journal | ✓ | — |
| M7 — Roadmaps & Milestones | ✓ | — |
| M8 — Tasks | ✓ | — |
| M9 — Analytics | ✓ | — |
| M10 — Search Graph | ✓ | — |
| S6.1 — Security Foundation | ✓ | 22 |
| S6.2 — Security Enforcement & Policy Engine | ✓ | 56 |
| S6.3 — Security Hardening | ✓ | 58 |
| S6.4 — Runtime Security Integration | ✓ | 29 |
| S7.1 — Memory Architecture Evaluation & Workspace Integration Design | ✓ | — (docs only) |
| S7.2 — Project Scope & Authority Alignment | ✓ | 62 |
| S7.2R — Repository Recovery & Baseline Restoration | ✓ | 451 (+8 desktop) |
| S7.3A — Canonical ADR Reconciliation | ✓ | 71 (+7 desktop) |
| S7.U1 — Upstream Hermes Reconciliation | U1A ✓ · U1B ✓ · U1C ✓ · U1D-A ✓ · U1D-B ✓ · U1D-C ✓ | 563 backend; 35 desktop vitest |

### S7.U1D-C — API Scope & Authorization Enforcement

Every Workspace REST operation now acts only on resources the effective
Workspace scope is authorized to access.  Invariant: possession of a
resource ID never grants access from another Workspace scope.

- **Route enforcement model.** Resource routes resolve the effective
  scope (`workspace_id` or session/cwd via the ProjectScopeResolver) and
  enforce membership — cross-workspace reads/writes/deletes return 404
  (no existence leak).  List/aggregate routes are scoped at the query
  level.  An unresolved scope returns 403 and NEVER widens to global.
- **Coverage.** ADR + journal + roadmap + milestone + task CRUD, task
  comments and dependencies (BOTH ends validated), related-entity
  traversal, search/graph/analytics/export/assistant aggregates.
  Task creation validates every referenced entity (roadmap/milestone/
  adr/journal/repository) belongs to the effective scope.
- **Graph.** `get_related` resolves the entity's owning workspace and
  rejects entities outside the declared scope (404); journal related-
  lookups are no longer global.
- **Assistant.** Analytics and related-entity context are computed for
  the effective workspace only.
- **Error translation.** One boundary (`_api_error`) maps domain/security/
  limit/conflict errors to 400/403/404/409/413/500 — no sensitive
  internal detail is leaked and failures never masquerade as empty
  successes.
- **Parent-resource anchoring (documented gap).** The Desktop plugin
  does not yet send `workspace_id` on milestone routes and comment-add;
  those routes enforce membership when a scope IS declared and otherwise
  anchor to the parent resource's workspace (never global).  U1D-G should
  thread `workspace_id` through those Desktop calls, then enforce strictly.
- **Approval/security mismatches (for U1D-F).** The transplanted S6.x
  capability guard evaluates `workspace.scope.read` etc. but approval-
  required decisions still permit execution (fail-open) — unchanged here.
- Tests: `backend/tests/test_workspace_isolation.py` (20 adversarial
  tests, workspaces A/B) covering tasks, roadmaps/milestones, ADR,
  journal, search, graph/related/shortest-path, analytics/export,
  assistant, unresolved-scope, and relationship validation.

**Next: S7.U1D-D — Project/session/profile authority alignment.**

### S7.U1D-B — SQLite Lifecycle, Concurrency & Migration Hardening

Workspace SQLite behavior is hardened for realistic runtime usage
(concurrent requests, multiple threads, multiple processes, startup
races, interrupted migrations). REST contracts and domain semantics are
unchanged.

- **Connection ownership.** File-backed ``workspace.db`` connections are
  THREAD-LOCAL (one configured connection per thread per
  ``DatabaseManager``) — concurrent FastAPI threadpool handlers never
  share a connection or interleave transactions. ``close()`` releases
  every live connection deterministically (epoch-invalidated thread
  slots transparently reopen). In-memory databases keep a single shared
  connection (test path). Transaction nesting depth is a ``ContextVar``;
  an abandoned transaction on a reused connection is rolled back before
  a new one begins (self-heal).
- **SQLite configuration.** WAL via the shared upstream helper
  ``hermes_state.apply_wal_with_fallback`` (honours
  ``database.journal_mode`` config; degrades safely to DELETE on
  filesystems that refuse WAL); explicit ``busy_timeout``
  (``HERMES_WORKSPACE_BUSY_TIMEOUT_MS``, default 10s); foreign keys on.
- **Migration lifecycle.** Each migration and its version record run in
  one ``BEGIN IMMEDIATE ... COMMIT`` transaction (atomic — a failure
  leaves no partial schema). Per-migration sentinel checks recover from
  a crash between schema application and version recording without
  re-running ``ALTER`` statements. Versions from a newer schema are
  logged and left untouched. ``MigrationRunner(conn, migrations_dir=…)``
  is the test seam for failure-recovery tests.
- **Cross-process initialization.** First-connect migration setup runs
  under a bounded cross-process file lock (``<db>.init.lock``, flock /
  msvcrt, deadline-bounded — mirrors the upstream kanban convention),
  plus an in-process per-path memo; two Hermes processes cannot race
  through schema migration, and a wedged lock holder cannot block
  startup forever.
- Tests: ``backend/tests/test_sqlite_hardening.py`` — 17 tests covering
  connection lifecycle, rollback, self-heal, 8-thread concurrent access,
  configuration (WAL/fallback/busy timeout/foreign keys/in-memory),
  migration idempotency, atomic failure recovery, sentinel recovery,
  newer-schema guard, and true cross-process concurrent initialization
  (4 real subprocesses).

**Next: S7.U1D-C — API scope + authorization enforcement.**

### S7.U1D-A — Profile-Scoped Backend Runtime Ownership

The Workspace backend no longer pins to the first profile a process sees.

- **`WorkspaceRuntime`** (`backend/runtime.py`) owns every
  profile-sensitive component — `DatabaseManager(home/"workspace.db")`,
  storage, authorization, resource limits, sandbox, audit logger, and all
  services — for ONE effective Hermes home.
- **Effective-home lookup at call time.** `get_workspace_runtime()`
  resolves `get_hermes_home()` per call (context-local override aware via
  `set_hermes_home_override`), then returns the cached runtime keyed by
  the normalized home. A single process serving multiple profiles gets
  one runtime per profile; normal Desktop pooled backends (one profile per
  process) hold a single entry. No import-time HERMES_HOME capture.
- **Lifecycle.** Lazy per-home creation; per-home reuse; explicit
  `close()`; `reset_workspace_runtimes()` for tests/shutdown; `atexit`
  cleanup. REST contracts unchanged.
- **Audit isolation.** Each runtime's `AuditLogger` writes to
  `<home>/logs/audit.log` for its own home — profile A never writes audit
  state into profile B.
- Tests: `backend/tests/test_runtime_isolation.py` proves same-process
  A → B → A isolation against real temporary HERMES_HOME dirs.

**Next: S7.U1D-B — SQLite lifecycle/concurrency (connection ownership is
unchanged in U1D-A).**

### S7.U1C — Desktop Plugin SDK & Runtime Adaptation

The Workspace desktop plugin was migrated to the CURRENT upstream Hermes
Desktop plugin boundary (`@hermes/plugin-sdk`). Commit: see
`git log --oneline -3` on `workspace-integration`.

- **SDK adoption.** All Workspace imports now come from `@hermes/plugin-sdk`
  (+ react) or plugin-local relative modules. No `@/` application internals,
  no direct `@tanstack/*` / `nanostores` / `@nanostores/react` imports. The
  plugin directory was flattened to the current upstream convention (single
  directory, like the Kanban plugin) because the plugin fence forbids
  `../` relative imports.
- **Session identity.** `host.state.activeSessionId` is a VOLATILE runtime
  identity and is never sent as `session_id` — `SessionDB.get_session()`
  keys on the durable stored id. Scope resolution uses only the sanctioned
  `host.state.cwd` + `host.state.profile`.
- **Scope authority.** The backend `ProjectScopeResolver` remains the
  authority (`POST /v1/scope/resolve` with `cwd`); unresolved scope never
  widens to a global query. Added explicit `unavailable` state + bounded
  Retry; backfill now refreshes the cached scope and invalidates Workspace
  queries so partial → scoped transitions land.
- **REST transport.** All mutation wrappers pass plain object bodies —
  `ctx.rest()` serializes them itself (pre-serialized JSON strings were
  double-encoded on the wire).
- **Navigation.** One contributed route (`/workspace`) per the current
  one-segment route contract; all eight surfaces (overview, ADRs, journal,
  roadmaps, tasks, search, analytics, assistant) switch through internal
  navigation in `workspace-shell.tsx`.
- **State re-home.** Request-shaped data lives in React Query keyed by the
  effective workspace; module atoms are pure UI state only. Assistant
  conversation state resets when the effective workspace changes.
- **Component contracts.** `ConfirmDialog` callers use the current
  `onClose` contract; local `MiniBar` gained the label it was passed;
  `SearchField` uses `containerClassName`; Button/Badge variants updated.
- **Graph scope.** Search/graph helpers transmit the effective workspace
  scope on every call — no unscoped fallback.
- **Verification.** `npm run typecheck` ✓ (0 errors), `npm run lint` ✓
  (0 errors in the plugin), `npm run test:ui` ✓ (35/35 Workspace tests;
  3322/3323 suite-wide — the single failure is a pre-existing
  timing-sensitive property-fuzz test in `src/lib/markdown-blocks.test.ts`
  that passes in isolation), `npm run build` ✓.
- **Smoke.** Production Electron booted the renderer without exceptions;
  the app then stalls at its interactive first-run setup choice because a
  source checkout has no managed `hermes` runtime (host/environment
  condition; `--no-sandbox` used as the known environment workaround).
  Backend plugin API mounting (incl. `/api/plugins/workspace/`) was
  verified live; the backend is untouched by U1C.

Remaining U1D issues (backend, out of U1C scope): profile-scoped
database/service singletons, SQLite concurrency + migration locking, WAL
fallback conventions, API authorization hardening, approval/audit
alignment with Hermes host primitives, ADR reconciliation hardening,
backup/profile-distribution coverage for `workspace.db`.

**Next: S7.U1D — Workspace backend integration verification.**

### S7.U1A — Upstream Baseline & Workspace Platform Transplant

Transplanted the Workspace Platform onto current `origin/main`
(`cd6585abf`) in an isolated worktree
(`~/Documents/AIProjects/HermesPlatform/repos/hermes-agent-upstream-integration`,
branch `workspace-integration`, transplant commit `1bd012bf6`). Exact tree
restore of the approved path set from checkpoint `5bcd9edee` — 135 files,
+31,994 lines; no historical commits cherry-picked (merge `c7eae8ca0`
excluded); legacy `package-lock.json` changes excluded (origin/main's
lockfile kept). `workspace-plugin@5bcd9edee` + `stash@{0}` untouched.

Backend baseline on modern upstream: **522/522 tests pass** (fresh
`uv sync --extra dev`; pytest 9.1.1) — zero backend adaptation needed.
Frontend baseline was toolchain-blocked on the old host Node; resolved with
Node v22.23.2 / npm v12.0.2 for U1C.

**Next (as of U1A): S7.U1B — Core/API Compatibility.**

### S7.3A — Canonical ADR Reconciliation

Git/file ADRs are canonical. `workspace.db` is the index/projection.

- **Convention:** canonical ADRs live at `<repo>/docs/adr/NNNN-slug.md`
  (identity = stem minus the ordering prefix), optional YAML frontmatter
  (`status`/`category`/`tags`), required `# H1`.
- **Migration 007:** `adrs` gains `canonical_path`, `content_hash`,
  `reconcile_state`, `source`, `last_indexed`, `last_error`; existing rows
  default to `db_legacy`/`workspace_db` (nothing destructive).
- **ADRReconcileService:** discover → parse → classify → refresh projection
  transactionally; states synced / file_new / file_changed / db_legacy /
  missing_file / conflict / invalid; rename self-healing; idempotent.
- **Legacy ADRs:** stay visible as `db_legacy`; explicit, previewable
  materialization writes the canonical file atomically (frontmatter +
  `source: workspace_db` provenance), then promotes the row.
- **CRUD:** canonical ADRs reject DB PUT/DELETE (409) — edits go through
  `PUT /v1/adrs/{id}/file`.
- **API:** `POST /v1/adrs/reconcile`, `GET /v1/adrs/reconcile/status`,
  `POST /v1/adrs/{id}/materialize`, `PUT /v1/adrs/{id}/file`;
  `/graph/stats` + `/graph/shortest-path` now workspace-scoped (403
  unscoped); search results carry `source_type`/`canonical_id`.
- **Security:** `adr.reconcile.read` / `adr.reconcile.write` capabilities;
  per-repo sandbox rooting; scope fail-closed; audit events for
  run/materialize/file-update.
- **Desktop:** state badges, canonical path, Reconcile button, legacy
  materialize UX, canonical-aware editor.
- **Tests:** 522 backend (451 baseline + 71), 15 vitest (8 scope + 7 adr).

Full design: `docs/reverse_engineering/CanonicalADRReconciliation.md`.
**Next: S7.U1B — Core/API Compatibility (after U1A transplant).**

### S7.2R — Repository Recovery & Baseline Restoration

A `hermes update` (2026-08-02) switched the worktree from `workspace-plugin`
to `main`, removing all branch-tracked files. An autostash (`stash@{0}`)
captured the complete uncommitted state including ALL S7.2 scope edits.
Recovery: backed up surviving untracked S7.2 files → switched back to
`workspace-plugin` → applied `stash@{0}` → no reconstruction needed.
Dev environment repaired (`uv sync --extra dev` for pytest, `npm install`
for `react-router-dom`). Verified: 451 backend + 8 vitest + desktop build.
`stash@{0}` preserved. No Hermes Core modifications.

### S7.1 — Memory Architecture Evaluation & Workspace Integration Design

Documentation milestone — no runtime code changed, no Hermes Core files
modified. Full analysis: `docs/reverse_engineering/MemoryArchitectureGapAnalysis.md`.

**Findings.** Hermes already provides layered, production-tested memory:
`MemoryStore` (`MEMORY.md` / `USER.md` curated memory), `SessionDB`
(`state.db`, FTS5 episodic/session authority), one optional external
`MemoryProvider` via `MemoryManager`, and a single active `ContextEngine`
(the built-in `ContextCompressor`). The Workspace plugin is not connected to
any of these: `register()` is a no-op, its assistant is deterministic, and its
search is `LIKE`-based.

**ADOPT before BUILD.** No new memory database, vector store, provider
framework, context engine, or core tool is justified. Session/episodic
memory, curated memory, retrieval, compression, context injection, inspection,
and editing are adopted as-is.

**Workspace ownership boundary.** `workspace.db` is structured engineering
data (workspaces, ADRs, journal, roadmaps, tasks, analytics) — it must NOT
become a second generic Hermes memory database. Canonical authority targets:
Hermes Projects for project identity, Git files for ADR content, Kanban for
task execution. Canonical Workspace structured data is never blindly copied
into generic memory; only explicit, provenance-tagged lessons are promoted.

**Stage 7 architecture direction.** Workspace context will ride the existing
`pre_llm_call` seam (user-message injection + `api_content` sidecar), never
the cached system prompt, and must NOT replace the `ContextEngine`. Project
identity is resolved before context injection.

**Next milestone: S7.U1B — Core/API Compatibility.**

### S7.2 — Project Scope & Authority Alignment

Wires the Hermes Project authority (per-profile `projects.db`, owned by
Hermes Core) to workspace-scoped data (`workspace.db`). Full design:
`docs/reverse_engineering/ProjectScopeAuthorityDesign.md`.

**Identity.** Hermes Project stays canonical. Workspaces keep
`workspace.db` identity. The only coupling is a soft, nullable
`workspaces.hermes_project_id` column (migration `006_project_scope`),
indexed, forward-only, no FK into `projects.db`.

**Resolution.** `ProjectScopeResolver` (`backend/services/scope_resolver.py`)
implements the precedence: explicit mapping → session cwd → session git
root → reverse mapping → `unmapped`/`partial`/`unresolved`. Path identity
uses `projects_db.project_for_path()` (longest-prefix, abspath) directly;
session metadata comes from `SessionDB.get_session()` (`cwd` /
`git_repo_root`). An unresolved scope NEVER falls back to a global query —
the API returns 403 `SCOPE_UNRESOLVED`.

**Enforcement.** Previously-global endpoints (`/tasks`, `/tasks/search`,
`/search`, `/graph`, `/analytics`, `/analytics/trends`,
`/analytics/insights`, `/analytics/export`, `/assistant/*`) now resolve
scope from `session_id` (or an explicit `workspace_id`) and reject
unresolvable requests. Analytics are computed per-workspace.
Get-by-ID endpoints (`/adrs/{id}`, `/journal/{id}`, `/roadmaps/{id}`,
`/tasks/{id}`) gained an optional `workspace_id` membership check that
returns 404 for cross-workspace lookups (no existence leak). Task
reassignment across different project scopes is rejected
(`CROSS_PROJECT_REASSIGNMENT`).

**Mapping surface.** `GET/PUT/DELETE /v1/workspaces/{id}/project`,
`POST /v1/scope/resolve` (diagnostic), `POST /v1/scope/backfill`
(inspection-first: `dry_run` propose → explicit apply; ambiguous /
already-linked cases never mutate). New capabilities
`workspace.scope.read` / `workspace.scope.link` are registered in the
Capability Registry and gated through the existing authz middleware.

**Desktop.** `apps/desktop/src/plugins/workspace/stores/scope.ts` caches
backend resolution keyed on the active session + shared project scope;
pages thread the resolved `workspace_id` and render a scope notice
(`scope-notice.tsx`) with an explicit backfill link button when unmapped.
Queries are gated — an unscoped page never issues a global request.

**Tests.** +62 backend tests (migration, storage mapping, resolver, API
scope/backfill/membership/reassignment, enforcement on analytics and
assistant endpoints) and +8 desktop vitest (scope mapping / query params /
readiness). Baseline `uv run pytest plugins/workspace` was 389; now 451.

### S6.4 — Runtime Security Integration

Wires S6.3 hardening components into actual service execution:

| Integration | Services |
|-------------|----------|
| **ResourceLimiter** | All 5 services — enforces title length, tag count, markdown size, description length, label count, dependency count, comment length |
| **PathSandbox** | WorkspaceService — validates workspace/repository paths against system/hidden deny lists |
| **Audit Events** | Every limit/sandbox violation emits `s6.4.violation.*` audit event before raising exception |
| **Missing Authz Guards** | `TaskService.add_comment` and `TaskService.set_dependencies` now properly gated |

Security components are injected via v1.py singletons alongside the AuthorizationMiddleware — all services share the same ResourceLimiter and PathSandbox instances.

### S6.3 — Security Hardening (Phase 3)

Implements ADR-SEC-007 hardening layers:

| Component | Purpose |
|-----------|---------|
| **Network Isolation** (`security/network_isolation.py`) | URL validation, protocol allow/deny lists, SSRF prevention, private IP detection |
| **Resource Limits** (`security/resource_limits.py`) | Enforce content sizes, tag counts, title lengths, path lengths, dependency counts |
| **Path Sandbox** (`security/sandbox.py`) | Filesystem isolation, system path denial, workspace scoping, hidden directory protection |
| **Prompt Injection Detection** (`security/sanitization.py`) | Enhanced in S6.1; pattern-based injection detection integrated with hardening |

#### Network Isolation

- Validates URLs against allowed protocols (http/https)
- Blocks `file://`, `ftp://`, `gopher://`, `javascript:`, `data:` protocols
- Detects and denies private IPs (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Blocks loopback (127.0.0.0/8, ::1), link-local (169.254.0.0/16), multicast
- Supports custom hostname allow/block lists

#### Resource Limits

| Resource | Default Limit |
|----------|---------------|
| Content size | 10 MB |
| Markdown size | 2 MB |
| Title length | 256 chars |
| Description length | 4096 chars |
| Tag count | 50 |
| Dependency count | 100 |

#### Path Sandbox

- Denied prefixes: `/etc/`, `/usr/`, `/proc/`, `/sys/`, `/dev/`, `/boot/`, `/var/log/`
- Denied hidden dirs: `.ssh/`, `.aws/`, `.gnupg/`, `.docker/`, `.kube/`
- Workspace-scoped path validation with canonicalization
- Configurable per-operation: read, write, delete, execute
- Symlink and hidden file controls

## Configuration

For the desktop plugin (renderer): **no configuration required.** Bundled
desktop plugins are discovered and activated automatically at build time.

For the Python backend plugin:

1. Add `workspace` to `plugins.enabled` in `config.yaml`:
   ```yaml
   plugins:
     enabled:
       - workspace
   ```
   Or run: `hermes plugins enable workspace`

2. Restart the backend (`hermes serve` or restart Hermes Desktop).

## Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| Hermes starts normally | ✓ | No core files modified |
| Workspace appears in navigation | ✓ | sidebar.nav contribution registered |
| Clicking Workspace opens the page | ✓ | routes contribution at /workspace |
| Health endpoint responds | ✓ | GET /api/plugins/workspace/health returns 200 |
| Frontend displays backend status | ✓ | Shows "Connected" or "Not Connected" |
| No Hermes core files modified | ✓ | All changes are in plugin directories |

## No Core Files Modified

The following Hermes core files were **not modified** in any way:

- `electron/main.ts`
- `electron/preload.ts`
- `src/app/` (any file)
- `src/components/` (any file)
- `src/store/` (any file)
- `src/contrib/` (any file)
- `hermes_cli/plugins.py`
- `hermes_cli/web_server.py`
- `run_agent.py`
- `cli.py`
- `model_tools.py`

All changes are confined to the `plugins/workspace/` directory.
