# ADR-SEC-006: Secrets Management

## Status

Proposed

## Context

The Workspace Platform handles credentials for API providers, tools,
platforms, and services. These secrets flow through the system at rest
(in config), in transit (API calls), and in context (prompts to LLMs).

## Decision

We adopt a layered secrets management strategy.

### 1. Storage

- **At rest**: Secrets stored in HERMES_HOME/.env, encrypted at rest
  using OS keyring where available (macOS Keychain, Linux Secret Service
  API / `secret-tool`, Windows Credential Manager)
- **In config**: NO secrets in config.yaml. Config only references
  credential identifiers, not values
- **In code**: NO hardcoded secrets. Zero tolerance — CI enforces this
  with secret scanning (gitleaks, GitHub secret scanning)
- **Provider credentials**: Stored per-provider, namespaced:
  `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, etc.

### 2. Encryption

- **Transport**: All external API calls use TLS 1.3
- **At rest (future)**: Encrypt .env with AES-256-GCM using a key
  derived from machine-specific entropy + optional user passphrase
- **Memory**: Secrets are stored as `SecretStr` in Pydantic models,
  auto-redacted in `__repr__` and `__str__`

### 3. API Key Isolation

| Key Type | Isolation | Rotation | Shared? |
|----------|-----------|----------|---------|
| Provider API keys | Per-provider, one key | Manual (provider console) | Yes (single key per org) |
| Tool credentials | Per-tool env var | Manual | Per-user |
| MCP server tokens | Per-server, stored in config | Server-specific | Per-server |
| Platform tokens (Telegram, etc.) | Per-platform adapter | Per-platform | Per-bot |
| Session tokens | Per-session, ephemeral | Auto (per session) | No (session-scoped) |
| Webhook secrets | Per-webhook, HMAC | Manual | Per-webhook |

### 4. Per-Tool Credentials

Tools that require credentials declare them via `requires_env` in their
registry entry:

```python
registry.register(
    name="example_tool",
    toolset="example",
    requires_env=["EXAMPLE_API_KEY"],
    check_fn=lambda: bool(os.getenv("EXAMPLE_API_KEY")),
)
```

The tool only appears in the agent's toolset when `check_fn` returns True.

### 5. Rotation

- **Provider keys**: Rotated manually via provider consoles
- **Session tokens**: Auto-generated, auto-rotated per session
- **Webhook secrets**: Can be regenerated via `hermes webhook rotate`
- **Audit log**: Records key rotation events

### 6. Redaction

Before any content enters the LLM prompt context:

1. Scan for patterns matching known secret formats (API key regexes)
2. Redact matched patterns to `[REDACTED:provider_name]`
3. Scan for high-entropy strings (>4.5 Shannon entropy)
4. Redact to `[REDACTED:high_entropy]`
5. Log redaction events at INFO level

### 7. Never in Context

The following are **never** sent to the LLM:

- Raw API keys
- Environment variable values matching secret patterns
- File paths containing credentials (~/.ssh, ~/.aws/credentials)
- .env file contents
- `HERMES_HOME` internal paths that could leak profile structure

### 8. CI/CD

- Secret scanning in CI (pre-commit + GitHub Actions)
- No secrets in build artifacts
- No secrets in test fixtures (use environment variables or mocks)
- Test environment has all `HERMES_*` and provider keys unset

## Consequences

- **Positive**: Secrets never reach the LLM, eliminating the most common leak vector
- **Positive**: Layered defense with many independent controls
- **Positive**: Redaction is transparent — user sees `[REDACTED]` in context
- **Negative**: Pattern-based redaction can miss novel secret formats
- **Negative**: High-entropy redaction may over-redact legitimate technical content

## References

- NIST SP 800-57: Recommendation for Key Management
- OWASP Secret Management Cheat Sheet
- Pydantic SecretStr documentation
