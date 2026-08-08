# ADR-SEC-004: Tool Permission Model

## Status

Proposed

## Context

The Workspace Platform exposes 30+ tools through the Hermes tool schema.
Each tool has different risk profiles. A uniform approval model would
either be too permissive (risking damage) or too restrictive (creating
approval fatigue).

## Decision

We implement a three-tier permission model with progressive approval
requirements.

### Tier 1 — Auto-Approved (Read-Only, Non-Destructive)

Tools that observe but do not mutate:

| Tool | Justification |
|------|-------------|
| `read_file` | Read-only, workspace-scoped |
| `search_files` | Read-only, pattern match |
| `web_search` | Read-only external |
| `web_extract` | Read-only content extraction |
| `list_files` | Read-only directory listing |
| `vision_analyze` | Read-only image processing |
| Memory read (`memory.*`) | Read-only recall |
| `search_session` | Read-only history |

**Behavior:** No approval prompt. Logged at DEBUG level.

### Tier 2 — Approval-Required (Mutative, Local)

Tools that modify local state:

| Tool | Approval | Audit |
|------|---------|-------|
| `write_file` | Confirm path | Yes |
| `patch` | Confirm changes | Yes |
| `terminal` (commands) | Confirm command | Yes |
| `git commit` | Confirm message | Yes |
| `git push` | Confirm branch + remote | Yes |
| `delegate_task` | Confirm goal + resources | Yes |
| `delete_files` | Confirm path (with undo) | Yes |
| `todo.*` | Auto-approve | Optional |

**Behavior:** Approval prompt showing: tool name, parameters, affected
paths/resources. User can Approve/Deny/Modify.

### Tier 3 — Admin-Gated (Destructive, External, High-Risk)

Tools that can cause significant damage:

| Tool | Gate | Audit |
|------|------|-------|
| `git force-push` | Admin capability | Yes |
| `terminal` (sudo/root) | Admin capability | Yes |
| `cronjob` (schedule) | Admin confirm | Yes |
| `plugin_install` | Admin confirm | Yes |
| `config_write` | Admin confirm | Yes |
| Network access to internal IPs | Admin capability | Yes |

**Behavior:** Requires admin capability + explicit confirmation. Double-confirm
for destructive operations.

### Approval Pipeline

```
Tool Call Request
    │
    ▼
┌──────────────────┐
│ 1. Check Tier    │
└──────┬───────────┘
       │
    ┌──▼──────────────────────────┐
    │ Tier 1 (Auto-Approve)        │
    │ → Execute immediately        │
    │ → Log at DEBUG               │
    └─────────────────────────────┘
       │
    ┌──▼──────────────────────────┐
    │ Tier 2 (Approval Required)   │
    │ → Show approval prompt       │
    │ → User: Approve/Deny/Modify  │
    │ → Log at INFO                │
    │ → Execute if approved        │
    └─────────────────────────────┘
       │
    ┌──▼──────────────────────────┐
    │ Tier 3 (Admin Gated)         │
    │ → Check admin capability     │
    │ → Show double-confirm        │
    │ → Log at WARNING             │
    │ → Execute if confirmed       │
    └─────────────────────────────┘
```

### Approval Sessions

Users can set approval modes per session:

- `always` — Every Tier 2+ operation requires approval (default)
- `duration` — Approve for a time window (e.g., 30 minutes)
- `scope` — Approve for a specific task/toolset
- `never` — Disable approvals (admin only, audited)

### Deny Default

If the policy engine cannot determine the tier for a tool, the default is
**deny**. Unknown tools are never auto-approved.

## Consequences

- **Positive**: Users maintain control over mutative operations
- **Positive**: Tiered model balances security with usability
- **Positive**: Configurable per-session reduces fatigue
- **Negative**: Approval UI must be fast and non-blocking
- **Negative**: Async approval race conditions — tool must pause, not fail

## References

- Android permission model (normal, dangerous, signature)
- Kubernetes RBAC
- macOS security permissions (TCC framework)
