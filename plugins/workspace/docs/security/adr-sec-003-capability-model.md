# ADR-SEC-003: Capability-Based Security

## Status

Proposed

## Context

The Workspace Platform's tool system exposes powerful operations (file
write, shell execute, git push, network access). Without a capability
model, any tool invocation can potentially perform any operation.

## Decision

We adopt a capability-based security model where every operation requires
an explicit, revocable capability grant.

### Capability Hierarchy

```
                    ┌──────────────────────┐
                    │    Admin Capability    │
                    │   (Full Access)        │
                    └──────────┬───────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐ ┌──────▼──────┐ ┌───────▼────────┐
    │ Write Capability │ │Read Capability│ │Execute Capability│
    │ (fs.write)       │ │ (fs.read)    │ │ (shell.exec)     │
    └────────┬────────┘ └──────┬───────┘ └────────┬────────┘
             │                 │                   │
    ┌────────▼────────┐ ┌──────▼───────┐ ┌────────▼────────┐
    │ file.write       │ │ file.read    │ │ terminal.run     │
    │ file.delete      │ │ search_files │ │ browser.navigate  │
    │ patch             │ │ web_search   │ │ git.*            │
    │ write_to_file     │ │ vision_*     │ │ delegate_task    │
    └─────────────────┘ └──────────────┘ └─────────────────┘
```

### Capability Definitions

| Capability | Scope | Approval Required | Audit Required | Recovery |
|-----------|-------|------------------|---------------|----------|
| `fs.read` | Read any file in workspace | No (auto-approve) | Optional | N/A |
| `fs.write` | Write/create in workspace | Yes | Yes | Git revert / snapshot |
| `fs.delete` | Delete files in workspace | Yes (confirm) | Yes | Git revert / snapshot |
| `shell.exec` | Execute commands | Yes | Yes | Working dir reset |
| `git.read` | Git status, log, diff | No | Optional | N/A |
| `git.commit` | Git commit | Yes | Yes | `git reset` |
| `git.push` | Git push to remote | Yes (confirm) | Yes | `git revert` + force-push rollback |
| `git.force_push` | Git force push | Admin only | Yes | Manual recovery |
| `network.http` | HTTP/HTTPS requests | Auto: tool required | Optional | N/A |
| `network.all` | Any network access | Yes | Yes | N/A |
| `browser.navigate` | Headless browser | Yes | Optional | Tab close |
| `search.internal` | Search local files | No | Optional | N/A |
| `search.external` | Web search | Yes | Optional | N/A |
| `delegate.spawn` | Create subagent | Yes | Yes | Subagent kill |
| `plugin.call` | Invoke plugin tool | Per-plugin | Optional | Plugin disable |

### Capability Assignment

Capabilities are assigned at multiple levels:

1. **Global defaults** — defined in `config.yaml` under `security.capabilities`
2. **Per-toolset** — overridden per enabled toolset
3. **Per-session** — overridden for a specific conversation
4. **Runtime approval** — user grants one-time capability on approval prompt

### Capability Revocation

Capabilities can be revoked:
- **Immediately**: On session termination or explicit `/revoke` command
- **Per-session**: Session-scoped capabilities expire when conversation ends
- **Config-driven**: Changes in `config.yaml` take effect on next session start

## Consequences

- **Positive**: Principle of least privilege enforced at the tool level
- **Positive**: Granular control over which operations require approval
- **Positive**: Audit trail of all capability-sensitive operations
- **Negative**: Adds latency from approval prompts
- **Negative**: Users may develop "approval fatigue" — mitigated by auto-approve patterns

## References

- Capability-based security (Dennis & Van Horn, 1966)
- Object-capability model (ocap)
- Docker capabilities model (CAP_CHOWN, CAP_NET_RAW, etc.)
