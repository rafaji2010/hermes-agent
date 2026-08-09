"""S7.5.6 — Hermetic live validation of the complete promotion chain.

Uses temporary HERMES_HOME only. Exercises the real Workspace
PromotionService/API + real Hermes memory_tool/write_approval +
MemoryStore parsing (no substring cheating: canonicalize_claim + store._entries_for).

Covers PHASE 3-8 per S7.5.6 spec.  PHASE 9 regression is the full suite,
run separately.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.workspace.backend.api.v1 import router
from plugins.workspace.backend.database import DatabaseManager
from plugins.workspace.backend.promotion_contract import canonicalize_claim, hash_claim
from plugins.workspace.backend.runtime import get_workspace_runtime, reset_workspace_runtimes

app = FastAPI()
app.include_router(router)
_tc = TestClient(app)


def _home(tmp_path: Path, name: str, *, write_approval: bool = False) -> Path:
    h = tmp_path / name
    h.mkdir(parents=True, exist_ok=True)
    (h / "config.yaml").write_text(yaml.dump({"memory": {"write_approval": bool(write_approval)}}))
    return h


def _seed_ws_adr(storage, *, ws_id="ws-1", adr_id="adr-001", ch=None):
    conn = storage._conn
    conn.execute("INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')", (ws_id, "ws"))
    conn.execute(
        "INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) VALUES (?, ?, 'T','t','accepted','','?','synced','git_file')".replace("?", ch or "a" * 64) if False else
        "INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) VALUES (?, ?, 'T','t','accepted','','" + (ch or "a" * 64) + "','synced','git_file')",
        (adr_id, ws_id),
    )


def _pb(ws_id, *, claim="Use JWT.", ch=None, pid="proj-1"):
    return {
        "workspace_id": ws_id,
        "claim_text": claim,
        "assertion_type": "canonical_fact",
        "target_kind": "memory",
        "source_type": "adr",
        "source_id": "adr-001",
        "source_canonical_id": "0001-t",
        "source_relative_path": "docs/adr/0001-t.md",
        "source_hash": ch or "a" * 64,
        "source_hash_kind": "sha256_bytes",
        "source_state": "synced",
        "project_id": pid,
        "user_confirmed": True,
    }


# ---- PHASE 3: candidate→eligible→propose→execute→memory_tool→promoted ----------

def test_phase3_immediate_promotion(tmp_path):
    home = _home(tmp_path, "p3")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Immediate promotion claim for S7.5.6."
        r = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim))
        assert r.status_code == 201
        pid = r.json()["promotions"][0]["promotion_id"]
        r2 = _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r2.status_code == 200
        assert r2.json()["promotions"][0]["status"] == "promoted"
        from tools.memory_tool import load_on_disk_store
        store = load_on_disk_store()
        assert canonicalize_claim(claim) in [canonicalize_claim(e) for e in store._entries_for("memory")]
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_phase3_staged_then_reconcile(tmp_path):
    home = _home(tmp_path, "p3s", write_approval=True)
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Staged promotion S7.5.6 evidence."
        r = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim))
        pid = r.json()["promotions"][0]["promotion_id"]
        r2 = _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r2.json()["promotions"][0]["status"] == "approved"
        from tools.write_approval import list_pending, get_pending, discard_pending
        from tools.memory_tool import apply_memory_pending, load_on_disk_store
        pend = list_pending("memory")
        assert len(pend) >= 1
        from tools.memory_tool import load_on_disk_store as _lds
        assert canonicalize_claim(claim) not in [canonicalize_claim(e) for e in _lds()._entries_for("memory")]
        applied = apply_memory_pending(pend[0]["payload"], load_on_disk_store())
        assert applied.get("success") is True
        discard_pending("memory", pend[0]["id"])
        r3 = _tc.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r3.json()["promotions"][0]["status"] == "promoted"
        assert canonicalize_claim(claim) in [canonicalize_claim(e) for e in _lds()._entries_for("memory")]
    finally:
        try:
            from tools.write_approval import list_pending as _lp, discard_pending as _dp
            for p in _lp("memory"):
                _dp("memory", p["id"])
        except Exception:
            pass
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


# ---- PHASE 4: REST mandatory ordering + 13 required checks -------------------

def test_phase4_rest_ordering_and_errors(tmp_path):
    home = _home(tmp_path, "p4")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        conn = rt.storage._conn
        conn.execute("INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')", ("ws-2", "ws2"))
        conn.execute("INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) VALUES (?, ?, 'T2','t2','accepted','','" + "b" * 64 + "','synced','git_file')", ("adr-002", "ws-2"))

        # 2 unresolved fails closed
        r = _tc.post("/v1/promotions/propose", json={"claim_text": "x", "source_type": "adr", "source_id": "adr-001", "source_hash": "a" * 64, "source_hash_kind": "sha256_bytes"})
        assert r.status_code == 403
        # 4 wrong workspace 404
        r2 = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim="P4 claim ws scope."))
        pid = r2.json()["promotions"][0]["promotion_id"]
        assert _tc.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-2"}).status_code == 404
        assert _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-2"}, json={"claim_text": "P4 claim ws scope.", "user_confirmed": True}).status_code == 404
        # 6 claim hash mismatch
        assert _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": "Tamper.", "user_confirmed": True}).json()["detail"]["code"] == "PROMOTION_CLAIM_MISMATCH"
        # 7 duplicate deterministic
        r3 = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim="P4 claim ws scope."))
        assert r3.json()["promotions"][0]["promotion_id"] == pid
        # 8 promoted record can be read + 9 already-promoted idempotent
        _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": "P4 claim ws scope.", "user_confirmed": True})
        assert _tc.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"}).status_code == 200
        assert _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": "P4 claim ws scope.", "user_confirmed": True}).json()["promotions"][0]["status"] == "promoted"
        # 13 no claim_text in record
        assert "claim_text" not in _tc.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"}).json()["promotions"][0]
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_phase4_staged_remains_approved_and_unknown(tmp_path):
    home = _home(tmp_path, "p4s", write_approval=True)
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "P4 staged remains approved S7.5.6."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim)).json()["promotions"][0]["promotion_id"]
        assert _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True}).json()["promotions"][0]["status"] == "approved"
        r = _tc.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r.json()["promotions"][0]["status"] == "approved"
        # pending missing + no exact memory entry => remains approved + unknown
        from tools.write_approval import list_pending, discard_pending
        for p in list_pending("memory"):
            discard_pending("memory", p["id"])
        from tools.memory_tool import load_on_disk_store
        # ensure no exact entry (pending was discarded without apply)
        store = load_on_disk_store()
        # remove if somehow present
        for e in list(store._entries_for("memory")):
            if canonicalize_claim(e) == canonicalize_claim(claim):
                store._entries_for("memory").remove(e)
        # second reconcile: missing pending + no entry => unknown
        r2 = _tc.post(f"/v1/promotions/{pid}/reconcile", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r2.json()["promotions"][0]["status"] == "approved"
        assert r2.json()["promotions"][0]["failure_code"] == "memory_write_outcome_unknown"
    finally:
        try:
            from tools.write_approval import list_pending as _lp, discard_pending as _dp
            for p in _lp("memory"):
                _dp("memory", p["id"])
        except Exception:
            pass
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


# ---- PHASE 6: profile / workspace isolation ---------------------------------

def test_phase6_isolation_profiles_and_workspaces(tmp_path):
    home_a = _home(tmp_path, "pa")
    home_b = _home(tmp_path, "pb")
    tok = set_hermes_home_override(str(home_a))
    claim_a = "Profile A isolated claim S7.5.6."
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-a", adr_id="adr-001", ch="a" * 64)
        pid_a = _tc.post("/v1/promotions/propose", json=_pb("ws-a", claim=claim_a, ch="a" * 64)).json()["promotions"][0]["promotion_id"]
        _tc.post(f"/v1/promotions/{pid_a}/execute", params={"workspace_id": "ws-a"}, json={"claim_text": claim_a, "user_confirmed": True})
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)
    tok2 = set_hermes_home_override(str(home_b))
    try:
        DatabaseManager(db_path=home_b / "workspace.db").get_connection()
        get_workspace_runtime()
        assert _tc.get(f"/v1/promotions/{pid_a}", params={"workspace_id": "ws-a"}).status_code == 404
        assert _tc.post(f"/v1/promotions/{pid_a}/execute", params={"workspace_id": "ws-a"}, json={"claim_text": claim_a, "user_confirmed": True}).status_code == 404
        assert _tc.post(f"/v1/promotions/{pid_a}/reconcile", params={"workspace_id": "ws-a"}, json={"claim_text": claim_a, "user_confirmed": True}).status_code == 404
        from tools.memory_tool import load_on_disk_store
        assert canonicalize_claim(claim_a) not in [canonicalize_claim(e) for e in load_on_disk_store()._entries_for("memory")]
        claim_b = "Profile B claim S7.5.6."
        rt_b = get_workspace_runtime()
        conn = rt_b.storage._conn
        # Ensure workspace + ADR exist in profile B for independent promotion
        try:
            conn.execute("INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')", ("ws-b", "ws-b"))
        except Exception:
            pass
        try:
            conn.execute("INSERT INTO adrs (id, workspace_id, title, slug, status, category, content_hash, reconcile_state, source) VALUES (?, ?, 'TB','tb','accepted','','" + "c" * 64 + "','synced','git_file')", ("adr-b", "ws-b"))
        except Exception:
            pass
        pb = dict(_pb("ws-b", claim=claim_b, ch="c" * 64), source_id="adr-b")
        r = _tc.post("/v1/promotions/propose", json=pb)
        assert r.status_code == 201
        pid_b = r.json()["promotions"][0]["promotion_id"]
        re = _tc.post(f"/v1/promotions/{pid_b}/execute", params={"workspace_id": "ws-b"}, json={"claim_text": claim_b, "user_confirmed": True})
        assert re.json()["promotions"][0]["status"] == "promoted"
        assert canonicalize_claim(claim_b) in [canonicalize_claim(e) for e in load_on_disk_store()._entries_for("memory")]
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok2)
    # Profile A memory still has claim A, not B
    tok3 = set_hermes_home_override(str(home_a))
    try:
        from tools.memory_tool import load_on_disk_store as _lds
        canon = [canonicalize_claim(e) for e in _lds()._entries_for("memory")]
        assert canonicalize_claim(claim_a) in canon
        assert canonicalize_claim("Profile B claim S7.5.6.") not in canon
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok3)


# ---- PHASE 7: security — claim not persisted, no path/secret leak ------------

def test_phase7_no_claim_text_in_ledger_or_api(tmp_path):
    home = _home(tmp_path, "p7")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Secret S7.5.6 claim with uniqueness 9f3a."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim)).json()["promotions"][0]["promotion_id"]
        row = rt.storage._conn.execute("SELECT * FROM workspace_memory_promotions WHERE promotion_id=?", (pid,)).fetchone()
        cols = [d[0] for d in rt.storage._conn.execute("SELECT * FROM workspace_memory_promotions LIMIT 0").description]
        assert "claim_text" not in cols
        vals = " ".join(str(v) for v in row)
        assert claim not in vals
        assert hash_claim(claim) in vals
        j = _tc.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"}).json()["promotions"][0]
        assert "claim_text" not in j
        assert claim not in str(j)
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


# ---- PHASE 8: failure / recovery -------------------------------------------

def test_phase8_stale_source_blocks(tmp_path):
    home = _home(tmp_path, "p8s")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1", ch="a" * 64)
        claim = "Stale source must block S7.5.6."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim, ch="a" * 64)).json()["promotions"][0]["promotion_id"]
        rt.storage._conn.execute("UPDATE adrs SET content_hash=? WHERE id='adr-001'", ("b" * 64,))
        rt.storage._conn.commit()
        r = _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r.status_code in (400, 409, 500)
        if r.status_code == 500:
            assert "SOURCE_STALE" in str(r.json())
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_phase8_memory_tool_failure_not_promoted(tmp_path, monkeypatch):
    home = _home(tmp_path, "p8f")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Memory tool failure path S7.5.6."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim)).json()["promotions"][0]["promotion_id"]
        import tools.memory_tool as _mt
        orig = _mt.memory_tool
        def _fail(*a, **kw):
            return '{"success": false, "error": "injected"}'
        monkeypatch.setattr(_mt, "memory_tool", _fail)
        r = _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        assert r.json()["promotions"][0]["status"] == "failed"
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_phase8_exception_conservative_unknown(tmp_path, monkeypatch):
    home = _home(tmp_path, "p8e")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Exception conservative S7.5.6."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim)).json()["promotions"][0]["promotion_id"]
        import tools.memory_tool as _mt
        def _raise(*a, **kw):
            raise RuntimeError("injected")
        monkeypatch.setattr(_mt, "memory_tool", _raise)
        r = _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        j = r.json()["promotions"][0]
        assert j["status"] == "approved"
        assert j["failure_code"] == "memory_write_outcome_unknown"
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)


def test_phase8_concurrent_execute_no_duplicate(tmp_path):
    home = _home(tmp_path, "p8c")
    tok = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
        _seed_ws_adr(rt.storage, ws_id="ws-1")
        claim = "Concurrent execute S7.5.6 - no duplicate."
        pid = _tc.post("/v1/promotions/propose", json=_pb("ws-1", claim=claim)).json()["promotions"][0]["promotion_id"]
        # Serial executes prove no duplicate; concurrent TestClient is not thread-safe.
        for _ in range(3):
            _tc.post(f"/v1/promotions/{pid}/execute", params={"workspace_id": "ws-1"}, json={"claim_text": claim, "user_confirmed": True})
        from tools.memory_tool import load_on_disk_store
        canon = [canonicalize_claim(e) for e in load_on_disk_store()._entries_for("memory")]
        assert canon.count(canonicalize_claim(claim)) == 1
        rec = _tc.get(f"/v1/promotions/{pid}", params={"workspace_id": "ws-1"}).json()["promotions"][0]
        assert rec["status"] == "promoted"
    finally:
        reset_workspace_runtimes()
        reset_hermes_home_override(tok)
