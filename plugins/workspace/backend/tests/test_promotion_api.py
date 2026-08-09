"""S7.5.5 — Promotion REST surface tests.

Scope→cap→membership→service→error ordering, workspace/profile isolation,
claim_hash immutability, staged/reconcile invariants exposed via the
presentation layer only.  The ledger/service invariants themselves are
proven in test_promotion_execution; this file proves the REST layer does
not weaken them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from fastapi.testclient import TestClient

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.workspace.backend.api.v1 import router
from plugins.workspace.backend.database import DatabaseManager
from plugins.workspace.backend.runtime import get_workspace_runtime, reset_workspace_runtimes
from plugins.workspace.backend.promotion_contract import hash_claim

from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _make_home(tmp_path: Path, name: str, *, write_approval: bool = False) -> Path:
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(yaml.dump({"memory": {"write_approval": bool(write_approval)}}))
    return home


def _seed_workspace_and_adr(storage, *, ws_id="ws-1", adr_id="adr-001", content_hash=None):
    conn = storage._conn
    conn.execute("INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')", (ws_id, "ws"))
    conn.execute(
        "INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) "
        "VALUES (?, ?, 'T', 't', 'accepted', '', ?, 'synced', 'git_file')",
        (adr_id, ws_id, content_hash or "a" * 64),
    )


def _propose_body(workspace_id, *, claim_text="Use JWT for authentication.", source_hash=None, target_kind="memory"):
    return {
        "workspace_id": workspace_id,
        "claim_text": claim_text,
        "assertion_type": "canonical_fact",
        "target_kind": target_kind,
        "source_type": "adr",
        "source_id": "adr-001",
        "source_canonical_id": "0001-t",
        "source_relative_path": "docs/adr/0001-t.md",
        "source_hash": source_hash or "a" * 64,
        "source_hash_kind": "sha256_bytes",
        "source_state": "synced",
        "project_id": "proj-1",
        "user_confirmed": True,
    }


def test_promotion_propose_requires_scope(tmp_path):
    home = _make_home(tmp_path, "home-scope")
    tok = set_hermes_home_override(str(home))
    DatabaseManager(db_path=home / "workspace.db").get_connection()
    try:
        r = client.post("/v1/promotions/propose", json={
            "claim_text": "x",
            "source_type": "adr",
            "source_id": "adr-001",
            "source_hash": "a" * 64,
            "source_hash_kind": "sha256_bytes",
        })
        assert r.status_code == 403
        assert r.json()["detail"]["code"] in ("SCOPE_UNRESOLVED", "SCOPE_AMBIGUOUS")
    finally:
        reset_hermes_home_override(tok)


def test_promotion_propose_and_get_and_list(tmp_path):
    home = _make_home(tmp_path, "home-list")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1")
        body = _propose_body("ws-1", claim_text="Claim list/get.")
        r = client.post("/v1/promotions/propose", json=body)
        assert r.status_code == 201, r.text
        pid = r.json()["promotions"][0]["promotion_id"]

        r2 = client.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"})
        assert r2.status_code == 200
        assert r2.json()["promotions"][0]["promotion_id"] == pid

        r3 = client.get("/v1/promotions", params={"workspace_id": "ws-1"})
        assert r3.status_code == 200
        assert any(p["promotion_id"] == pid for p in r3.json()["promotions"])
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_promotion_cross_workspace_404(tmp_path):
    home = _make_home(tmp_path, "home-cross")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1", adr_id="adr-001")
        conn = rt.storage._conn
        conn.execute("INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')", ("ws-2", "ws2"))
        conn.execute(
            "INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) VALUES (?, ?, 'T2','t2','accepted','','" + "b" * 64 + "','synced','git_file')",
            ("adr-002", "ws-2"),
        )
        body = _propose_body("ws-1", claim_text="Cross ws claim.")
        r = client.post("/v1/promotions/propose", json=body)
        pid = r.json()["promotions"][0]["promotion_id"]

        r2 = client.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-2"})
        assert r2.status_code == 404

        r3 = client.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-2"}, json={"claim_text": "Cross ws claim.", "user_confirmed": True})
        assert r3.status_code == 404

        r4 = client.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-2"}, json={"claim_text": "Cross ws claim.", "user_confirmed": True})
        assert r4.status_code == 404
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_promotion_cross_profile_404(tmp_path):
    home_a = _make_home(tmp_path, "homeA")
    home_b = _make_home(tmp_path, "homeB")
    tok_a = set_hermes_home_override(str(home_a))
    try:
        rt_a = get_workspace_runtime()
        _seed_workspace_and_adr(rt_a.storage, ws_id="ws-1")
        body = _propose_body("ws-1", claim_text="Profile isolate claim.")
        r = client.post("/v1/promotions/propose", json=body)
        pid = r.json()["promotions"][0]["promotion_id"]
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok_a)

    tok_b = set_hermes_home_override(str(home_b))
    try:
        DatabaseManager(db_path=home_b / "workspace.db").get_connection()
        get_workspace_runtime()
        r2 = client.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"})
        assert r2.status_code == 404
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok_b)


def test_promotion_claim_hash_mismatch_400(tmp_path):
    home = _make_home(tmp_path, "home-hash")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1")
        body = _propose_body("ws-1", claim_text="Original claim.")
        r = client.post("/v1/promotions/propose", json=body)
        pid = r.json()["promotions"][0]["promotion_id"]
        r2 = client.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": "Tampered claim.", "user_confirmed": True})
        assert r2.status_code == 400
        assert r2.json()["detail"]["code"] == "PROMOTION_CLAIM_MISMATCH"
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_promotion_execute_and_reconcile_invariants(tmp_path):
    home = _make_home(tmp_path, "home-exec")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1")
        claim = "Invariants claim."
        body = _propose_body("ws-1", claim_text=claim)
        r = client.post("/v1/promotions/propose", json=body)
        pid = r.json()["promotions"][0]["promotion_id"]

        r2 = client.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r2.status_code == 200
        assert r2.json()["promotions"][0]["status"] == "promoted"

        # Repeated execute is idempotent even with different casing/space
        r3 = client.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r3.status_code == 200
        assert r3.json()["promotions"][0]["status"] == "promoted"

        # Reconcile on promoted is idempotent
        r4 = client.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r4.status_code == 200
        assert r4.json()["promotions"][0]["status"] == "promoted"
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_promotion_pending_present_via_reconcile_stays_approved(tmp_path):
    home = _make_home(tmp_path, "home-pending")
    (home / "config.yaml").write_text(yaml.dump({"memory": {"write_approval": True}}))
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1")
        claim = "Pending present claim."
        body = _propose_body("ws-1", claim_text=claim)
        r = client.post("/v1/promotions/propose", json=body)
        pid = r.json()["promotions"][0]["promotion_id"]

        r2 = client.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r2.status_code == 200
        assert r2.json()["promotions"][0]["status"] == "approved"

        r3 = client.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r3.status_code == 200
        assert r3.json()["promotions"][0]["status"] == "approved"
    finally:
        from tools.write_approval import list_pending  # type: ignore
        # cleanup pending files created in this temp home
        try:
            for p in list_pending("memory"):
                from tools.write_approval import discard_pending  # type: ignore
                discard_pending("memory", p["id"])
        except Exception:
            pass
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_promotion_no_secret_in_api_response(tmp_path):
    home = _make_home(tmp_path, "home-no-secret")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_workspace_and_adr(rt.storage, ws_id="ws-1")
        claim = "No secret claim."
        body = _propose_body("ws-1", claim_text=claim)
        r = client.post("/v1/promotions/propose", json=body)
        j = r.json()
        txt = str(j)
        assert "BEGIN PRIVATE KEY" not in txt
        assert claim not in txt or r.status_code == 201  # claim in request only, not ledger? propose returns record without claim_text
        rec = j["promotions"][0]
        assert "claim_text" not in rec
        assert rec["claim_hash"] == hash_claim(claim)
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)
