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
│   ├── migrations/              # SQL migrations (001-005)
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
**Next: S7.3B — Kanban Task Authority & Roadmap Linkage.**

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

**Next milestone: S7.3B — Kanban Task Authority & Roadmap Linkage.**

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
