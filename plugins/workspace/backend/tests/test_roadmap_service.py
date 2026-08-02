"""Tests for RoadmapService."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (
    InvalidMilestoneStatusError,
    MilestoneCreate,
    MilestoneReorder,
    MilestoneUpdate,
    RoadmapCreate,
    RoadmapUpdate,
)
from plugins.workspace.backend.services.roadmap_service import RoadmapService


@pytest.fixture
def roadmap_svc(storage):
    return RoadmapService(storage)


def test_create_roadmap(storage, roadmap_svc):
    ws = storage.create_workspace("svc-ws", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="Q1", description="desc"))
    assert r.name == "Q1"
    assert r.description == "desc"


def test_list_roadmaps(storage, roadmap_svc):
    ws = storage.create_workspace("svc-list", "")
    roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R1"))
    roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R2"))
    roadmaps = roadmap_svc.list_roadmaps(ws.id)
    assert len(roadmaps) == 2


def test_update_roadmap(storage, roadmap_svc):
    ws = storage.create_workspace("svc-upd", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="Old"))
    updated = roadmap_svc.update_roadmap(r.id, RoadmapUpdate(name="New"))
    assert updated.name == "New"


def test_delete_roadmap(storage, roadmap_svc):
    ws = storage.create_workspace("svc-del", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="Del"))
    roadmap_svc.delete_roadmap(r.id)


def test_create_milestone(storage, roadmap_svc):
    ws = storage.create_workspace("svc-ms", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    m = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="Task 1", status="in_progress"))
    assert m.title == "Task 1"
    assert m.status == "in_progress"


def test_create_milestone_invalid_status(storage, roadmap_svc):
    ws = storage.create_workspace("svc-bad", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    with pytest.raises(InvalidMilestoneStatusError):
        roadmap_svc.create_milestone(r.id, MilestoneCreate(title="Bad", status="nope"))


def test_update_milestone(storage, roadmap_svc):
    ws = storage.create_workspace("svc-ums", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    m = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="T"))
    updated = roadmap_svc.update_milestone(m.id, MilestoneUpdate(title="Updated", status="completed"))
    assert updated.title == "Updated"
    assert updated.status == "completed"


def test_delete_milestone(storage, roadmap_svc):
    ws = storage.create_workspace("svc-dms", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    m = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="Del"))
    roadmap_svc.delete_milestone(m.id)


def test_reorder_milestones(storage, roadmap_svc):
    ws = storage.create_workspace("svc-ord", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    m1 = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="A"))
    m2 = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="B"))
    m3 = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="C"))
    reordered = roadmap_svc.reorder_milestones(r.id, MilestoneReorder(ids=[m3.id, m1.id, m2.id]))
    assert reordered[0].id == m3.id
    assert reordered[1].id == m1.id
    assert reordered[2].id == m2.id


def test_progress_updates(storage, roadmap_svc):
    ws = storage.create_workspace("svc-prog", "")
    r = roadmap_svc.create_roadmap(RoadmapCreate(workspace_id=ws.id, name="R"))
    m = roadmap_svc.create_milestone(r.id, MilestoneCreate(title="T"))
    assert r.progress == 0.0
    roadmap_svc.update_milestone(m.id, MilestoneUpdate(status="completed"))
    updated = roadmap_svc.get_roadmap(r.id)
    assert updated.progress == 100.0
    assert updated.completed_count == 1
