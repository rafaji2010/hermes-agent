"""Tests for WorkspaceAssistantService."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import ChatRequest
from plugins.workspace.backend.services.analytics_service import AnalyticsService, reset_analytics_cache
from plugins.workspace.backend.services.assistant_service import WorkspaceAssistantService
from plugins.workspace.backend.services.graph_service import GraphService
from plugins.workspace.backend.services.search_service import SearchService


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_analytics_cache()


@pytest.fixture
def assistant_svc(storage):
    search = SearchService(storage)
    graph = GraphService(storage)
    analytics = AnalyticsService(storage)
    return WorkspaceAssistantService(search, graph, analytics)


@pytest.fixture
def populated(storage):
    ws = storage.create_workspace("asst-ws", "")
    r = storage.create_roadmap(ws.id, "Q1", "Q1 goals")
    storage.create_milestone(r.id, "M1", "", "completed", "")
    storage.create_milestone(r.id, "M2", "", "in_progress", "")
    storage.create_task(title="Fix bug", workspace_id=ws.id, status="blocked",
                        priority="high", labels=["bug"])
    storage.create_task(title="Overdue", workspace_id=ws.id, status="todo",
                        due_date="2020-01-01")
    return ws.id


def test_chat_generic(populated, assistant_svc):
    req = ChatRequest(question="What tasks are there?", workspace_id=populated)
    resp = assistant_svc.chat(req)
    assert len(resp.answer) > 0
    assert resp.confidence > 0
    assert resp.conversation_id


def test_chat_blocked(populated, assistant_svc):
    req = ChatRequest(question="What is blocked?", workspace_id=populated)
    resp = assistant_svc.chat(req)
    assert "blocked" in resp.answer.lower()


def test_chat_roadmap(populated, assistant_svc):
    req = ChatRequest(question="Summarize roadmap", workspace_id=populated)
    resp = assistant_svc.chat(req)
    assert len(resp.answer) > 0


def test_chat_week(populated, assistant_svc):
    req = ChatRequest(question="What changed this week?", workspace_id=populated)
    resp = assistant_svc.chat(req)
    assert len(resp.answer) > 0


def test_context_builder(populated, assistant_svc):
    ctx = assistant_svc.build_context("blocked", populated)
    assert ctx.entity_count >= 0
    assert ctx.question == "blocked"


def test_follow_up(populated, assistant_svc):
    req1 = ChatRequest(question="Show blocked tasks", workspace_id=populated)
    resp1 = assistant_svc.chat(req1)
    cid = resp1.conversation_id

    req2 = ChatRequest(question="What about milestones?", conversation_id=cid,
                       workspace_id=populated)
    resp2 = assistant_svc.chat(req2)
    assert resp2.conversation_id == cid


def test_suggestions(assistant_svc):
    resp = assistant_svc.get_suggestions()
    assert len(resp.suggestions) > 0


def test_empty_workspace(assistant_svc):
    req = ChatRequest(question="What tasks?")
    resp = assistant_svc.chat(req)
    assert len(resp.answer) > 0
