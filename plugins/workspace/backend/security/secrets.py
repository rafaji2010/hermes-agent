"""Secret Detection & Redaction.

Implements ADR-SEC-006 Layer 6 — detect and redact API keys, tokens,
passwords, bearer tokens, and private keys before content enters the
LLM prompt context.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Tuple

# ── Secret patterns ─────────────────────────────────────────────────────────

_SECRET_PATTERNS = [
    # API key patterns
    (re.compile(r"(?:api[_-]?key|apikey|api)\s*[:=]\s*['\"]?\s*([a-zA-Z0-9_\-\.]{16,})['\"]?",
                re.IGNORECASE), "api_key", 1),

    # Bearer tokens
    (re.compile(r"(?:bearer|auth(?:orization)?)\s+([a-zA-Z0-9_\-\.=]{20,})",
                re.IGNORECASE), "bearer_token", 1),

    # AWS-style access keys (AKIA...)
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "aws_access_key", 1),

    # Generic token patterns (sk-, ghp_, glpat-, etc.)
    (re.compile(r"\b((?:sk|pk|ghp|glpat|hf|hf_|xox[bpras]|t\.[a-f0-9]+)-[a-zA-Z0-9_\-]{16,})\b"),
     "service_token", 1),

    # OpenAI / Anthropic key patterns
    (re.compile(r"\b(sk-[a-zA-Z0-9]{32,})\b"), "provider_key", 1),
    (re.compile(r"\b(sk-ant-[a-zA-Z0-9\-_]{40,})\b"), "provider_key", 1),

    # Private key headers (PEM)
    (re.compile(r"-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PGP)\s+PRIVATE\s+KEY-----"),
     "private_key", 0),

    # Connection strings with passwords
    (re.compile(r"://[^:@\s]+:([^@\s]+)@"), "connection_string", 1),

    # JWT tokens
    (re.compile(r"\b(eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,})\b"),
     "jwt_token", 1),
]

# Redaction placeholders — stable per match to preserve partial context
_REDACT_PREFIX = "[REDACTED:{}]"
_used_placeholders: int = 0


def _reset_placeholders():
    global _used_placeholders
    _used_placeholders = 0


def _next_placeholder(label: str) -> str:
    global _used_placeholders
    _used_placeholders += 1
    return _REDACT_PREFIX.format(f"{label}_{_used_placeholders}")


# ── Shannon entropy ─────────────────────────────────────────────────────────

def _shannon_entropy(text: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not text:
        return 0.0
    n = len(text)
    counts = Counter(text)
    entropy = 0.0
    for count in counts.values():
        p = count / n
        entropy -= p * math.log2(p)
    return entropy


# ── Detection ───────────────────────────────────────────────────────────────

def detect_secrets(text: str) -> List[Tuple[str, str, str, int]]:
    """Detect secrets in text.

    Returns a list of ``(label, secret_preview, full_match, position)`` tuples.
    """
    found: List[Tuple[str, str, str, int]] = []

    for pattern, label, group_idx in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            full = match.group(0)
            # For grouped patterns, use the captured group
            if group_idx > 0 and match.lastindex and match.lastindex >= group_idx:
                secret = match.group(group_idx)
            else:
                secret = full
            preview = secret[:8] + "..." if len(secret) > 8 else secret
            found.append((label, preview, full, match.start()))

    # Sort by position (left to right) so redaction is stable
    found.sort(key=lambda x: x[3])
    return found


# ── Redaction ───────────────────────────────────────────────────────────────

def redact_secrets(text: str, *, min_entropy: float = 4.5) -> str:
    """Redact secrets from text, replacing them with stable placeholders.

    Also redacts high-entropy substrings that may be unrecognized secrets.
    Returns the sanitized text.
    """
    _reset_placeholders()
    result = text
    offset = 0

    secrets = detect_secrets(result)
    for label, preview, full, pos in secrets:
        adjusted_pos = pos + offset
        placeholder = _next_placeholder(label)
        result = result[:adjusted_pos] + placeholder + result[adjusted_pos + len(full):]
        offset += len(placeholder) - len(full)

    # High-entropy redaction pass: scan for long high-entropy substrings
    # that weren't caught by patterns
    words = result.split()
    new_words = []
    for word in words:
        if len(word) >= 16 and _shannon_entropy(word) >= min_entropy:
            # Check if it's not already redacted
            if _REDACT_PREFIX.format("") not in word:
                new_words.append(_next_placeholder("high_entropy"))
            else:
                new_words.append(word)
        else:
            new_words.append(word)
    result = " ".join(new_words)

    return result
