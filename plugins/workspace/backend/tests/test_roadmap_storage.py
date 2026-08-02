"""Tests for roadmap + milestone storage operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (
    InvalidMilestoneStatusError,
    MilestoneNotFoundError,
    RoadmapNotFoundError,
)


def test_create_and_get_roadmap(storage):
    ws = storage.create_workspace("roadmap-ws", "")
    r = storage.create_roadmap(ws.id, "Q3 Goals", "Plan for Q3")
    assert r.id
    assert r.name == "Q3 Goals"
    assert r.description == "Plan for Q3"
    assert r.workspace_id == ws.id
    assert r.milestone_count == 0
    assert r.completed_count == 0
    assert r.progress == 0.0


def test_list_roadmaps(storage):
    ws = storage.create_workspace("list-ws", "")
    storage.create_roadmap(ws.id, "R1", "")
    storage.create_roadmap(ws.id, "R2", "")
    roadmaps = storage.list_roadmaps(ws.id)
    assert len(roadmaps) == 2
    names = {r.name for r in roadmaps}
    assert names == {"R1", "R2"}


def test_update_roadmap(storage):
    ws = storage.create_workspace("upd-ws", "")
    r = storage.create_roadmap(ws.id, "Original", "desc")
    updated = storage.update_roadmap(r.id, name="Updated", description="new desc")
    assert updated.name == "Updated"
    assert updated.description == "new desc"


def test_update_roadmap_partial(storage):
    ws = storage.create_workspace("part-ws", "")
    r = storage.create_roadmap(ws.id, "Keep Name", "old")
    updated = storage.update_roadmap(r.id, description="new only")
    assert updated.name == "Keep Name"
    assert updated.description == "new only"


def test_delete_roadmap(storage):
    ws = storage.create_workspace("del-ws", "")
    r = storage.create_roadmap(ws.id, "To Delete", "")
    storage.delete_roadmap(r.id)
    assert storage.get_roadmap(r.id) is None


def test_delete_roadmap_not_found(storage):
    with pytest.raises(RoadmapNotFoundError):
        storage.delete_roadmap("nonexistent")


def test_create_milestone(storage):
    ws = storage.create_workspace("ms-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m = storage.create_milestone(r.id, "M1", "First milestone", "planned", "")
    assert m.title == "M1"
    assert m.status == "planned"
    assert m.sort_order == 0
    assert m.roadmap_id == r.id


def test_milestone_ordering(storage):
    ws = storage.create_workspace("ord-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m1 = storage.create_milestone(r.id, "M1", "", "planned", "")
    m2 = storage.create_milestone(r.id, "M2", "", "planned", "")
    m3 = storage.create_milestone(r.id, "M3", "", "planned", "")
    assert m1.sort_order == 0
    assert m2.sort_order == 1
    assert m3.sort_order == 2


def test_reorder_milestones(storage):
    ws = storage.create_workspace("reo-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m1 = storage.create_milestone(r.id, "M1", "", "planned", "")
    m2 = storage.create_milestone(r.id, "M2", "", "planned", "")
    m3 = storage.create_milestone(r.id, "M3", "", "planned", "")
    reordered = storage.reorder_milestones(r.id, [m3.id, m1.id, m2.id])
    assert reordered[0].id == m3.id
    assert reordered[0].sort_order == 0
    assert reordered[1].id == m1.id


def test_milestone_status_lifecycle(storage):
    ws = storage.create_workspace("stat-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m = storage.create_milestone(r.id, "M", "", "planned", "")
    for status in ("in_progress", "blocked", "completed"):
        updated = storage.update_milestone(m.id, status=status)
        assert updated.status == status


def test_invalid_milestone_status(storage):
    ws = storage.create_workspace("inv-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    with pytest.raises(InvalidMilestoneStatusError):
        storage.create_milestone(r.id, "Bad", "", "invalid_status", "")


def test_update_milestone_invalid_status(storage):
    ws = storage.create_workspace("upd-st-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m = storage.create_milestone(r.id, "M", "", "planned", "")
    with pytest.raises(InvalidMilestoneStatusError):
        storage.update_milestone(m.id, status="bogus")


def test_delete_milestone(storage):
    ws = storage.create_workspace("del-m-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    m = storage.create_milestone(r.id, "M", "", "planned", "")
    storage.delete_milestone(m.id)
    assert storage.get_milestone(m.id) is None


def test_progress_calculation(storage):
    ws = storage.create_workspace("prog-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    storage.create_milestone(r.id, "A", "", "completed", "")
    storage.create_milestone(r.id, "B", "", "in_progress", "")
    storage.create_milestone(r.id, "C", "", "planned", "")
    storage.create_milestone(r.id, "D", "", "blocked", "")
    roadmap = storage.get_roadmap(r.id)
    assert roadmap.milestone_count == 4
    assert roadmap.completed_count == 1
    assert roadmap.progress == 25.0


def test_get_roadmap_counts(storage):
    ws = storage.create_workspace("cnt-ws", "")
    r = storage.create_roadmap(ws.id, "R", "")
    storage.create_milestone(r.id, "A", "", "completed", "")
    storage.create_milestone(r.id, "B", "", "planned", "")
    counts = storage.get_roadmap_counts()
    assert counts["total_roadmaps"] >= 1
    assert counts["total_milestones"] >= 2
    assert counts["completed_milestones"] >= 1
