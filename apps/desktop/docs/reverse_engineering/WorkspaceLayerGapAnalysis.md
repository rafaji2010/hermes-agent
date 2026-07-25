# Workspace Layer — Gap Analysis Against Hermes Desktop

> Reverse-engineering analysis. Read-only. No source code was modified.

---

## 1. Capabilities Hermes Desktop Already Provides

The following Workspace Layer capabilities are **already implemented** in Hermes
Desktop and should be **reused**, not rebuilt.

### A. Context Assembly for AI

Hermes already assembles rich context for every agent turn. The Workspace Layer
should inject into this pipeline rather than building its own.

| Existing Capability | Where | How It Works |
|---------------------|-------|--------------|
| `AGENTS.md` / `CLAUDE.md` loading | `workspace-cwd.ts`, cron `context_from` | Detects and loads per-project instruction files automatically |
| Skills system | `skills/`, `optional-skills/` | Specialized instructions loaded per session; SKILL.md frontmatter with prerequisites, procedures, pitfalls |
| Context engine plugin | `plugins/context_engine/` | Pluggable context assembly pipeline |
| Memory providers | `plugins/memory/` (honcho, mem0, etc.) | Long-term learning across sessions |
| Session resume with history | `session.resume` RPC | Rehydrates full conversation context |
| File attachment (@file refs) | `src/app/chat/composer/attachments.tsx` | Workspace-relative file references resolved by the gateway |

**Verdict:** Do not rebuild context assembly. Extend the existing context engine
plugin or register a new context provider that adds workspace-layer metadata
(roadmap status, sprint assignments, ADRs) to the agent's context.

### B. Project and Repository Primitives

| Existing Capability | Where | How It Works |
|---------------------|-------|--------------|
| Project/folder management | `src/store/projects.ts`, `src/app/chat/sidebar/projects/` | Users add project directories; the app tracks them in session state |
| Git repository scanning | `electron/git-repo-scan.ts` | Finds all git repos under a root directory |
| Git root detection | `electron/git-root.ts` | Given any path, finds the nearest `.git` |
| Git worktree operations | `electron/git-worktree-ops.ts` | List, add, remove worktrees; switch branches |
| Git review operations | `electron/git-review-ops.ts` | Stage, unstage, diff, commit, push, create PR, ship-info |
| Workspace CWD tracking | `electron/workspace-cwd.ts`, `$currentCwd` store | Per-session working directory |
| Branch tracking | `$currentBranch` store | Per-session current git branch |

**Verdict:** Do not rebuild file/git operations. The Workspace Layer should
consume these primitives via the existing IPC bridge. Add new IPC handlers only
for truly new operations (e.g., cross-repo branch comparison, repo health
scoring).

### C. Task Management

| Existing Capability | Where | How It Works |
|---------------------|-------|--------------|
| Kanban board | `plugins/kanban/` | SQLite-backed multi-agent work queue with assignees, blocking, linking, comments |
| Todos (per-session) | `src/store/todos.ts`, `tools/todo_tool.py` | Agent-managed todo list per conversation |
| Cron jobs | `cron/` (Python), `src/app/cron/`, `src/store/cron.ts` | Scheduled recurring agent runs |
| Delegation | `tools/delegate_tool.py` | Subagent spawning (single + batch + background) |

**Verdict:** Do not rebuild task primitives. The existing kanban plugin is a
full task-management backend. The Workspace Layer needs only a **desktop UI**
for it (contributed route) and friction-reducing affordances (context assembly).

### D. Knowledge Persistence

| Existing Capability | Where | How It Works |
|---------------------|-------|--------------|
| Session persistence | `hermes_state.py` (SessionDB) | SQLite with FTS5 full-text search |
| Memory providers | `plugins/memory/` | Honcho, mem0, supermemory, etc. |
| Starmap / learning graph | `src/app/starmap/`, `/api/learning/graph` | Visual knowledge graph from agent-learned facts |
| Curator (skill lifecycle) | `agent/curator.py` | Tracks skill usage, auto-archives stale skills |

**Verdict:** The Workspace Layer should **store its own structured data** (ADRs,
roadmaps, journal entries) using the same patterns (SQLite via a Python backend
plugin), but should **not** rebuild session persistence or memory.

### E. Communication and UI Shell

| Existing Capability | Where | How It Works |
|---------------------|-------|--------------|
| Chat transcript + composer | `src/app/chat/` | Full chat UI on @assistant-ui/react |
| Sidebar navigation | `src/app/chat/sidebar/` | Session list, profile switcher, projects |
| Right-rail panes | `src/app/right-sidebar/` | Preview, files, review, terminal |
| Pane layout engine | `src/components/pane-shell/tree/` | Grid-based resizable pane system |
| Overlay views | `src/app/overlays/` | Settings, command center, profiles |
| Command palette | `src/app/command-palette/` | ⌘K search + actions |
| Notifications | `src/store/notifications.ts` | Toast + OS notifications |
| Statusbar | `src/app/shell/statusbar-controls.tsx` | Connection status, model info |

**Verdict:** The Workspace Layer should add **pages and panes** — not rebuild
the shell. Every workspace feature should feel native to the existing app
chrome.

---

## 2. Capabilities That Exist But Need a Desktop Surface

These exist in the Hermes ecosystem but lack a desktop UI or are only accessible
via CLI/agent tools.

| Capability | Backend Exists? | Desktop UI Exists? | Gap |
|------------|----------------|-------------------|-----|
| **Kanban board** | Yes — `plugins/kanban/` | No | Full Python backend (SQLite, dispatcher, REST) with zero desktop surface. Needs a contributed route + sidebar nav entry. |
| **Cron job management** | Yes — `cron/` + REST API | Partial — `src/app/cron/` page exists but is read-only-ish | The desktop cron page exists but could be augmented with workspace-level scheduling views. |
| **Sprint management** | Partial — kanban has concepts of tenants/boards | No | Kanban's tenant isolation model can serve as a sprint boundary, but no sprint-level grouping/timeline. |
| **Repository metadata** | Partial — `git-repo-scan.ts` finds repos, `git-review-ops.ts` gives diff/status | Partial — file tree in right rail, review pane | No cross-repo aggregate view, no metadata store (last commit, CI status, dependency graph). |
| **Context assembly** | Yes — context engine, skills, memory, `AGENTS.md` | No UI for composing/auditing context | The engine runs, but a user cannot inspect or tune what context the agent received. |
| **Engineering journal** | No structured store | No | Sessions serve as ad-hoc journals, but there is no daily/task-scoped journal with templates and review. |

---

## 3. Completely Missing Capabilities

These have **no Hermes equivalent** and must be built from scratch.

| Capability | Status | Why It's New |
|------------|--------|--------------|
| **Workspace intelligence dashboard** | Missing | No aggregate view across all projects/repos in a workspace. Hermes is conversation-scoped; there is no "workspace home" surface. |
| **Roadmaps** | Missing | No timeline/roadmap visualization. Kanban is task-level; cron is scheduled agents. Neither models a dated roadmap with milestones. |
| **Architecture Decision Records (ADRs)** | Missing | No template system, no decision log, no status tracking (proposed/accepted/deprecated/superseded). The agent can write markdown files but there is no structured ADR lifecycle. |
| **Structured engineering journal** | Missing | Sessions are transcripts of agent conversations, not structured journal entries. A journal needs date-scoped entries, tags, templates, and review workflows. |
| **Multi-project orchestration view** | Missing | Hermes has profiles (isolated instances) but no cross-profile dashboard. The kanban plugin has a board-level dispatcher but no cross-project coordination UI. |
| **Repository health scoring** | Missing | No metrics aggregation across repos (commit frequency, open issues, dependency staleness, test coverage). |
| **Sprint timeline/velocity** | Missing | Kanban tracks task state but does not model sprint boundaries, velocity, or burndown. |

---

## 4. Recommended Integration Points for Missing Capabilities

For each missing capability, the best extension point from the architecture
document (Section 9 of DesktopArchitecture.md):

### Workspace Intelligence Dashboard

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) — register `/workspace` as a full page |
| **Sidebar** | Sidebar nav contribution (#1) — add "Workspace" row with codicon |
| **Data source** | Python backend plugin with REST endpoints (`/api/plugins/workspace/*`) accessed via `pluginRest()` (#8) |
| **State** | New nanostores in the workspace plugin, not core |
| **Why** | A contributed route gives a full page in the app shell with zero core changes |

### Roadmaps

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) — register `/workspace/roadmap` |
| **Data source** | Python backend plugin storing roadmap data in SQLite |
| **Visualization** | Reuse the existing `react-arborist` (tree) and `d3-force` (graph) already in `package.json` |
| **Integration with kanban** | Link roadmap items to kanban tasks via the existing `kanban_link` tool |
| **Why** | Roadmap items ARE kanban tasks on a timeline — reuse the kanban data model, add a time-axis view |

### Sprint Management

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) — register `/workspace/sprints` |
| **Data source** | Python backend plugin that wraps the kanban plugin's SQLite, adding sprint grouping |
| **Kanban integration** | Use the kanban tenant model as sprint boundaries; each sprint is a tenant |
| **UI** | Pane contribution (#2) for the sprint sidebar alongside chat |
| **Why** | The kanban backend is already built — sprint management is a thin grouping layer + a desktop UI |

### Architecture Decision Records (ADRs)

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) — register `/workspace/adrs` |
| **Templates** | Hermes skill (#11) — create a `skills/workspace/adr/SKILL.md` that teaches the agent how to create ADRs |
| **Storage** | File system — ADRs stored as markdown in `<project>/docs/adr/` per convention |
| **File access** | Reuse existing `readDir()`, `readFileText()`, `writeTextFile()` IPC methods |
| **Preview** | Reuse existing preview pane (#2) — ADR markdown renders natively |
| **Versioning** | ADRs are git-tracked files — reuse existing `git-review-ops` IPC |
| **Why** | ADRs are files. Hermes already reads/writes/previews/diffs files. A skill teaches the agent the ADR format; a contributed route provides browsing/searching. |

### Engineering Journal

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) — register `/workspace/journal` |
| **Data source** | Python backend plugin with SQLite storage, REST endpoints via `pluginRest()` (#8) |
| **Entry creation** | Composer middleware (#5) — add a "Journal Entry" command that routes text to the journal instead of a chat session |
| **Context injection** | Context engine plugin (#11 in backend) — add recent journal entries to agent context |
| **Templates** | Skill (#11) — `skills/workspace/journal/SKILL.md` for agent-driven journaling |
| **Why** | Journal entries are NOT chat messages — they need their own store. But they should feel like a first-class action in the composer, not a separate app. |

### Repository Metadata

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | New IPC handler (#12) — `hermes:workspace:repo-meta` aggregating across repos |
| **Backend** | Python backend plugin that scans repos and caches metadata |
| **UI** | Statusbar contribution (#4) — show aggregate workspace health |
| **Panes** | Pane contribution (#2) — repository detail pane |
| **File tree** | Reuse the existing file tree in the right sidebar (`src/app/right-sidebar/files/`) |
| **Why** | Metadata aggregation needs filesystem access beyond the current CWD — a new IPC handler is warranted but can be in a plugin, not core |

### Multi-Project Orchestration

| Aspect | Recommendation |
|--------|---------------|
| **Extension point** | Contributed route (#1) + Python plugin + cron integration |
| **Data source** | Python backend plugin that coordinates across profiles using the existing profile API |
| **Execution** | Cron jobs (`cron/`) — each orchestration step is a scheduled or triggered agent run |
| **Delegation** | Reuse `delegate_task` — the orchestrator agent spawns subagents per project |
| **Status** | Statusbar contribution (#4) — show running orchestrations |
| **Why** | The primitives are all there (cron, delegation, profiles). Orchestration is composition, not new infrastructure. |

---

## 5. Capabilities That Should NOT Be Duplicated

The Workspace Layer must **not** reimplement these Hermes primitives:

| Hermes Capability | Why Not Duplicate | Workspace Layer Should Instead... |
|-------------------|-------------------|-----------------------------------|
| **Session/conversation persistence** | Hermes SessionDB is battle-tested, supports FTS5, and handles compression, branching, resumption | Store workspace-specific metadata in a SEPARATE SQLite (via a Python plugin), link to session IDs by reference |
| **Git operations** | Electron main has working, tested, sandbox-safe git IPC | Call the existing git IPC methods from the plugin; add new ones only when truly new (e.g., cross-repo diff) |
| **File system access** | Electron main has security-hardened path resolution | Use existing `readDir`, `readFileText`, `writeTextFile`, `trashPath` IPC |
| **Memory/learning** | Memory providers already learn across sessions | Inject workspace-relevant memories into context; don't build a parallel memory system |
| **Agent conversation loop** | AIAgent in `run_agent.py` is the single source of truth for model interactions | Never reimplement prompt→model→tools→response. Always go through the gateway's `prompt.submit`. |
| **WebSocket/RPC transport** | JsonRpcGatewayClient is shared between desktop and dashboard | Extend via plugin WebSocket door (`pluginSocket()`) if needed, don't open a second raw WebSocket |
| **Model/provider config** | Centralized config in `config.yaml` + `/api/config` | Read from existing config; add workspace-layer keys under a new `workspace:` section |
| **Theme/i18n** | Four-locale i18n system, theme tokens, CSS vars | Follow the existing conventions; add strings to the plugin's own i18n scope |
| **Skill system** | SKILL.md format, frontmatter, prerequisites | Author workspace skills (ADR, journal, roadmap) using the existing skill format |
| **Cron scheduler** | Tick loop, catchup windows, file locks, multi-platform delivery | Schedule workspace maintenance jobs through the existing cron system |
| **Notifications** | Toast + OS notification stack | Use existing `notify()` store; add workspace-specific notification categories |
| **Approval/security** | Clarify, sudo, secret prompts | Reuse the existing approval infrastructure — don't add a parallel security layer |

---

## 6. Recommended Architecture

```
┌──────────────────────────────────────────────────────────────┐
│ USER                                                         │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ WORKSPACE LAYER (independent package)                        │
│                                                              │
│  ┌─ Desktop plug-in (renderer) ──────────────────────────┐  │
│  │  • Contributed routes: /workspace, /workspace/*        │  │
│  │  • Contributed panes: workspace sidebar, sprint panel  │  │
│  │  • Contributed statusbar: workspace health, active     │  │
│  │    sprint indicator                                    │  │
│  │  • Composer middleware: "Journal Entry" action         │  │
│  │  • Command palette: workspace-level commands           │  │
│  │  • Nanostores: workspace-specific state atoms          │  │
│  │  • Uses: pluginRest(), pluginSocket(), existing IPC     │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Python back-end plug-in ─────────────────────────────┐  │
│  │  Installed at: ~/.hermes/plugins/workspace/           │  │
│  │  • REST endpoints at /api/plugins/workspace/*          │  │
│  │     • /workspace   — workspace status, repo list       │  │
│  │     • /roadmap     — roadmap CRUD, milestone tracking  │  │
│  │     • /sprints     — sprint CRUD, kanban integration   │  │
│  │     • /adrs        — ADR browse, search, validate      │  │
│  │     • /journal     — journal entry CRUD, templates     │  │
│  │     • /context     — context assembly for agents       │  │
│  │  • SQLite database: workspace.db                       │  │
│  │     • repos, roadmaps, sprints, adrs, journal_entries  │  │
│  │  • WebSocket: live updates via pluginSocket()          │  │
│  │  • Context engine provider: inject workspace context   │  │
│  │  • Integrates with: kanban plugin, cron, delegation    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ Skills (bundled with the plugin) ────────────────────┐  │
│  │  • SKILL.md for ADR creation and review                │  │
│  │  • SKILL.md for engineering journaling                 │  │
│  │  • SKILL.md for roadmap planning & sprint planning     │  │
│  │  • SKILL.md for workspace context assembly             │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ HERMES DESKTOP (unmodified)                                  │
│                                                              │
│  ContributionRegistry ←── workspace plugin registers here    │
│  pluginRest()         ←── workspace calls this for REST      │
│  pluginSocket()       ←── workspace calls this for WS        │
│  hermesDesktop.api()  ←── workspace calls existing IPC       │
│  JsonRpcGatewayClient ←── workspace uses shared RPC base     │
│  Cron scheduler       ←── workspace schedules jobs here      │
│  Kanban plugin        ←── workspace extends kanban data      │
│  Context engine       ←── workspace plugs into context       │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ HERMES BACKEND (unmodified)                                  │
│                                                              │
│  hermes serve        ←── workspace plugin runs inside this   │
│  REST API            ←── /api/plugins/workspace/* routes     │
│  WebSocket           ←── pluginSocket() events               │
│  AIAgent             ←── agent uses workspace context        │
│  Skill loader        ←── loads workspace skills              │
│  Memory providers    ←── workspace memories stored            │
│  Tools (terminal,    ←── agent tools unchanged               │
│    file, browser,    │                                       │
│    delegation)       │                                       │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│ LLMs / Tools / APIs                                          │
└──────────────────────────────────────────────────────────────┘
```

### Key architectural decisions

1. **The Workspace Layer is a standalone desktop plugin + Python backend plugin.**
   It installs into `~/.hermes/plugins/workspace/` and registers at the
   contribution-level surfaces. It does **not** fork or patch Hermes core.

2. **Data lives in the backend, not the renderer.** Roadmaps, sprints, ADRs,
   and journal entries are stored in a `workspace.db` SQLite managed by the
   Python plugin. The renderer is a cache, same as every other Hermes surface.

3. **Context assembly plugs into the existing pipeline.** The workspace
   plugin registers a context-engine provider that injects relevant workspace
   metadata (active sprint, recent ADRs, open kanban tasks) into every agent
   turn — no prompt modification, no cache-breaking.

4. **Task management reuses kanban.** Sprint items ARE kanban tasks with a
   sprint group column. Roadmap items ARE kanban tasks with a target
   milestone date. No second task store.

5. **Files remain files.** ADRs are markdown files in the repo. The agent
   reads/writes them with existing `read_file`/`write_file` tools. The
   desktop plugin surfaces them for browsing — it does not duplicate the
   file system.

6. **Skills teach the agent, not code in the core.** Workspace behaviors
   (creating ADRs, writing journal entries, planning sprints) are encoded
   as SKILL.md files. The agent loads them on demand. Zero core changes.

---

## 7. Capability Mapping Table

| Capability | Hermes Already Has | Workspace Layer | Integration Point | Notes |
|-----------|-------------------|----------------|-------------------|-------|
| **Workspace intelligence** | Project folders, git scanning, CWD tracking | Dashboard aggregating across repos: health scores, recent activity, open tasks | Contributed route #1 + Python plugin REST #8 + statusbar contribution #4 | Hermes knows individual repos. Workspace layer aggregates them. |
| **Roadmaps** | Kanban tasks, cron scheduler | Timeline view of kanban tasks with milestone grouping. Roadmap CRUD. | Contributed route #1 + kanban plugin data reuse | Roadmap items are kanban tasks with dates. Extend, don't rebuild. |
| **Sprint management** | Kanban plugin (Python), cron jobs | Sprint grouping on kanban, velocity tracking, burndown chart, sprint CRUD | Contributed route #1 + pane contribution #2 + kanban plugin extension | Each sprint is a kanban tenant. Desktop UI is the only missing piece. |
| **Project context** | CWD tracking, AGENTS.md, project sidebar, git branch, worktrees | Context inspector (what the agent sees), project-level settings (default skills, model) | Pane contribution #2 + context engine provider #11 + statusbar #4 | Hermes already assembles context. Workspace layer makes it visible and tunable. |
| **Engineering journal** | Session transcript persistence | Structured journal with entries, tags, templates, date scoping. Separate from chat sessions. | Contributed route #1 + Python plugin REST #8 + composer middleware #5 + skill #11 | Sessions are conversations, not journals. Different data, same storage pattern. |
| **Architecture decisions (ADRs)** | File system tools, git diff, preview pane | ADR template system, status tracking (proposed/accepted/deprecated), browsing/search UI | Contributed route #1 + skill #11 + reuse file/git IPC | ADRs are markdown files. Hermes already reads/writes/previews files. Skill teaches the format. |
| **Repository metadata** | git-repo-scan, git-review-ops (diff, status, commit, PR) | Cross-repo aggregate: CI status, dependency graph, last commit, open PR count | New IPC handler #12 + statusbar #4 + pane #2 | Metadata aggregation needs cross-repo filesystem access. A new IPC handler is justified. |
| **Context assembly for AI** | Context engine plugin, skills system, memory providers, AGENTS.md loading | Workspace-aware context provider: injects active sprint, recent ADRs, open tasks, journal context | Context engine provider #11 in backend (Python plugin) | DO NOT REBUILD. Extend the existing context engine. |
| **Multi-project orchestration** | Profiles (isolated instances), delegation (subagents), cron scheduler, kanban dispatcher | Cross-profile orchestration dashboard, dependency tracking between projects, orchestration run history | Contributed route #1 + Python plugin REST #8 + cron integration | The primitives are there. Orchestration is composition + a UI. |

---

## 8. Migration Strategy

### Principle: Add, don't modify. Plug in, don't fork.

```
Phase 0: Audit (week 1)
  ├── Confirm all extension points listed above are stable and working
  ├── Test pluginRest() and pluginSocket() end-to-end with a minimal plugin
  ├── Verify kanban plugin REST API surface can be consumed from desktop
  └── Document any Hermes core issues that would block workspace integration

Phase 1: Backend data layer (week 2-3)
  ├── Create workspace Python plugin at ~/.hermes/plugins/workspace/
  │   ├── plugin.yaml manifest
  │   ├── __init__.py with register(ctx)
  │   ├── workspace.db schema (repos, roadmaps, sprints, adrs, journal_entries)
  │   ├── REST endpoints at /api/plugins/workspace/*
  │   └── Context engine provider (injects workspace context into agent turns)
  ├── Integration tests against a real hermes serve backend
  └── NO desktop code yet — test via curl and hermes CLI

Phase 2: Desktop shell (week 4-5)
  ├── Create desktop workspace plugin (src/plugins/workspace/)
  │   ├── Contributed route: /workspace (dashboard home)
  │   ├── Contributed sub-routes: /workspace/roadmap, /workspace/sprints,
  │   │   /workspace/adrs, /workspace/journal
  │   ├── Sidebar nav contribution: "Workspace" row with codicon
  │   ├── Statusbar contributions: workspace health indicator
  │   ├── Composer middleware: "Journal Entry" quick action
  │   ├── Command palette commands: workspace-level actions
  │   └── Nanostores: $workspaceRepos, $activeSprint, $roadmapItems, …
  │
  ├── Discover via discoverBundledPlugins() or runtime loader
  └── All data flows through pluginRest() → backend plugin REST

Phase 3: Skills (week 5-6)
  ├── Create workspace skills (bundled with the plugin):
  │   ├── skills/workspace/adr/SKILL.md     — ADR creation & review
  │   ├── skills/workspace/journal/SKILL.md — Structured journaling
  │   ├── skills/workspace/sprint/SKILL.md  — Sprint planning & retro
  │   └── skills/workspace/context/SKILL.md — Workspace context awareness
  ├── Each skill references the workspace plugin's REST API for data
  └── Install via hermes skills install

Phase 4: Integration (week 6-7)
  ├── Link workspace sprint tasks to kanban (reuse kanban_link)
  ├── Schedule workspace maintenance via cron (daily journal prompt, weekly review)
  ├── Wire context engine provider into agent sessions
  ├── End-to-end validation: agent creates ADR → appears in workspace UI →
  │   agent references it in a later session
  └── Performance validation (SQLite queries, context size budget)

Phase 5: Polish (week 7-8)
  ├── i18n for all workspace UI strings (en only initially)
  ├── Theme compliance (use --ui-* tokens, no hardcoded colors)
  ├── Keyboard shortcuts (register via KEYBINDS_AREA)
  ├── Notification integration (workspace alerts via notify())
  └── Documentation and onboarding flow
```

### Zero-Core-Change Guarantee

The entire Workspace Layer can be built and shipped without modifying a single
file in the Hermes Desktop core (`electron/`, `src/app/`, `src/components/`,
`src/store/`, `src/lib/`, `src/contrib/`). Every integration point listed above
is a **declared, stable extension surface** from the Hermes plugin system.

The only Hermes-side action required is **enabling the plugin** — either by
placing it in `~/.hermes/plugins/workspace/` (runtime discovery) or in
`src/plugins/workspace/` (bundled, build-time discovery). Both paths are
already implemented in `src/contrib/plugins.ts` and
`src/contrib/runtime-loader.ts`.

### Risk Items

| Risk | Mitigation |
|------|------------|
| `pluginRest()` and `pluginSocket()` are not battle-tested against a real consumer | Phase 0 explicitly validates them end-to-end |
| The kanban plugin's REST surface may need extension for sprint grouping | This is a kanban plugin change, not a core change |
| Context window inflation from workspace context | Skills use `## When to Use` gates; context provider is configurable with token budgets |
| Performance of cross-repo metadata aggregation | Async caching in the Python plugin; statusbar reads a cached summary, not live scan |
| Plugin conflicts (two plugins wanting the same route) | The ContributionRegistry enforces unique `path` registration |
