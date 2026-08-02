"""API integration tests for ADR reconciliation endpoints (S7.3A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.workspace.backend.services.workspace_service import WorkspaceService  # type: ignore[import-untyped]
from plugins.workspace.dashboard.plugin_api import router  # type: ignore[import-untyped]

VALID_1 = (
    "---\n"
    "status: accepted\n"
    "category: Architecture\n"
    "tags:\n"
    "  - database\n"
    "---\n"
    "# Use SQLite\n\n"
    "We chose SQLite for storage.\n"
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    """Pin the legacy DB singleton AND the Workspace runtime to one
    fresh in-memory database (U1D-A)."""
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


def _setup_ws_with_repo(c: TestClient, git_root: Path, name: str = "recon-api-ws") -> str:
    r = c.post("/v1/workspaces", json={"name": name})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/repositories", json={
        "workspace_id": ws_id,
        "name": "recon-repo",
        "path": str(git_root),
    })
    return ws_id


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------


def test_reconcile_indexes_canonical_file(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)

    resp = c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["indexed"] == 1
    assert data["scanned_files"] == 1
    assert data["dry_run"] is False

    # The projected ADR is now readable through the existing list API.
    lst = c.get(f"/v1/adrs?workspace_id={ws_id}")
    assert lst.status_code == 200
    adr = lst.json()["adrs"][0]
    assert adr["source"] == "git_file"
    assert adr["reconcile_state"] == "synced"
    assert adr["canonical_path"] == "docs/adr/0001-use-sqlite.md"


def test_reconcile_dry_run_previews():
    c = client()
    ws_id = _setup_ws_with_repo(c, Path("/nonexistent-repo-xyz"))
    resp = c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": True})
    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True


def test_reconcile_unscoped_rejected():
    c = client()
    resp = c.post("/v1/adrs/reconcile", json={"workspace_id": "", "dry_run": True})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "SCOPE_UNRESOLVED"


def test_reconcile_status(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    # legacy DB-only ADR
    c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Legacy", "status": "proposed",
    })
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})

    resp = c.get(f"/v1/adrs/reconcile/status?workspace_id={ws_id}")
    assert resp.status_code == 200
    statuses = resp.json()["statuses"]
    by_slug = {s["slug"]: s for s in statuses}
    assert by_slug["use-sqlite"]["reconcile_state"] == "synced"
    assert by_slug["use-sqlite"]["canonical_path"] == "docs/adr/0001-use-sqlite.md"
    assert by_slug["legacy"]["reconcile_state"] == "db_legacy"


def test_reconcile_status_unscoped_rejected():
    c = client()
    resp = c.get("/v1/adrs/reconcile/status")
    assert resp.status_code == 403


def test_materialize_preview_then_apply(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Legacy ADR", "status": "accepted",
        "markdown": "# Legacy ADR\n\nBody.",
    }).json()["adrs"][0]

    # Preview
    resp = c.post(f"/v1/adrs/{adr['id']}/materialize", json={"dry_run": True},
                  params={"workspace_id": ws_id})
    assert resp.status_code == 200
    preview = resp.json()
    assert preview["status"] == "preview"
    assert preview["target_path"] == "docs/adr/0001-legacy-adr.md"
    assert not (temp_git_repo / "docs/adr").exists()

    # Apply
    resp = c.post(f"/v1/adrs/{adr['id']}/materialize", json={"dry_run": False},
                  params={"workspace_id": ws_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "materialized"
    assert (temp_git_repo / "docs/adr/0001-legacy-adr.md").is_file()

    adr2 = c.get(f"/v1/adrs/{adr['id']}").json()["adrs"][0]
    assert adr2["source"] == "git_file"
    assert adr2["reconcile_state"] == "synced"
    assert adr2["canonical_path"] == "docs/adr/0001-legacy-adr.md"


def test_materialize_target_exists(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-legacy-adr.md", VALID_1)
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Legacy ADR", "status": "proposed",
        "markdown": "# Legacy ADR\n\nDifferent.",
    }).json()["adrs"][0]

    resp = c.post(f"/v1/adrs/{adr['id']}/materialize", json={"dry_run": False},
                  params={"workspace_id": ws_id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "MATERIALIZE_TARGET_EXISTS"


def test_materialize_already_canonical(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    adr = c.get(f"/v1/adrs?workspace_id={ws_id}").json()["adrs"][0]

    resp = c.post(f"/v1/adrs/{adr['id']}/materialize", json={"dry_run": False},
                  params={"workspace_id": ws_id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ADR_ALREADY_CANONICAL"


def test_materialize_missing_adr_404():
    c = client()
    resp = c.post("/v1/adrs/nope/materialize", json={"dry_run": True})
    assert resp.status_code == 404


def test_update_file(temp_git_repo):
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    adr = c.get(f"/v1/adrs?workspace_id={ws_id}").json()["adrs"][0]

    new_content = VALID_1.replace("# Use SQLite", "# Use SQLite (Updated)")
    resp = c.put(f"/v1/adrs/{adr['id']}/file", json={"markdown": new_content},
                 params={"workspace_id": ws_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"
    assert "Updated" in (temp_git_repo / "docs/adr/0001-use-sqlite.md").read_text()

    adr2 = c.get(f"/v1/adrs/{adr['id']}").json()["adrs"][0]
    assert "Updated" in adr2["markdown"]
    assert adr2["reconcile_state"] == "synced"


def test_update_file_rejects_legacy():
    c = client()
    r = c.post("/v1/workspaces", json={"name": "no-repo-file"})
    ws_id = r.json()["workspaces"][0]["id"]
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Legacy", "status": "proposed",
    }).json()["adrs"][0]

    resp = c.put(f"/v1/adrs/{adr['id']}/file", json={"markdown": "# New\n"},
                 params={"workspace_id": ws_id})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ADR_CANONICAL_UPDATE"


def test_canonical_adr_crud_guards(temp_git_repo):
    """PUT/DELETE on a canonical ADR must be rejected (409) — file authority."""
    c = client()
    ws_id = _setup_ws_with_repo(c, temp_git_repo)
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    adr = c.get(f"/v1/adrs?workspace_id={ws_id}").json()["adrs"][0]

    resp = c.put(f"/v1/adrs/{adr['id']}", json={"status": "rejected"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ADR_CANONICAL_UPDATE"

    resp = c.delete(f"/v1/adrs/{adr['id']}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ADR_CANONICAL_DELETE"

    # The canonical file is untouched and still indexed.
    assert "We chose SQLite" in (temp_git_repo / "docs/adr/0001-use-sqlite.md").read_text()
    lst = c.get(f"/v1/adrs?workspace_id={ws_id}")
    assert len(lst.json()["adrs"]) == 1


def test_legacy_adr_crud_still_works():
    """DB-only ADRs keep full CRUD (they are not canonical yet)."""
    c = client()
    r = c.post("/v1/workspaces", json={"name": "legacy-crud"})
    ws_id = r.json()["workspaces"][0]["id"]
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_id, "title": "Legacy", "status": "proposed",
    }).json()["adrs"][0]

    resp = c.put(f"/v1/adrs/{adr['id']}", json={"status": "accepted"})
    assert resp.status_code == 200
    resp = c.delete(f"/v1/adrs/{adr['id']}")
    assert resp.status_code == 200


def test_cross_workspace_isolation(temp_git_repo):
    """Reconciliation must never leak ADRs across workspaces."""
    c = client()
    ws_a = _setup_ws_with_repo(c, temp_git_repo, name="iso-a")
    ws_b = _setup_ws_with_repo(c, temp_git_repo, name="iso-b")
    _write(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)

    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_a, "dry_run": False})
    assert len(c.get(f"/v1/adrs?workspace_id={ws_a}").json()["adrs"]) == 1
    # Workspace B shares the same repo path but must see nothing.
    assert c.get(f"/v1/adrs?workspace_id={ws_b}").json()["adrs"] == []


def test_materialize_membership_guard(temp_git_repo):
    """Materializing another workspace's ADR must be rejected."""
    c = client()
    ws_a = _setup_ws_with_repo(c, temp_git_repo, name="mem-a")
    ws_b = _setup_ws_with_repo(c, temp_git_repo, name="mem-b")
    adr = c.post("/v1/adrs", json={
        "workspace_id": ws_a, "title": "Secret", "status": "proposed",
    }).json()["adrs"][0]

    resp = c.post(f"/v1/adrs/{adr['id']}/materialize", json={"dry_run": True},
                  params={"workspace_id": ws_b})
    assert resp.status_code == 404  # no existence leak
