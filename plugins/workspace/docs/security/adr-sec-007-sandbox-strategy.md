# ADR-SEC-007: Sandbox Strategy

## Status

Proposed

## Context

The Workspace Platform executes user commands (terminal), writes files,
runs git operations, navigates browsers, and processes external content.
Each of these operations carries a risk of unintended system modification
or data exfiltration.

## Decision

We implement a multi-layered sandbox strategy combining filesystem,
process, network, and resource isolation.

### Layer 1 — Working Directory Isolation

```
Agent Working Directory (cwd)
  │
  ├── /workspace/<workspace-name>/        ← Can read/write
  │   ├── repos/<repo-name>/              ← Git root
  │   └── temp/<session-id>/              ← Ephemeral session files
  │
  ├── /tmp/hermes-agent/<session-id>/     ← Ephemeral, auto-cleaned
  │
  └── Everything else                     ← Denied
      ├── /etc/     → Denied
      ├── /usr/     → Denied
      ├── /proc/    → Denied
      ├── /sys/     → Denied
      ├── ~/.ssh/   → Denied
      ├── ~/.aws/   → Denied
      ├── ~/.hermes/ (outside workspace)  → Denied
      └── /         → Denied (except workspace + temp)
```

### Layer 2 — Temporary Execution

- All temporary work occurs in `/tmp/hermes-agent/<session-id>/`
- Sessions get a unique subdirectory per conversation
- Session directories are created at session start
- Cleaned up on session termination (normal or error)
- No session can access another session's temp directory

### Layer 3 — Container Boundaries (Future)

For high-risk operations (optional, opt-in):

```
┌─────────────────────────────────────┐
│ Docker / Podman Container            │
│                                      │
│  Filesystem: RO except /workspace    │
│  Network:   Isolated (no internet)   │
│  PID:       Private namespace        │
│  IPC:       Disabled                 │
│  Memory:    Limited (configurable)   │
│  CPU:       Limited (configurable)   │
│  Timeout:   300s (default)           │
│                                      │
│  Mounts:    /workspace (host:RW)     │
│             /tmp       (tmpfs:RW)    │
└─────────────────────────────────────┘
```

### Layer 4 — Filesystem Restrictions

Enforced at the tool level:

| Operation | Workspace | Temp | Git Root | External |
|-----------|-----------|------|----------|----------|
| Read | ✓ | ✓ | ✓ | ✗ |
| Write | ✓ | ✓ | ✓ | ✗ |
| Delete | ✓ | ✓ | ✗ (git clean only) | ✗ |
| Execute | ✓ (scripts) | ✓ | ✗ | ✗ |
| Symlink | Denied | Denied | Denied | N/A |
| Chmod +x | Denied | Denied | Denied | N/A |

### Layer 5 — Network Restrictions

```
Allowed Protocols:
  ✓ HTTP/HTTPS (public internet)
  ✓ DNS (for name resolution)

Denied Protocols:
  ✗ file://
  ✗ ftp://
  ✗ smb://
  ✗ gopher://
  ✗ Custom schemes

Denied Networks:
  ✗ localhost (127.0.0.0/8, ::1)
  ✗ Link-local (169.254.0.0/16)
  ✗ Private (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
  ✗ Multicast (224.0.0.0/4)
```

### Layer 6 — Resource Limits

| Resource | Limit | Config Key |
|----------|-------|-----------|
| Shell execution timeout | 300s | `security.sandbox.shell_timeout` |
| File write max size | 10 MB | `security.sandbox.max_file_size` |
| Web fetch timeout | 30s | `security.sandbox.web_timeout` |
| Git operation timeout | 60s | `security.sandbox.git_timeout` |
| Max subprocesses per session | 50 | `security.sandbox.max_processes` |
| Max temp space per session | 500 MB | `security.sandbox.max_temp_space` |

### Layer 7 — Cleanup

On session termination:

1. Kill all child processes (SIGTERM, then SIGKILL after 5s)
2. Remove temp directories
3. Remove session-scoped locks
4. Close file handles
5. Log cleanup completion

On crash:
- OS-level cleanup (process death releases handles)
- Session lock file is stale — reclaimed on next acquisition attempt
- Temp files persist for forensic analysis (30-day TTL)

## Consequences

- **Positive**: Defense in depth makes single-layer bypass insufficient
- **Positive**: Container option provides strong isolation for high-risk ops
- **Positive**: Cleanup is deterministic on normal exit
- **Negative**: Performance overhead from path validation on every file operation
- **Negative**: Container mode requires Docker/Podman installed (not default)

## References

- Docker security best practices
- Linux namespaces, cgroups, seccomp
- OWASP File System Access Control
