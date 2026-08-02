"""Workspace AI Assistant Service.

Rule-based assistant that answers engineering questions using the
existing SearchService, GraphService, and AnalyticsService.  Maintains
short-lived conversation context for follow-up questions.

No LLM is used — all answers are deterministic, structured, and
explainable.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Dict, List, Optional

from ..models import (
    AssistantContext,
    ChatRequest,
    ChatResponse,
    ChatMessage,
    ReferencedEntity,
    Suggestion,
    SuggestionsResponse,
)
from .analytics_service import AnalyticsService
from .graph_service import GraphService
from .search_service import SearchService

_log = logging.getLogger("hermes.plugins.workspace.assistant")

# In-memory conversation store (short-lived, process-local)
_CONVERSATIONS: Dict[str, dict] = {}
_CONVERSATION_TTL = 300  # 5 minutes


def _clean_old_conversations():
    now = time.time()
    expired = [cid for cid, v in _CONVERSATIONS.items() if now - v["ts"] > _CONVERSATION_TTL]
    for cid in expired:
        del _CONVERSATIONS[cid]


class WorkspaceAssistantService:
    """Engineering workspace assistant."""

    def __init__(self, search: SearchService, graph: GraphService,
                 analytics: AnalyticsService):
        self._search = search
        self._graph = graph
        self._analytics = analytics

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------

    def chat(self, req: ChatRequest) -> ChatResponse:
        _clean_old_conversations()
        conv_id = req.conversation_id or str(uuid.uuid4())[:8]
        question = req.question.strip().lower()

        # Gather context
        ctx = self._build_context(question, req.workspace_id)
        entities = ctx.entities
        # U1D-C: analytics are computed for the effective workspace only —
        # never global aggregates.
        analytics = self._analytics.get_analytics(req.workspace_id)

        # Check for follow-up references
        if conv_id in _CONVERSATIONS:
            prev = _CONVERSATIONS[conv_id]
            previous_entities = prev.get("entities", [])
            question = self._resolve_references(question, previous_entities)

        # Route to handler
        answer, explanation, confidence = self._answer(question, ctx, analytics, req.workspace_id)

        # Build response
        ref_entities = entities[:8]
        related = [f"{e.type}:{e.title}" for e in ref_entities if e.id]

        resp = ChatResponse(
            conversation_id=conv_id,
            answer=answer,
            referenced_entities=ref_entities,
            analytics_support=ctx.analytics_summary,
            related_items=related,
            confidence=confidence,
            explanation=explanation,
        )

        # Store conversation context
        _CONVERSATIONS[conv_id] = {
            "ts": time.time(),
            "entities": ref_entities,
            "last_question": question,
            "workspace_id": req.workspace_id,
        }

        return resp

    # ------------------------------------------------------------------
    # Context Builder
    # ------------------------------------------------------------------

    def build_context(self, question: str, workspace_id: str = "") -> AssistantContext:
        return self._build_context(question, workspace_id)

    def _build_context(self, question: str, workspace_id: str) -> AssistantContext:
        entities: List[ReferencedEntity] = []
        seen: set = set()

        def add(e_type: str, e_id: str, title: str, status: str = "", relevance: str = ""):
            key = f"{e_type}:{e_id}"
            if key not in seen:
                seen.add(key)
                entities.append(ReferencedEntity(
                    id=e_id, type=e_type, title=title,
                    status=status, relevance=relevance,
                ))

        # Search for directly relevant entities
        search_q = question
        try:
            resp = self._search.search(q=search_q, workspace_id=workspace_id, limit=10)
            for r in resp.results:
                add(r.type, r.id, r.title, r.status, "search_match")
        except Exception:
            pass

        # Expand graph: get related entities for top matches
        for e in list(entities[:5]):
            try:
                # U1D-C: related lookups are workspace-scoped — the graph
                # service rejects entities outside the effective scope.
                related = self._graph.get_related(e.type, e.id, workspace_id)
                for item in related.items[:5]:
                    add(item.type, item.id, item.title, item.status, f"related_to_{e.type}")
            except Exception:
                pass

        # Add workspace-scoped entities if workspace_id is provided
        if workspace_id:
            try:
                tasks = self._graph._storage.list_tasks(workspace_id)
                for t in tasks[:5]:
                    add("task", t.id, t.title, t.status, "workspace_task")
            except Exception:
                pass

        return AssistantContext(
            question=question,
            entities=entities,
            analytics_summary=f"Found {len(entities)} relevant entities.",
            entity_count=len(entities),
        )

    # ------------------------------------------------------------------
    # Answer Engine (rule-based)
    # ------------------------------------------------------------------

    def _answer(self, question: str, ctx: AssistantContext,
                analytics, workspace_id: str) -> tuple:
        q = question.lower()

        # ── Overdue / Blocked ──────────────────────────────────
        if any(w in q for w in ("overdue", "late", "past due")):
            overdue = [e for e in ctx.entities if e.status == "overdue"]
            if overdue:
                items = "\n".join(f"- {e.type}: {e.title}" for e in overdue)
                return (
                    f"Found {len(overdue)} overdue items:\n\n{items}",
                    f"Queried task list for overdue tasks (status not done, due_date < today).",
                    0.95,
                )
            return (
                f"There are {analytics.tasks.overdue} overdue tasks across all workspaces. No specific overdue items were found in this context.",
                f"Task analytics report {analytics.tasks.overdue} overdue tasks total.",
                0.9,
            )

        if any(w in q for w in ("blocked", "blocking")):
            blocked_tasks = [
                e for e in ctx.entities
                if e.status == "blocked" and e.type == "task"
            ]
            if blocked_tasks:
                items = "\n".join(f"- {e.title}" for e in blocked_tasks)
                return (
                    f"Found {len(blocked_tasks)} blocked tasks:\n\n{items}",
                    "Filtered context entities for status=blocked type=task.",
                    0.95,
                )
            return (
                f"There are {analytics.tasks.blocked} blocked tasks total. No blocked tasks in current context.",
                f"Task analytics report {analytics.tasks.blocked} blocked tasks.",
                0.9,
            )

        # ── Summarize roadmap ──────────────────────────────────
        if any(w in q for w in ("summarize roadmap", "roadmap status", "roadmap overview")):
            roadmaps = [e for e in ctx.entities if e.type == "roadmap"]
            if roadmaps:
                lines = []
                for r in roadmaps[:3]:
                    try:
                        rel = self._graph.get_related("roadmap", r.id)
                        ms_count = sum(1 for i in rel.items if i.type == "milestone")
                        task_count = sum(1 for i in rel.items if i.type == "task")
                        lines.append(
                            f"**{r.title}**: {ms_count} milestones, {task_count} tasks"
                        )
                    except Exception:
                        lines.append(f"**{r.title}**")
                return (
                    "Roadmap summary:\n\n" + "\n".join(lines),
                    f"Queried graph for each roadmap to count related milestones and tasks.",
                    0.9,
                )
            return (
                f"Found {analytics.roadmaps.total} roadmaps. Active: {analytics.roadmaps.active}, "
                f"Completed: {analytics.roadmaps.completed}. Average progress: {analytics.roadmaps.avg_progress}%.",
                "Used analytics service for roadmap metrics.",
                0.9,
            )

        # ── What changed this week ─────────────────────────────
        if any(w in q for w in ("week", "today", "recent", "changed")):
            return (
                f"This week: {analytics.journal.entries_this_week} journal entries, "
                f"{analytics.tasks.open} open tasks, "
                f"{analytics.tasks.blocked} blocked tasks, "
                f"{analytics.tasks.overdue} overdue tasks.",
                "Gathered analytics: journal entries this week, task counts.",
                0.85,
            )

        # ── Most active repository ─────────────────────────────
        if any(w in q for w in ("most active repos", "busy repo", "active repository")):
            return (
                f"Most active repository: **{analytics.repositories.most_active}** "
                f"({analytics.repositories.most_active_task_count} tasks).",
                "Queried repository analytics for most active.",
                0.9,
            )

        # ── What should I work on ──────────────────────────────
        if any(w in q for w in ("what should i", "next task", "work on next", "priority")):
            tasks = [e for e in ctx.entities if e.type == "task"]
            high = [t for t in tasks if getattr(t, 'priority', '') == 'high']
            if high:
                return (
                    f"Highest priority: **{high[0].title}**.\n"
                    f"Also consider the {analytics.tasks.open} open tasks, "
                    f"{analytics.tasks.blocked} blocked tasks need unblocking.",
                    "Ranked context entities by priority and status.",
                    0.85,
                )
            return (
                f"{analytics.tasks.open} open tasks to choose from. "
                f"{analytics.tasks.blocked} are blocked — consider unblocking them.\n"
                f"Highest priority tasks are listed in the Tasks page.",
                "Used task analytics for open/blocked counts.",
                0.8,
            )

        # ── What ADRs ──────────────────────────────────────────
        if any(w in q for w in ("adr", "architecture decision")):
            adrs = [e for e in ctx.entities if e.type == "adr"]
            if adrs:
                items = "\n".join(
                    f"- {e.title} ({e.status})" for e in adrs[:5]
                )
                return (
                    f"Found {len(adrs)} ADRs:\n\n{items}",
                    f"Searched for ADRs matching the question.",
                    0.9,
                )
            return (
                f"Found {analytics.adrs.total} ADRs total. "
                f"{analytics.adrs.recently_added} added in the last 30 days.",
                "Used ADR analytics for counts.",
                0.85,
            )

        # ── Generic / fallback ─────────────────────────────────
        entity_types = {}
        for e in ctx.entities:
            entity_types[e.type] = entity_types.get(e.type, 0) + 1
        type_summary = ", ".join(f"{c} {t}s" for t, c in sorted(entity_types.items()))

        return (
            f"I searched for information about your question and found "
            f"{len(ctx.entities)} relevant items ({type_summary or 'none'}).\n\n"
            f"Here is what I know:\n"
            f"- {analytics.tasks.total} tasks ({analytics.tasks.blocked} blocked, {analytics.tasks.overdue} overdue)\n"
            f"- {analytics.roadmaps.total} roadmaps ({analytics.roadmaps.active} active)\n"
            f"- {analytics.adrs.total} ADRs\n"
            f"- {analytics.journal.entries_this_week} journal entries this week\n"
            f"- Writing streak: {analytics.journal.writing_streak_days} days\n\n"
            f"Try a more specific question like: show blocked tasks, "
            f"summarize my roadmap, what ADRs are there, "
            f"what should I work on, or summarize today's activity.",
            f"Built context with {len(ctx.entities)} entities. "
            f"Used search, graph, and analytics services.",
            0.7,
        )

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    def get_suggestions(self, workspace_id: str = "") -> SuggestionsResponse:
        suggestions: List[Suggestion] = []
        try:
            # U1D-C: suggestions are computed for the effective workspace
            # only — never global aggregates.
            analytics = self._analytics.get_analytics(workspace_id)

            if analytics.tasks.blocked > 0:
                suggestions.append(Suggestion(
                    type="warning",
                    title=f"Unblock {analytics.tasks.blocked} tasks",
                    description="Resolve blocked tasks to restore progress.",
                    priority="high",
                ))

            if analytics.tasks.overdue > 0:
                suggestions.append(Suggestion(
                    type="danger",
                    title=f"{analytics.tasks.overdue} overdue tasks",
                    description="Prioritize overdue tasks that are past their due dates.",
                    priority="critical",
                ))

            if analytics.roadmaps.total > 0 and analytics.roadmaps.milestones_blocked > 0:
                suggestions.append(Suggestion(
                    type="warning",
                    title=f"{analytics.roadmaps.milestones_blocked} blocked milestones",
                    description="Address blocked milestones in active roadmaps.",
                    priority="high",
                ))

            if analytics.journal.writing_streak_days == 0:
                suggestions.append(Suggestion(
                    type="tip",
                    title="Start writing a journal entry",
                    description="Regular journal entries help track engineering progress.",
                    priority="medium",
                ))

            if analytics.roadmaps.completed == 0 and analytics.roadmaps.total > 0:
                suggestions.append(Suggestion(
                    type="info",
                    title="Complete a roadmap",
                    description=f"{analytics.roadmaps.total} roadmaps are in progress.",
                    priority="low",
                ))

            if analytics.tasks.open > 0:
                suggestions.append(Suggestion(
                    type="task",
                    title=f"Review {analytics.tasks.open} open tasks",
                    description="Open tasks need prioritization and assignment.",
                    priority="medium",
                ))

            suggestions.append(Suggestion(
                type="prompt",
                title="Ask: What should I work on next?",
                description="Get a prioritized list of engineering tasks.",
            ))

            suggestions.append(Suggestion(
                type="prompt",
                title="Ask: Summarize today's activity",
                description="Get an overview of recent engineering work.",
            ))

        except Exception:
            pass

        return SuggestionsResponse(suggestions=suggestions)

    # ------------------------------------------------------------------
    # Reference resolution (pronouns, previous entities)
    # ------------------------------------------------------------------

    def _resolve_references(self, question: str,
                            previous_entities: List[ReferencedEntity]) -> str:
        q = question
        if not previous_entities:
            return q

        # Simple pronoun / reference resolution
        refs = {
            "it": previous_entities[0] if previous_entities else None,
            "this": previous_entities[0] if previous_entities else None,
            "that": previous_entities[0] if previous_entities else None,
            "them": None,
        }

        resolved = q
        for pronoun, entity in refs.items():
            if entity and pronoun in resolved.split():
                resolved = resolved.replace(pronoun, f"{entity.type} {entity.title}")

        return resolved
