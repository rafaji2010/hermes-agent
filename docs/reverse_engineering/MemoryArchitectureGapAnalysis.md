# Hermes Memory Architecture — Gap Analysis & Workspace Integration Design

> **Milestone:** S7.1 — Memory Architecture Evaluation & Workspace Integration Design
> **Status:** Complete (documentation milestone — no runtime code changed)
> **Scope:** Reverse-engineering of the existing Hermes memory/context/persistence
> architecture, ADOPT/EXTEND/BUILD decisions for the Workspace plugin, Workspace
> ownership boundaries, and the Stage 7 roadmap.
> **Constraint honored:** Hermes Core was not modified. No Workspace runtime
> source files were modified. No database, memory provider, vector store, or
> context engine was created.

---

## 1. Executive Summary

Hermes already contains a complete, layered memory architecture. It does **not**
need another generic memory subsystem, and Stage 7 must not build one.

The reverse-engineering found four distinct, production-tested persistence
planes, each with a narrow, deliberate job:

1. **Curated persistent memory** — profile-scoped `MEMORY.md` / `USER.md`,
   managed by `MemoryStore` and injected into the cached system prompt as a
   frozen snapshot.
2. **Conversation/session history** — profile-scoped SQLite `state.db`
   (`SessionDB`) with FTS5 full-text search, compression lineage, and durable
   byte-exact `api_content` replay. This is the episodic/session authority.
3. **One optional external memory provider** — selected by `memory.provider`,
   orchestrated by `MemoryManager` behind the `MemoryProvider` ABC
   (honcho, hindsight, mem0, holographic, openviking, byterover, retaindb,
   supermemory). Only one external provider is active at a time.
4. **A pluggable context engine** — one active `ContextEngine` (default: the
   built-in `ContextCompressor`), responsible for compaction and optionally
   per-request context *selection*. It is **not** a composable collection of
   context providers.

The Workspace plugin is a dashboard/backend plugin with its own
`workspace.db` (workspaces, repositories, ADRs, journal, roadmaps, tasks) plus
security utilities built in Stage 6 (S6.1–S6.4). Today it is **not connected**
to the agent's memory, retrieval, or context-assembly paths: its `register()`
is a no-op, its assistant is deterministic (no LLM), and its search is
`LIKE`-based, not FTS5.

The core conclusion of this milestone is **ADOPT before BUILD**:

- Session/episodic memory, curated memory, retrieval, compression, context
  injection, inspection, and editing are all **ADOPT**ed as-is.
- The only genuinely missing pieces are **integration components**: a
  Workspace→Hermes Project scope resolver, a Workspace context assembler wired
  through the existing `pre_llm_call` seam, a reconciliation layer for
  Workspace-vs-Project/Git/Kanban authority, and a security egress adapter.
- A Workspace context provider must **not** replace the `ContextEngine`; it
  must ride the existing per-turn user-message injection path so the cached
  system prompt stays byte-stable and prompt caching is preserved.

The next milestone is **S7.2 — Project Scope and Authority Alignment**:
resolving Hermes Project identity and canonical ownership before any Workspace
data reaches the agent.

---

## 2. Existing Hermes Memory Architecture

### 2.1 Curated persistent memory (built-in)

- **Implementation:** `tools/memory_tool.py` (`MemoryStore`), initialized in
  `agent/agent_init.py` (~lines 1598–1626), injected by
  `agent/system_prompt.py` (~lines 497–518).
- **Storage:** `$HERMES_HOME/memories/MEMORY.md` (agent notes) and
  `$HERMES_HOME/memories/USER.md` (user profile), `§`-delimited entries with
  per-file character budgets (defaults 2200 / 1375).
- **Read path:** entries are loaded once at agent init into a **frozen
  system-prompt snapshot**; the live entry list is maintained separately for
  tool responses. The system prompt is rebuilt only at compression or explicit
  invalidation (`agent/system_prompt.py:571-580`).
- **Write path:** `memory` tool actions `add` / `replace` / `remove`, or an
  atomic `operations` batch; file locking + atomic rename protect concurrent
  writers; drift detection refuses to clobber externally-modified files.
- **Security:** every write and every snapshot build runs the shared
  threat-pattern scanner (`tools/threat_patterns.py`, strict scope); flagged
  entries are replaced by `[BLOCKED: …]` placeholders in the snapshot while
  the raw text stays inspectable. Optional write approval gates writes
  through `tools/write_approval.py` + `/memory pending|approve|reject`.
- **Mirroring:** successful built-in memory writes are mirrored to the
  external provider via `MemoryManager.notify_memory_tool_write()`
  (`agent/memory_manager.py:1073-1128`), called from the tool executors
  (`agent/tool_executor.py:1320-1345`,
  `agent/agent_runtime_helpers.py:2581-2606`).

### 2.2 Session / episodic memory (SessionDB)

- **Implementation:** `hermes_state.py` (`SessionDB`, ~10.7k lines), persisted
  at `$HERMES_HOME/state.db` (`DEFAULT_DB_PATH`, `hermes_state.py:215`).
- **Schema:** `sessions`, `messages` (with `active`/`compacted` soft-state,
  `api_content` sidecar), `session_model_usage`, `gateway_routing`,
  `compression_locks`, `async_delegations`, plus FTS5 virtual tables
  (`messages_fts`, trigram/CJK variants) — `hermes_state.py:1046-1283`.
- **Write path:** `AIAgent._flush_messages_to_session_db()`
  (`run_agent.py:1865-2111`) appends turn messages with intrinsic
  `_db_persisted` markers; the user row is written once per turn after
  prefetch/plugin injection so the `api_content` sidecar carries the exact
  bytes sent to the API (`agent/turn_context.py:1142-1197`).
- **Read path:** `get_messages_as_conversation()` replays live rows
  (`active=1`); compaction archives pre-compaction rows
  (`active=0, compacted=1`) that remain FTS-searchable and recoverable
  (`archive_and_compact`, `hermes_state.py:6588-6638`).
- **Retrieval:** FTS5 with BM25 ranking, lineage dedup, anchored windows and
  session bookends via `tools/session_search_tool.py` (`session_search` tool,
  `_discover` at line 626). Sources `subagent`/`tool` are hidden; cron rows are
  demoted.
- **Lifecycle:** session rows are created at first turn
  (`run_agent.py:607-639`), ended at real boundaries (CLI exit, `/reset`,
  gateway expiry), and are safe against WAL-reset and malformed-schema cases
  with in-place FTS rebuild paths.

### 2.3 External memory providers (MemoryManager + MemoryProvider ABC)

- **Contract:** `agent/memory_provider.py` — `is_available()`, `initialize()`,
  `system_prompt_block()`, `prefetch()`/`queue_prefetch()`, `sync_turn()`,
  `get_tool_schemas()`/`handle_tool_call()`, `shutdown()`, plus optional
  `on_turn_start`, `on_session_end`, `on_session_switch`, `on_pre_compress`,
  `on_memory_write`, `on_delegation`, `backup_paths()`.
- **Manager:** `agent/memory_manager.py` — one external provider at a time;
  prefetch bounded to 8s on a daemon thread; turn sync serialized on one
  background worker; bounded shutdown drain.
- **Discovery:** `plugins/memory/__init__.py` — bundled
  `plugins/memory/<name>/` first, then `$HERMES_HOME/plugins/<name>/`;
  user plugins of the same name do not override bundled ones here (bundled
  wins).
- **Activation:** `memory.provider` in `config.yaml`
  (`hermes_cli/config.py:2351-2355`); init in `agent/agent_init.py:1630-1699`
  — note `agent_workspace` is hard-coded to `"hermes"` (line 1686), **not**
  the Workspace plugin's `workspace_id`.
- **Injection:** provider `prefetch()` text is fenced into a
  `<memory-context>` block and appended to the API-bound user message, never
  the system prompt (`agent/turn_context.py:52-84`,
  `agent/memory_manager.py:347-361`).
- **Provider storage shapes:** cloud/self-hosted APIs (honcho, supermemory,
  retaindb, hindsight-cloud), local daemons (hindsight embedded PostgreSQL,
  openviking server), local SQLite (holographic `memory_store.db` with FTS5 +
  HRR vectors + trust scoring), CLI-owned trees (byterover `brv`), and
  in-process OSS (mem0 with Qdrant/PGVector). Hermes itself performs **no
  core embeddings**.

### 2.4 Context engine (compression + selection)

- **Contract:** `agent/context_engine.py` — `ContextEngine` ABC with
  `update_from_response()`, `should_compress()`, `compress()`,
  `select_context()` (per-request selection), `on_turn_complete()`
  (per-turn observation), tool schemas, and session lifecycle hooks.
- **Selection:** `context.engine` in `config.yaml` (default `"compressor"`);
  plugin engines load from `plugins/context_engine/<name>/`
  (`plugins/context_engine/__init__.py`). Only **one** engine is active; the
  built-in `ContextCompressor` is the default implementation
  (`agent/context_compressor.py`).
- **Cache invariants:** the system prompt is built once per session and cached
  (`agent/system_prompt.py:544-568`); ephemeral context rides the API copy of
  the user message and is persisted as `api_content` for byte-stable replay
  (`agent/turn_context.py:52-84`, `agent/conversation_loop.py:1200-1255`);
  `select_context()` may replace the request list but only per-request.
- **Secret handling:** `sanitize_memory_context()` runs
  `redact_sensitive_text(force=True)` before provider context reaches a
  compression handoff (`agent/context_engine.py:40-53`).

### 2.5 Learning graph / Starmap / journey

- `agent/learning_graph.py` builds a graph from learned (non-base) skills and
  **all** `MEMORY.md`/`USER.md` chunks (lexical token-overlap edges). It does
  not include session history, external provider memories, or Workspace data.
- Surfaces: `hermes journey` CLI, TUI `/journey` overlay, desktop Starmap
  (`apps/desktop/src/app/starmap/`), REST `/api/learning/graph`.
- `agent/learning_mutations.py` implements node edit/delete; note it rewrites
  memory files directly and bypasses the memory tool's threat scanning and
  write-approval gate (a documented gap, carried forward).

### 2.6 Background memory/skill review

- `agent/background_review.py` forks a quiet agent every
  `memory.nudge_interval` turns with `skip_memory=True`, a
  memory/skills-only tool whitelist, `_persist_disabled=True` (no state.db
  writes), and a shared cached system prompt for cache warmth. Its built-in
  memory writes are **not** mirrored to the external provider (documented
  gap).

### 2.7 Memory CLI / desktop UI

- CLI: `hermes memory setup|status|off|reset`
  (`hermes_cli/main.py`, `hermes_cli/memory_setup.py`,
  `hermes_cli/subcommands/memory.py`), `/memory pending|approve|reject`
  (`hermes_cli/cli_commands_mixin.py:1598-1629`), provider CLIs
  (e.g. `hermes honcho …`).
- Desktop: Settings → memory provider dropdown + provider config panels +
  OAuth connect (`apps/desktop/src/app/settings/memory/`), command-center
  memory file sizes/reset (`src/app/command-center/maintenance.tsx`), Starmap
  learning graph panel.

---

## 3. Important Execution Flows

### 3.1 Agent initialization (`agent/agent_init.py`)

```text
AIAgent.__init__ (run_agent.py)
  -> agent/agent_init.py
      -> SessionDB/state.db init + session row bookkeeping
      -> load MEMORY.md / USER.md into MemoryStore (frozen snapshot)
      -> optional: MemoryManager + one MemoryProvider (memory.provider)
           -> initialize_all(session_id, platform, hermes_home, ...)
           -> agent_workspace = "hermes" (hard-coded)
      -> inject provider tools into the agent tool surface
      -> select one ContextEngine (config: context.engine)
      -> build + cache the system prompt (stable/context/volatile tiers)
```

### 3.2 Per-turn context assembly (`agent/turn_context.py`, `agent/conversation_loop.py`)

```text
build_turn_context()
  -> restore conversation history (state.db replay, sanitize_context strip)
  -> restore/build cached system prompt (never mutated mid-session)
  -> preflight compression (if over threshold)
  -> plugin hook pre_llm_call -> plugin context strings
  -> external memory prefetch_all(query) -> fenced <memory-context> block
  -> compose_user_api_content(clean, prefetch, plugin_context)
       -> stamped onto the user message as api_content sidecar
       -> persisted in state.db so replay reproduces the exact sent bytes
  -> API-bound message build: system + history + user (with sidecar)
  -> optional ContextEngine.select_context() (request-only replacement)
  -> sanitizers (orphaned tools, role normalization, whitespace)
  -> provider request
```

Key property: **everything ephemeral goes into the API copy of the user
message**; the stored transcript content stays clean and the cached system
prompt stays byte-identical.

### 3.3 Turn finalization (`agent/turn_finalizer.py`)

```text
finalize_turn()
  -> persist final transcript to state.db
  -> context engine on_turn_complete() observation
  -> _sync_external_memory_for_turn(): MemoryManager.sync_all + queue_prefetch_all
       (skipped when interrupted)
  -> optional background memory/skill review fork
```

`on_session_end()` on providers runs only at real session boundaries
(CLI exit, `/reset`, gateway expiry/eviction), via
`run_agent.py:3669-3719` (`shutdown_memory_provider`,
`commit_memory_session`).

### 3.4 Workspace plugin flow (current, unconnected)

```text
Dashboard HTTP auth (web_server.py token/session)
  -> dashboard plugin router (plugin_api.py -> backend/api/v1.py)
  -> module-level singletons:
       AuthorizationMiddleware + CapabilityRegistry + AuditLogger
       ResourceLimiter, PathSandbox
  -> service layer (guarded writes; reads unguarded)
  -> AbstractStorage -> SQLiteStorage -> workspace.db
```

The Workspace `register(ctx)` entry point
(`plugins/workspace/__init__.py:29-36`) is a log-only no-op: it registers no
hooks, tools, memory provider, or context integration. Workspace data is
therefore not injected into turns, not searchable via `session_search`, not
mirrored to any memory provider, and not visible in the Starmap.

### 3.5 Where a future Workspace context integration participates

```text
Hermes session_id + current CWD
  -> ProjectScopeResolver (projects.db project_for_path + session metadata)
  -> Workspace scope mapping (workspace_id, profile, repository)
  -> WorkspaceContextAssembler (bounded, scoped, budgeted)
  -> Workspace security egress (label -> sanitize -> redact -> fence -> audit)
  -> pre_llm_call plugin hook (existing seam)
  -> compose_user_api_content -> api_content sidecar -> model
```

External memory stays on its own parallel path (MemoryManager prefetch →
`<memory-context>` block → same API-bound user message). The Workspace plugin
must **not** call a hypothetical generic provider `search()` API — the
`MemoryProvider` ABC exposes no such method, and only one external provider
may be active.

---

## 4. File / Module Inventory

### 4.1 Hermes core (read-only — not modified)

| Area | Files |
|---|---|
| Curated memory | `tools/memory_tool.py`, `tools/threat_patterns.py`, `tools/write_approval.py`, `tools/skill_usage.py` |
| Memory init | `agent/agent_init.py` (memory + provider + engine wiring) |
| System prompt | `agent/system_prompt.py` (stable/context/volatile tiers, cache) |
| Memory orchestration | `agent/memory_manager.py`, `agent/memory_provider.py` |
| Memory providers | `plugins/memory/{honcho,hindsight,mem0,holographic,openviking,byterover,retaindb,supermemory}/` |
| Sessions | `hermes_state.py` (SessionDB, FTS5), `gateway/session.py`, `docs/session-lifecycle.md` |
| Session retrieval | `tools/session_search_tool.py` |
| Turn context | `agent/turn_context.py`, `agent/conversation_loop.py`, `agent/turn_finalizer.py` |
| Context engine | `agent/context_engine.py`, `agent/context_compressor.py`, `plugins/context_engine/__init__.py` |
| Context references | `agent/context_references.py` (`@file:@url:` expansion, `allowed_root`) |
| Learning graph | `agent/learning_graph.py`, `agent/learning_mutations.py`, `hermes_cli/journey.py` |
| Background review | `agent/background_review.py` |
| Redaction | `agent/redact.py` (`redact_sensitive_text`), `agent/context_engine.py` (`sanitize_memory_context`) |
| URL safety (core) | `tools/url_safety.py` (`is_safe_url`, `async_is_safe_url`, safe HTTP clients) |
| Projects | `hermes_cli/projects_db.py` (`projects.db`, `project_for_path`) |
| Memory CLI | `hermes_cli/main.py`, `hermes_cli/memory_setup.py`, `hermes_cli/subcommands/memory.py`, `hermes_cli/write_approval_commands.py`, `hermes_cli/cli_commands_mixin.py` |
| Memory REST | `hermes_cli/web_server.py` (`/api/memory`, `/api/memory/provider`, `/api/memory/reset`, `/api/learning/graph`) |
| Config | `hermes_cli/config.py` (`memory:` and `context:` sections, `DEFAULT_CONFIG`) |
| Kanban | `hermes_cli/kanban_db.py`, `tools/kanban_tools.py` (existing task authority) |

### 4.2 Workspace plugin (current implementation — not modified in S7.1)

| Area | Files |
|---|---|
| Manifest/entry | `plugins/workspace/plugin.yaml`, `__init__.py` |
| Dashboard API | `plugins/workspace/dashboard/manifest.json`, `plugin_api.py` |
| REST v1 | `plugins/workspace/backend/api/v1.py` (48 routes) |
| Models | `plugins/workspace/backend/models.py` |
| Database | `plugins/workspace/backend/database.py` (module-global singleton, `workspace.db`) |
| Migrations | `plugins/workspace/backend/migrations/001..005` |
| Storage | `plugins/workspace/backend/storage/` (AbstractStorage ABC + SQLiteStorage) |
| Services | `plugins/workspace/backend/services/` (workspace, repository, adr, journal, roadmap, task, analytics, graph, search, assistant) |
| Security | `plugins/workspace/backend/security/` (labels, sanitization, secrets, capabilities, audit, policy, authorization, network_isolation, resource_limits, sandbox) |
| Tests | `plugins/workspace/backend/tests/` (389 tests) |
| Docs | `plugins/workspace/docs/security/` (10 ADR docs, status: Proposed) |
| Desktop renderer | `apps/desktop/src/plugins/workspace/` (routes + nanostores + lib) |

### 4.3 Design documents (aspirational, not current behavior)

- `apps/desktop/docs/reverse_engineering/WorkspacePluginDesign.md` — proposes
  `WorkspaceContextProvider` as a context-engine component, Git-first ADRs,
  Kanban-first tasks, sprints, `context_snapshots` — **not implemented**.
- `apps/desktop/docs/reverse_engineering/WorkspaceLayerGapAnalysis.md` —
  earlier ADOPT/EXTEND gap analysis; largely confirmed by this milestone.
- `docs/design/profile-builder.md` — dashboard-native profile creation design
  (status: proposal).
- `docs/security/network-egress-isolation.md` — Docker egress isolation guide
  (infrastructure-level, separate from Workspace NetworkValidator).

---

## 5. Storage Mechanisms

| Data | Location | Mechanism | Retrieval |
|---|---|---|---|
| Curated memory | `$HERMES_HOME/memories/{MEMORY,USER}.md` | `§`-delimited text files, atomic rename, file locks | Frozen snapshot into cached system prompt |
| Sessions/transcripts | `$HERMES_HOME/state.db` | SQLite WAL + application-level retry, FTS5 | Replay (`active=1`), FTS5 search, anchored windows, lineage |
| Compression archive | same `state.db` | `active=0, compacted=1` rows | FTS-searchable, recoverable |
| Holographic facts | `$HERMES_HOME/memory_store.db` | SQLite FTS5 + HRR phase vectors + trust scores | Hybrid FTS5/Jaccard/HRR retrieval |
| RetainDB | Remote API + `$HERMES_HOME/retaindb_queue.db` | Cloud vector/BM25/rerank; local durable write-behind queue | API search/context |
| Hindsight | Cloud API / local embedded PostgreSQL / external server | Observations + entity graph | recall (facts/observations), reflect (LLM synthesis) |
| Mem0 | Cloud / self-hosted server / Qdrant or PGVector (OSS) | LLM fact extraction + semantic store | `mem0_search` etc. |
| Honcho | Cloud / self-hosted | Sessions, peers, representations, conclusions | hybrid semantic+keyword, dialectic |
| OpenViking | OpenViking server | `viking://` hierarchical knowledge base | tiered semantic search/read |
| Supermemory | Hosted / self-hosted API | containers + conversations | profile + semantic search |
| ByteRover | `$HERMES_HOME/byterover/` via `brv` CLI | hierarchical knowledge tree | fuzzy → LLM-driven retrieval |
| Workspace records | `$HERMES_HOME/workspace.db` | SQLite (WAL, plain tables, no FTS) | SQL + `LIKE` matching; in-memory graph rebuild |
| Hermes Projects | `$HERMES_HOME/projects.db` | SQLite (projects, folders, discovered repos) | `project_for_path()` longest-prefix match |
| Starmap | derived (memory files + skill dirs) | in-memory graph | lexical token overlap |
| Kanban | `~/.hermes/kanban/…` (root-anchored) | SQLite board DB | kanban tools/CLI |

`workspace.db` is a **structured engineering store**, not a memory database.
It must not become a second generic Hermes memory database, and it must not
duplicate canonical Hermes records.

---

## 6. Memory Lifecycle

### 6.1 Built-in curated memory

- **Load:** at agent init; snapshot frozen for the session
  (`load_from_disk()`).
- **Write:** `memory` tool (add/replace/remove/batch) → threat scan → optional
  approval gate → locked read-modify-write → atomic rename.
- **Mirror:** committed writes fan out to the external provider via
  `notify_memory_tool_write()`.
- **Refresh into prompt:** only at next session start or compression-time
  system-prompt invalidation (`invalidate_system_prompt()`).
- **Edit/delete:** also via journey mutations (bypasses scanning/gate — gap)
  and `/memory pending|approve|reject`.

### 6.2 External providers

- `initialize(session_id, …)` at agent start; `on_session_switch()` on
  `/resume|/branch|/reset|/new|compression` (only hindsight/openviking/
  supermemory implement it); `sync_turn()` after each completed turn (single
  background worker); `queue_prefetch()` for next turn; `on_pre_compress()`
  before compression; `on_session_end()` + `shutdown()` at real session
  boundaries; `backup_paths()` opt-in for `hermes backup`.
- Documented lifecycle gaps: most providers implement only `add` mirroring
  (replace/remove can leave stale remote facts); gateway soft-eviction can
  drop buffered provider state (supermemory buffers until session end);
  hindsight has no `on_session_end()` flush for `retain_every_n_turns` buffers;
  retaindb `queue_prefetch()` caches `_session_id` and can prefetch against a
  stale session after rotation; background review never mirrors its built-in
  writes to the external provider.

### 6.3 Sessions

- Create on first turn; end on real boundaries with `end_reason`; compression
  either rotates to a child session (legacy) or archives rows in place
  (current default); archived rows remain searchable; rewound rows
  (`active=0, compacted=0`) stay hidden.

### 6.4 Workspace plugin (current)

- `workspace.db` created on first API use; migrations 001–005 applied
  automatically; module-global DB/service singletons pin the **first resolved
  profile** in a long-lived process (a profile-scoping risk to fix in S7.2).

---

## 7. Context-Engine Integration

### 7.1 What the context engine actually is

`ContextEngine` is a single active engine whose primary responsibility is
compaction, with optional per-request `select_context()` and per-turn
`on_turn_complete()` hooks. It is selected via `context.engine`; only one
engine exists at a time; `ctx.register_context_engine()` **replaces** the
built-in compressor. There is no bundled non-compressor engine in
`plugins/context_engine/` today.

### 7.2 Why Workspace must not register as a context engine

- It would replace (not augment) the `ContextCompressor`, removing the
  production compaction lifecycle every session depends on.
- `select_context()` replaces the request message list and can change the
  prompt-cache prefix — wrong tool for additive, evidence-style Workspace
  context.
- The earlier `WorkspacePluginDesign.md` proposal (`ctx.register_context_engine(provider)`)
  does not match the actual contract and is superseded.

### 7.3 The correct seam: `pre_llm_call` + user-message injection

```text
Memory provider prefetch ──┐
                          ├──> compose_user_api_content()
plugin pre_llm_call ──────┘         |
                                    v
                      current user message (API copy)
                                    |
                                    v
                    api_content sidecar (persisted) -> model
```

- `pre_llm_call` hooks return `{"context": "..."}` strings that are appended
  to the API copy of the current user message
  (`agent/turn_context.py:1030-1081`, `hermes_cli/plugins.py:1892-1927`).
- The persisted `api_content` sidecar keeps replay byte-exact
  (`agent/conversation_loop.py:1200-1255`), so the cache prefix is preserved.
- Oversized hook output is spilled to disk via `tools/hook_output_spill.py`.
- The system prompt is never rebuilt mid-conversation (only compression or
  explicit invalidation rebuilds it), which is the single most important
  invariant for Workspace context work.

### 7.4 Recommendation

Build a **WorkspaceContextAssembler** that produces a bounded, scoped,
sanitized markdown block and register it via the Workspace plugin's
`register(ctx)` using `ctx.register_hook("pre_llm_call", …)`. Reuse the same
assembler in the Workspace context preview/assistant endpoint so the desktop
preview matches what is injected into agent turns.

---

## 8. Workspace Interaction Analysis

### 8.1 What the Workspace plugin owns today

- `workspaces` + `repositories` (with git-root detection via
  `git rev-parse`), ADRs (+ content + tags), journal entries (+ tags),
  roadmaps + milestones, tasks (+ labels, comments, dependencies),
  analytics, knowledge graph (in-memory), global search (`LIKE`-based),
  deterministic assistant (keyword routing; 5-minute process-local
  conversation map).
- 48 REST endpoints under `/v1/`, mounted by the dashboard plugin loader.
- Security utilities from S6.1–S6.4 (labels, sanitization, secrets,
  capabilities, audit, policy, authorization middleware, network isolation,
  resource limits, path sandbox) — with **ResourceLimiter and PathSandbox
  enforced in services** (S6.4), while labels/sanitization/redaction/
  NetworkValidator remain standalone utilities.

### 8.2 Known integration gaps (evidence-based)

| Gap | Evidence |
|---|---|
| Workspace data not injected into turns | `plugins/workspace/__init__.py` register() is a no-op |
| Not searchable via `session_search` | separate `workspace.db`, no FTS |
| Not mirrored to memory providers | no hooks registered |
| Not in Starmap | `learning_graph.py` reads only memory files + skills |
| No shared delete/forget semantics | independent stores |
| Hard-coded `agent_workspace = "hermes"` | `agent/agent_init.py:1686` — not the plugin's workspace_id |
| No mapping to Hermes Projects | `workspace.db` has no `project_id`; `projects.db` is the core project authority |
| Unscoped reads | search/analytics/assistant default to global when `workspace_id` empty |
| Assistant not user/session-bound | process-global `_CONVERSATIONS` dict, 5-min TTL |
| Profile pinning | module-global DB/service singletons in `database.py` / `v1.py` |
| ADR authority conflict | design doc says Git files canonical; implementation stores markdown rows |
| Task authority conflict | design doc says Kanban tasks canonical; implementation has its own tasks table |
| Assistant `ChatRequest` carries no Hermes session/profile identity | `models.py:917-923` |
| Desktop pages take free-form Workspace ID | `plugin.tsx` (ADRs/journal pass `workspaceId=""`), assistant/search pages have manual ID inputs |
| `workspace.db` absent from quick-snapshot set | `hermes_cli/backup.py` quick snapshot list (full backup walks HERMES_HOME) |
| WAL without NFS fallback/retry | `plugins/workspace/backend/database.py:93-107` vs `hermes_state` fallback |

### 8.3 Workspace assistant

Rule-based; no LLM; `_build_context()` searches, expands graph, adds up to 5
workspace tasks, uses global analytics; answer handlers match keywords.
**Not** a memory system and **not** agent context. A future Workspace-aware
agent integration should either replace this with the shared assembler +
agent loop or keep it as a thin, scoped, deterministic preview.

---

## 9. ADOPT / EXTEND / BUILD Matrix

| Capability | Decision | Evidence / reasoning |
|---|---|---|
| Short-term/session memory | **ADOPT** | In-memory transcript + `state.db` |
| Persistent curated memory | **ADOPT** | `MemoryStore` (bounded, durable, threat-scanned) |
| Episodic memory | **ADOPT** | `state.db` + FTS5 + lineage-aware `session_search` |
| Semantic memory | **ADOPT** | existing providers (holographic = local option) |
| Project/workspace memory | **EXTEND** | Workspace DB is structured engineering data, not memory; needs scope resolver |
| Retrieval | **ADOPT** | FTS5 session search + provider recall; no new core index |
| Ranking | **ADOPT** | FTS5 BM25; holographic trust weighting; provider-side reranking |
| Context injection | **EXTEND** | `pre_llm_call` seam + `compose_user_api_content`; no system-prompt mutation |
| Consolidation | **ADOPT** | built-in batch ops + compression summarization + background review |
| Deduplication | **ADOPT** | `MemoryStore` dedup; provider-side dedup (e.g. hindsight observations) |
| Forgetting/pruning | **ADOPT** | `remove`/batch ops, provider delete tools, curator archiving, session rewind |
| Provenance | **EXTEND** | extend `on_memory_write` metadata conventions with Project/artifact references |
| Memory inspection | **ADOPT** | `session_search`, `hermes memory status`, journey, desktop panels |
| Memory editing/deletion | **ADOPT** | memory tool, journey mutations, provider tools (gap: journey bypasses scanning) |
| Memory security | **EXTEND** | egress adapter reusing labels/sanitization/redaction/audit/limits |
| Cross-session persistence | **ADOPT** | SessionDB + provider lifecycle hooks |
| Session search across Workspace artifacts | **BUILD** (integration) | Workspace is not in `state.db`; a scoped Workspace search service already exists — extend it, don't add a core tool |
| Workspace→Project scope resolution | **BUILD** (integration) | no equivalent exists in the plugin |
| Workspace context assembler | **BUILD** (integration) | no equivalent exists; must ride `pre_llm_call` |
| Workspace record reconciliation | **BUILD** (integration) | Git-first ADRs, Kanban-first tasks need migration |
| Workspace security egress | **BUILD** (integration) | utilities exist (ADOPT), the egress boundary does not |
| Network enforcement at new client boundaries | **BUILD** (deferred) | S7.6; only if a network-enabled Workspace/memory boundary appears |
| New core memory tool / vector store / provider framework | **— (rejected)** | nothing in the inventory justifies it |

**No** new memory database, vector store, memory provider framework, context
engine, or core tool is proposed.

---

## 10. Workspace / Memory Ownership Boundaries

| Data | Canonical owner | Context behavior | Memory behavior |
|---|---|---|---|
| Project identity, folders, active CWD | Hermes `projects.db` (core) | Include current Project/repo identity in assembled context | Never copy into generic memory |
| Repository files / git history | Filesystem / Git | Concise status when relevant | Never duplicate repo contents |
| ADR content | **Target:** Git-tracked ADR files (`docs/adr/…`); index/metadata in Workspace | Title/status/reference in context; full body fetched on demand | Only explicit derived lessons with reference |
| ADR index/metadata (current) | Workspace `workspace.db` (transitional) | scoped retrieval | no mirroring |
| Engineering journal | Workspace `workspace.db` | small recent summary in context | promote only durable lessons with provenance |
| Roadmaps / milestones | Workspace `workspace.db` | active milestone summary | never mirror |
| Task execution state | **Kanban DB** (existing authority) | relevant task references/status | never copy tables |
| Workspace tasks (current) | Workspace `workspace.db` (transitional duplicate) | do not treat as second task authority | no mirroring |
| Analytics | Workspace-derived projection | on-demand | never persist as memory |
| Search graph | Workspace-derived projection | on-demand relationships | never persist as memory |
| Session transcripts | Hermes `state.db` | existing session/session_search | never duplicate into Workspace memory |
| Stable engineering lessons | Hermes memory | — | allowed, explicit, with provenance |

Rules: **avoid multiple sources of truth**; `workspace.db` must not become a
second generic Hermes memory database; Workspace memory should never be an
automatic bidirectional mirror of Workspace rows; every promoted memory record
should carry `project_id` / artifact type / artifact id / source /
`created_from_session`.

---

## 11. Security Integration Analysis

### 11.1 What Stage 7 must build on (S6.1–S6.4)

`ContentLabel`, sanitization pipeline, secret redaction, `CapabilityRegistry`
(44 capabilities), `PolicyEngine` (structured `PolicyDecision`), 
`AuthorizationMiddleware` (single gate), `AuditLogger` (JSON Lines, always-on
for authz), `ResourceLimiter` (enforced at runtime in services since S6.4),
`PathSandbox` (enforced for workspace/repository paths since S6.4),
`NetworkValidator` (standalone, not integrated).

### 11.2 Required controls for new memory/context capabilities

| Operation | Controls |
|---|---|
| Workspace context read | Project/session scope; authz; audit with actor identity; bounded results |
| Context model egress | label + boundary markers + injection scan + secret redaction (reuse `agent.redact`, Workspace sanitization) |
| Context persistence | redact before `api_content` sidecar; document retention |
| Workspace writes | authz + **real approval enforcement** (see gap) + audit + limits |
| Workspace paths | Project-rooted `PathSandbox` (symlink/hidden-dir controls) |
| Memory promotion | explicit/approved; provenance; audit; no auto full-record mirroring |
| Memory deletion | memory/provider delete semantics + Workspace reference cleanup |
| Search/assistant output | scope enforcement, output limits, sanitization, redaction |
| External memory calls | URL validation at the real client boundary (S7.6) + timeouts + audit |
| Desktop mutation paths | same policy/sanitization path as agent/API mutations |

### 11.3 Documented gaps to carry forward

1. Workspace labels/sanitization/redaction are **not applied** to search
   output, assistant output, or (future) context egress.
2. `AuthorizationMiddleware.guard()` returns approval-required decisions
   **without raising** by default, and services call it with the default, so
   tier-2 (approval) capabilities can proceed at runtime.
3. Workspace API routes do not pass Hermes actor/session identity into audit
   events.
4. Dashboard auth ≠ per-Workspace ownership.
5. `PathSandbox()` default has no workspace root and allows reads outside it.
6. Workspace assistant conversation cache is process-global, not user-bound.
7. Starmap memory mutations bypass the memory tool's threat scan + approval
   gate.
8. `workspace.db` stores raw markdown/task/journal content without an
   egress sanitization bridge; no at-rest encryption.
9. Content-label defaults classify file content as `trusted`, conflicting
   with the ADR's lower-trust stance for retrieved content.
10. Prompt-injection detection remains pattern-based (per ADR-SEC-005).

---

## 12. S6.4 NetworkValidator Limitation Disposition

**Status: explicitly deferred, not silently dropped.**

- S7.1 introduces **no outbound network path**. The proposed context
  integration reads only `state.db`, `projects.db`, `workspace.db`, and local
  Git metadata.
- The Workspace `NetworkValidator`
  (`plugins/workspace/backend/security/network_isolation.py`) remains a
  standalone, tested utility. It validates literal IPs and protocol
  allow/deny lists but does **not** perform DNS resolution, redirect
  validation, or connect-time enforcement.
- Hermes core already provides stronger primitives in `tools/url_safety.py`
  (`is_safe_url` with DNS resolution, always-blocked cloud-metadata floor,
  `async_is_safe_url`, connect-time safe HTTP clients). Any future Workspace
  or memory network client should reuse those rather than reimplementing.
- **Resolution milestone: S7.6 — External Memory Egress and Security
  Validation**, gated on S6.5 (Testing & CI). S7.6 must wire enforcement at
  every new network client boundary **before** any network-enabled Workspace
  ingestion or Workspace-specific provider call ships.

---

## 13. Proposed Stage 7 Architecture

```text
Hermes session_id + current CWD
        |
        v
ProjectScopeResolver
  - SessionDB session metadata (cwd, profile, repo root)
  - projects.db project_for_path() (longest-prefix)
  - active profile identity
        |
        v
WorkspaceScopeMapping (S7.2)
  - workspace <-> project/repository mapping
  - strict profile + repository scoping
        |
        v
WorkspaceContextAssembler (S7.4)
  - active roadmap/milestone summary
  - relevant Kanban task references
  - recent ADR metadata (file-first, S7.3)
  - recent journal summary
  - optional repo health
  - bounded token/character budget (truncation order: journal -> ADRs -> health -> tasks)
        |
        v
Workspace security egress
  - label -> sanitize -> redact -> fence -> audit
        |
        v
pre_llm_call plugin hook
        |
        v
compose_user_api_content() -> api_content sidecar -> model
```

Design constraints (non-negotiable):

- No new memory database, vector store, or core tool.
- SessionDB stays the episodic/session authority.
- The Workspace plugin never replaces `ContextEngine`.
- The system prompt is never mutated mid-conversation.
- Workspace canonical structured data is never blindly copied into memory.
- Project identity is resolved **before** context injection.
- Full artifact details are fetched on demand through Workspace APIs/tools,
  not dumped into the prompt.
- One shared assembler implementation serves both agent injection and
  desktop context preview.

---

## 14. Stage 7 Milestones

### S7.1 — Memory Architecture Evaluation & Workspace Integration Design (this milestone, COMPLETE)

- **Purpose:** reverse-engineer existing Hermes memory/context/persistence;
  ADOPT/EXTEND/BUILD decisions; ownership boundaries; Stage 7 roadmap.
- **Delivery:** this document + handbook/README updates. No runtime code.

### S7.2 — Project Scope and Authority Alignment (NEXT)

- **ADOPT:** `projects.db` / `project_for_path()`, SessionDB metadata,
  Workspace services, Git, Kanban.
- **EXTEND:** Workspace schema/read paths with explicit Project/repository
  mapping; scoped queries; actor/session audit fields; Project-rooted
  PathSandbox.
- **BUILD:** small scope resolver + compatibility migration.
- **DoD:** temp `HERMES_HOME` maps session CWD → one Project → one Workspace
  scope; no unscoped global reads from the agent context path; legacy
  dashboard behavior documented/compatible; 389+ tests green.

### S7.3 — Canonical Artifact and Task Reconciliation

- **ADOPT:** storage ABC, Git, ADR/journal services, Kanban DB/tools.
- **EXTEND:** ADR indexing → Git-file authority; roadmap/milestone → Kanban
  task references; journal → session/project references.
- **BUILD:** reconciliation/migration routines where no equivalent exists.
- **DoD:** ADR indexes rebuildable from canonical files; no second task
  lifecycle; dangling references explicit and tested.

### S7.4 — Workspace Context Adapter and Inspector

- **ADOPT:** `pre_llm_call`, `compose_user_api_content`, `api_content`
  replay, existing redaction + cache invariants.
- **EXTEND:** Workspace labels/sanitization/redaction/limits; scoped search;
  context preview endpoint.
- **BUILD:** `WorkspaceContextAssembler` + hook registration.
- **DoD:** real agent-turn integration proves cached system prompt unchanged,
  context scoped/bounded, sidecar replay byte-exact, failures fail open,
  desktop preview matches injected context.

### S7.5 — Explicit Memory Promotion and Provenance

- **ADOPT:** built-in memory tool, provider write bridge, provider delete
  tools, Starmap inspection.
- **EXTEND:** provenance conventions (project, artifact type/id, source,
  session).
- **BUILD:** only a promotion workflow/skill if a concrete flow requires it.
- **DoD:** promoted lesson points back to canonical Workspace source; no
  auto-mirroring; deletion/replacement leaves no stale provider facts;
  prompt caching intact.

### S7.6 — External Memory Egress and Security Validation

- **ADOPT:** `tools.url_safety`, safe HTTP clients, provider timeouts,
  ResourceLimiter, AuditLogger, S6.5 testing infrastructure.
- **EXTEND:** Workspace NetworkValidator only if retained as public API
  (DNS/redirect/connect-time semantics).
- **BUILD:** enforcement wrappers at every new network client boundary +
  E2E/fuzz coverage.
- **Prerequisite:** S6.5 (Testing & CI) and any S7 milestone introducing
  remote access.
- **DoD:** internal/metadata/private/redirect/rebinding/disallowed-protocol
  paths rejected at the connection boundary; approved endpoints work; all
  decisions audited.

**S6.5 (Testing & CI)** remains outstanding from Stage 6 and is a
prerequisite for S7.6 hardening.

---

## 15. Risks and Open Questions

1. Should Workspace `workspaces` become an alias for Hermes Projects, or a
   mapped domain entity? (Decision needed in S7.2.)
2. Are ADR files or `workspace.db` markdown rows canonical during migration?
3. Should Workspace tasks migrate to Kanban references, or remain a
   separately named planning entity?
4. How long should Workspace context remain in `state.db` via `api_content`
   sidecars (retention)?
5. Should Workspace context reads use a new scoped capability or an existing
   plugin capability?
6. Which actor identity is recorded for CLI/Desktop/gateway/multi-user
   sessions in Workspace audit events?
7. Should direct Starmap memory mutations be routed through the memory tool
   before any Workspace memory promotion ships?
8. External provider inconsistencies: profile isolation (mem0 Qdrant path,
   supermemory container tags, honcho workspace sharing), `backup_paths()`
   coverage, `on_session_switch()` implementation gaps, hindsight
   `retain_every_n_turns` buffer flush, retaindb stale-session prefetch.
9. Workspace module-global DB/service singletons pin the first profile in a
   long-lived process.
10. The 389-test baseline was accepted from the handbook/user verification
    and independently re-run in S7.1 BUILD (see verification record).
11. No standalone S6.4 Final Report exists; handbook/README carry the S6.4
    limitations forward.
12. Worktree contains pre-existing uncommitted S6.1–S6.4 work; S7.1 must not
    disturb it (verified — see repository cleanliness record).

---

## 16. Recommended Immediate Next Milestone

**S7.2 — Project Scope and Authority Alignment.**

Rationale: any context injection built before S7.2 would inherit the current
ambiguities — separate Project/Workspace identities, free-form
`workspace_id`, global fallback reads, unbound assistant state, a hard-coded
`"hermes"` provider workspace name, and conflicting ADR/task authority. S7.2
resolves identity and authority first; S7.3 (reconciliation) and S7.4
(context adapter) then build on a stable base. S7.2 must **not** be started
as part of S7.1.

---

## Verification Record (S7.1 BUILD)

- Backend: `uv run pytest plugins/workspace` — result recorded in the S7.1
  milestone entry of `docs/Hermes_Project_Handbook.md`.
- Desktop: `apps/desktop` package.json scripts inspected; typecheck/lint/UI
  tests/build results recorded in the handbook.
- No runtime source changes; git status diff limited to the three documented
  files (plus pre-existing uncommitted work preserved untouched).
