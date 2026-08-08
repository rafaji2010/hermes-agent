# Workspace Platform — Security Architecture

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Trust Boundary Diagram](#trust-boundary-diagram)
3. [Capability Matrix](#capability-matrix)
4. [Threat Model Summary](#threat-model-summary)
5. [Prompt Injection Architecture](#prompt-injection-architecture)
6. [Sandbox Design](#sandbox-design)
7. [Secrets Architecture](#secrets-architecture)
8. [Audit Architecture](#audit-architecture)
9. [Recovery Architecture](#recovery-architecture)
10. [Security Roadmap](#security-roadmap)

---

## Architecture Overview

```
┌────────────────────────────────────────────────────────────────────────┐
│                          HERMES WORKSPACE SECURITY                      │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │   Human User  │    │  Gateway     │    │   External Systems        │  │
│  │   (Trusted)   │    │  (Trusted)   │    │   LLM, MCP, Web (Untrust) │  │
│  └──────┬───────┘    └──────┬───────┘    └───────────┬──────────────┘  │
│         │                   │                        │                  │
│         ▼                   ▼                        ▼                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     POLICY ENGINE                                 │  │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐  │  │
│  │  │ Input     │ │ Capability │ │ Content   │ │ Permission       │  │  │
│  │  │ Validation│ │ Check      │ │ Labeling  │ │ Evaluation       │  │  │
│  │  └──────────┘ └───────────┘ └──────────┘ └──────────────────┘  │  │
│  └───────────────────┬──────────────────────────────────────────────┘  │
│                      │                                                  │
│                      ▼                                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     APPROVAL LAYER                                │  │
│  │  Tier 1: Auto-Approve    Tier 2: User Approve    Tier 3: Admin   │  │
│  └───────────────────┬──────────────────────────────────────────────┘  │
│                      │                                                  │
│                      ▼                                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                      TOOL SANDBOX                                 │  │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐  │  │
│  │  │ Filesystem│ │ Process   │ │ Network  │ │ Resource         │  │  │
│  │  │ Isolation │ │ Isolation │ │ Isolation│ │ Limits           │  │  │
│  │  └──────────┘ └───────────┘ └──────────┘ └──────────────────┘  │  │
│  └───────────────────┬──────────────────────────────────────────────┘  │
│                      │                                                  │
│         ┌────────────┼────────────┐                                    │
│         ▼            ▼            ▼                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                               │
│  │ Filesystem│ │   Git    │ │ Network  │                               │
│  │ (cwd)     │ │ (commit) │ │ (HTTP)   │                               │
│  └──────────┘ └──────────┘ └──────────┘                               │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │                     AUDIT & RECOVERY                              │  │
│  │  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌──────────────────┐  │  │
│  │  │ Audit    │ │ Secret    │ │ Git       │ │ Workspace        │  │  │
│  │  │ Logging  │ │ Redaction │ │ Rollback  │ │ Snapshots        │  │  │
│  │  └──────────┘ └───────────┘ └──────────┘ └──────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Trust Boundary Diagram

```
╔═══════════════════════════════════════════════════════════════════╗
║  TRUST BOUNDARY MAP                                               ║
║  ═══ = Trust Boundary    → = Data Flow    ★ = Enforcement Point  ║
╚═══════════════════════════════════════════════════════════════════╝

┌──────────┐
│  User    │  Trust Level: ★★★★★  (Highest)
└────┬─────┘
     │  /slash commands, messages, config changes
═════╪══════════════════════ T1: Authentication Boundary
     │  → Auth tokens validated per platform
     ▼
┌──────────┐
│  Gateway │  Trust Level: ★★★★☆
│  / TUI   │  → Input validation, command parsing
└────┬─────┘
     │  Tool call requests, capability requests
═════╪══════════════════════ T2: Policy Boundary
     │  → Permission evaluation ★
     ▼
┌──────────┐
│  Policy  │  Trust Level: ★★★★★  (Internal)
│  Engine  │  → Capability check, content labeling, tier dispatch
└────┬─────┘
     │  Approved tool calls, sanitized params
═════╪══════════════════════ T3: Approval Boundary
     │  → Tier 1: auto-pass  Tier 2: user prompt ★  Tier 3: admin gate
     ▼
┌──────────┐
│ Approval │  Trust Level: ★★★★☆
│  Layer   │  → Display prompt, collect response, timeout
└────┬─────┘
     │  Explicitly approved execution
═════╪══════════════════════ T4: Sandbox Boundary
     │  → Filesystem restriction ★  → Network restriction ★
     ▼  → Process isolation ★  → Resource limits ★
┌──────────┐
│  Tool    │  Trust Level: ★★☆☆☆
│  Sandbox │  → cwd: /workspace/<name>
└────┬─────┘  → temp: /tmp/hermes/<session>
     │         → Denied: /etc, /usr, ~/.ssh
     │
     ├──────────────┬──────────────┐
     ▼              ▼              ▼
┌────────┐  ┌──────────┐  ┌──────────┐
│  FS    │  │   Git    │  │ Network  │
│ (RW)   │  │ (commit) │  │ (HTTP)   │
└────────┘  └──────────┘  └──────────┘
★★☆☆☆     ★★☆☆☆        ★☆☆☆☆

══════════════════════════════════════════
 EXTERNAL INPUTS (All Untrusted ★☆☆☆☆)
══════════════════════════════════════════

┌─────────────────────────────────────────┐
│  LLM Response  ──▶ Response Parser      │  T8: Untrusted
│  → Tool calls must be re-validated ★    │  Response → policy re-eval
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  External Docs ──▶ Content Parser       │  T7: Untrusted
│  → Labeled [EXTERNAL] before context ★  │  Content → sanitization
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Web Content   ──▶ URL Validator        │  T6: Untrusted
│  → SSRF check ★, domain allowlist ★     │  Web → network sandbox
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  MCP Server    ──▶ MCP Gateway          │  T9: Untrusted
│  → Auth token check ★, tool allowlist ★  │  MCP → capability gate
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│  Plugin Code   ──▶ Plugin Loader        │  T9: Untrusted
│  → Manifest validation ★, API restrict ★ │  Plugin → sandbox
└─────────────────────────────────────────┘

══════════════════════════════════════════
 AUDIT & RECOVERY (All Enforcement Points)
══════════════════════════════════════════

          ┌─────────────────────┐
          │    Audit Logger     │
          │  → Every enforcement│
          │    point emits log  │
          └─────────┬───────────┘
                    │
          ┌─────────▼───────────┐
          │   audit.log         │
          │   (JSON Lines)      │
          └─────────────────────┘

          ┌─────────────────────┐
          │   Recovery Engine   │
          │  → Git rollback     │
          │  → Workspace snap   │
          │  → Crash cleanup    │
          └─────────────────────┘
```

---

## Capability Matrix

| # | Capability | Scope | Approval Required | Audit | Recovery |
|---|-----------|-------|------------------|-------|----------|
| C1 | `fs.read` | Workspace + repos | No (auto) | Optional | N/A |
| C2 | `fs.write` | Workspace + temp | Yes (user) | Yes | Git revert |
| C3 | `fs.delete` | Workspace only | Yes (confirm) | Yes | Git revert |
| C4 | `fs.execute` | Workspace scripts | Yes (user) | Yes | Process kill |
| C5 | `shell.exec` | Any command | Yes (user) | Yes | Cleanup temp |
| C6 | `shell.background` | Async execution | Yes (user) | Yes | Kill + cleanup |
| C7 | `shell.sudo` | Root commands | Admin only | Yes | Full rollback |
| C8 | `git.read` | status, log, diff | No (auto) | Optional | N/A |
| C9 | `git.commit` | Create commit | Yes (user) | Yes | `git reset` |
| C10 | `git.push` | Push to remote | Yes (confirm) | Yes | `git revert` |
| C11 | `git.force_push` | Force push | Admin only | Yes | Manual |
| C12 | `network.http` | Public HTTP/HTTPS | Auto: tool needs | Optional | N/A |
| C13 | `network.internal` | Private/LAN IPs | Admin only | Yes | N/A |
| C14 | `network.file` | file:// protocol | Denied (always) | Yes (deny log) | N/A |
| C15 | `browser.navigate` | Headless browser | Yes (user) | Optional | Tab close |
| C16 | `search.internal` | Local file search | No (auto) | Optional | N/A |
| C17 | `search.external` | Web search | Yes (user) | Optional | N/A |
| C18 | `vision.analyze` | Image processing | No (auto) | Optional | N/A |
| C19 | `delegate.spawn` | Create subagent | Yes (user) | Yes | Kill child |
| C20 | `plugin.call` | Plugin tool invoke | Per-plugin gate | Optional | Disable |
| C21 | `memory.read` | Recall memories | No (auto) | Optional | N/A |
| C22 | `memory.write` | Store memories | No (auto) | Optional | N/A |
| C23 | `config.read` | Config values | No (auto) | Optional | N/A |
| C24 | `config.write` | Config modify | Admin only | Yes | Config backup |
| C25 | `cron.schedule` | Schedule job | Admin confirm | Yes | Job removal |

---

## Prompt Injection Architecture

```
External Content Retrieval
    │
    ▼
┌─────────────────────────────────────────────────────┐
│ STEP 1: FETCH & VALIDATE                            │
│ → Check MIME type (reject: application/octet-stream,│
│   application/x-executable, application/x-sh)       │
│ → Check size (< security.content_max_chars)          │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│ STEP 2: NORMALIZE                                   │
│ → Strip control characters (0x00-0x1F except \n\t)  │
│ → Strip ANSI escape sequences                       │
│ → Strip Unicode control chars (U+200B zero-width)   │
│ → Truncate to max length                            │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│ STEP 3: SCAN FOR DANGER SIGNALS                     │
│ → Pattern: "ignore (previous|all) (instructions"     │
│ → Pattern: "you are now"                            │
│ → Pattern: "system:" (as standalone line)            │
│ → Pattern: "new directive:"                          │
│ → High-entropy strings (>4.5 Shannon) → [REDACTED]  │
│ → Secret-like patterns → [REDACTED]                  │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│ STEP 4: LABEL                                       │
│ → Assign trust label based on source                 │
│ → If suspicious: [SUSPICIOUS_CONTENT]                │
│ → ⚠ Never drop silently — always include label      │
└────────────────────┬────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────┐
│ STEP 5: ISOLATE                                     │
│ → Wrap in boundary markers:                         │
│   ═══ BEGIN [LABEL:source] ───                       │
│   <content>                                          │
│   ═══ END [LABEL:source] ───                         │
│ → Append to USER message, never SYSTEM message       │
│ → Include metadata line: source, fetch_time          │
└─────────────────────────────────────────────────────┘
```

---

## Sandbox Design

```
┌──────────────────────────────────────────────────────────────┐
│  SANDBOX CONFIGURATION                                       │
│                                                               │
│  cwd (Working Directory):                                     │
│    ┌────────────────────────────────────────────────────────┐ │
│    │ /home/user/projects/<project>/                         │ │
│    │   ├── src/        ← R/W (within project)               │ │
│    │   ├── docs/       ← R/W                                │ │
│    │   └── .git/       ← R-only (git manages)               │ │
│    └────────────────────────────────────────────────────────┘ │
│                                                               │
│  Temp Directory:                                              │
│    ┌────────────────────────────────────────────────────────┐ │
│    │ /tmp/hermes-agent/<session-id>/                        │ │
│    │   └── <tool-call-id>/     ← R/W, auto-clean            │ │
│    └────────────────────────────────────────────────────────┘ │
│                                                               │
│  Denied Paths:                                                │
│    ~/.ssh/              ~/.aws/              ~/.gnupg/        │
│    ~/.hermes/           /etc/                /usr/            │
│    /proc/               /sys/                /dev/            │
│    /boot/               /var/log/                            │
│                                                               │
│  Network:                                                     │
│    ✓ HTTP/HTTPS (0.0.0.0/0 except blocked)                   │
│    ✗ 127.0.0.0/8, ::1                                       │
│    ✗ 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16             │
│    ✗ 169.254.0.0/16, 224.0.0.0/4                            │
│                                                               │
│  Resources:                                                   │
│    Shell timeout:   300s                                      │
│    Web fetch:       30s                                       │
│    Git op:          60s                                       │
│    Max file size:   10 MB                                     │
│    Max temp:        500 MB                                    │
│    Max processes:   50                                        │
└──────────────────────────────────────────────────────────────┘
```

---

## Security Roadmap

### Phase 1 — Foundation (Week 1-2)
- [ ] Implement trust boundary validation in tool dispatcher
- [ ] Implement basic path sandbox (deny list + canonicalization)
- [ ] Implement content labeling for external sources
- [ ] Add secret redaction before LLM calls

### Phase 2 — Permissions & Approvals (Week 3-4)
- [ ] Implement 3-tier permission model
- [ ] Build approval UI in CLI, Gateway, and TUI
- [ ] Implement capability model with grant/revoke
- [ ] Add configurable auto-approve rules

### Phase 3 — Hardening (Week 5-6)
- [ ] Implement network isolation (URL validation, SSRF prevention)
- [ ] Implement prompt injection detection (pattern scanning)
- [ ] Implement container sandbox option (Docker/Podman)
- [ ] Implement resource limits (timeouts, sizes, counts)

### Phase 4 — Audit & Recovery (Week 7-8)
- [ ] Implement structured audit logging (JSON Lines)
- [ ] Implement git-based rollback for all file writes
- [ ] Implement workspace snapshots (tar.gz)
- [ ] Implement crash recovery (stale lock cleanup)

### Phase 5 — Testing & CI (Week 9-10)
- [ ] Add security unit tests for all boundaries
- [ ] Add fuzzing harness for tool parameters
- [ ] Integrate bandit, semgrep, gitleaks into CI
- [ ] Run penetration test scenarios (PT-01 to PT-10)
- [ ] Documentation and security guide

---

## Open Questions

1. **Container vs native sandbox**: Should we default to Docker for high-risk
   operations, or is filesystem + network isolation sufficient? Recommendation:
   start with native isolation, add container as opt-in for users who need it.

2. **Prompt injection — heuristic vs ML**: Should we train a classifier for
   injection detection, or remain pattern-based? Recommendation: start
   pattern-based to avoid complexity; revisit if bypasses become common.

3. **Audit log integrity**: Should we implement HMAC chaining for the audit
   log? Recommendation: defer to Phase 4; initially rely on file permissions
   and append-only mode.

4. **Plugin signing**: How do we verify plugin authenticity? Recommendation:
   not in scope for Stage 6; rely on plugin source transparency (human
   review of plugin code) for now.

5. **Cross-workspace boundary**: Should one workspace be able to access
   another workspace's data? Recommendation: No — workspaces are isolated
   at the filesystem level (different HERMES_HOME per workspace).

---

## References

- [ADR-SEC-001: Trust Boundaries](./adr-sec-001-trust-boundaries.md)
- [ADR-SEC-002: Threat Model](./adr-sec-002-threat-model.md)
- [ADR-SEC-003: Capability-Based Security](./adr-sec-003-capability-model.md)
- [ADR-SEC-004: Tool Permission Model](./adr-sec-004-tool-permissions.md)
- [ADR-SEC-005: Prompt Injection Defense](./adr-sec-005-prompt-injection.md)
- [ADR-SEC-006: Secrets Management](./adr-sec-006-secrets-management.md)
- [ADR-SEC-007: Sandbox Strategy](./adr-sec-007-sandbox-strategy.md)
- [ADR-SEC-008: Audit Logging](./adr-sec-008-audit-logging.md)
- [ADR-SEC-009: Recovery Strategy](./adr-sec-009-recovery-strategy.md)
- [ADR-SEC-010: Security Testing Strategy](./adr-sec-010-security-testing.md)
- [Threat Model Summary](#threat-model-summary)
- [Capability Matrix](#capability-matrix)
