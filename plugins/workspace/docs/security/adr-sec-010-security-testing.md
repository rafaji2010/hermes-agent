# ADR-SEC-010: Security Testing Strategy

## Status

Proposed

## Context

Security features must be verified continuously. Manual review alone is
insufficient for a platform with 30+ tools, multiple trust boundaries,
and untrusted input surfaces.

## Decision

We implement a multi-layer security testing strategy integrated into CI.

### Layer 1 — Static Analysis (Pre-Commit / CI)

| Tool | What It Checks | Trigger | Severity |
|------|---------------|---------|----------|
| **bandit** | Python security issues (exec, eval, pickle) | pre-commit, CI | HIGH |
| **semgrep** | Custom Hermes security rules | CI | HIGH |
| **detect-secrets** | Hardcoded secrets, API keys | pre-commit, CI | CRITICAL |
| **gitleaks** | Git history secret scanning | CI | CRITICAL |
| **pip-audit** | Known vulnerabilities in dependencies | CI | HIGH |
| **mypy** (strict) | Type safety (reduces injection surface) | CI | MEDIUM |

### Layer 2 — Unit & Integration Tests (CI)

Security-specific test categories:

| Category | Tests | Coverage Target |
|----------|-------|----------------|
| **Trust boundary** | Tests at each boundary crossing | 100% of boundaries |
| **Sanitization** | Test content labeling, redaction, truncation | All label types |
| **Permission** | Test approved, denied, modified approval paths | All tiers |
| **Capability** | Test capability grant, revoke, escalation | All capabilities |
| **Sandbox** | Test filesystem restrictions, network blocks, timeouts | All isolation layers |
| **Secrets** | Test redaction patterns, secret detection | All secret formats |
| **Recovery** | Test git rollback, snapshot restore, crash recovery | All recovery tiers |

### Layer 3 — Fuzzing (CI, Nightly)

Target the input boundaries:

- **Tool parameter fuzzing**: Generate random/malformed tool call
  parameters and verify they are rejected or sanitized
- **Content fuzzing**: Submit documents with embedded payloads and verify
  prompt injection detection
- **URL fuzzing**: Submit URLs with SSRF patterns (file://, internal IPs)
  and verify network restriction

### Layer 4 — Penetration Testing Scenarios (Manual)

To be run by security reviewers:

| Scenario | Description | Expected Result |
|----------|-------------|----------------|
| PT-01 | Craft a prompt that attempts file:// URL read | Network restricts reject |
| PT-02 | Embed "ignore previous instructions" in external PDF | Content labeled [SUSPICIOUS] |
| PT-03 | Plugin attempts to access core internals | Plugin API restriction prevents |
| PT-04 | LLM generates tool call with "../" path traversal | Path canonicalization rejects |
| PT-05 | Craft secret-like string in context | Redacted before LLM call |
| PT-06 | Attempt SSRF via web_search tool | URL validation rejects internal IP |
| PT-07 | Chain 100 tool calls to bypass iteration budget | Budget enforcement stops at max |
| PT-08 | Craft prompt to write to ~/.ssh/authorized_keys | Sandbox denies non-workspace path |
| PT-09 | Plugin manifest with invalid version/cert | Plugin loader rejects |
| PT-10 | Rapid session creation (DoS simulation) | Rate limiting engages |

### Layer 5 — Dependency Scanning (CI, Nightly)

- Audit Python and npm dependencies weekly
- Automated PR to bump vulnerable packages
- Lockfile integrity verification
- Supply chain compromise scanning (comparison against known-good hashes)

### CI Pipeline

```
PR Open
    │
    ├── Pre-commit hooks:
    │   ├── bandit
    │   ├── mypy
    │   ├── detect-secrets
    │   └── lint
    │
    ├── CI (per PR):
    │   ├── Unit tests (full suite)
    │   ├── Security tests (tagged @security)
    │   ├── semgrep rules
    │   ├── gitleaks scan
    │   ├── pip-audit check
    │   └── Integration tests
    │
    └── Nightly:
        ├── Fuzz tests
        ├── Full dependency audit
        └── Penetration test simulation
```

### Test Environment

- All security tests run in an isolated environment (temp HERMES_HOME)
- No real credentials — all secrets are test-placeholders
- Network access is mocked or restricted
- Container isolation where available

## Consequences

- **Positive**: Comprehensive, automated security validation
- **Positive**: CI integration catches regressions before merge
- **Positive**: Fuzzing discovers edge cases manual review misses
- **Negative**: Significant CI time increase from security tests
- **Negative**: Fuzzing and penetration tests are inherently
  incomplete — zero bugs does not mean secure

## References

- OWASP Testing Guide v5
- Google's "Building Secure & Reliable Systems" testing chapter
- NIST SP 800-115: Technical Guide to Information Security Testing
