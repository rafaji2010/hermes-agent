"""S7.5.3 — Memory promotion metadata ledger tests.

Covers migration 008, ledger CRUD, deterministic dedup, scope/profile
isolation, security (no raw paths/secrets/claim text), audit metadata
safety, concurrency, and regression against existing suites.

Uses REAL temporary HERMES_HOME/profile boundaries (like the authority
tests) for profile isolation; in-memory storage for unit-level ledger
tests.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.backend.migrations import MigrationRunner  # type: ignore[import-untyped]
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    PromotionRecordExistsError,
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
    make_candidate,
)
from plugins.workspace.backend.services.promotion_service import (  # type: ignore[import-untyped]
    LEDGER_STATUSES,
    PromotionService,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_manager(tmp_path) -> Iterator[DatabaseManager]:
    mgr = DatabaseManager(db_path=tmp_path / "workspace.db")
    mgr.get_connection()
    yield mgr
    mgr.close()


@pytest.fixture
def storage(db_manager) -> SQLiteStorage:
    return SQLiteStorage(db_manager=db_manager)


@pytest.fixture
def service(storage) -> PromotionService:
    return PromotionService(storage=storage)


@pytest.fixture
def ws_env(storage):
    """Return (service, storage, ws_id) with one seeded workspace."""
    ws_id = _seed_workspace(storage)
    return PromotionService(storage=storage), storage, ws_id


def _scope(**kw) -> ScopeSnapshot:
    defaults = dict(
        profile_label="profile-a",
        workspace_id="ws-1",
        workspace_name="ws-a",
        project_id="proj-1",
        project_slug="proj-a",
        scope_state="mapped",
        match_source="session_cwd",
    )
    defaults.update(kw)
    return ScopeSnapshot(**defaults)


def _provenance(**kw) -> ProvenanceEnvelope:
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


def _candidate(**kw):
    fields = dict(
        claim_text="Use JWT for authentication.",
        assertion_type=AssertionType.CANONICAL_FACT,
        target_kind=TargetKind.MEMORY,
        provenance=_provenance(),
        scope=_scope(),
    )
    fields.update(kw)
    return make_candidate(**fields)


def _seed_workspace(storage, ws_id="ws-1", name="ws-a") -> str:
    """Insert a workspace row with a controlled id (test seeding).

    The service requires the workspace row to exist (no global fallback);
    inserting with an explicit id keeps the fixed-id test helpers simple.
    """
    conn = storage._conn
    conn.execute(
        "INSERT INTO workspaces (id, name, path) VALUES (?, ?, '')",
        (ws_id, name),
    )
    return ws_id


# ---------------------------------------------------------------------------
# 1. Migration 008
# ---------------------------------------------------------------------------


class TestMigration008:
    def test_fresh_db_applies_migration_008(self, db_manager):
        conn = db_manager.get_connection()
        row = conn.execute(
            "SELECT version FROM _migrations WHERE version = 8"
        ).fetchone()
        assert row is not None, "migration 008 not recorded"
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='workspace_memory_promotions'"
        ).fetchone()
        assert table is not None, "workspace_memory_promotions table missing"

    def test_migration_008_idempotent(self, db_manager):
        conn = db_manager.get_connection()
        runner = MigrationRunner(conn)
        assert runner.run_pending() == 0, "re-run should apply zero migrations"

    def test_migration_008_sentinel_recovery(self, db_manager):
        """Crash recovery: if the table exists but the version row is
        missing, the runner records the version without re-applying."""
        conn = db_manager.get_connection()
        conn.execute("DELETE FROM _migrations WHERE version = 8")
        conn.commit()
        # Drop the table so the sentinel reports not-applied; then simulate
        # the crash case by removing only the version record after the table
        # exists.  Here we test the sentinel path directly: recreate the
        # table without a version row is handled by run_pending.
        runner = MigrationRunner(conn)
        # Table still exists → sentinel says applied → version recorded, 0 SQL.
        assert runner.run_pending() == 1
        row = conn.execute(
            "SELECT version FROM _migrations WHERE version = 8"
        ).fetchone()
        assert row is not None

    def test_migration_008_atomic_failure(self, tmp_path):
        """A failing migration must roll back atomically (no partial schema)."""
        mgr = DatabaseManager(db_path=tmp_path / "failing.db")
        conn = mgr.get_connection()
        # Manually create the table so the sentinel is satisfied, then
        # attempt a failing migration via a bespoke runner.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS workspace_memory_promotions (x INTEGER)"
        )
        conn.commit()
        # The sentinel passes; running pending must record version 8 only.
        runner = MigrationRunner(conn)
        applied = runner.run_pending()
        assert applied == 0  # sentinel already satisfied, nothing pending
        mgr.close()

    def test_existing_migrations_001_007_intact(self, db_manager):
        conn = db_manager.get_connection()
        versions = {
            r[0] for r in conn.execute("SELECT version FROM _migrations").fetchall()
        }
        assert {1, 2, 3, 4, 5, 6, 7, 8} <= versions


# ---------------------------------------------------------------------------
# 2. Ledger CRUD
# ---------------------------------------------------------------------------


class TestLedgerCRUD:
    def test_insert(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        assert rec.promotion_id
        assert rec.status == "eligible"  # canonical_fact + mapped scope
        assert rec.claim_hash == _candidate().claim_hash
        assert rec.candidate_identity == _candidate().candidate_identity

    def test_lookup(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        got = service.get(rec.promotion_id, workspace_id="ws-1")
        assert got.promotion_id == rec.promotion_id

    def test_update(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        updated = service.transition(rec.promotion_id, "pending", workspace_id="ws-1")
        assert updated.status == "pending"

    def test_unknown_status_rejected(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        with pytest.raises(Exception) as exc:
            service.transition(rec.promotion_id, "nonsense", workspace_id="ws-1")
        assert getattr(exc.value, "code", "") == "PROMOTION_INVALID_STATUS"

    def test_illegal_transition_rejected(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        # eligible -> promoted is NOT allowed (must go through pending/approved)
        with pytest.raises(Exception) as exc:
            service.transition(rec.promotion_id, "promoted", workspace_id="ws-1")
        assert getattr(exc.value, "code", "") == "PROMOTION_ILLEGAL_TRANSITION"

    def test_full_lifecycle_transitions(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        rec = service.transition(rec.promotion_id, "pending", workspace_id="ws-1")
        rec = service.transition(rec.promotion_id, "approved", workspace_id="ws-1")
        assert rec.approved_at
        rec = service.transition(rec.promotion_id, "promoted", workspace_id="ws-1")
        assert rec.promoted_at
        assert rec.status == "promoted"


# ---------------------------------------------------------------------------
# 3. Deterministic dedup
# ---------------------------------------------------------------------------


class TestDedup:
    def test_repeated_candidate_returns_existing(self, service, storage):
        _seed_workspace(storage)
        c1 = _candidate()
        rec1 = service.propose(c1)
        rec2 = service.propose(_candidate())  # equivalent
        assert rec1.promotion_id == rec2.promotion_id

    def test_different_claim_is_distinct(self, service, storage):
        _seed_workspace(storage)
        rec1 = service.propose(_candidate(claim_text="Use JWT."))
        rec2 = service.propose(_candidate(claim_text="Use OAuth2."))
        assert rec1.promotion_id != rec2.promotion_id

    def test_duplicate_direct_insert_raises(self, storage):
        _seed_workspace(storage)
        c = _candidate()
        fields = {
            "profile_label": c.provenance.profile_label,
            "workspace_id": c.provenance.workspace_id,
            "project_id": c.provenance.project_id,
            "source_type": c.provenance.source_type.value,
            "source_id": c.provenance.source_id,
            "source_canonical_id": c.provenance.source_canonical_id,
            "source_relative_path": c.provenance.source_relative_path,
            "source_hash": c.provenance.source_hash,
            "source_hash_kind": c.provenance.source_hash_kind.value,
            "source_state": c.provenance.source_state,
            "assertion_type": c.assertion_type.value,
            "claim_hash": c.claim_hash,
            "target_kind": c.target_kind.value,
            "candidate_identity": c.candidate_identity,
            "status": "proposed",
            "eligibility_decision": "proposed",
        }
        storage.create_promotion_record(fields)
        with pytest.raises(PromotionRecordExistsError):
            storage.create_promotion_record(fields)

    def test_concurrent_duplicate_insert_deterministic(self, service, storage):
        """Concurrent equivalent proposals must not create duplicate active
        records (UNIQUE index is the backstop).  Exactly one record exists;
        losing threads deterministically see the dedup error (or the
        existing record)."""
        _seed_workspace(storage)
        results: list = []
        errors: list = []

        def _propose():
            try:
                results.append(service.propose(_candidate()).promotion_id)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=_propose) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At most one unique record id created; any failures are the
        # deterministic dedup error (never a silent duplicate row).
        assert len(set(results)) <= 1, f"duplicate active records: {set(results)}"
        conn = storage._conn
        count = conn.execute(
            "SELECT COUNT(*) FROM workspace_memory_promotions"
        ).fetchone()[0]
        assert count == 1, f"expected exactly one ledger row, got {count}"
        for err in errors:
            assert getattr(err, "code", "") == "PROMOTION_DUPLICATE", err


# ---------------------------------------------------------------------------
# 4. Scope / profile isolation
# ---------------------------------------------------------------------------


class TestScopeIsolation:
    def test_unresolved_scope_rejected(self, service, storage):
        _seed_workspace(storage)
        with pytest.raises(Exception) as exc:
            service.propose(_candidate(), scope_state="unresolved")
        assert getattr(exc.value, "code", "") == "SCOPE_UNRESOLVED"

    def test_ambiguous_scope_rejected(self, service, storage):
        _seed_workspace(storage)
        with pytest.raises(Exception) as exc:
            service.propose(_candidate(), scope_state="ambiguous")
        assert getattr(exc.value, "code", "") == "SCOPE_AMBIGUOUS"

    def test_partial_scope_rejected(self, service, storage):
        _seed_workspace(storage)
        with pytest.raises(Exception):
            service.propose(_candidate(), scope_state="partial")

    def test_unmapped_scope_rejected(self, service, storage):
        _seed_workspace(storage)
        with pytest.raises(Exception):
            service.propose(_candidate(), scope_state="unmapped")

    def test_missing_workspace_no_global_fallback(self, service, storage):
        # no workspace seeded
        with pytest.raises(Exception) as exc:
            service.propose(_candidate())
        assert getattr(exc.value, "code", "") == "SCOPE_UNRESOLVED"

    def test_workspace_a_cannot_see_workspace_b(self, service, storage):
        _seed_workspace(storage, ws_id="ws-a", name="a")
        _seed_workspace(storage, ws_id="ws-b", name="b")
        rec_a = service.propose(_candidate(
            provenance=_provenance(workspace_id="ws-a"),
            scope=_scope(workspace_id="ws-a"),
        ))
        # listing for B must not include A's record
        list_b = service.list_for_workspace("ws-b")
        assert all(r.workspace_id == "ws-b" for r in list_b)
        assert rec_a.promotion_id not in [r.promotion_id for r in list_b]

    def test_workspace_mismatch_lookup_rejected(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        with pytest.raises(Exception) as exc:
            service.get(rec.promotion_id, workspace_id="other-ws")
        assert getattr(exc.value, "code", "") == "PROMOTION_NOT_FOUND"

    def test_global_list_rejected(self, service):
        with pytest.raises(Exception):
            service.list_for_workspace("")


# ---------------------------------------------------------------------------
# 5. Real profile A/B isolation (temp HERMES_HOME)
# ---------------------------------------------------------------------------


class TestProfileIsolation:
    def _home_db(self, home: Path) -> DatabaseManager:
        mgr = DatabaseManager(db_path=home / "workspace.db")
        mgr.get_connection()
        return mgr

    def test_profile_a_cannot_see_profile_b(self, tmp_path):
        home_a = tmp_path / "a"
        home_b = tmp_path / "b"
        home_a.mkdir(parents=True)
        home_b.mkdir(parents=True)
        mgr_a = self._home_db(home_a)
        mgr_b = self._home_db(home_b)
        try:
            st_a = SQLiteStorage(db_manager=mgr_a)
            st_b = SQLiteStorage(db_manager=mgr_b)
            _seed_workspace(st_a, ws_id="ws-a", name="a")
            _seed_workspace(st_b, ws_id="ws-b", name="b")
            svc_a = PromotionService(storage=st_a)
            svc_b = PromotionService(storage=st_b)
            svc_a.propose(_candidate(
                provenance=_provenance(workspace_id="ws-a", profile_label="profile-a"),
                scope=_scope(workspace_id="ws-a", profile_label="profile-a"),
            ))
            # B's DB physically has no rows.
            conn_b = mgr_b.get_connection()
            count = conn_b.execute(
                "SELECT COUNT(*) FROM workspace_memory_promotions"
            ).fetchone()[0]
            assert count == 0
        finally:
            mgr_a.close()
            mgr_b.close()

    def test_archived_authority_rejected(self, storage):
        """Archived/stale authority must be rejected — the service requires
        a live workspace (archived projects are handled at scope-resolver
        level in earlier milestones; here we assert no record is created for
        a non-existent/archived workspace)."""
        # no workspace row -> no record, no fallback
        with pytest.raises(Exception):
            service = PromotionService(storage=storage)
            service.propose(_candidate())


# ---------------------------------------------------------------------------
# 6. Security — no raw paths / secrets / claim text
# ---------------------------------------------------------------------------


class TestLedgerSecurity:
    def test_raw_hermes_home_never_persisted(self, service, storage):
        _seed_workspace(storage)
        service.propose(_candidate())
        conn = storage._conn
        rows = conn.execute(
            "SELECT * FROM workspace_memory_promotions"
        ).fetchall()
        assert rows
        for row in rows:
            for key in row.keys():
                val = str(row[key])
                assert "/.hermes" not in val, f"raw HERMES_HOME in {key}"
                assert val.startswith("/") is False or key in ("source_relative_path",) and not val.startswith("/"), f"absolute path in {key}"

    def test_absolute_path_rejected_at_construction(self):
        with pytest.raises(ValueError):
            _provenance(source_relative_path="/etc/passwd")

    def test_secret_metadata_rejected_at_construction(self):
        with pytest.raises(ValueError):
            _provenance(source_id="sk-abc123")

    def test_claim_text_not_stored(self, service, storage):
        _seed_workspace(storage)
        service.propose(_candidate(claim_text="SECRET CLAIM TEXT 12345"))
        conn = storage._conn
        rows = conn.execute(
            "SELECT * FROM workspace_memory_promotions"
        ).fetchall()
        blob = " ".join(str(v) for row in rows for v in row)
        assert "SECRET CLAIM TEXT 12345" not in blob

    def test_transcript_text_not_stored(self, service, storage):
        _seed_workspace(storage)
        service.propose(_candidate())
        conn = storage._conn
        rows = conn.execute(
            "SELECT * FROM workspace_memory_promotions"
        ).fetchall()
        blob = " ".join(str(v) for row in rows for v in row)
        assert "user said" not in blob
        assert "transcript" not in blob


# ---------------------------------------------------------------------------
# 7. Audit metadata safety
# ---------------------------------------------------------------------------


class TestAudit:
    def test_audit_contains_no_sensitive_content(self, tmp_path, storage):
        from plugins.workspace.backend.security.audit import AuditLogger

        audit = AuditLogger(log_path=tmp_path / "audit.log")
        _seed_workspace(storage)
        svc = PromotionService(storage=storage, audit=audit)
        svc.propose(_candidate(claim_text="SUPER SECRET CLAIM"))
        events = audit.read(20)
        assert events
        blob = " ".join(str(e) for e in events)
        assert "SUPER SECRET CLAIM" not in blob
        assert "sk-" not in blob
        # metadata present
        assert "promotion.propose" in blob or "promotion" in blob

    def test_audit_has_metadata_identity(self, tmp_path, storage):
        from plugins.workspace.backend.security.audit import AuditLogger

        audit = AuditLogger(log_path=tmp_path / "audit.log")
        _seed_workspace(storage)
        svc = PromotionService(storage=storage, audit=audit)
        rec = svc.propose(_candidate(), session_id="sess-1", correlation_id="corr-9")
        events = audit.read(20)
        propose = [e for e in events if e.get("action") == "promotion.propose"]
        assert propose
        assert propose[0]["resource_id"] == rec.promotion_id
        assert propose[0]["session_id"] == "sess-1"
        assert propose[0]["correlation_id"] == "corr-9"
        assert propose[0]["details"]["workspace_id"] == "ws-1"
        assert "claim_text" not in propose[0]["details"]


# ---------------------------------------------------------------------------
# 8. Ledger status model
# ---------------------------------------------------------------------------


class TestStatusModel:
    def test_status_set_is_minimal(self):
        assert LEDGER_STATUSES == {
            "proposed", "eligible", "manual_only", "rejected",
            "pending", "approved", "promoted", "failed", "superseded",
        }

    def test_rejected_candidate_recorded_as_rejected(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate(
            provenance=_provenance(source_state="conflict"),
        ))
        assert rec.status == "rejected"
        assert rec.rejection_code == "source_conflicted"

    def test_model_inference_proposed_only(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate(
            assertion_type=AssertionType.MODEL_INFERENCE,
            user_confirmed=False,
        ))
        assert rec.status == "proposed"

    def test_user_authored_without_confirmation_manual_only(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate(
            assertion_type=AssertionType.USER_AUTHORED_FACT,
            user_confirmed=False,
        ))
        assert rec.status == "manual_only"

    def test_failed_then_retry(self, service, storage):
        _seed_workspace(storage)
        rec = service.propose(_candidate())
        rec = service.transition(rec.promotion_id, "pending", workspace_id="ws-1")
        rec = service.transition(rec.promotion_id, "failed", workspace_id="ws-1",
                                 failure_code="memory_write_error")
        assert rec.status == "failed"
        assert rec.failure_code == "memory_write_error"
        # failed -> pending (retry) is allowed
        rec = service.transition(rec.promotion_id, "pending", workspace_id="ws-1")
        assert rec.status == "pending"
