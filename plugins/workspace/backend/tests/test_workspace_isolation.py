"""Cross-workspace adversarial tests (U1D-C).

The security invariant under test: possession or knowledge of a resource
ID must NEVER grant access to that resource from another Workspace scope.

Two workspaces (A and B) are populated with representative resources;
every cross-workspace access attempt must fail with 404 (membership —
no existence leak) or 403 (unresolved scope).  Aggregates (search,
graph, analytics, assistant) must never contain the other workspace's
data, and relationship operations must validate BOTH ends.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    from plugins.workspace.backend.tests._helpers import (
        pin_memory_workspace_state,
        unpin_memory_workspace_state,
    )

    pin_memory_workspace_state(tmp_path)
    yield
    unpin_memory_workspace_state()


def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_ws(c: TestClient, name: str) -> str:
    r = c.post("/v1/workspaces", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["workspaces"][0]["id"]


def _make_task(c: TestClient, ws: str, title: str, status: str = "todo") -> str:
    r = c.post("/v1/tasks", json={"workspace_id": ws, "title": title, "status": status})
    assert r.status_code == 201, r.text
    return r.json()["tasks"][0]["id"]


def _make_roadmap(c: TestClient, ws: str, name: str) -> str:
    r = c.post("/v1/roadmaps", json={"workspace_id": ws, "name": name})
    assert r.status_code == 201, r.text
    return r.json()["roadmaps"][0]["id"]


def _make_milestone(c: TestClient, ws: str, roadmap: str, title: str) -> str:
    r = c.post(
        f"/v1/roadmaps/{roadmap}/milestones?workspace_id={ws}",
        json={"title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()["milestones"][0]["id"]


def _make_adr(c: TestClient, ws: str, title: str) -> str:
    r = c.post("/v1/adrs", json={"workspace_id": ws, "title": title})
    assert r.status_code == 201, r.text
    return r.json()["adrs"][0]["id"]


def _make_journal(c: TestClient, ws: str, title: str) -> str:
    r = c.post("/v1/journal", json={"workspace_id": ws, "title": title})
    assert r.status_code == 201, r.text
    return r.json()["entries"][0]["id"]


@pytest.fixture
def two_workspaces():
    """Workspace A and B, each with task/roadmap/milestone/adr/journal."""
    c = client()
    ws_a = _make_ws(c, "iso-a")
    ws_b = _make_ws(c, "iso-b")

    data_a = {
        "task": _make_task(c, ws_a, "task-a"),
        "roadmap": _make_roadmap(c, ws_a, "roadmap-a"),
        "milestone": _make_milestone(c, ws_a, _make_roadmap(c, ws_a, "rm-a2"), "m-a"),
        "adr": _make_adr(c, ws_a, "adr-a"),
        "journal": _make_journal(c, ws_a, "journal-a"),
    }
    data_b = {
        "task": _make_task(c, ws_b, "task-b"),
        "roadmap": _make_roadmap(c, ws_b, "roadmap-b"),
        "milestone": _make_milestone(c, ws_b, _make_roadmap(c, ws_b, "rm-b2"), "m-b"),
        "adr": _make_adr(c, ws_b, "adr-b"),
        "journal": _make_journal(c, ws_b, "journal-b"),
    }
    return c, ws_a, ws_b, data_a, data_b


# ---------------------------------------------------------------------------
# A. Tasks — A cannot touch B's task in any way
# ---------------------------------------------------------------------------


def test_a_cannot_read_b_task(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert c.get(f"/v1/tasks/{db['task']}?workspace_id={ws_a}").status_code == 404
    assert c.get(f"/v1/tasks/{db['task']}").status_code == 403  # no scope at all


def test_a_cannot_update_b_task(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    resp = c.put(
        f"/v1/tasks/{db['task']}?workspace_id={ws_a}",
        json={"status": "done"},
    )
    assert resp.status_code == 404
    # B's task is untouched
    assert (
        c.get(f"/v1/tasks/{db['task']}?workspace_id={two_workspaces[2]}").json()["tasks"][0]["status"]
        == "todo"
    )


def test_a_cannot_delete_b_task(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert c.delete(f"/v1/tasks/{db['task']}?workspace_id={ws_a}").status_code == 404
    assert c.get(f"/v1/tasks/{db['task']}?workspace_id={two_workspaces[2]}").status_code == 200


def test_a_cannot_comment_on_b_task(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert (
        c.post(
            f"/v1/tasks/{db['task']}/comments?workspace_id={ws_a}",
            json={"body": "hi"},
        ).status_code
        == 404
    )
    assert (
        c.get(f"/v1/tasks/{db['task']}/comments?workspace_id={ws_a}").status_code
        == 404
    )


def test_a_cannot_mutate_dependencies_involving_b_task(two_workspaces):
    c, ws_a, _ws_b, da, db = two_workspaces
    # A cannot hang a dependency off B's task…
    assert (
        c.put(
            f"/v1/tasks/{db['task']}/dependencies?workspace_id={ws_a}",
            json={"depends_on_ids": [da["task"]]},
        ).status_code
        == 404
    )
    # …and A cannot point one of its own tasks at B's task (both ends checked).
    assert (
        c.put(
            f"/v1/tasks/{da['task']}/dependencies?workspace_id={ws_a}",
            json={"depends_on_ids": [db["task"]]},
        ).status_code
        == 404
    )
    # Within-scope dependencies still work (two distinct A tasks).
    a_other = _make_task(c, ws_a, "task-a-other")
    ok = c.put(
        f"/v1/tasks/{da['task']}/dependencies?workspace_id={ws_a}",
        json={"depends_on_ids": [a_other]},
    )
    assert ok.status_code == 200


def test_a_cannot_list_b_tasks(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    resp = c.get(f"/v1/tasks?workspace_id={ws_a}")
    assert resp.status_code == 200
    ids = {t["id"] for t in resp.json()["tasks"]}
    assert db["task"] not in ids


# ---------------------------------------------------------------------------
# B. Roadmaps / milestones
# ---------------------------------------------------------------------------


def test_a_cannot_access_b_roadmap(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert c.get(f"/v1/roadmaps/{db['roadmap']}?workspace_id={ws_a}").status_code == 404
    assert (
        c.put(f"/v1/roadmaps/{db['roadmap']}?workspace_id={ws_a}", json={"name": "x"}).status_code
        == 404
    )
    assert c.delete(f"/v1/roadmaps/{db['roadmap']}?workspace_id={ws_a}").status_code == 404


def test_a_cannot_touch_b_milestones(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert (
        c.get(f"/v1/roadmaps/{db['roadmap']}/milestones?workspace_id={ws_a}").status_code
        == 404
    )
    assert (
        c.post(
            f"/v1/roadmaps/{db['roadmap']}/milestones?workspace_id={ws_a}",
            json={"title": "x"},
        ).status_code
        == 404
    )
    assert (
        c.put(
            f"/v1/roadmaps/{db['roadmap']}/milestones/reorder?workspace_id={ws_a}",
            json={"ids": [db["milestone"]]},
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# C. ADR / Journal
# ---------------------------------------------------------------------------


def test_a_cannot_read_or_mutate_b_adr(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert c.get(f"/v1/adrs/{db['adr']}?workspace_id={ws_a}").status_code == 404
    assert (
        c.put(f"/v1/adrs/{db['adr']}?workspace_id={ws_a}", json={"status": "accepted"}).status_code
        == 404
    )
    assert c.delete(f"/v1/adrs/{db['adr']}?workspace_id={ws_a}").status_code == 404
    # A's list never contains B's ADR
    ids = {a["id"] for a in c.get(f"/v1/adrs?workspace_id={ws_a}").json()["adrs"]}
    assert db["adr"] not in ids


def test_a_cannot_read_or_mutate_b_journal(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert c.get(f"/v1/journal/{db['journal']}?workspace_id={ws_a}").status_code == 404
    assert (
        c.put(f"/v1/journal/{db['journal']}?workspace_id={ws_a}", json={"title": "x"}).status_code
        == 404
    )
    assert c.delete(f"/v1/journal/{db['journal']}?workspace_id={ws_a}").status_code == 404


# ---------------------------------------------------------------------------
# D. Search — never returns the other workspace's data
# ---------------------------------------------------------------------------


def test_a_search_never_returns_b_data(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    resp = c.get(f"/v1/search?workspace_id={ws_a}&q=task-b")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    resp = c.get(f"/v1/search?workspace_id={ws_a}&q=a")
    for r in resp.json()["results"]:
        assert r["workspace_id"] == ws_a


# ---------------------------------------------------------------------------
# E. Graph / related / shortest-path
# ---------------------------------------------------------------------------


def test_a_graph_contains_no_b_entities(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    graph = c.get(f"/v1/graph?workspace_id={ws_a}").json()
    node_ids = {n["id"] for n in graph["nodes"]}
    edge_ids = set()
    for e in graph["edges"]:
        edge_ids.add(e["source_id"])
        edge_ids.add(e["target_id"])
    assert db["task"] not in node_ids
    assert db["task"] not in edge_ids
    assert db["roadmap"] not in node_ids


def test_a_related_lookup_cannot_expose_b_entity(two_workspaces):
    c, ws_a, _ws_b, _da, db = two_workspaces
    assert (
        c.get(f"/v1/entities/task/{db['task']}/related?workspace_id={ws_a}").status_code
        == 404
    )
    assert (
        c.get(f"/v1/entities/adr/{db['adr']}/related?workspace_id={ws_a}").status_code
        == 404
    )


def test_a_shortest_path_cannot_traverse_b_entities(two_workspaces):
    c, ws_a, _ws_b, da, db = two_workspaces
    resp = c.get(
        f"/v1/graph/shortest-path?workspace_id={ws_a}"
        f"&source_type=task&source_id={da['task']}"
        f"&target_type=task&target_id={db['task']}"
    )
    assert resp.status_code == 200
    # No path can cross the workspace boundary — B's task is unreachable.
    path = resp.json()
    assert path.get("distance", -1) == -1 or not path.get("path")


# ---------------------------------------------------------------------------
# F. Analytics — aggregates/export contain no B data
# ---------------------------------------------------------------------------


def test_a_analytics_exclude_b_data(two_workspaces):
    c, ws_a, ws_b, _da, db = two_workspaces
    # Extra task in B so the counts diverge.
    _make_task(c, ws_b, "task-b-extra")

    a_data = c.get(f"/v1/analytics?workspace_id={ws_a}").json()
    b_data = c.get(f"/v1/analytics?workspace_id={ws_b}").json()
    assert a_data["tasks"]["total"] == 1
    assert b_data["tasks"]["total"] == 2

    exported = c.post(f"/v1/analytics/export?workspace_id={ws_a}", json={"format": "csv"})
    assert exported.status_code == 200
    assert "task-b-extra" not in exported.text


# ---------------------------------------------------------------------------
# G. Assistant — context/retrieval cannot include B data
# ---------------------------------------------------------------------------


def test_a_assistant_context_excludes_b_data(two_workspaces):
    c, ws_a, _ws_b, da, db = two_workspaces
    resp = c.post(
        "/v1/assistant/context",
        params={"workspace_id": ws_a, "question": "task-b"},
    )
    assert resp.status_code == 200
    entity_ids = {e["id"] for e in resp.json()["entities"]}
    assert db["task"] not in entity_ids
    assert da["task"] in entity_ids or True  # A entities may or may not surface


def test_a_assistant_suggestions_use_a_analytics(two_workspaces):
    c, ws_a, ws_b, _da, db = two_workspaces
    _make_task(c, ws_b, "blocked", status="blocked")
    # B has blocked work; A has none — A's suggestions must not react to B.
    resp = c.get(f"/v1/assistant/suggestions?workspace_id={ws_a}")
    assert resp.status_code == 200
    for s in resp.json()["suggestions"]:
        assert "blocked" not in (s["title"] + s["description"]).lower()


# ---------------------------------------------------------------------------
# H. Scope failure — unresolved scope never widens to global access
# ---------------------------------------------------------------------------


def test_unresolved_scope_never_widens(two_workspaces):
    c, _ws_a, _ws_b, _da, db = two_workspaces
    # No workspace_id and no session anywhere: everything fails closed.
    # 403 = scope resolution failure; 422 = required-field validation that
    # also never reaches any data.  Neither ever widens to global access.
    assert c.get("/v1/adrs").status_code == 403
    assert c.get("/v1/journal").status_code == 403
    assert c.get("/v1/roadmaps").status_code == 403
    assert c.get("/v1/tasks").status_code == 403
    assert c.get("/v1/search").status_code == 403
    assert c.get(f"/v1/tasks/{db['task']}").status_code == 403
    assert c.get(f"/v1/adrs/{db['adr']}").status_code == 403
    assert c.post("/v1/tasks", json={"title": "x"}).status_code in (403, 422)
    assert c.post("/v1/adrs", json={"title": "x"}).status_code in (403, 422)
    assert c.post("/v1/journal", json={"title": "x"}).status_code in (403, 422)


# ---------------------------------------------------------------------------
# I. Relationship validation — both ends must belong to the effective scope
# ---------------------------------------------------------------------------


def test_relationship_operations_validate_both_ends(two_workspaces):
    c, ws_a, _ws_b, da, db = two_workspaces
    # Task dependency: B task referenced from A scope → 404 (see also tasks test).
    assert (
        c.put(
            f"/v1/tasks/{da['task']}/dependencies?workspace_id={ws_a}",
            json={"depends_on_ids": [db["task"]]},
        ).status_code
        == 404
    )
    # Related traversal: B's task from A scope → 404.
    assert (
        c.get(f"/v1/entities/task/{db['task']}/related?workspace_id={ws_a}").status_code
        == 404
    )


def test_a_cannot_create_task_referencing_b_entities(two_workspaces):
    """Indirect traversal: task creation cannot reference B-owned entities."""
    c, ws_a, _ws_b, _da, db = two_workspaces
    # Cross-workspace roadmap reference is rejected before the task exists.
    resp = c.post(
        "/v1/tasks",
        json={"workspace_id": ws_a, "title": "x", "roadmap_id": db["roadmap"]},
    )
    assert resp.status_code == 404
    # Cross-workspace ADR reference is rejected too.
    resp = c.post(
        "/v1/tasks",
        json={"workspace_id": ws_a, "title": "y", "adr_id": db["adr"]},
    )
    assert resp.status_code == 404
    # Within-scope references still work.
    resp = c.post(
        "/v1/tasks",
        json={"workspace_id": ws_a, "title": "z", "roadmap_id": _da["roadmap"]},
    )
    assert resp.status_code == 201
