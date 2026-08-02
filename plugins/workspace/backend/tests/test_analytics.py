"""Tests for AnalyticsService."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.services.analytics_service import AnalyticsService, reset_analytics_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_analytics_cache()


@pytest.fixture
def analytics_svc(storage):
    return AnalyticsService(storage)


def test_analytics_empty(analytics_svc):
    data = analytics_svc.get_analytics()
    assert data.roadmaps.total == 0
    assert data.tasks.total == 0


def test_roadmap_analytics(storage, analytics_svc):
    ws = storage.create_workspace("ana-ws", "")
    r = storage.create_roadmap(ws.id, "Q1", "Plan")
    storage.create_milestone(r.id, "M1", "", "completed", "")
    storage.create_milestone(r.id, "M2", "", "in_progress", "")
    storage.create_milestone(r.id, "M3", "", "blocked", "")

    data = analytics_svc.get_analytics()
    assert data.roadmaps.total >= 1
    assert data.roadmaps.total_milestones >= 3
    assert data.roadmaps.milestones_completed >= 1
    assert data.roadmaps.milestones_in_progress >= 1
    assert data.roadmaps.milestones_blocked >= 1


def test_task_analytics(storage, analytics_svc):
    ws = storage.create_workspace("ana-t-ws", "")
    storage.create_task(title="A", workspace_id=ws.id, status="todo", priority="high")
    storage.create_task(title="B", workspace_id=ws.id, status="done", priority="low")
    storage.create_task(title="C", workspace_id=ws.id, status="blocked", priority="critical",
                        due_date="2020-01-01")

    data = analytics_svc.get_analytics()
    assert data.tasks.total >= 3
    assert data.tasks.open >= 2
    assert data.tasks.completed >= 1
    assert data.tasks.blocked >= 1
    assert data.tasks.overdue >= 1


def test_journal_analytics(storage, analytics_svc):
    ws = storage.create_workspace("ana-j-ws", "")
    import datetime
    today = datetime.date.today().isoformat()
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Entry",
        summary="", markdown="", entry_date=today, tags=[],
    )
    data = analytics_svc.get_analytics()
    assert data.journal.entries_this_week >= 1
    assert data.journal.entries_this_month >= 1


def test_trends(storage, analytics_svc):
    ws = storage.create_workspace("ana-tr-ws", "")
    storage.create_task(title="Done", workspace_id=ws.id, status="done")
    data = analytics_svc.get_trends(7)
    assert data.period_days == 7
    assert len(data.task_completion) == 7


def test_insights_blocked_tasks(storage, analytics_svc):
    ws = storage.create_workspace("ana-in-ws", "")
    storage.create_task(title="Blocked task", workspace_id=ws.id, status="blocked")
    data = analytics_svc.get_insights()
    blocked = [i for i in data.insights if "Blocked" in i.title]
    assert len(blocked) >= 1


def test_insights_empty_roadmap(storage, analytics_svc):
    ws = storage.create_workspace("ana-emp-ws", "")
    storage.create_roadmap(ws.id, "Empty", "")
    data = analytics_svc.get_insights()
    empty = [i for i in data.insights if "no milestones" in i.description.lower()]
    assert len(empty) >= 1


def test_repository_analytics(storage, analytics_svc):
    ws = storage.create_workspace("ana-r-ws", "")
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as td:
        import os
        os.makedirs(os.path.join(td, ".git"), exist_ok=True)
        repo = storage.register_repository(ws.id, "test-repo", td, td, "main")
        storage.create_task(title="RepoTask", workspace_id=ws.id, repository_id=repo.id)
        data = analytics_svc.get_analytics()
        assert data.repositories.total >= 1
        assert data.repositories.active >= 1
