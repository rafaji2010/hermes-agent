"""Tests for task + comment + dependency storage operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (
    CircularDependencyError,
    InvalidTaskPriorityError,
    InvalidTaskStatusError,
    TaskNotFoundError,
)


def test_create_task_minimal(storage):
    ws = storage.create_workspace("task-ws", "")
    t = storage.create_task(title="Test", workspace_id=ws.id)
    assert t.title == "Test"
    assert t.status == "todo"
    assert t.priority == "medium"
    assert t.workspace_id == ws.id


def test_create_task_full(storage):
    ws = storage.create_workspace("full-ws", "")
    t = storage.create_task(
        title="Full Task", description="desc", status="in_progress",
        priority="high", workspace_id=ws.id, labels=["backend", "api"],
        estimate_hours=2.5, due_date="2026-07-30",
    )
    assert t.description == "desc"
    assert t.status == "in_progress"
    assert t.priority == "high"
    assert sorted(t.labels) == ["api", "backend"]
    assert t.estimate_hours == 2.5
    assert t.due_date == "2026-07-30"


def test_task_with_links(storage):
    ws = storage.create_workspace("link-ws", "")
    r = storage.create_roadmap(ws.id, "Q1", "")
    m = storage.create_milestone(r.id, "M1", "", "planned", "")
    t = storage.create_task(title="Linked", milestone_id=m.id, roadmap_id=r.id)
    assert t.milestone_id == m.id
    assert t.roadmap_id == r.id


def test_invalid_status(storage):
    with pytest.raises(InvalidTaskStatusError):
        storage.create_task(title="Bad", status="bogus")


def test_invalid_priority(storage):
    with pytest.raises(InvalidTaskPriorityError):
        storage.create_task(title="Bad", priority="urgent")


def test_update_task(storage):
    ws = storage.create_workspace("upd-ws", "")
    t = storage.create_task(title="Old", workspace_id=ws.id)
    updated = storage.update_task(t.id, title="New", status="in_progress", priority="critical")
    assert updated.title == "New"
    assert updated.status == "in_progress"
    assert updated.priority == "critical"


def test_update_status_to_done_sets_completed_at(storage):
    ws = storage.create_workspace("done-ws", "")
    t = storage.create_task(title="Task", workspace_id=ws.id)
    updated = storage.update_task(t.id, status="done")
    assert updated.status == "done"
    assert updated.completed_at is not None


def test_delete_task(storage):
    ws = storage.create_workspace("del-ws", "")
    t = storage.create_task(title="Del", workspace_id=ws.id)
    storage.delete_task(t.id)
    assert storage.get_task(t.id) is None


def test_list_tasks_with_filters(storage):
    ws = storage.create_workspace("filt-ws", "")
    storage.create_task(title="A", workspace_id=ws.id, status="todo", priority="high", labels=["bug"])
    storage.create_task(title="B", workspace_id=ws.id, status="done", priority="low", labels=["feature"])
    storage.create_task(title="C", workspace_id=ws.id, status="blocked", priority="high")

    todos = storage.list_tasks(ws.id, status="todo")
    assert len(todos) == 1
    assert todos[0].title == "A"

    highs = storage.list_tasks(ws.id, priority="high")
    assert len(highs) == 2

    bugs = storage.list_tasks(ws.id, label="bug")
    assert len(bugs) == 1


def test_task_search(storage):
    ws = storage.create_workspace("srch-ws", "")
    storage.create_task(title="Fix login", description="auth bug", workspace_id=ws.id)
    storage.create_task(title="Add dashboard", description="frontend", workspace_id=ws.id)

    results = storage.list_tasks(ws.id, q="login")
    assert len(results) == 1
    assert results[0].title == "Fix login"

    results2 = storage.list_tasks(ws.id, q="frontend")
    assert len(results2) == 1


def test_comments(storage):
    ws = storage.create_workspace("cmt-ws", "")
    t = storage.create_task(title="Task", workspace_id=ws.id)
    c = storage.add_comment(t.id, "dev", "Looks good")
    assert c.body == "Looks good"
    assert c.author == "dev"

    comments = storage.list_comments(t.id)
    assert len(comments) == 1


def test_dependencies(storage):
    ws = storage.create_workspace("dep-ws", "")
    t1 = storage.create_task(title="T1", workspace_id=ws.id)
    t2 = storage.create_task(title="T2", workspace_id=ws.id)

    storage.set_dependencies(t1.id, [t2.id])
    deps, depends_on = storage.get_dependencies(t1.id)
    assert len(depends_on) == 1
    assert depends_on[0].id == t2.id

    deps2, _ = storage.get_dependencies(t2.id)
    assert len(deps2) == 1
    assert deps2[0].id == t1.id


def test_circular_dependency_detection(storage):
    ws = storage.create_workspace("circ-ws", "")
    t1 = storage.create_task(title="T1", workspace_id=ws.id)
    t2 = storage.create_task(title="T2", workspace_id=ws.id)
    t3 = storage.create_task(title="T3", workspace_id=ws.id)

    storage.set_dependencies(t1.id, [t2.id])
    storage.set_dependencies(t2.id, [t3.id])

    with pytest.raises(CircularDependencyError):
        storage.set_dependencies(t3.id, [t1.id])


def test_self_dependency_rejected(storage):
    ws = storage.create_workspace("self-ws", "")
    t = storage.create_task(title="Self", workspace_id=ws.id)
    with pytest.raises(CircularDependencyError):
        storage.set_dependencies(t.id, [t.id])


def test_overdue_detection(storage):
    ws = storage.create_workspace("ovd-ws", "")
    t = storage.create_task(title="Overdue", workspace_id=ws.id,
                             status="todo", due_date="2020-01-01")
    assert t.is_overdue is True

    t2 = storage.create_task(title="Future", workspace_id=ws.id,
                              status="todo", due_date="2099-01-01")
    assert t2.is_overdue is False


def test_stats(storage):
    ws = storage.create_workspace("stat-ws", "")
    storage.create_task(title="A", workspace_id=ws.id, status="todo")
    storage.create_task(title="B", workspace_id=ws.id, status="in_progress")
    storage.create_task(title="C", workspace_id=ws.id, status="blocked")
    storage.create_task(title="D", workspace_id=ws.id, status="done")

    stats = storage.get_task_stats()
    assert stats.total == 4
    assert stats.open == 3
    assert stats.completed == 1
    assert stats.blocked == 1
