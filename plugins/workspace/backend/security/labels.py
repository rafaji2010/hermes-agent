"""Content Labeling System.

Assigns origin metadata and trust levels to content entering the
Workspace system.  Implements ADR-SEC-005 Layer 2.
"""

from __future__ import annotations

from .models import ContentLabel

LABEL_TRUSTED_USER = "trusted_user"
LABEL_WORKSPACE_DATA = "workspace_data"
LABEL_EXTERNAL_DOCUMENT = "external_document"
LABEL_REPOSITORY_CONTENT = "repository_content"
LABEL_WEB_CONTENT = "web_content"
LABEL_LLM_OUTPUT = "llm_output"
LABEL_MCP_RESPONSE = "mcp_response"
LABEL_PLUGIN_OUTPUT = "plugin_output"
LABEL_UNKNOWN = "unknown"

# ── Label metadata per source ──────────────────────────────────────────────

_LABEL_PROFILES = {
    "user": {
        "source": LABEL_TRUSTED_USER,
        "trust_level": "trusted",
    },
    "file": {
        "source": LABEL_WORKSPACE_DATA,
        "trust_level": "trusted",
    },
    "repo": {
        "source": LABEL_REPOSITORY_CONTENT,
        "trust_level": "trusted",
    },
    "web": {
        "source": LABEL_WEB_CONTENT,
        "trust_level": "untrusted",
    },
    "document": {
        "source": LABEL_EXTERNAL_DOCUMENT,
        "trust_level": "untrusted",
    },
    "llm": {
        "source": LABEL_LLM_OUTPUT,
        "trust_level": "untrusted",
    },
    "mcp": {
        "source": LABEL_MCP_RESPONSE,
        "trust_level": "untrusted",
    },
    "plugin": {
        "source": LABEL_PLUGIN_OUTPUT,
        "trust_level": "untrusted",
    },
}


def label_content(
    source_type: str,
    *,
    mime_type: str = "",
    origin_url: str = "",
    origin_path: str = "",
    size_bytes: int = 0,
    truncated: bool = False,
    suspicious: bool = False,
    sanitized: bool = False,
    **metadata,
) -> ContentLabel:
    """Create a content label for the given source type.

    Source types: ``user``, ``file``, ``repo``, ``web``, ``document``,
    ``llm``, ``mcp``, ``plugin``.

    Returns a ``ContentLabel`` with origin metadata.
    """
    from datetime import datetime, UTC

    profile = _LABEL_PROFILES.get(source_type, {
        "source": LABEL_UNKNOWN,
        "trust_level": "unknown",
    })

    return ContentLabel(
        source=profile["source"],
        trust_level=profile["trust_level"],
        mime_type=mime_type,
        origin_url=origin_url,
        origin_path=origin_path,
        fetch_time=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        size_bytes=size_bytes,
        truncated=truncated,
        suspicious=suspicious,
        sanitized=sanitized,
        metadata=dict(metadata),
    )


def get_label_prefix(label: ContentLabel) -> str:
    """Return a human-readable prefix for a content label.

    Example: ``[WEB:https://example.com]``
    """
    if label.source == LABEL_TRUSTED_USER:
        return "[USER]"
    if label.source == LABEL_WORKSPACE_DATA:
        return f"[FILE:{label.origin_path}]" if label.origin_path else "[FILE]"
    if label.source == LABEL_WEB_CONTENT:
        return f"[WEB:{label.origin_url}]" if label.origin_url else "[WEB]"
    if label.source == LABEL_EXTERNAL_DOCUMENT:
        return f"[DOC:{label.origin_path}]" if label.origin_path else "[DOC]"
    if label.source == LABEL_LLM_OUTPUT:
        return "[LLM]"
    if label.source == LABEL_MCP_RESPONSE:
        return "[MCP]"
    if label.source == LABEL_PLUGIN_OUTPUT:
        return "[PLUGIN]"
    return f"[{label.source.upper()}]"


def format_safe_boundary(label: ContentLabel, content: str) -> str:
    """Wrap content in safe boundary markers with label metadata.

    Format::

        ═══ BEGIN [LABEL:source] (trust:level, size:NNN) ═══
        <content>
        ═══ END [LABEL:source] ═══
    """
    prefix = get_label_prefix(label)
    header = f"═══ BEGIN {prefix} (trust:{label.trust_level}, size:{label.size_bytes}) ═══"
    footer = f"═══ END {prefix} ═══"
    return f"{header}\n{content}\n{footer}"
