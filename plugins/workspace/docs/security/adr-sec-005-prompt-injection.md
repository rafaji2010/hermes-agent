# ADR-SEC-005: Prompt Injection Defense

## Status

Proposed

## Context

The Workspace Platform processes user prompts alongside potentially
injected content from external documents, web pages, and LLM responses.
Traditional prompt injection attacks exploit the lack of separation
between instructions and data.

In the Workspace context, the agent reads external documents, processes
web content, and interacts with files — all of which may contain
instruction-like text designed to override or manipulate the agent's
behavior.

## Decision

We implement a defense-in-depth strategy with five layers.

### Layer 1 — Instruction/Data Separation

Strict structural separation between system instructions and user/retrieved
data, enforced at the message format level.

```
System Prompt (instructions, rules, policies)
  → Role: "system"
  → Immutable for the session
  → Never mixed with data

User Message (direct input)
  → Role: "user"
  → Labeled as user-origin
  → Follows instruction rules

Retrieved Data (external content)
  → Role: "user", labeled with [EXTERNAL_CONTENT] prefix
  → Surrounded by boundary markers: ═══ BEGIN ─── / ═══ END ───
  → Metadata header: source, fetch time, trust level
```

### Layer 2 — Content Labeling

All content that enters the prompt context receives a label:

⚠ NEVER inject unlabeled data into the prompt stream.

| Label | Source | Trust | Format |
|-------|--------|-------|--------|
| `[USER]` | Human input | High | Direct message |
| `[SYSTEM]` | System prompt | Highest | Immutable header |
| `[FILE:path]` | Workspace file | Low | Wrapped in boundary markers |
| `[WEB:url]` | Web extraction | Untrusted | Wrapped + sanitized |
| `[DOC:type]` | External document | Untrusted | Wrapped + sanitized |
| `[SKILL:name]` | Skill instruction | Medium | Source-attributed |
| `[MEMORY]` | Retrieved memory | Medium | Source-attributed |
| `[SEARCH:query]` | Search result | Untrusted | Wrapped + truncated |

### Layer 3 — Prompt Sanitization

Before any external content enters the context:

1. **Truncation**: Content is limited to `security.content_max_chars` (default: 8000)
2. **Control character stripping**: Remove null bytes, ANSI escapes, Unicode control chars
3. **Boundary escaping**: Escape any content that matches the boundary marker pattern
4. **Instruction pattern detection**: Scan for patterns like "ignore previous instructions",
   "you are now", "system: new directive". Flag at `[SUSPICIOUS_CONTENT]` label

### Layer 4 — Context Isolation

External content is isolated from the system prompt:

- External content is **appended** to user messages, not the system prompt
- The system prompt is **cached** and never rebuilt mid-conversation
- Retrieved content has a **freshness TTL** — stale content is discarded
- Content from different sources is **never concatenated** without separators

### Layer 5 — Safe Quoting Rules

When the agent needs to quote or reference external content in its response:

1. Use explicit attribution: `From [FILE:path]: "..." — created by <author>`
2. Include source metadata in the response
3. Never repeat raw content verbatim without labeling
4. Strip executable / script-like content (`#!/`, `# shell:`, etc.) from quoted text

### Defense Flow

```
External Content Request
    │
    ▼
Fetch & Validate MIME type
    │
    ▼
Truncate to max size
    │
    ▼
Strip control characters
    │
    ▼
Check for instruction patterns [SUSPICIOUS?]
    │
    ▼
Wrap in boundary markers
    │
    ▼
Label with source metadata
    │
    ▼
Append to user message (NEVER system)
    │
    ▼
Send to LLM
```

### Response to Injection Detection

When suspicious content is detected:

1. Content is still provided (no silent dropping) but labeled `[SUSPICIOUS_CONTENT]`
2. A warning is added: "⚠ The following content may contain misleading instructions..."
3. The incident is logged at WARNING level
4. The user is notified in the UI

## Consequences

- **Positive**: Five-layer defense reduces probability of successful injection
- **Positive**: Transparent handling (no silent content modification)
- **Positive**: Compatible with prompt caching (system prompt never mutates)
- **Negative**: Boundary markers consume context window tokens
- **Negative**: Pattern-based detection can be bypassed with creative wording
- **Residual risk**: LLMs are inherently vulnerable to well-crafted injections;
  the defense is probabilistic, not absolute

## References

- OWASP LLM01: Prompt Injection
- Anthropic's prompt injection defense research
- "Ignore Previous Instructions" taxonomy (Goodside, 2023)
