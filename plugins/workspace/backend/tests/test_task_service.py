"""Tests for TaskService."""

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
    TaskCommentCreate,
    TaskCreate,
    TaskDependencyCreate,
    TaskSearchParams,
    TaskUpdate,
)
from plugins.workspace.backend.services.task_service import TaskService


@pytest.fixture
def task_svc(storage):
    return TaskService(storage)


def test_create_task(task_svc, storage):
    ws = storage.create_workspace("svc-ws", "")
    t = task_svc.create_task(TaskCreate(title="SVC Task", workspace_id=ws.id))
    assert t.title == "SVC Task"
    assert t.status == "todo"


def test_create_invalid_status(task_svc):
    with pytest.raises(InvalidTaskStatusError):
        task_svc.create_task(TaskCreate(title="Bad", status="invalid"))


def test_create_invalid_priority(task_svc):
    with pytest.raises(InvalidTaskPriorityError):
        task_svc.create_task(TaskCreate(title="Bad", priority="top"))


def test_update_task(task_svc, storage):
    ws = storage.create_workspace("svc-upd", "")
    t = task_svc.create_task(TaskCreate(title="Old", workspace_id=ws.id))
    updated = task_svc.update_task(t.id, TaskUpdate(title="New", status="in_progress"))
    assert updated.title == "New"


def test_delete_task(task_svc, storage):
    ws = storage.create_workspace("svc-del", "")
    t = task_svc.create_task(TaskCreate(title="Del", workspace_id=ws.id))
    task_svc.delete_task(t.id)


def test_comments(task_svc, storage):
    ws = storage.create_workspace("svc-cmt", "")
    t = task_svc.create_task(TaskCreate(title="Task", workspace_id=ws.id))
    c = task_svc.add_comment(t.id, TaskCommentCreate(body="Nice work"))
    assert c.body == "Nice work"
    comments = task_svc.list_comments(t.id)
    assert len(comments) == 1


def test_dependencies(task_svc, storage):
    ws = storage.create_workspace("svc-dep", "")
    t1 = task_svc.create_task(TaskCreate(title="T1", workspace_id=ws.id))
    t2 = task_svc.create_task(TaskCreate(title="T2", workspace_id=ws.id))
    result = task_svc.set_dependencies(t1.id, TaskDependencyCreate(depends_on_ids=[t2.id]))
    assert len(result.depends_on) == 1
    assert result.depends_on[0].id == t2.id


def test_circular_prevented(task_svc, storage):
    ws = storage.create_workspace("svc-circ", "")
    t1 = task_svc.create_task(TaskCreate(title="T1", workspace_id=ws.id))
    t2 = task_svc.create_task(TaskCreate(title="T2", workspace_id=ws.id))
    task_svc.set_dependencies(t1.id, TaskDependencyCreate(depends_on_ids=[t2.id]))
    with pytest.raises(CircularDependencyError):
        task_svc.set_dependencies(t2.id, TaskDependencyCreate(depends_on_ids=[t1.id]))


def test_search(task_svc, storage):
    ws = storage.create_workspace("svc-srch", "")
    task_svc.create_task(TaskCreate(title="Fix auth", workspace_id=ws.id))
    task_svc.create_task(TaskCreate(title="Add tests", workspace_id=ws.id))
    results = task_svc.search_tasks(TaskSearchParams(workspace_id=ws.id, q="auth"))
    assert len(results) == 1
    assert results[0].title == "Fix auth"


def test_stats(task_svc, storage):
    ws = storage.create_workspace("svc-stat", "")
    task_svc.create_task(TaskCreate(title="A", workspace_id=ws.id, status="todo"))
    task_svc.create_task(TaskCreate(title="B", workspace_id=ws.id, status="blocked"))
    stats = task_svc.get_stats()
    assert stats.total >= 2
    assert stats.open >= 2
    assert stats.blocked >= 1
