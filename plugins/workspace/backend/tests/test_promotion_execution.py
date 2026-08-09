"""S7.5.4a — Explicit memory promotion execution tests.

Proves the execute_promotion flow end-to-end against the REAL Hermes
memory_tool dispatcher (headless load_on_disk_store) with isolated temp
HERMES_HOME so MEMORY.md lands in a temp directory — never production.

Covers the 18 required scenarios:
  eligible->pending->approved->promoted
  rejected never writes
  unconfirmed model_inference never writes
  unresolved / ambiguous scope fail closed
  workspace mismatch fail closed
  stale source hash fails before write
  memory write failure -> failed
  write_approval=true -> staged=true -> remains approved
  duplicate execution idempotent
  concurrent execution one successful transition
  crash-after-write not falsely promoted
  caller claim_text hash mismatch
  profile mismatch
  malformed memory_tool response
  missing source
  source conflict
  repeated promoted execution does not write twice
  no MemoryStore bypass (memory_tool is the only path)
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

from hermes_constants import (  # type: ignore[import-untyped]
    reset_hermes_home_override,
    set_hermes_home_override,
)
from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    PromotionRecordError,
    PromotionRecordNotFoundError,
)
from plugins.workspace.backend.promotion_models import (  # type: ignore[import-untyped]
    AssertionType,
    ProvenanceEnvelope,
    ScopeSnapshot,
    SourceHashKind,
    SourceType,
    TargetKind,
)
from plugins.workspace.backend.promotion_contract import (  # type: ignore[import-untyped]
    hash_claim,
    hash_structured_snapshot,
    make_candidate,
    structured_snapshot_fields,
)
from plugins.workspace.backend.services.promotion_service import (  # type: ignore[import-untyped]
    PromotionService,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Fixtures — isolated temp HERMES_HOME + real workspace.db + MEMORY.md
# ---------------------------------------------------------------------------


@pytest.fixture
def env(tmp_path) -> Iterator[tuple]:
    """A real profile home: workspace.db + config.yaml, override active."""
    home = tmp_path / "profile-a"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(yaml.dump({"memory": {"write_approval": False}}))
    tok = set_hermes_home_override(str(home))
    mgr = DatabaseManager(db_path=home / "workspace.db")
    mgr.get_connection()
    storage = SQLiteStorage(db_manager=mgr)
    _seed_workspace(storage, ws_id="ws-1", name="ws-a")
    _seed_adr(storage, ws_id="ws-1", adr_id="adr-001")
    service = PromotionService(storage=storage)
    try:
        yield home, storage, service
    finally:
        reset_hermes_home_override(tok)
        mgr.close()


def _seed_workspace(storage, ws_id="ws-1", name="ws-a") -> str:
    conn = storage._conn
    conn.execute(
        "INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')",
        (ws_id, name),
    )
    return ws_id


def _seed_adr(storage, ws_id="ws-1", adr_id="adr-001", *, state="synced",
              content_hash=None, workspace_override=None) -> str:
    conn = storage._conn
    ws = workspace_override or ws_id
    conn.execute(
        "INSERT INTO adrs (id, workspace_id, title, slug, status, category, "
        "content_hash, reconcile_state, source) "
        "VALUES (?, ?, 'Auth Design', 'auth-design', 'accepted', '', ?, ?, 'git_file')",
        (adr_id, ws, content_hash or "a" * 64, state),
    )
    return adr_id


def _adr_provenance(**kw) -> ProvenanceEnvelope:
    defaults = dict(
        source_type=SourceType.ADR,
        source_id="adr-001",
        source_canonical_id="0001-auth-design",
        source_relative_path="docs/adr/0001-auth-design.md",
        source_hash="a" * 64,
        source_hash_kind=SourceHashKind.SHA256_BYTES,
        source_state="synced",
        workspace_id="ws-1",
        project_id="proj-1",
        profile_label="profile-a",
    )
    defaults.update(kw)
    return ProvenanceEnvelope(**defaults)


def _scope(**kw) -> ScopeSnapshot:
    defaults = dict(
        profile_label="profile-a",
        workspace_id="ws-1",
        workspace_name="ws-a",
        project_id="proj-1",
        project_slug="proj-a",
        scope_state="mapped",
        match_source="ledger",
    )
    defaults.update(kw)
    return ScopeSnapshot(**defaults)


def _candidate(claim_text="Use JWT for authentication.", **kw):
    fields = dict(
        claim_text=claim_text,
        assertion_type=AssertionType.CANONICAL_FACT,
        target_kind=TargetKind.MEMORY,
        provenance=_adr_provenance(),
        scope=_scope(),
        user_confirmed=True,
    )
    fields.update(kw)
    return make_candidate(**fields)


def _propose(service, **kw) -> str:
    return service.propose(_candidate(**kw)).promotion_id


def _memory_md(home: Path) -> str:
    p = home / "memories" / "MEMORY.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_eligible_to_promoted(self, env):
        home, storage, service = env
        pid = _propose(service)
        assert service.get(pid).status == "eligible"

        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "promoted"
        assert rec.promoted_at
        assert "Use JWT for authentication." in _memory_md(home)

    def test_user_target_uses_USER_md(self, env):
        home, storage, service = env
        pid = _propose(service, claim_text="User prefers JWT.",
                       target_kind=TargetKind.USER)
        rec = service.execute_promotion(
            pid, "User prefers JWT.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "promoted"
        user_md = home / "memories" / "USER.md"
        assert "User prefers JWT." in user_md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. Rejection paths — never write
# ---------------------------------------------------------------------------


class TestRejectionPaths:
    def test_rejected_candidate_never_writes(self, env):
        home, storage, service = env
        pid = _propose(service, provenance=_adr_provenance(source_state="conflict"))
        assert service.get(pid).status == "rejected"
        with pytest.raises(PromotionRecordError):
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert _memory_md(home) == ""

    def test_unconfirmed_model_inference_never_writes(self, env):
        home, storage, service = env
        pid = _propose(
            service,
            claim_text="Maybe use OAuth2.",
            assertion_type=AssertionType.MODEL_INFERENCE,
            user_confirmed=False,
        )
        assert service.get(pid).status == "proposed"
        with pytest.raises(PromotionRecordError) as exc:
            service.execute_promotion(
                pid, "Maybe use OAuth2.",
                workspace_id="ws-1", user_confirmed=False,
            )
        assert getattr(exc.value, "code", "") == "PROMOTION_NOT_CONFIRMED"
        assert _memory_md(home) == ""
        # still proposed — not rejected, not written
        assert service.get(pid).status == "proposed"

    def test_user_authored_without_confirmation_never_writes(self, env):
        home, storage, service = env
        pid = _propose(
            service,
            assertion_type=AssertionType.USER_AUTHORED_FACT,
            user_confirmed=False,
        )
        assert service.get(pid).status == "manual_only"
        with pytest.raises(PromotionRecordError):
            service.execute_promotion(
                pid, "Manual fact.",
                workspace_id="ws-1", user_confirmed=False,
            )
        assert _memory_md(home) == ""

    def test_missing_source_fails_closed(self, env):
        home, storage, service = env
        pid = _propose(service)
        # delete the ADR row after proposal
        conn = storage._conn
        conn.execute("DELETE FROM adrs WHERE id = 'adr-001'")
        conn.commit()
        with pytest.raises(PromotionRecordError) as exc:
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert getattr(exc.value, "code", "") == "SOURCE_STALE"
        assert _memory_md(home) == ""

    def test_source_conflict_fails_closed(self, env):
        home, storage, service = env
        pid = _propose(service)
        # flip the ADR to conflicted after proposal
        conn = storage._conn
        conn.execute(
            "UPDATE adrs SET reconcile_state = 'conflict' WHERE id = 'adr-001'"
        )
        conn.commit()
        with pytest.raises(PromotionRecordError):
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert _memory_md(home) == ""

    def test_stale_source_hash_fails_before_write(self, env):
        home, storage, service = env
        pid = _propose(service)
        # change the canonical bytes -> content_hash changes
        conn = storage._conn
        conn.execute(
            "UPDATE adrs SET content_hash = 'b' * 64 WHERE id = 'adr-001'"
        )
        conn.commit()
        with pytest.raises(PromotionRecordError) as exc:
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert getattr(exc.value, "code", "") == "SOURCE_STALE"
        assert _memory_md(home) == ""


# ---------------------------------------------------------------------------
# 3. Scope / workspace / profile guards
# ---------------------------------------------------------------------------


class TestScopeGuards:
    def test_workspace_mismatch_fails_closed(self, env):
        home, storage, service = env
        pid = _propose(service)
        with pytest.raises(PromotionRecordNotFoundError):
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="other-ws", user_confirmed=True,
            )
        assert _memory_md(home) == ""

    def test_profile_mismatch_fails_closed(self, tmp_path, env):
        home, storage, service = env
        pid = _propose(service)
        # switch override to a different profile while keeping the same storage
        other = tmp_path / "profile-b"
        other.mkdir(parents=True)
        tok = set_hermes_home_override(str(other))
        try:
            with pytest.raises(PromotionRecordNotFoundError):
                service.execute_promotion(
                    pid, "Use JWT for authentication.",
                    workspace_id="ws-1", user_confirmed=True,
                )
        finally:
            reset_hermes_home_override(tok)
        # profile-a memory unchanged
        assert _memory_md(home) == ""

    def test_unresolved_scope_fails_closed(self, env):
        """A workspace that no longer exists fails closed (no global fallback)."""
        home, storage, service = env
        pid = _propose(service)
        conn = storage._conn
        conn.execute("DELETE FROM workspaces WHERE id = 'ws-1'")
        conn.commit()
        with pytest.raises(Exception):
            service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert _memory_md(home) == ""

    def test_ambiguous_scope_fails_closed_at_propose(self, env):
        """Ambiguous scope never reaches the ledger (existing propose guard)."""
        home, storage, service = env
        from plugins.workspace.backend.models import ScopeAmbiguousError

        with pytest.raises(ScopeAmbiguousError):
            service.propose(_candidate(), scope_state="ambiguous")


# ---------------------------------------------------------------------------
# 4. Claim / result integrity
# ---------------------------------------------------------------------------


class TestClaimAndResultIntegrity:
    def test_caller_claim_hash_mismatch_rejected(self, env):
        home, storage, service = env
        pid = _propose(service, claim_text="Use JWT for authentication.")
        with pytest.raises(PromotionRecordError) as exc:
            service.execute_promotion(
                pid, "Use OAuth2 instead!",  # different claim
                workspace_id="ws-1", user_confirmed=True,
            )
        assert getattr(exc.value, "code", "") == "PROMOTION_CLAIM_MISMATCH"
        assert _memory_md(home) == ""

    def test_malformed_memory_tool_response_fails(self, env, monkeypatch):
        home, storage, service = env
        pid = _propose(service)
        import plugins.workspace.backend.services.promotion_service as ps

        def _bad(*a, **k):
            return "this is not json"

        monkeypatch.setattr(
            "tools.memory_tool.memory_tool", _bad
        )
        # load_on_disk_store is fine; only memory_tool result is malformed.
        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "failed"
        assert rec.failure_code == "malformed_memory_tool_response"

    def test_memory_write_failure_marks_failed(self, env, monkeypatch):
        home, storage, service = env
        pid = _propose(service)

        def _fail(*a, **k):
            return json.dumps({"success": False, "error": "memory is full"})

        monkeypatch.setattr("tools.memory_tool.memory_tool", _fail)
        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "failed"
        assert "memory is full" in rec.failure_code

    def test_memory_tool_exception_conservative_approved(self, env, monkeypatch):
        """A raised memory_tool leaves the outcome UNKNOWN: the ledger stays
        at approved with an explicit marker — never guessed promoted/failed."""
        home, storage, service = env
        pid = _propose(service)

        def _boom(*a, **k):
            raise RuntimeError("store unavailable")

        monkeypatch.setattr("tools.memory_tool.memory_tool", _boom)
        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"


# ---------------------------------------------------------------------------
# 5. write_approval=true -> staged
# ---------------------------------------------------------------------------


class TestStagedApproval:
    def _env_with_approval(self, tmp_path):
        home = tmp_path / "profile-a"
        home.mkdir(parents=True)
        (home / "config.yaml").write_text(
            yaml.dump({"memory": {"write_approval": True}})
        )
        tok = set_hermes_home_override(str(home))
        mgr = DatabaseManager(db_path=home / "workspace.db")
        mgr.get_connection()
        storage = SQLiteStorage(db_manager=mgr)
        _seed_workspace(storage)
        _seed_adr(storage)
        service = PromotionService(storage=storage)
        pid = service.propose(_candidate()).promotion_id
        return home, storage, service, pid, tok, mgr

    def test_write_approval_true_stages_and_stays_approved(self, tmp_path):
        home, storage, service, pid, tok, mgr = self._env_with_approval(tmp_path)
        try:
            rec = service.execute_promotion(
                pid, "Use JWT for authentication.",
                workspace_id="ws-1", user_confirmed=True,
            )
            # staged -> remains approved, NOT promoted
            assert rec.status == "approved"
            assert _memory_md(home) == ""
            # the Hermes pending file is the source of truth
            pending_dir = home / "pending" / "memory"
            pending = list(pending_dir.glob("*.json")) if pending_dir.exists() else []
            assert len(pending) >= 1, "staged write must create a pending file"
        finally:
            reset_hermes_home_override(tok)
            mgr.close()


# ---------------------------------------------------------------------------
# 6. Idempotency / concurrency / crash
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_duplicate_execution_idempotent(self, env):
        home, storage, service = env
        pid = _propose(service)
        r1 = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert r1.status == "promoted"
        r2 = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert r2.status == "promoted"
        assert r2.promotion_id == r1.promotion_id
        # exactly one entry in MEMORY.md
        md = _memory_md(home)
        assert md.count("Use JWT for authentication.") == 1

    def test_repeated_promoted_does_not_write_twice(self, env):
        home, storage, service = env
        pid = _propose(service)
        service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        md_before = _memory_md(home)
        service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert _memory_md(home) == md_before

    def test_concurrent_execution_one_success(self, env):
        """Concurrent execute: exactly one thread transitions to promoted and
        the memory write lands once; losing threads see the deterministic
        illegal-transition error.

        Each worker establishes the HERMES_HOME override itself — the
        ContextVar is not copied into new threads, so a worker without the
        override correctly fails closed (profile mismatch), mirroring the
        per-request override pattern the gateway uses."""
        home, storage, service = env
        pid = _propose(service)
        results: list = []
        errors: list = []

        def _run():
            tok = set_hermes_home_override(str(home))
            try:
                rec = service.execute_promotion(
                    pid, "Use JWT for authentication.",
                    workspace_id="ws-1", user_confirmed=True,
                )
                results.append(rec.status)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                reset_hermes_home_override(tok)

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        promoted = [s for s in results if s == "promoted"]
        assert promoted == ["promoted"], f"expected one promoted winner, got {results}"
        assert len(errors) == 3, f"expected 3 losers, got {len(errors)}"
        for err in errors:
            assert getattr(err, "code", "") == "PROMOTION_ILLEGAL_TRANSITION", err
        # memory written once
        assert _memory_md(home).count("Use JWT for authentication.") == 1

    def test_crash_after_write_not_falsely_promoted(self, env, monkeypatch):
        """Simulate a crash after MEMORY.md write but before ledger promoted:
        the ledger must stay at approved — never guessed promoted."""
        home, storage, service = env
        pid = _propose(service)

        calls = {"n": 0}
        import tools.memory_tool as mt

        real_memory_tool = mt.memory_tool

        def _real_then_crash(*a, **k):
            # perform the real write (store arrives via kwargs), then raise
            # before the service can transition to promoted
            raw = real_memory_tool(*a, **k)
            calls["n"] += 1
            raise RuntimeError("simulated crash after write")

        monkeypatch.setattr("tools.memory_tool.memory_tool", _real_then_crash)
        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        # The write happened; the exception makes the outcome UNKNOWN.
        # Conservatively the ledger stays at approved — never guessed promoted.
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"
        assert calls["n"] == 1

        # A follow-up execution must not auto-promote from MEMORY.md contents.
        monkeypatch.undo()
        rec2 = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        # still approved (no false promoted)
        assert rec2.status == "approved"


# ---------------------------------------------------------------------------
# 7. No MemoryStore bypass
# ---------------------------------------------------------------------------


class TestNoBypass:
    def test_execution_uses_memory_tool_only(self, env, monkeypatch):
        """The service reaches MemoryStore ONLY through the memory_tool
        dispatcher (which internally calls store.add).  A direct service
        call would still go through the dispatcher — the assertion is that
        exactly one dispatcher call happens and the write lands."""
        home, storage, service = env
        pid = _propose(service)
        called = {"memory_tool": 0, "store_add": 0}

        import tools.memory_tool as mt

        real_memory_tool = mt.memory_tool
        real_store_add = mt.MemoryStore.add

        def _wrapped(*a, **k):
            called["memory_tool"] += 1
            return real_memory_tool(*a, **k)

        def _tracked_add(self, *a, **k):
            called["store_add"] += 1
            return real_store_add(self, *a, **k)

        monkeypatch.setattr(mt, "memory_tool", _wrapped)
        monkeypatch.setattr(mt.MemoryStore, "add", _tracked_add)

        rec = service.execute_promotion(
            pid, "Use JWT for authentication.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "promoted"
        assert called["memory_tool"] == 1
        assert called["store_add"] == 1  # via the dispatcher, once


# ---------------------------------------------------------------------------
# 8. Mutable structured sources (journal/task/roadmap) via snapshot
# ---------------------------------------------------------------------------


class TestStructuredSources:
    def test_journal_snapshot_freshness(self, env):
        home, storage, service = env
        conn = storage._conn
        conn.execute(
            "INSERT INTO journal_entries (id, workspace_id, title, summary, "
            "entry_date) VALUES ('je-1', 'ws-1', 'Structured logging', 'Summary', "
            "'2026-08-01')"
        )
        conn.commit()
        row = storage.get_journal_entry("je-1")
        snap = hash_structured_snapshot(structured_snapshot_fields(SourceType.JOURNAL, row))
        provenance = _adr_provenance(
            source_type=SourceType.JOURNAL,
            source_id="je-1",
            source_hash=snap,
            source_hash_kind=SourceHashKind.STRUCTURED_SNAPSHOT,
            source_state="mutable",
        )
        pid = service.propose(make_candidate(
            claim_text="Use structured logging.",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=provenance,
            scope=_scope(),
            user_confirmed=True,
        )).promotion_id
        rec = service.execute_promotion(
            pid, "Use structured logging.",
            workspace_id="ws-1", user_confirmed=True,
        )
        assert rec.status == "promoted"
        assert "Use structured logging." in _memory_md(home)

    def test_journal_snapshot_stale_fails(self, env):
        home, storage, service = env
        conn = storage._conn
        conn.execute(
            "INSERT INTO journal_entries (id, workspace_id, title, summary, "
            "entry_date) VALUES ('je-1', 'ws-1', 'Structured logging', 'Summary', "
            "'2026-08-01')"
        )
        conn.commit()
        row = storage.get_journal_entry("je-1")
        snap = hash_structured_snapshot(structured_snapshot_fields(SourceType.JOURNAL, row))
        provenance = _adr_provenance(
            source_type=SourceType.JOURNAL,
            source_id="je-1",
            source_hash=snap,
            source_hash_kind=SourceHashKind.STRUCTURED_SNAPSHOT,
            source_state="mutable",
        )
        pid = service.propose(make_candidate(
            claim_text="Use structured logging.",
            assertion_type=AssertionType.USER_CONFIRMED_SUMMARY,
            target_kind=TargetKind.MEMORY,
            provenance=provenance,
            scope=_scope(),
            user_confirmed=True,
        )).promotion_id
        # mutate the journal entry after proposal -> snapshot changes
        conn.execute(
            "UPDATE journal_entries SET summary = 'Changed' WHERE id = 'je-1'"
        )
        conn.commit()
        with pytest.raises(PromotionRecordError) as exc:
            service.execute_promotion(
                pid, "Use structured logging.",
                workspace_id="ws-1", user_confirmed=True,
            )
        assert getattr(exc.value, "code", "") == "SOURCE_STALE"
        assert _memory_md(home) == ""


# ---------------------------------------------------------------------------
# S7.5.4b — Reconciliation tests
# ---------------------------------------------------------------------------


def _write_pending(home: Path, pending_id: str, target: str, content: str) -> None:
    """Create a Hermes staged pending memory file under the profile home."""
    import time

    d = home / "pending" / "memory"
    d.mkdir(parents=True, exist_ok=True)
    record = {
        "id": pending_id,
        "subsystem": "memory",
        "action": "add",
        "summary": "add to memory",
        "origin": "foreground",
        "created_at": time.time(),
        "payload": {"action": "add", "target": target, "content": content},
    }
    (d / f"{pending_id}.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )


def _remove_pending(home: Path, pending_id: str) -> None:
    p = home / "pending" / "memory" / f"{pending_id}.json"
    if p.exists():
        p.unlink()


def _write_memory_entry(home: Path, target: str, content: str) -> None:
    """Write an exact entry using Hermes' own MemoryStore (no bypass)."""
    from tools.memory_tool import load_on_disk_store

    store = load_on_disk_store()
    store.add(target, content)


def _make_approved(service, *, claim_text="Use JWT for authentication.") -> str:
    """Propose + transition to approved via execute (non-staged is the
    fastest path; here we force approved by directly staging)."""
    # propose an eligible candidate
    pid = _propose(service, claim_text=claim_text)
    # move to approved via execute with write_approval staged? Instead just
    # transition the ledger directly through the allowed path.
    service.transition(pid, "pending", workspace_id="ws-1")
    service.transition(pid, "approved", workspace_id="ws-1")
    return pid


class TestReconcilePromotion:
    def test_pending_present_remains_approved(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "abc12345", "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"

    def test_pending_missing_exact_entry_promoted(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_memory_entry(home, "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "promoted"
        assert rec.promoted_at

    def test_pending_missing_no_entry_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_malformed_pending_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        d = home / "pending" / "memory"
        d.mkdir(parents=True, exist_ok=True)
        (d / "zzz99999.json").write_text("{ not json", encoding="utf-8")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_claim_hash_mismatch(self, env):
        home, storage, service = env
        pid = _make_approved(service, claim_text="Use JWT for authentication.")
        with pytest.raises(PromotionRecordError) as exc:
            service.reconcile_promotion(
                pid, workspace_id="ws-1", user_confirmed=True,
                claim_text="Use OAuth2 instead!",
            )
        assert getattr(exc.value, "code", "") == "PROMOTION_CLAIM_MISMATCH"

    def test_duplicate_pending_ids_no_arbitrary_selection(self, env):
        """Two pending files with the same canonical claim and no recorded
        pending_id -> ambiguous/unknown, never arbitrary selection."""
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "aaaa1111", "memory", "Use JWT for authentication.")
        _write_pending(home, "bbbb2222", "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        # ambiguous fallback -> unknown (not promoted)
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_ambiguous_fallback_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "aaaa1111", "memory", "Use JWT for authentication.")
        _write_pending(home, "cccc3333", "user", "Use JWT for authentication.")
        # one target-matching file -> not ambiguous by target; but the recorded
        # pending_id is absent so fallback matches exactly one -> should use it.
        # This proves the exactly-one rule works.
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        # exactly one memory-target match -> uses it -> still pending (file present)
        assert rec.status == "approved"

    def test_cross_profile_isolation(self, tmp_path, env):
        home, storage, service = env
        pid = _make_approved(service)
        other = tmp_path / "profile-b"
        other.mkdir(parents=True)
        tok = set_hermes_home_override(str(other))
        try:
            with pytest.raises(PromotionRecordNotFoundError):
                service.reconcile_promotion(
                    pid, workspace_id="ws-1", user_confirmed=True,
                    claim_text="Use JWT for authentication.",
                )
        finally:
            reset_hermes_home_override(tok)

    def test_cross_workspace_isolation(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        with pytest.raises(PromotionRecordNotFoundError):
            service.reconcile_promotion(
                pid, workspace_id="other-ws", user_confirmed=True,
                claim_text="Use JWT for authentication.",
            )

    def test_stale_after_write_promoted(self, env):
        """Source becoming stale AFTER a successful write must NOT block."""
        home, storage, service = env
        pid = _make_approved(service)
        _write_memory_entry(home, "memory", "Use JWT for authentication.")
        # stale the ADR source after the write
        conn = storage._conn
        conn.execute(
            "UPDATE adrs SET content_hash = 'b' * 64, reconcile_state = 'conflict' "
            "WHERE id = 'adr-001'"
        )
        conn.commit()
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "promoted"

    def test_stale_before_completion_no_reauthorize(self, env):
        """Pending present + source stale -> reconciliation does NOT
        re-authorize; remains approved."""
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "abc12345", "memory", "Use JWT for authentication.")
        conn = storage._conn
        conn.execute(
            "UPDATE adrs SET content_hash = 'b' * 64 WHERE id = 'adr-001'"
        )
        conn.commit()
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"

    def test_concurrent_reconciliation_one_transition(self, env):
        import threading

        home, storage, service = env
        pid = _make_approved(service)
        _write_memory_entry(home, "memory", "Use JWT for authentication.")
        results: list = []
        errors: list = []

        def _run():
            tok = set_hermes_home_override(str(home))
            try:
                rec = service.reconcile_promotion(
                    pid, workspace_id="ws-1", user_confirmed=True,
                    claim_text="Use JWT for authentication.",
                )
                results.append(rec.status)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
            finally:
                reset_hermes_home_override(tok)

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert set(results) == {"promoted"}, f"got {results} errors={errors}"
        # exactly one transition, others idempotent-return promoted
        assert len(results) == 4
        assert not errors

    def test_repeated_reconciliation_idempotent(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_memory_entry(home, "memory", "Use JWT for authentication.")
        r1 = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert r1.status == "promoted"
        md_before = _memory_md(home)
        r2 = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert r2.status == "promoted"
        assert _memory_md(home) == md_before

    def test_crash_after_write_promoted_on_reconcile(self, env):
        """Crash after memory write before ledger promote -> reconcile finds
        the exact entry and promotes."""
        home, storage, service = env
        pid = _make_approved(service)
        _write_memory_entry(home, "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "promoted"

    def test_crash_before_write_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_already_promoted_idempotent(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        service.transition(pid, "promoted", workspace_id="ws-1")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "promoted"

    def test_foreign_pending_payload_cannot_satisfy_claim(self, env):
        """A foreign pending payload with different content cannot satisfy the
        recorded claim hash."""
        home, storage, service = env
        pid = _make_approved(service, claim_text="Use JWT for authentication.")
        _write_pending(home, "deadbeef", "memory", "Completely different content.")
        # fallback scan: content mismatch -> zero matches -> unknown (not promoted)
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_missing_pending_id_one_valid_fallback(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "aaaa1111", "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        # exactly one match -> uses it -> file present -> still approved
        assert rec.status == "approved"

    def test_missing_pending_id_multiple_matching_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "aaaa1111", "memory", "Use JWT for authentication.")
        _write_pending(home, "bbbb2222", "memory", "Use JWT for authentication.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"

    def test_missing_pending_id_zero_matching_unknown(self, env):
        home, storage, service = env
        pid = _make_approved(service)
        _write_pending(home, "aaaa1111", "user", "Some other content.")
        rec = service.reconcile_promotion(
            pid, workspace_id="ws-1", user_confirmed=True,
            claim_text="Use JWT for authentication.",
        )
        assert rec.status == "approved"
        assert rec.failure_code == "memory_write_outcome_unknown"
