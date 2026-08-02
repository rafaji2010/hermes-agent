"""Content Sanitization.

Implements ADR-SEC-005 Layer 3 — control character removal, null-byte
stripping, size limits, Unicode normalization, safe truncation, and
dangerous pattern detection.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List

from .models import SanitizationResult

# ── Constants ───────────────────────────────────────────────────────────────

DEFAULT_MAX_CHARS = 8000

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_NULL_BYTE_RE = re.compile(r"\x00")
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\ufeff]")

# ── Dangerous pattern detection ─────────────────────────────────────────────

_DANGEROUS_PATTERNS = [
    (re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+(instructions?|prompts?)",
                re.IGNORECASE), "injection_ignore_instructions"),
    (re.compile(r"you\s+are\s+now\s+(a\s+)?(different\s+)?(ai|assistant|system)",
                re.IGNORECASE), "injection_role_change"),
    (re.compile(r"^(system|assistant|user):", re.MULTILINE | re.IGNORECASE),
     "injection_role_pretend"),
    (re.compile(r"(override|bypass|disable)\s+(the\s+)?(above|previous)",
                re.IGNORECASE), "injection_override"),
    (re.compile(r"<\|im_start\|>|<\|im_end\|>"), "injection_token_marker"),
    (re.compile(r"```(?:python|bash|sh|javascript)\s",
                re.MULTILINE), "code_block"),
]


def detect_dangerous_patterns(text: str) -> List[str]:
    """Return a list of dangerous pattern names detected in text."""
    found: List[str] = []
    for pattern, name in _DANGEROUS_PATTERNS:
        if pattern.search(text):
            found.append(name)
    return found


# ── Single-value container for pass-by-reference mutation ──────────────────

class _Counter:
    """Mutable integer container."""
    def __init__(self, value: int = 0):
        self.value = value


# ── Sanitization pipeline ──────────────────────────────────────────────────

def sanitize_content(
    content: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    strip_control: bool = True,
    strip_nulls: bool = True,
    strip_ansi: bool = True,
    strip_zero_width: bool = True,
    normalize_unicode: bool = True,
    detect_dangerous: bool = True,
    safe_truncate: bool = True,
) -> SanitizationResult:
    """Sanitize content through multiple passes.

    Returns a ``SanitizationResult`` with the cleaned content and
    metadata about what was changed.
    """
    original_size = len(content)
    ctrl_count = _Counter(0)
    null_count = _Counter(0)

    result = content

    if strip_nulls:
        def _count_null(m):
            null_count.value += 1
            return ""
        result = _NULL_BYTE_RE.sub(_count_null, result)

    if strip_control:
        def _count_ctrl(m):
            ctrl_count.value += 1
            return ""
        result = _CONTROL_CHAR_RE.sub(_count_ctrl, result)

    if strip_nulls:
        def _count_null(m):
            null_count.value += 1
            return ""
        result = _NULL_BYTE_RE.sub(_count_null, result)

    if strip_ansi:
        result = _ANSI_ESCAPE_RE.sub("", result)

    if strip_zero_width:
        result = _ZERO_WIDTH_RE.sub("", result)

    if normalize_unicode:
        result = unicodedata.normalize("NFC", result)

    truncated = False
    if safe_truncate and len(result) > max_chars:
        # Truncate at last whitespace within max_chars to avoid splitting words
        trunc_pos = result.rfind(" ", 0, max_chars)
        if trunc_pos == -1:
            trunc_pos = max_chars
        result = result[:trunc_pos]
        truncated = True

    dangerous: List[str] = []
    if detect_dangerous:
        dangerous = detect_dangerous_patterns(result)

    return SanitizationResult(
        content=result,
        original_size=original_size,
        sanitized_size=len(result),
        truncated=truncated,
        control_chars_removed=ctrl_count.value,
        null_bytes_removed=null_count.value,
        dangerous_patterns=dangerous,
    )
