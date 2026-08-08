# ADR-SEC-001: Trust Boundaries

## Status

Proposed

## Context

The Hermes Workspace Platform operates at the intersection of multiple
untrusted input surfaces: user prompts, LLM-generated responses, external
documents, web content, MCP servers, plugins, and skills. Each surface
represents a different trust level and attack surface.

Without clear trust boundaries, a compromise in one component can escalate to
the entire system.

## Decision

We define the following trust boundaries with explicit isolation:

### T1 — Human User → Hermes Core

```
[Human User] ──(trusted)──▶ [CLI / Gateway / TUI]
```

- **Trust level:** Highest
- **Inputs:** Slash commands, chat messages, configuration
- **Boundary:** Authentication (platform adapters), input validation
- **What crosses:** Plain text, structured commands, workspace selection

### T2 — Hermes Core → Planner / Policy Engine

```
[Hermes Core] ──(trusted)──▶ [Policy Engine] ──▶ [Approval Layer]
```

- **Trust level:** High (internal)
- **What crosses:** Tool call requests, capability requests
- **Boundary:** Policy evaluation, capability checks
- **Enforcement:** Before every tool invocation

### T3 — Approval Layer → Tool Sandbox

```
[Approval Layer] ──(gated)──▶ [Tool Sandbox] ──▶ [Filesystem / Git / Network]
```

- **Trust level:** Medium (gated by approval)
- **What crosses:** Approved tool executions, file paths, git operations
- **Boundary:** Sandbox isolation (working directory, filesystem, network, timeouts)
- **Enforcement:** Per-tool sandbox configuration

### T4 — Tool Sandbox → Filesystem

```
[Tool Sandbox] ──(restricted)──▶ [Workspace Directory]
                             ──▶ [Temp Directory]
                             ✗──▶ [System Directories]
```

- **Trust level:** Low (sandboxed)
- **Boundary:** Directory whitelist, symlink restrictions, mount namespace
- **Read:** Workspace root + configured repos
- **Write:** Workspace root + temp only
- **Denied:** /etc, /usr, /proc, /sys, ~/.ssh, ~/.hermes (outside workspace)

### T5 — Tool Sandbox → Git

```
[Tool Sandbox] ──(restricted)──▶ [Git Operations]
```

- **Trust level:** Low
- **Boundary:** Git operations gated by capability flags (commit, push, force-push)
- **Allowed:** status, diff, log, branch (read-only)
- **Gated:** commit, push, tag, force-push (require approval + audit)

### T6 — Tool Sandbox → Network

```
[Tool Sandbox] ──(restricted)──▶ [HTTP/HTTPS Only]
                             ✗──▶ [Localhost Services]
                             ✗──▶ [Internal Networks]
```

- **Trust level:** Low
- **Boundary:** URL validation, protocol restriction, domain allowlist
- **Allowed:** HTTP/HTTPS to public internet
- **Denied:** file://, localhost, 127.0.0.1, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16
- **Configurable:** Domain allowlist for enterprise deployments

### T7 — External Content → Agent Context

```
[External Content] ──(untrusted)──▶ [Content Parser]
                                ──▶ [Sanitization]
                                ──▶ [Labeled Context]
```

- **Trust level:** Untrusted
- **Boundary:** Content labeling, prompt sanitization, size limits
- **What crosses (after processing):** Labeled, sanitized text with source metadata
- **Never crosses raw:** Unlabeled content, executable scripts, binary data

### T8 — LLM Response → Agent

```
[LLM] ──(untrusted)──▶ [Response Parser]
                    ──▶ [Tool Call Extractor]
                    ──▶ [Policy Re-evaluation]
```

- **Trust level:** Untrusted
- **Boundary:** Tool call validation, parameter sanitization, re-policy-check
- **What crosses (after processing):** Validated tool calls with sanitized parameters

### T9 — MCP Server / Plugin → Agent

```
[MCP Server] ──(untrusted)──▶ [MCP Client]
                           ──▶ [Tool Gate]
```

- **Trust level:** Untrusted
- **Boundary:** MCP server authentication, tool set allowlist, capability gating
- **Gated by:** User approval on first connection, periodic re-auth

### T10 — Workspace Plugin → Core

```
[Workspace Plugin] ──(trusted)──▶ [Hermes Core API]
```

- **Trust level:** High (plugin runs in same process)
- **Boundary:** Plugin registration validation, API surface restriction
- **What crosses:** REST API calls, method invocations

## Consequences

- **Positive:** Clear isolation boundaries prevent lateral movement from any single compromise
- **Positive:** Each boundary can be independently tested and hardened
- **Negative:** Performance overhead from serialization/crossing operations at each boundary
- **Negative:** Complexity in debugging issues that cross multiple boundaries

## References

- OWASP Threat Modeling
- Capability-based security model (Capability Myths Demolished, Miller 2006)
- Docker/container security patterns
