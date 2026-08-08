# ADR-SEC-002: Threat Model

## Status

Proposed

## Context

The Workspace Platform processes untrusted content from multiple sources:
user inputs, LLM outputs, external documents, web content, MCP servers,
plugins, and skills. Each source carries different threat vectors.

## Decision

We adopt a structured threat model based on STRIDE with source-specific
analysis.

### Threat Model Summary

| Threat Source | Trust | STRIDE Category | Attack Surface | Primary Mitigation |
|--------------|-------|-----------------|---------------|-------------------|
| User Input | High | Elevation | Prompt injection masquerading as user | Input validation, sandbox |
| User Input | High | Tampering | Malicious file paths, command injection | Path canonicalization, parameter sanitization |
| LLM Response | Low | Spoofing | Tool call fabrication, hallucinated commands | Response validation, tool call schema enforcement |
| LLM Response | Low | Info Disclosure | Leakage of previous conversation secrets | Redaction, short-lived credentials |
| LLM Response | Low | Denial | Recursive tool calls, infinite loops | Iteration budget, timeout enforcement |
| External Documents | Untrusted | Spoofing | Malicious PDFs, poisoned content | Content labeling, MIME validation |
| External Documents | Untrusted | Tampering | Embedded code execution | Sandboxed rendering, no raw execution |
| Web Content | Untrusted | Info Disclosure | SSRF, credential exfiltration | Network isolation, URL validation |
| Web Content | Untrusted | Tampering | XSS in headless browser, DOM manipulation | DOM sandbox, no credential access |
| MCP Servers | Untrusted | Spoofing | Fake MCP servers, tool name collisions | Server auth, first-use approval, toolset allowlist |
| MCP Servers | Untrusted | Elevation | Privileged tool exposure | Capability gating per server |
| Plugins | Untrusted | Elevation | Plugin code execution in agent process | Plugin sandbox, capability model, code review |
| Plugins | Untrusted | Tampering | Plugin manifest manipulation | Manifest signing (future), hash verification |
| Skills | Untrusted | Spoofing | Malicious skill instructions | Skill source verification, content labeling |
| Skills | Untrusted | Info Disclosure | Skill exfiltration attempts | Network sandbox for skill execution |
| Human Instructions | Highest | N/A | Social engineering of operator | Approval gating, audit trail |

### Risk Matrix

```
Impact
    ↑
HIGH│  Web SSRF        │  Plugin Escalation  │ LLM Fabrication
    │  MCP Spoof        │  Prompt Injection   │
    │                   │                     │
    │──────────────────────────────────────────│
    │                   │                     │
MED │  Content Spoof    │  File Tamper        │  Secrets Leak
    │                   │                     │
    │──────────────────────────────────────────│
    │                   │                     │
LOW │  Skill Spam       │  Path Traversal     │  DoS (Iteration)
    │                   │                     │
    └───────────────────┼─────────────────────┼──→ Likelihood
                       LOW                   HIGH
```

### Top 5 Threats (Prioritized)

1. **Plugin Privilege Escalation** (High Impact, Medium Likelihood)
   - A plugin inside the Python process has access to core internals
   - Mitigation: Plugin API surface restriction, capability model
   - Residual risk: High — plugins run in-process

2. **LLM Tool Call Fabrication** (High Impact, Medium Likelihood)
   - The LLM generates tool calls with parameters designed to exfiltrate data
   - Mitigation: Parameter validation, tool schema enforcement, sandbox
   - Residual risk: Medium — validation can catch patterns but not intent

3. **Prompt Injection via External Content** (High Impact, Medium Likelihood)
   - External documents contain prompt-injection payloads
   - Mitigation: Content labeling, instruction/data separation, context isolation
   - Residual risk: Medium — labeling is pattern-based, not semantic

4. **Web Content SSRF** (High Impact, Low Likelihood)
   - Web scraping tools access internal network services
   - Mitigation: URL validation, network isolation, domain allowlist
   - Residual risk: Low — network-layer enforcement

5. **Secrets Leak via LLM Responses** (Medium Impact, Medium Likelihood)
   - Secrets from context appear in LLM-generated responses
   - Mitigation: Secret redaction before sending to LLM, short-lived credentials
   - Residual risk: Medium — redaction is fragile

## Consequences

- Threats are explicitly modeled and ranked
- Mitigations are targeted at the highest-risk surfaces
- Residual risk is documented for acceptance or further mitigation
- Threat model should be reviewed quarterly and after each new tool addition

## References

- STRIDE threat modeling framework (Microsoft)
- OWASP Top 10 for LLM Applications
- MITRE ATLAS (Adversarial Threat Landscape for AI Systems)
