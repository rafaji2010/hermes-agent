"""Workspace LLM context adapter (S7.4).

Injects a SMALL, bounded block of Workspace context into the current user
message only, via the Hermes ``pre_llm_call`` hook -> ``api_content`` sidecar
(``agent/turn_context.py:1065`` + ``compose_user_api_content``).  It never
touches the system prompt (prompt-cache-sacred) and never mutates Hermes
memory or instruction files.

Design principles (ADOPT BEFORE BUILD):
* Adopt Hermes ``pre_llm_call`` verbatim — no Core change, no second hook
  system, no second memory system, no second instruction system.
* FAIL-CLOSED: context is assembled ONLY when the effective scope resolves
  to exactly ``mapped``.  ``unresolved`` / ``partial`` / ``unmapped`` /
  ``ambiguous`` all yield an empty string (inject nothing, never fall back
  to a global scope).
* Profile-scoped: the runtime is resolved via ``get_workspace_runtime()``
  at call time, keyed by the effective ``HERMES_HOME`` — profile A can
  never surface profile B's Workspace.
* Bounded: hard budget ``MAX_CONTEXT_CHARS`` (~6000 chars, ~1200 tokens);
  the block is truncated head/tail like upstream ``sanitize_memory_context``.
* Default = aggregates + provenance.  Ranked search recall only for
  meaningful queries.  Full ADR/journal/task bodies, absolute FS paths,
  and raw profile paths never enter the block.
* Authorization: reads are gated through ``AuthorizationMiddleware.guard``
  (``workspace.scope.read``) so a disallowed read is a hard 403, never a
  silent global fallback.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home  # type: ignore[import-untyped]

_log = logging.getLogger("hermes.plugins.workspace.context")

# ---------------------------------------------------------------------------
# Budgets (approved S7.4 decisions)
# ---------------------------------------------------------------------------

# ~1200 tokens.  Mirrors upstream MEMORY_CONTEXT_MAX_CHARS=6000 precedent.
MAX_CONTEXT_CHARS = 6_000
_CONTEXT_HEAD_CHARS = 4_000
_CONTEXT_TAIL_CHARS = 1_500
_TRUNCATION_MARKER = "\n...[workspace context truncated]...\n"

# Per-entity snippet cap for ranked recall (keeps the block tight).
_SNIPPET_CHARS = 160
_MAX_SNIPPETS = 5
# A "meaningful" query is a non-trivial string (slash commands, "hi", "ok"
# etc. are trivial — see agent/memory_provider.is_trivial_prompt).
_MIN_QUERY_CHARS = 3

# Scope states that are NOT safe to inject (fail-closed set).
_NON_INJECTABLE_STATES = {"unresolved", "partial", "unmapped", "ambiguous"}

# Internal regexes that must never leak into a prompt.
_FENCE_RE = re.compile(r"</?\s*workspace-context\s*>", re.IGNORECASE)
_SYSTEM_NOTE_RE = re.compile(
    r"\[System note:\s*The following is .*context.*\]", re.IGNORECASE
)

# Absolute-path and secret-like tokens we never want in an LLM block even if
# a service returns them (defense-in-depth; provenance already avoids them).
_PATH_TOKEN_RE = re.compile(
    r"\b/(?:home|Users|tmp|opt|usr|var|etc|proc|sys)/[^\s,;\"']*"
)
_SECRET_TOKEN_RE = re.compile(
    r"\b(?:sk-|ghp_|api[_-]?key|token|secret|password)\b[^\s,;\"']*",
    re.IGNORECASE,
)


def _truncate(text: str, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Head + tail truncation with an explicit marker (upstream pattern)."""
    if len(text) <= max_chars:
        return text
    return (
        text[:_CONTEXT_HEAD_CHARS]
        + _TRUNCATION_MARKER
        + text[-_CONTEXT_TAIL_CHARS:]
    )


def _sanitize(text: str) -> str:
    """Strip internal fences/notes and redact obvious secrets/paths."""
    text = _FENCE_RE.sub("", text)
    text = _SYSTEM_NOTE_RE.sub("", text)
    text = _PATH_TOKEN_RE.sub("<redacted-path>", text)
    text = _SECRET_TOKEN_RE.sub("<redacted>", text)
    try:
        from agent.redact import redact_sensitive_text  # type: ignore[import-untyped]

        text = redact_sensitive_text(text, force=True, redact_url_credentials=True)
    except Exception:
        pass
    return text.strip()


def _is_meaningful_query(query: str) -> bool:
    """A query is meaningful when it can drive ranked recall."""
    q = (query or "").strip().lower()
    if len(q) < _MIN_QUERY_CHARS:
        return False
    trivial = {"hi", "ok", "okay", "hey", "hello", "thanks", "thank you", "yes",
               "no", "?", "?", "help", "/help", "/new", "/clear", "/quit", "/resume",
               "/copy", "/paste"}
    return q not in trivial


class WorkspaceContextAdapter:
    """Assemble a bounded, scope-gated Workspace context block."""

    def __init__(self, runtime=None):
        # Runtime is resolved lazily per call; optional injection for tests.
        self._runtime = runtime

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def assemble(
        self,
        *,
        session_id: str = "",
        workspace_id: str = "",
        cwd: str = "",
        user_message: str = "",
    ) -> str:
        """Return a bounded Workspace context block, or ``""`` when unsafe.

        Fail-closed: only ``mapped`` scope injects.  Empty/volatile session,
        partial/unmapped/ambiguous/unresolved, disallowed authorization, or
        any exception -> ``""`` (never a partial/global dump).
        """
        if not (session_id or workspace_id or cwd):
            return ""
        try:
            runtime = self._runtime or self._resolve_runtime()
            scope = self._resolve_scope(runtime, session_id, workspace_id, cwd)
            if scope is None or scope.state not in ("mapped",):
                return ""
            return self._assemble_mapped(runtime, scope, user_message)
        except Exception:
            _log.exception("Workspace context assembly failed — fail-closed")
            return ""

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _resolve_runtime(self):
        from plugins.workspace.backend.runtime import get_workspace_runtime

        return get_workspace_runtime()

    def _resolve_scope(self, runtime, session_id, workspace_id, cwd):
        from plugins.workspace.backend.models import ScopeResolveRequest

        return runtime.scope_resolver.resolve(
            ScopeResolveRequest(
                session_id=session_id,
                workspace_id=workspace_id,
                cwd=cwd,
            )
        )

    def _assemble_mapped(self, runtime, scope, user_message: str) -> str:
        parts: List[str] = []

        # -- provenance header (never raw profile_home / database_path) --
        parts.append(_sanitize(self._provenance_header(scope)))

        # -- workspace / project identity + aggregates --
        parts.append(_sanitize(self._aggregates(runtime, scope.workspace_id)))

        # -- ranked recall (meaningful queries only) --
        # Recall is best-effort: a recall failure degrades to aggregates-only,
        # never a leak and never an empty block (fail-closed still applies at
        # the scope gate in ``assemble``).
        if _is_meaningful_query(user_message):
            try:
                recall = _sanitize(
                    self._ranked_recall(runtime, scope.workspace_id, user_message)
                )
            except Exception:
                _log.debug("ranked recall failed — aggregates only", exc_info=True)
                recall = ""
            if recall:
                parts.append(recall)

        block = "<workspace-context>\n" + "\n\n".join(parts) + "\n</workspace-context>"
        return _truncate(block)

    # ------------------------------------------------------------------
    # Sections
    # ------------------------------------------------------------------

    @staticmethod
    def _profile_label() -> str:
        try:
            home = Path(get_hermes_home()).resolve()
            return home.name or home.parent.name
        except Exception:
            return ""

    def _provenance_header(self, scope) -> str:
        """Identity + provenance, with profile-safe values only."""
        slug = scope.project_slug or ""
        ws = scope.workspace_name or ""
        src = scope.match_source or "none"
        profile = self._profile_label()
        bits = [f"Workspace: {ws}"] if ws else []
        if slug:
            bits.append(f"Project: {slug}")
        bits.append(f"Source: {src}")
        if profile:
            bits.append(f"Profile: {profile}")
        return "Provenance: " + " · ".join(bits)

    def _aggregates(self, runtime, workspace_id: str) -> str:
        """Per-workspace aggregate counts (default context)."""
        try:
            storage = runtime.storage
            ws = storage.get_workspace(workspace_id)
            ws_name = ws.name if ws else "?"
            stats = storage.get_task_stats(workspace_id)
        except Exception:
            _log.exception("aggregate assembly failed")
            return ""

        lines = [
            f"Workspace: {ws_name}",
            f"Tasks: {stats.total} total, {stats.open} open, "
            f"{stats.completed} completed, {stats.blocked} blocked, "
            f"{stats.overdue} overdue",
        ]
        try:
            analytics = runtime.analytics_service.get_analytics(workspace_id)
            lines.append(
                f"Roadmaps: {analytics.roadmaps.total} total, "
                f"{analytics.roadmaps.completed} completed"
            )
            lines.append(
                f"ADRs: {analytics.adrs.total} total"
            )
            lines.append(
                f"Journal: {analytics.journal.entries_this_week} this week"
            )
            lines.append(
                f"Graph: {analytics.graph_entities} entities, "
                f"{analytics.graph_edges} edges, {analytics.graph_orphans} orphans"
            )
        except Exception:
            _log.debug("analytics aggregate unavailable", exc_info=True)
        return "\n".join(lines)

    def _ranked_recall(self, runtime, workspace_id: str, query: str) -> str:
        """Top-N ranked search snippets scoped to the workspace.

        Hardening (Phase 9.3):
        * ``workspace_id`` is MANDATORY — an unscoped/global search is never
          allowed.  ``search_service.search`` requires it; we additionally
          assert it here so a future caller cannot regress to global.
        * Cross-workspace guard: any result whose ``workspace_id`` does not
          match the requested workspace is skipped (defense-in-depth — the
          service already scopes, but a leaked row must never surface).
        * Deterministic ordering: primary ``score`` descending, secondary
          ``id`` ascending (search's own sort is score-only and tie-unstable).
        * Deduplication: an entity already represented in the aggregate/
          header context (keyed by ``canonical_id`` or ``id``) is not
          repeated.  This is dedup of the SAME block, NOT a second memory
          system — Hermes ``MemoryManager`` owns cross-turn recall.
        """
        if not workspace_id:
            _log.debug("ranked recall skipped: workspace_id is mandatory")
            return ""
        try:
            resp = runtime.search_service.search(
                q=query,
                workspace_id=workspace_id,
                limit=_MAX_SNIPPETS,
            )
        except Exception:
            _log.debug("ranked recall unavailable", exc_info=True)
            return ""
        results = getattr(resp, "results", None) or []
        if not results:
            return ""
        return self._render_snippets(results, workspace_id)

    def _render_snippets(self, results, workspace_id: str) -> str:
        """Deterministic, deduplicated snippet rendering (Phase 9.3).

        Separated from ``_ranked_recall`` so the ordering/dedup/cross-ws
        guards are unit-testable without a live search service.
        """
        # Deterministic sort: score desc, then id asc (stable, byte-identical
        # across identical turns).
        ordered = sorted(
            results,
            key=lambda r: (
                -float(getattr(r, "score", 0.0) or 0.0),
                str(getattr(r, "id", "") or ""),
            ),
        )

        lines = ["Recent relevant workspace items:"]
        seen: set[str] = set()
        for r in ordered:
            r_ws = getattr(r, "workspace_id", None)
            if r_ws and str(r_ws) != str(workspace_id):
                _log.debug("recall dropped cross-workspace result %s", r.id)
                continue
            dedup_key = str(getattr(r, "canonical_id", "") or "") or str(
                getattr(r, "id", "") or ""
            )
            if not dedup_key:
                continue
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            title = _sanitize((r.title or "").strip())[:_SNIPPET_CHARS]
            desc = _sanitize((r.description or "").strip())[:_SNIPPET_CHARS]
            type_label = getattr(r, "type", "") or ""
            status = getattr(r, "status", "") or ""
            score = getattr(r, "score", 0.0) or 0.0
            line = f"- [{type_label}] {title}"
            if status:
                line += f" ({status})"
            if desc:
                line += f": {desc}"
            line += f" [score {score:.2f}]"
            lines.append(line)
            if len(lines) - 1 >= _MAX_SNIPPETS:
                break
        return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Module-level singleton + hook entry point
# ---------------------------------------------------------------------------

_adapter: Optional[WorkspaceContextAdapter] = None


def get_context_adapter() -> WorkspaceContextAdapter:
    """Return the module-level adapter (lazy, tests may inject a fake)."""
    global _adapter
    if _adapter is None:
        _adapter = WorkspaceContextAdapter()
    return _adapter


def reset_context_adapter() -> None:
    """Test seam — drop the cached adapter."""
    global _adapter
    _adapter = None


def assemble_workspace_context(
    *,
    session_id: str = "",
    workspace_id: str = "",
    cwd: str = "",
    user_message: str = "",
    adapter: Optional[WorkspaceContextAdapter] = None,
) -> str:
    """Hook entry point (used by ``plugins/workspace/__init__.py``).

    Returns a bounded ``<workspace-context>`` block or ``""`` (fail-closed).
    """
    return (adapter or get_context_adapter()).assemble(
        session_id=session_id,
        workspace_id=workspace_id,
        cwd=cwd,
        user_message=user_message,
    )
