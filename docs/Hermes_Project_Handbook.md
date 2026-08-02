# Hermes Project Handbook

Master document for the Hermes AI Agent project — architecture, milestones, conventions, and handoff.

---

## Project Overview

**Purpose:** Hermes is a personal AI agent that runs the same agent core across a CLI, a messaging gateway (Telegram, Discord, Slack, and ~20 other platforms), a TUI, and an Electron desktop app. It learns across sessions (memory + skills), delegates to subagents, runs scheduled jobs, and drives a real terminal and browser.

**Design Philosophy:** The core is a narrow waist; capability lives at the edges. Extend primarily through plugins and skills, not by growing the core.

**Plugin-First Architecture:** New capabilities arrive as CLI commands + skills, service-gated tools, plugins, or MCP servers — not as core surface. Tools added to core must survive a high bar: every core tool ships on every API call.

---

## Current Repository

- **Path:** `~/Documents/AIProjects/HermesPlatform/repos/hermes-agent`
- **Workspace Plugin:** `plugins/workspace/`
- **Branch:** `workspace-plugin`

---

## Overall Roadmap

### Completed Milestones

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
| S7.2 — Project Scope & Authority Alignment | ✓ | 62 (+8 desktop) |
| S7.2R — Repository Recovery & Baseline Restoration | ✓ | 451 (+8 desktop) |
| S7.3A — Canonical ADR Reconciliation | ✓ | 71 (+7 desktop) |
| S7.U1 — Upstream Hermes Reconciliation (U1A only) | U1A ✓ | 522 backend on modern upstream |

### Current Milestone

**S7.U1 — Upstream Hermes Reconciliation (U1A — Baseline & Workspace Transplant COMPLETE)**

The Workspace Platform was transplanted onto current `origin/main`
(`cd6585abf`) via an isolated integration worktree
(`~/Documents/AIProjects/HermesPlatform/repos/hermes-agent-upstream-integration`,
branch `workspace-integration`). The exact final tree from checkpoint
`5bcd9edee` was restored for the approved path set only
(`plugins/workspace/`, `apps/desktop/src/plugins/workspace/`,
`apps/desktop/docs/reverse_engineering/`,
`docs/Hermes_Project_Handbook.md`, `docs/reverse_engineering/`) — **135
files, +31,994 lines**. The historical upstream merge `c7eae8ca0` was
intentionally excluded (no cherry-pick of any historical commit); the
legacy `package-lock.json` peer-marker changes were intentionally excluded
(origin/main's lockfile preserved). `workspace-plugin@5bcd9edee` and
`stash@{0}` remain untouched as the historical development branch.

Transplant commit: `1bd012bf6` on `workspace-integration` (single commit
directly on `origin/main`; no old Workspace history replayed — verified
via `merge-base --is-ancestor`).

Baseline results on modern upstream: **522/522 backend tests pass** with a
fresh `uv sync --extra dev` environment (pytest 9.1.1, modern lockfile) —
the entire Workspace backend (migrations 001–007, security, scope
resolver, ADR reconciliation) is compatible with modern Core with zero
adaptation. Frontend baseline (vitest + desktop build) is **toolchain
blocked**: modern upstream requires Node >=22.22.0 / npm >=11.17.0; the
machine has Node v20.20.2 — resolved at the start of U1C.

### S7.3A — Canonical ADR Reconciliation (COMPLETED)

Git/file ADRs are now canonical. Workspace stores an index/projection, not
a competing copy of truth. Migration `007_adr_reconciliation.sql` adds
`canonical_path`, `content_hash`, `reconcile_state`, `source`,
`last_indexed`, `last_error` to `adrs` (existing rows default to
`db_legacy`/`workspace_db` — nothing destructive). `ADRReconcileService`
discovers `docs/adr/*.md` under registered repositories (sandbox-rooted),
parses frontmatter + H1, classifies drift (synced / file_new /
file_changed / db_legacy / missing_file / conflict / invalid), refreshes
the projection transactionally (files win; conflicts visible), handles
renames, and materializes legacy DB-only ADRs through an explicit,
previewable operation. Canonical ADR CRUD is guarded (PUT/DELETE → 409;
edits go through `PUT /v1/adrs/{id}/file`). `/graph/stats` and
`/graph/shortest-path` are now workspace-scoped (403 unscoped) — the S7.2
global-aggregate carry-forward is closed. Search results carry
`source_type`/`canonical_id` provenance. New capabilities
`adr.reconcile.read`/`adr.reconcile.write`. Desktop shows state badges,
canonical paths, a Reconcile button, and legacy materialization UX.
Baseline 451 → **522** backend tests; vitest 8 → **15**. Design record:
`docs/reverse_engineering/CanonicalADRReconciliation.md`.

### S7.2R — Repository Recovery & Baseline Restoration (COMPLETED)

A Hermes Desktop update (`hermes update`, 2026-08-02 05:05:21) switched
the working tree from the `workspace-plugin` branch to `main`, removing
all branch-tracked Workspace plugin files from the working directory.
The update created an autostash (`stash@{0}: On workspace-plugin:
hermes-update-autostart-20260802-050521`) that captured the complete
uncommitted state — including all S7.2 scope edits to `models.py`,
`api/v1.py`, `sqlite_storage.py`, `storage/__init__.py`,
`workspace_service.py`, and the desktop ADR/journal pages. The S7.2
**new** files (untracked) — `scope_resolver.py`, scope tests, migration
006, desktop `stores/scope.ts`, `scope-notice.tsx` — survived on disk
because `git checkout main` does not remove untracked files.

**Recovery procedure:**
1. Backed up all surviving untracked workspace files to
   `/tmp/opencode/recovery/` (72 files).
2. Switched back to `workspace-plugin` branch — restored all 34
   branch-tracked backend + 11 desktop M0–M6 files. No conflicts: every
   branch-tracked path was absent from disk.
3. Applied `stash@{0}` — restored the S6.4-era + S7.2 uncommitted work
   (12 tracked files modified: `models.py` +717, `api/v1.py` +1173,
   `sqlite_storage.py` +684, `storage/__init__.py` +189, etc.). The
   stash's untracked-file entries were correctly skipped (they already
   existed on disk with newer S7.2-edited versions).
4. **No reconstruction was needed** — the autostash had captured the
   COMPLETE S7.2 implementation, contrary to the S7.3 PLAN's initial
   assessment (which had grep'd the stash diff incorrectly).
5. Repaired the dev environment: `uv sync --extra dev` restored pytest
   9.0.2; `npm install` restored `react-router-dom` (missing after the
   update's node_modules changes).

**Verification:** 451 backend tests passed (0 failed); 8 vitest scope
tests passed; Desktop production build passed; plugin API imports
(53 routes in plugin_api, 52 in v1); no Hermes Core source modifications
(only `package-lock.json` changed from `npm install`); `stash@{0}`
preserved.

**S7.2 — Project Scope & Authority Alignment (COMPLETED)**

Workspace data now resolves and enforces the Hermes Project authority.
Soft mapping column (`workspaces.hermes_project_id`, migration 006),
`ProjectScopeResolver` (explicit mapping → session cwd → git root →
reverse mapping → partial/unmapped/unresolved; never global), scope
enforcement on all previously-global endpoints, get-by-ID membership
checks (404, no existence leak), cross-project reassignment guard,
backfill with dry-run inspection, `workspace.scope.read` /
`workspace.scope.link` capabilities, and desktop scope store + notice
with explicit link UX. Baseline backend 389 → 451 tests; vitest +8.
Design record: `docs/reverse_engineering/ProjectScopeAuthorityDesign.md`.

### Upcoming Milestones

| Milestone | Purpose |
|-----------|---------|
| S7.U1 — Upstream Hermes Reconciliation | U1A ✓ (transplant + baseline). U1B — Core/API compat · U1C — Desktop plugin SDK/runtime-plugin adaptation (requires Node ≥22.22 toolchain) · U1D — Workspace backend integration verification · U1E — Kanban/task authority reconciliation · U1F — regression/security/upgrade verification |
| S6.5 — Testing & CI | Fuzzing, security scanning, pen test scenarios, security guide (prerequisite for S7.6) |
| S7.3 — Canonical Artifact & Task Reconciliation | Git-first ADRs (S7.3A ✓ on old base), Kanban-first tasks (S7.3B — redesign onto modern project-scoped Kanban) |
| S7.4 — Workspace Context Adapter & Inspector | Bounded context via `pre_llm_call` + preview parity |
| S7.5 — Explicit Memory Promotion & Provenance | Promoted lessons with provenance, no auto-mirroring |
| S7.6 — External Memory Egress & Security Validation | NetworkValidator enforcement at real client boundaries |

---

## Architecture

### High-Level

```
REST API (v1.py, 48 routes)
  → Security Components (singletons, shared across all services):
      → AuthorizationMiddleware (capability gating)
      → ResourceLimiter (size/count/length enforcement)
      → PathSandbox (path validation, system denial)
  → Workspace Services (guarded + limited writes)
  → SQLiteStorage → workspace.db
```

### Security Architecture

```
External Input → Content Labeling → Sanitization → Secret Redaction
                                                    ↓
AuthorizationMiddleware → PolicyEngine → CapabilityRegistry
         ↓
    AuditLogger (ALLOW / DENY / APPROVAL_REQUIRED + violation events)
         ↓
    Runtime Enforcement (integrated in services via S6.4):
    ResourceLimiter → ResourceLimitExceeded + audit
    PathSandbox → InvalidPathError + audit
    NetworkValidator → available for URL validation
```

### Storage Architecture

```
AbstractStorage (ABC)
  └── SQLiteStorage (implementation)
        └── DatabaseManager (SQLite connection pool)
              └── workspace.db (single file, supports SAVEPOINT nesting)
```

### Service Architecture

Each domain has a service class:
- `WorkspaceService` — workspace + repository CRUD
- `ADRService` — architecture decision records CRUD
- `JournalService` — engineering journal CRUD
- `RoadmapService` — roadmaps + milestones CRUD
- `TaskService` — tasks + comments + dependencies CRUD
- `AnalyticsService` — metrics, trends, insights
- `SearchService` — full-text search across entities
- `GraphService` — knowledge graph + relationship queries
- `AssistantService` — AI workspace assistant

All services depend on `AbstractStorage` (never concrete). All services optionally accept `AuthorizationMiddleware`.

---

## Workspace Plugin

### Current Capabilities

- Workspace and repository CRUD
- Architecture Decision Records (CRUD, slug, tags, search)
- Engineering Journal (CRUD, tags, date-based filtering)
- Roadmaps + Milestones (CRUD, progress, reordering)
- Tasks (CRUD, comments, dependencies, circular detection, overdue)
- Analytics (roadmap, task, ADR, journal, trends, auto-insights)
- Knowledge Graph (nodes, edges, shortest path, orphans)
- Global Search (full-text across all entity types)
- AI Workspace Assistant (context-aware chat)
- Content Labeling (origin + trust levels)
- Content Sanitization (null bytes, control chars, ANSI, Unicode, truncation)
- Secret Detection & Redaction (pattern + entropy-based)
- Capability Registry (44 capabilities, 3 tiers)
- Audit Logging (JSON Lines, thread-safe)
- Policy Engine (PolicyDecision: allow/deny/approval)
- Authorization Middleware (single enforcement gate)
- Network Isolation (URL validation, SSRF prevention, private IP blocking)
- Resource Limits (content size, tag count, title/path length enforcement)
- Path Sandbox (system path denial, hidden dir protection, workspace scoping)

### Implemented APIs

48 REST endpoints under `/v1/`:
- Workspaces: health, CRUD
- Repositories: register, list
- ADRs: CRUD + search + tags + categories
- Journal: CRUD + search + tags
- Roadmaps: CRUD
- Milestones: CRUD + reorder
- Tasks: CRUD + search + comments + dependencies + stats
- Analytics: dashboard, trends, insights, export
- Graph: nodes, edges, shortest path, stats
- Search: global full-text
- Assistant: chat, context, suggestions

### Current Test Count

**389 backend tests** (all passing)

---

## Stage 7 — Persistent Intelligence / Context Layer

### Purpose

Stage 7 connects the Workspace plugin's structured engineering data to
Hermes' existing memory/context architecture without building a new memory
subsystem. S7.1 was the architecture and reverse-engineering milestone;
S7.2–S7.6 implement scope alignment, reconciliation, context injection,
memory promotion, and network-egress security in that order.

### S7.1 Major Repository Findings

Hermes already has layered, production-tested memory — no new generic memory
subsystem is needed:

1. **`MemoryStore`** (`tools/memory_tool.py`) — curated persistent memory in
   profile-scoped `MEMORY.md` / `USER.md`, injected as a frozen snapshot into
   the cached system prompt; threat-scanned, bounded, drift-protected, with an
   optional write-approval gate and external-provider mirroring.
2. **`SessionDB`** (`hermes_state.py`) — profile-scoped SQLite `state.db`
   with FTS5, compression lineage (`active`/`compacted` soft state), and a
   byte-exact `api_content` sidecar that keeps prompt-cache prefixes stable.
   This remains the episodic/session authority.
3. **`MemoryProvider` / `MemoryManager`** (`agent/memory_provider.py`,
   `agent/memory_manager.py`) — one optional external provider
   (honcho, hindsight, mem0, holographic, openviking, byterover, retaindb,
   supermemory); prefetch fenced into `<memory-context>` and appended to the
   API-bound user message, never the system prompt.
4. **`ContextEngine`** (`agent/context_engine.py`) — one active engine;
   default is the built-in `ContextCompressor`. It is a compaction/selection
   engine, NOT a composable provider registry.
5. **`session_search`** — FTS5 retrieval over session history with lineage
   dedup and anchored windows.
6. **Starmap / journey** (`agent/learning_graph.py`) — graph over learned
   skills + all curated memory chunks; session history and Workspace data are
   not included.
7. **Workspace plugin** — independent `workspace.db` structured store
   (workspaces, repositories, ADRs, journal, roadmaps, tasks, analytics,
   graph, search, deterministic assistant); `register()` is a no-op; not
   connected to any Hermes memory/context path.

### ADOPT Decisions

- Short-term/session memory — existing transcript + SessionDB.
- Persistent curated memory — MemoryStore.
- Episodic memory — state.db + FTS5 + lineage-aware session_search.
- Semantic memory — existing providers (holographic = local option).
- Context compression — built-in ContextCompressor + ContextEngine lifecycle.
- Memory inspection — session_search, journey, desktop panels.
- Memory editing/deletion — memory tool + journey mutations + provider tools.
- Cross-session persistence — SessionDB + provider lifecycle hooks.
- Provider setup/configuration — `hermes memory setup`, config API, desktop.
- Network safety for Hermes-owned HTTP — `tools.url_safety`.

### EXTEND Decisions

- Hermes Project/CWD resolution into the Workspace plugin (no second project
  identity).
- Workspace storage with explicit Hermes Project mapping.
- Workspace search/graph services with strict project/workspace scoping.
- Existing `pre_llm_call` seam with a bounded Workspace context assembler.
- Workspace labels/sanitization/redaction/audit/limits to the
  Workspace-to-model egress boundary.
- Memory provenance conventions with canonical artifact references.
- Workspace context preview/assistant APIs to share the agent's assembler.
- ADR/task representations toward Git-file-first and Kanban-reference
  ownership.
- Desktop Workspace pages to resolve the active Project instead of
  free-form Workspace IDs.

### BUILD Decisions (integration only — no new memory architecture)

- Workspace→Hermes Project scope resolver. — **built in S7.2** (`ProjectScopeResolver` +
  `workspaces.hermes_project_id` mapping + scope enforcement; see
  `docs/reverse_engineering/ProjectScopeAuthorityDesign.md`)
- Workspace context assembler via the `pre_llm_call` plugin hook seam. — S7.4
- Reconciliation layer for Workspace records vs Projects/Git/Kanban. — S7.3
- Security egress adapter (label → sanitize → redact → fence → audit). — S7.5
- Network enforcement at actual client boundaries — deferred to S7.6, only if
  a network-enabled Workspace/memory boundary exists.

### Stage 7 Architecture

```
Hermes session_id + current CWD
  -> ProjectScopeResolver (projects.db + session metadata)
  -> WorkspaceScopeMapping (project/repository/profile scoping)
  -> WorkspaceContextAssembler (bounded, budgeted)
  -> Workspace security egress (label/sanitize/redact/fence/audit)
  -> pre_llm_call plugin hook
  -> compose_user_api_content() -> api_content sidecar -> model
```

Invariants: SessionDB stays the episodic authority; the Workspace plugin never
replaces `ContextEngine`; the system prompt is never mutated mid-conversation;
canonical Workspace data is never blindly copied into memory; Project identity
is resolved before context injection; one shared assembler serves both agent
injection and desktop preview.

### Stage 7 Milestone Sequence

| Milestone | Purpose | DoD summary |
|-----------|---------|-------------|
| S7.1 | Memory architecture evaluation & integration design | this document + handbook/README; no runtime code |
| S7.2 | Project Scope & Authority Alignment | CWD → Project → Workspace scope; no unscoped reads; legacy behavior compatible — **COMPLETED** |
| S7.3 (NEXT) | Canonical Artifact & Task Reconciliation | Git-first ADRs, Kanban-first tasks, no second task lifecycle |
| S7.4 | Workspace Context Adapter & Inspector | cached system prompt unchanged; scoped/bounded context; preview parity |
| S7.5 | Explicit Memory Promotion & Provenance | provenance-tagged lessons; no auto-mirroring; no stale provider facts |
| S7.6 | External Memory Egress & Security Validation | SSRF/rebinding/redirect rejection at real client boundaries (needs S6.5) |

### Security Implications

- Workspace context reads need Project/session scope, authorization, audit
  with actor identity, and bounded results.
- Model egress needs labeling, boundary markers, injection scanning, and
  secret redaction; redact before `api_content` sidecar persistence.
- Workspace writes need real approval enforcement (currently
  `guard()` returns without raising on approval-required), audit, and limits.
- PathSandbox should be rooted at the Project/Workspace for write/delete.
- Memory promotion must be explicit/approved with provenance; deletion must
  clean up provider mirrors.
- External memory calls need URL validation at the real client boundary
  (S7.6), timeouts, and audit.

### NetworkValidator Disposition

The S6.4 limitation is **explicitly deferred, not dropped**: S7.1 introduces
no outbound network path (context reads `state.db`, `projects.db`,
`workspace.db`, local git only). The Workspace `NetworkValidator` remains a
standalone, tested utility without DNS/redirect/connect-time enforcement;
Hermes core `tools.url_safety` is the stronger primitive to reuse. Resolution
lands in **S7.6 — External Memory Egress and Security Validation**, gated on
S6.5.

---

## Milestone History

### S6.1 — Security Foundation

**Purpose:** Build reusable security building blocks — no enforcement, purely foundational.

**Files added (6):**
- `backend/security/__init__.py`
- `backend/security/models.py`
- `backend/security/labels.py`
- `backend/security/sanitization.py`
- `backend/security/secrets.py`
- `backend/security/capabilities.py`
- `backend/security/audit.py`

**Architecture:** Content labeling, sanitization pipeline, secret redaction, 25-capability registry, audit logging framework.

**Tests added:** 22 (test_security.py)

**Verification:** All security components tested independently; zero Hermes core modifications.

---

### S6.2 — Security Enforcement & Policy Engine

**Purpose:** Make the S6.1 security infrastructure operational. Centralize authorization.

**Files added (4):**
- `backend/security/exceptions.py` — SecurityError, AuthorizationDenied, ApprovalRequired, CapabilityNotFound, PolicyViolation
- `backend/security/policy.py` — PolicyEngine + PolicyDecision
- `backend/security/authorization.py` — AuthorizationMiddleware
- `backend/tests/test_s6_2_security.py` — 56 tests

**Files modified (8):**
- `backend/security/__init__.py` — new exports
- `backend/security/capabilities.py` — 19 workspace domain capabilities added (44 total)
- `backend/security/labels.py` — datetime.utcnow() → datetime.now(UTC)
- `backend/services/workspace_service.py` — optional authz middleware
- `backend/services/adr_service.py` — optional authz middleware
- `backend/services/journal_service.py` — optional authz middleware
- `backend/services/roadmap_service.py` — optional authz middleware
- `backend/services/task_service.py` — optional authz middleware
- `backend/api/v1.py` — shared AuthorizationMiddleware singleton injected into services

**Architecture:** REST API → AuthorizationMiddleware → PolicyEngine → CapabilityRegistry → AuditLogger. Write operations in all services guarded by authz.guard(). Backward-compatible (authz=None preserves old behavior).

**Security model:** 44 capabilities, 3-tiers, custom rules (first-match wins), structured PolicyDecision (never boolean), all decisions audited (ALLOW/DENY/APPROVAL_REQUIRED).

**Tests added:** 56

**Verification:** 302 total tests passing. Zero datetime.utcnow() deprecation warnings. No Hermes core modifications.

**Lessons learned:** Middleware optionality pattern works well for backward compatibility. PolicyDecision factories (allow/deny/require_approval) make tests clean and descriptive.

---

### S6.3 — Security Hardening

**Purpose:** Implement ADR-SEC-007 hardening layers — network isolation, resource limits, and path sandbox.

**Files added (4):**
- `backend/security/network_isolation.py` — NetworkValidator, URL validation, SSRF prevention, private IP detection
- `backend/security/resource_limits.py` — ResourceLimiter, ResourceLimits, ResourceLimitExceeded
- `backend/security/sandbox.py` — PathSandbox, SandboxConfig, PathValidationResult
- `backend/tests/test_s6_3_security.py` — 58 tests

**Files modified (1):**
- `backend/security/__init__.py` — exports for NetworkValidator, ResourceLimiter, PathSandbox and associated types

**Architecture:** Three independent hardening subsystems:
1. Network Isolation — protocol deny-list (file://, ftp://, gopher://, …), IP range classification (loopback, private, link-local, multicast, CGNAT), custom hostname allow/block
2. Resource Limits — centralized ResourceLimits dataclass, ResourceLimiter with per-resource checks (content, markdown, title, description, tags, labels, dependencies), ResourceLimitExceeded exception
3. Path Sandbox — system path denial (/etc/, /proc/, /sys/, …), hidden directory protection (.ssh/, .aws/, …), workspace scoping, operation-level config (read/write/delete/execute), symlink control

**Tests added:** 58

**Verification:** 360 total tests passing. All existing S6.1/S6.2 tests continue to pass. No Hermes core modifications.

**Lessons learned:** IP-based validation is more reliable than DNS-based for SSRF prevention (no DNS rebinding bypass). Resource limits should be a dataclass (not a flat dict) so they can be overridden per-operation with explicit keyword args.

---

### S6.4 — Runtime Security Integration

**Purpose:** Integrate the S6.3 hardening components (ResourceLimiter, PathSandbox) into the actual service execution path so limits are enforced at runtime — not just defined as standalone utilities.

**Files added (1):**
- `backend/tests/test_s6_4_security.py` — 29 tests

**Files modified (6):**
- `backend/services/workspace_service.py` — accepts `limits`, `sandbox`; enforces on create_workspace, register_repository
- `backend/services/adr_service.py` — accepts `limits`; enforces on create_adr, update_adr (title, tags, markdown)
- `backend/services/journal_service.py` — accepts `limits`; enforces on create_entry, update_entry (title, tags, markdown)
- `backend/services/roadmap_service.py` — accepts `limits`; enforces on create_roadmap, create_milestone, reorder_milestones
- `backend/services/task_service.py` — accepts `limits`; enforces on create_task, update_task (title, labels, deps); fixed missing authz guards on `add_comment` and `set_dependencies`
- `backend/api/v1.py` — creates shared ResourceLimiter + PathSandbox singletons; injects into all 5 services

**Architecture:** Each service gains `_check_limit()` helper that calls ResourceLimiter, audits violations via `authz.audit.log()`, and raises `ResourceLimitExceeded`. WorkspaceService additionally validates paths through PathSandbox and audits sandbox violations. All components are optional (`= None`) preserving backward compatibility.

**Runtime enforcement flow:** Service write method → `if self._limits: check()` → `if violation: audit + raise` → if ok: proceed to storage.

**Tests added:** 29

**Verification:** 389 total tests passing. All existing S6.1-S6.3 tests continue to pass. No Hermes core modifications. Pydantic model constraints act as first line of defense; ResourceLimiter provides defense-in-depth with customizable per-operation limits.

**Lessons learned:** Pydantic model constraints (max_length) catch type-level violations before ResourceLimiter; tests must use custom ResourceLimits with tighter bounds than Pydantic to verify enforcement. The `_check_limit()` helper pattern reduces duplication across services while keeping audit integration centralized.

---

### S7.1 — Memory Architecture Evaluation & Workspace Integration Design

**Purpose:** Reverse-engineer the existing Hermes memory, persistence,
retrieval, and context-assembly architecture; classify every required
capability as ADOPT / EXTEND / BUILD; define Workspace↔memory ownership
boundaries; derive the Stage 7 roadmap. Documentation-only milestone — no
runtime code changed, no Hermes Core files modified.

**Files created (1):**
- `docs/reverse_engineering/MemoryArchitectureGapAnalysis.md` — the full
  16-section engineering document (existing architecture, execution flows,
  file inventory, storage mechanisms, lifecycle, context-engine integration,
  Workspace interaction analysis, ADOPT/EXTEND/BUILD matrix, ownership
  boundaries, security analysis, NetworkValidator disposition, proposed
  architecture, milestones, risks, next milestone).

**Files updated (2):**
- `plugins/workspace/README.md` — S7.1 milestone row + brief section
- `docs/Hermes_Project_Handbook.md` — this entry + Stage 7 section

**Architecture decision (final):** Hermes does NOT need another generic
memory subsystem. SessionDB stays the episodic/session authority. Existing
memory infrastructure is ADOPTED. `workspace.db` stays a structured
engineering store, never a second generic memory database. A Workspace
context provider must NOT replace `ContextEngine`; Workspace context rides the
existing `pre_llm_call` user-message injection seam. Canonical Workspace
structured data is never blindly copied into memory. Project identity must be
resolved before context injection (S7.2).

**Verification (executed in BUILD phase):**
- `uv run pytest plugins/workspace` — see Current Status for exact count.
- Desktop frontend — typecheck, lint, UI tests, build — see Current Status
  for exact commands and results.
- `git status` diff limited to the three documented files; pre-existing
  uncommitted S6.1–S6.4 work preserved untouched.

**Remaining limitations carried forward:**

- NetworkValidator not integrated into service runtime — deferred to S7.6,
  explicitly gated on the existence of an actual outbound network boundary.
- Workspace labels/sanitization/redaction not yet applied to search output,
  assistant output, or future context egress.
- Approval-required capabilities return without raising at the service layer
  (guard default); tier-2 operations can proceed.
- Workspace API audit events lack Hermes actor/session identity.
- PathSandbox default has no workspace root and allows reads outside it.
- Workspace assistant conversation cache is process-global (not user-bound).
- Starmap memory mutations bypass the memory tool's threat scan + approval
  gate.
- ADR (Git-file vs DB row) and task (Kanban vs DB table) authority conflicts
  unresolved — S7.3.
- Workspace `workspace.db` not in the quick-snapshot backup set; module-global
  DB/service singletons pin the first profile.
- Provider lifecycle gaps: incomplete `on_session_switch` coverage,
  hindsight buffer flush at session end, retaindb stale-session prefetch,
  profile-isolation inconsistencies (mem0 Qdrant path, supermemory container
  tags, honcho workspace sharing).
- No standalone S6.4 Final Report existed; S6.4 limitations were carried
  forward from handbook/README.

**Recommended next milestone: S7.2 — Project Scope & Authority Alignment.**

---

## Current Status

- **Current milestone:** S7.1 — Memory Architecture Evaluation & Workspace Integration Design (completed)
- **Backend test count:** 389 (all passing) — re-verified in S7.1 BUILD
- **Desktop frontend:** production build passes; typecheck/lint fail on pre-existing uncommitted Workspace plugin files (see below); UI tests 2196 passed / 1 load-timeout / 1 skipped (flaky fuzz test passes in isolation)
- **Repository health:** Backend green; no S7.1 regressions; no Hermes Core modifications
- **Outstanding work:** S7.2 is the next milestone (Project Scope & Authority Alignment)
- **Known limitations:**
  - NetworkValidator is available but not yet integrated into service runtime — explicitly deferred to S7.6 (no outbound network boundary exists yet; S7.1 introduces none)
  - Resource limits are enforced at the Pydantic model layer AND the service layer — Pydantic catches type-level violations first, ResourceLimiter provides configurable defense-in-depth
  - Path sandbox does not enforce mount namespaces (container mode not implemented — opt-in per ADR-SEC-007)
  - Prompt injection detection is pattern-based only — no ML classifier (per ADR-SEC-005 recommendation)
  - Workspace labels/sanitization/redaction not yet applied to search output, assistant output, or future context egress
  - Approval-required capabilities return without raising at the service layer (guard default); tier-2 operations can proceed
  - Workspace API audit events lack Hermes actor/session identity
  - Workspace assistant conversation cache is process-global (not user-bound)
  - Starmap memory mutations bypass the memory tool's threat scan + approval gate
  - ADR (Git-file vs DB row) and task (Kanban vs DB table) authority conflicts unresolved — S7.3
  - Workspace `workspace.db` not in the quick-snapshot backup set; module-global DB/service singletons pin the first profile
  - Provider lifecycle gaps: incomplete `on_session_switch` coverage, hindsight buffer flush at session end, retaindb stale-session prefetch, profile-isolation inconsistencies

### Desktop Frontend Verification Detail (S7.1 BUILD, exact commands)

- `npm run typecheck` — **fails**: 28 `error TS` across 9 files, all in `src/plugins/workspace/**` (pre-existing uncommitted desktop plugin files; e.g. `ConfirmDialogProps` has no `onCancel`, `Button` variant `"secondary"` not in the union). No S7.1 files are involved.
- `npm run lint` — **fails**: 416 problems (323 errors, 93 warnings) across 29 files — 19 `src/plugins/workspace/**` (uncommitted) + 10 tracked-but-clean core files whose errors predate this checkout (lint-rule drift on the `workspace-plugin` branch). None caused by S7.1.
- `npm run test:ui` — **fails with 1 test**: 2196 passed, 1 failed (`src/lib/markdown-blocks.test.ts` property-fuzz test timed out at 30s under full-suite load), 1 skipped. The fuzz test passes in isolation (9.9s), so this is a load-sensitive flake, not a regression.
- `npm run build` — **passes**: vite build (6.89s), electron-main bundle, preload bundle, node-pty staging, `assert-dist-built` OK.
- Workspace plugin import check — **passes**: `from plugins.workspace.dashboard.plugin_api import router`, `from plugins.workspace.backend.security import *`, and the v1 router all import cleanly.

---

## Model Benchmark Metadata (S7.1)

| Field | Plan phase | Build phase |
|---|---|---|
| Model | GPT-5.6 Luna Max (`opencode-go/gpt-5.6-luna`, variant max) | DeepSeek V4 Flash (`opencode-go/deepseek-v4-flash`) |
| Client | OpenCode Desktop | OpenCode Desktop |
| OpenCode version | 1.18.11 | 1.18.11 |
| Usage source | `opencode stats --project "" --models` (project-level, model-attributed) | same |
| Input tokens | 483 (rollup; see note) | 580.3K |
| Output tokens | 122.6K | 26.3K |
| Cache read tokens | 60.2M | 16.6M |
| Cache write tokens | 1.8M | 0 |
| Cost (USD) | $1.6369 | $0.1352 |
| Messages (model-attributed) | 161 | 30 |

**Notes on availability:** Per-session token/cost breakdowns are NOT exposed by
OpenCode's export or stats tooling (message-level usage fields are absent from
session exports). The figures above are **project-level, model-attributed**
aggregates from `opencode stats --project "" --models` over the current
project window — the closest exposed attribution. Execution duration per
session is not exposed; measured sub-task durations are recorded where
relevant (pytest 4.66s; vite build 6.89s; vitest suite 164.51s). Tool-call
counts are not exposed per session; the Build phase performed 1 `write`,
4 `edit`, 1 `todowrite`, and 28 `bash` calls as counted from the session
record. Unavailable values must not be estimated.

---

## Handoff Notes

### Coding Conventions

- Python 3.12+ with `from __future__ import annotations`
- All timestamps use `datetime.now(UTC).isoformat().replace("+00:00", "Z")`
- Services depend on `AbstractStorage` (ABC), never concrete SQLiteStorage
- Security middleware is always optional (`authz: "AuthorizationMiddleware | None" = None`)
- Dataclasses over dicts for structured data (PolicyDecision, NetworkValidationResult, LimitCheckResult, PathValidationResult)
- Never return booleans from security decisions — use structured result objects with reason fields
- Test files at `<module>/tests/` matching `test_*.py` pattern
- Exception hierarchy under `WorkspaceError` for domain errors, `SecurityError` for security errors
- `TYPE_CHECKING` blocks for optional type imports to avoid circular dependencies

### Architecture Conventions

- Plugin lives entirely in `plugins/workspace/` — no core modifications
- REST API layer (v1.py) creates and injects shared singletons (AuthorizationMiddleware)
- Service constructors accept storage + optional authz
- Write operations guarded; reads unguarded (audit-only pattern)
- Policy engine evaluates via first-match-wins custom rules → registry defaults → default allow/deny
- Audit logging is always-on for authorization decisions; never silently bypassed

### Documentation Conventions

- Two mandatory docs: `plugins/workspace/README.md` + `docs/Hermes_Project_Handbook.md`
- README: folder structure, security architecture, milestones, configuration, acceptance criteria
- Handbook: project overview, roadmap, architecture, milestone history, handoff notes
- Update both documents after every completed milestone

### Testing Expectations

- 389 tests minimum (current baseline, re-verified in S7.1 BUILD)
- Run: `uv run pytest plugins/workspace` (canonical; also works:
  `python -m pytest plugins/workspace/backend/tests/ -q`)
- Test categories: policy engine, authorization middleware, capability enforcement, network isolation, resource limits, path sandbox, service integration, regression
- New security modules need their own test file following `test_s6_N_security.py` convention
- Shared fixtures: `conftest.py` provides `storage`, `temp_db`, `temp_git_repo`

### Quality Gates

- All 389 tests must pass
- No `datetime.utcnow()` deprecation warnings
- No Hermes core modifications
- Plugin API imports cleanly: `from plugins.workspace.dashboard.plugin_api import router`
- All security modules import cleanly: `from plugins.workspace.backend.security import *`

### Repository Layout

```
plugins/workspace/
├── backend/
│   ├── database.py, models.py
│   ├── storage/ (AbstractStorage ABC + SQLiteStorage)
│   ├── services/ (one service per domain)
│   ├── api/ (v1.py — 48 REST endpoints)
│   ├── migrations/ (001-005 SQL)
│   ├── security/ (exceptions, models, labels, sanitization, secrets,
│   │              capabilities, audit, policy, authorization,
│   │              network_isolation, resource_limits, sandbox)
│   └── tests/ (test_*.py files, conftest.py)
├── dashboard/ (plugin_api.py, manifest.json)
└── docs/security/ (10 ADR documents)
```

### Stage 7 Reference Docs

- `docs/reverse_engineering/MemoryArchitectureGapAnalysis.md` — the S7.1
  engineering document: existing Hermes memory architecture, execution flows,
  ADOPT/EXTEND/BUILD matrix, Workspace ownership boundaries, proposed Stage 7
  architecture and milestones. Read this before any Stage 7 implementation
  work; it is the authoritative successor to the plan-phase findings.
