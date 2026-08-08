# ADR-SEC-009: Recovery Strategy

## Status

Proposed

## Context

The Workspace Platform performs file writes, git operations, and shell
executions that may produce unintended changes. Without a recovery
strategy, a single bad tool invocation can result in data loss.

## Decision

We implement a multi-tier recovery strategy with increasing cost and
completeness.

### Tier 1 — Git-Based Recovery (Instant, Free)

Applies to: any file or directory within a git repository.

```
Before write:
  ┌──────────────────────────────────────┐
  │ 1. git status --porcelain            │  ← Capture pre-state
  │ 2. git diff                          │  ← Capture delta
  │ 3. Write new content                 │
  │ 4. git diff                          │  ← Capture post-delta
  │ 5. Store recovery info in session    │
  └──────────────────────────────────────┘

Recovery:
  git checkout -- <path>                 ← Instant revert
  git clean -fd                          ← Remove untracked
```

Workspace snapshots at maximum once per N tool calls, where N is
configurable (default: 10).

### Tier 2 — Workspace Snapshots (Fast, Local)

Applies to: non-git files, temp directory, entire workspace.

```
Before high-risk operation:
  ┌──────────────────────────────────────┐
  │ 1. tar -czf snapshot.tar.gz          │  ← Compressed snapshot
  │    <workspace-root>                  │
  │ 2. Store in ~/.hermes/snapshots/     │
  │    <session-id>/<timestamp>.tar.gz   │
  │ 3. Cap total snapshots per session   │  ← default: 20
  │ 4. Auto-delete oldest when cap hits  │
  └──────────────────────────────────────┘

Recovery:
  tar -xzf <snapshot> -C <workspace-root> ← Full restore
```

### Tier 3 — Git Recovery (External)

Applies to: git-pushed changes that need to be rolled back remotely.

```
Recovery:
  git revert <commit>                    ← Create revert commit
  git push origin <branch>              ← Push revert (requires approval)
  OR
  git reset --hard <previous>           ← Local + force-push (admin only)
```

### Tier 4 — Crash Recovery (Automatic)

Applies to: process crashes, power loss, unexpected termination.

```
On startup:
  1. Check for stale session locks      ← Clean up if >5min old
  2. Check for orphaned temp dirs        ← Remove if no active session
  3. Check for incomplete snapshots      ← Delete partial .tar.gz
  4. Restore last known good state?      ← No (manual by default)
  5. Log recovery actions                ← INFO level
```

### Failure Handling

| Failure Type | Detection | Response | Recovery |
|-------------|-----------|---------|----------|
| File write error (permission) | Tool returns error | Abort, report to user | None needed |
| File write error (disk full) | Tool returns ENOSPC | Abort, warn user | Free space |
| Git operation timeout | Timeout signal | Kill process, report | Git rollback |
| Shell command error (non-zero exit) | Exit code check | Report to user | User decides |
| Shell command infinite loop | Timeout | SIGTERM → SIGKILL | Cleanup temp |
| Agent crash | Process watchdog | Restart gateway | Session resume |
| Snapshot corruption | Checksum fail | Skip snapshot, log | Next snapshot |
| Git repo corruption | git fsck fails | Alert user, stop writes | Manual git repair |

### Configuration

```yaml
security:
  recovery:
    git_rollback_enabled: true
    workspace_snapshots_enabled: true
    max_snapshots_per_session: 20
    snapshot_interval_tool_calls: 10
    auto_cleanup_orphan_temp: true
    stale_lock_timeout_seconds: 300
```

## Consequences

- **Positive**: Multi-tier recovery covers local and remote scenarios
- **Positive**: Git-based recovery is instant and zero-cost for repos
- **Positive**: Snapshot system is transparent to the user
- **Negative**: Snapshot creation adds latency before writes
- **Negative**: Snapshot storage grows linearly with workspace size ×
  operation count

## References

- Git internals: git-checkout, git-revert, git-reflog
- Backup strategies: Grandfather-Father-Son rotation
- SQLite rollback journal pattern
