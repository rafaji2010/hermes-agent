# ADR-SEC-008: Audit Logging

## Status

Proposed

## Context

Security-relevant operations (file writes, git actions, approval decisions,
shell execution, tool calls) must be traceable. Without audit logging,
security incidents cannot be investigated or attributed.

## Decision

We implement structured audit logging with mandatory events for all
security-sensitive operations.

### Audit Event Schema

```json
{
  "event_id": "uuid",
  "timestamp": "ISO 8601",
  "actor": "session-id or user-id",
  "action": "category.action",
  "status": "success|denied|error|approved|denied_by_user",
  "resource": { "type": "file|git|tool|config", "id": "..." },
  "details": { "before": "...", "after": "...", "diff": "..." },
  "session_id": "...",
  "hermes_home": "...",
  "source_ip": "...", (gateway only)
  "platform": "cli|telegram|discord|..."
}
```

### Mandatory Audit Events

| Event | When | Level |
|-------|------|-------|
| `tool.invoke` | Any tool execution | INFO |
| `tool.denied` | Tool denied by policy | WARNING |
| `tool.error` | Tool execution failed | ERROR |
| `file.write` | File created or modified | INFO |
| `file.delete` | File deleted | WARNING |
| `git.commit` | Git commit created | INFO |
| `git.push` | Git push to remote | WARNING |
| `git.force_push` | Git force push | CRITICAL |
| `shell.exec` | Shell command executed | INFO |
| `shell.sudo` | Shell command with elevated permissions | CRITICAL |
| `approval.granted` | User approved operation | INFO |
| `approval.denied` | User denied operation | INFO |
| `approval.modified` | User modified and approved | INFO |
| `session.start` | New session created | INFO |
| `session.end` | Session terminated | INFO |
| `secret.redacted` | Secret redacted from context | INFO |
| `secret.redact_fail` | Redaction pattern matched but failed | WARNING |
| `injection.detected` | Suspicious content detected | WARNING |
| `policy.change` | Security policy modified | WARNING |
| `capability.granted` | Capability granted | INFO |
| `capability.revoked` | Capability revoked | INFO |
| `config.write` | Config file modified | WARNING |
| `plugin.load` | Plugin loaded | INFO |
| `plugin.error` | Plugin error | ERROR |
| `auth.failure` | Authentication failure | WARNING |

### Storage

- **Default**: File-based in `HERMES_HOME/logs/audit.log`
- **Format**: JSON Lines (one JSON object per line)
- **Rotation**: Daily rotation, keep 90 days, compress old files
- **Remote** (optional): Syslog, Elasticsearch, or HTTP webhook forwarding

### Query and Search

Audit logs are searchable via:
- `hermes logs --audit` — filtered audit log view
- `hermes audit search tool=shell.exec status=denied` — query interface
- Direct access: `~/.hermes/logs/audit.log` + jq

### Privacy

- Audit logs never contain raw secrets (redacted before logging)
- User messages are not logged (tool calls are, content is not)
- Session IDs are logged, not personal identifiers

### Integrity

- Audit log file is append-only (file permissions)
- Log tampering is detectable via checksum chain (future: HMAC
  chaining)
- Admin alerts on log file truncation/modification

## Consequences

- **Positive**: Every security-relevant action is traceable
- **Positive**: Structured format enables automated analysis
- **Positive**: Privacy-preserving — no user message content in audit
- **Negative**: Log volume may be high in production (mitigated by rotation)
- **Negative**: File-based log is vulnerable to local tampering (mitigated
  by remote forwarding option)

## References

- NIST SP 800-92: Guide to Computer Security Log Management
- OWASP Logging Cheat Sheet
- Linux Audit daemon (auditd) design patterns
