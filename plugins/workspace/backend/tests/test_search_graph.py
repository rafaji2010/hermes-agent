"""Tests for search and graph services + storage."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.services.graph_service import GraphService
from plugins.workspace.backend.services.search_service import SearchService


@pytest.fixture
def populate(storage):
    """Create a workspace with all entity types for testing."""
    ws = storage.create_workspace("graph-ws", "")
    r = storage.create_roadmap(ws.id, "Q1", "Q1 Roadmap")
    m1 = storage.create_milestone(r.id, "M1", "First", "planned", "")
    m2 = storage.create_milestone(r.id, "M2", "Second", "in_progress", "")
    storage.create_task(title="Task A", workspace_id=ws.id, roadmap_id=r.id,
                        milestone_id=m1.id, status="todo", priority="high",
                        labels=["backend"])
    storage.create_task(title="Task B", workspace_id=ws.id, status="in_progress",
                        priority="medium")
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="ADR 1", slug="adr-1",
        status="proposed", category="", markdown="Use microservices.", tags=["arch"],
    )
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="ADR 2", slug="adr-2",
        status="accepted", category="", markdown="Use SQLite.", tags=["db"],
    )
    storage.create_journal_entry(
        workspace_id=ws.id, repository_id=None, title="Day 1", summary="Started",
        markdown="Worked on login", entry_date="2026-07-01", tags=["dev"],
    )
    return ws.id


def test_search_all(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="", workspace_id=populate)
    assert resp.total >= 7  # 1 roadmap + 2 milestones + 2 tasks + 2 ADRs + 1 journal
    types = {r.type for r in resp.results}
    assert "roadmap" in types
    assert "milestone" in types
    assert "task" in types
    assert "adr" in types
    assert "journal" in types


def test_search_text(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="Task", workspace_id=populate)
    assert resp.total >= 2
    assert all("Task" in r.title for r in resp.results if r.type == "task")


def test_search_filter_type(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="", filters={"type": "adr"}, workspace_id=populate)
    assert all(r.type == "adr" for r in resp.results)


def test_search_inline_filter(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="type:task", workspace_id=populate)
    assert resp.total >= 2
    assert all(r.type == "task" for r in resp.results)


def test_search_status_filter(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="", filters={"status": "proposed"}, workspace_id=populate)
    tasks = [r for r in resp.results if r.type == "adr"]
    if tasks:
        assert any(r.status == "proposed" for r in tasks)


def test_search_ranking(populate, storage):
    svc = SearchService(storage)
    resp = svc.search(q="Task A", workspace_id=populate)
    if resp.results:
        scores = [r.score for r in resp.results]
        assert scores == sorted(scores, reverse=True)


def test_graph_related(populate, storage):
    svc = GraphService(storage)
    maps = storage.list_roadmaps(populate)
    assert len(maps) > 0
    r = maps[0]
    items = svc.get_related("roadmap", r.id)
    assert items.entity_type == "roadmap"
    milestone_items = [i for i in items.items if i.type == "milestone"]
    assert len(milestone_items) == 2


def test_graph_build(populate, storage):
    svc = GraphService(storage)
    graph = svc.get_graph(populate)
    assert len(graph.nodes) > 0
    assert len(graph.edges) > 0


def test_shortest_path(populate, storage):
    svc = GraphService(storage)
    maps = storage.list_roadmaps(populate)
    ms = maps[0].milestones
    tasks = storage.list_tasks(populate)
    if ms and tasks:
        resp = svc.shortest_path("milestone", ms[0].id, "task", tasks[0].id)
        assert resp.distance >= 0


def test_graph_stats(populate, storage):
    svc = GraphService(storage)
    stats = svc.get_graph_stats()
    assert stats.total_entities > 0
    assert stats.total_edges > 0
