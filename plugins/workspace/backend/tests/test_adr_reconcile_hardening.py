"""ADR filesystem reconciliation hardening tests (U1D-E).

Covers the filesystem authority chain, path containment (traversal,
absolute, prefix-confusion, symlink escapes), byte-level hashing,
no-clobber/compare-and-swap semantics, first-class conflicts, atomic
writes, crash/recovery semantics, concurrent reconciliation, and the
API boundary.  Uses real temp repositories and a real workspace DB.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.database import DatabaseManager  # type: ignore[import-untyped]
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ADRReconcileError,
)
from plugins.workspace.backend.services.adr_reconcile_service import (  # type: ignore[import-untyped]
    ADRReconcileService,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]

VALID_1 = (
    "---\n"
    "status: accepted\n"
    "category: Architecture\n"
    "---\n"
    "# Use SQLite\n\n"
    "We chose SQLite for storage.\n"
)


_UNSET = object()


class _Env:
    def __init__(self, tmp_path: Path, repo_root: Path | None = None,
                 project_folders=None, outside_root: Path | None = None):
        self.home = tmp_path / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.db = DatabaseManager(self.home / "ws.db")
        self.storage = SQLiteStorage(db_manager=self.db)
        self.ws = self.storage.create_workspace("recon-ws", "")
        self.repo_root = repo_root or (tmp_path / "repo")
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self.outside_root = outside_root or (tmp_path / "outside")
        self.repo = self.storage.register_repository(
            workspace_id=self.ws.id,
            name="repo",
            path=str(self.repo_root),
            git_root=str(self.repo_root),
            default_branch="main",
        )
        self.storage.link_project(self.ws.id, "p_recon_test")
        if project_folders is None:
            project_folders = lambda _pid: [str(self.repo_root)]  # noqa: E731
        self.svc = ADRReconcileService(
            self.storage, project_folders=project_folders
        )

    def close(self) -> None:
        self.db.close()

    def write_adr(self, rel: str, content: str) -> Path:
        p = self.repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def adr(self, title: str = "Use SQLite", slug: str = "use-sqlite",
            repository_id=_UNSET, canonical_path: str | None = None,
            source: str = "workspace_db", content_hash: str = ""):
        rid = self.repo.id if repository_id is _UNSET else repository_id
        adr = self.storage.create_adr(
            workspace_id=self.ws.id,
            repository_id=rid,
            title=title,
            slug=slug,
            status="proposed",
            category="",
            markdown=f"# {title}\n",
            tags=[],
        )
        if source != "workspace_db" or canonical_path is not None or content_hash:
            self.storage.update_adr_reconcile_meta(
                adr.id,
                canonical_path=canonical_path,
                content_hash=content_hash or None,
                source=source,
            )
        return self.storage.get_adr(adr.id)


@pytest.fixture
def env(tmp_path) -> _Env:
    e = _Env(tmp_path)
    yield e
    e.close()


# ---------------------------------------------------------------------------
# A. Authority
# ---------------------------------------------------------------------------


def test_reconcile_without_project_link_fails_before_filesystem(env, tmp_path):
    env.storage.unlink_project(env.ws.id)
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    with pytest.raises(ADRReconcileError) as exc:
        env.svc.reconcile(env.ws.id)
    assert exc.value.code == "ADR_NO_PROJECT_AUTHORITY"


def test_reconcile_archived_project_fails_closed(env, tmp_path):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    svc = ADRReconcileService(
        env.storage, project_folders=lambda _pid: None  # archived/missing
    )
    with pytest.raises(ADRReconcileError) as exc:
        svc.reconcile(env.ws.id)
    assert exc.value.code == "ADR_NO_PROJECT_AUTHORITY"


def test_repo_outside_project_folders_skipped(env, tmp_path):
    # The project folder is NOT the repo root: no filesystem authority.
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    svc = ADRReconcileService(
        env.storage, project_folders=lambda _pid: [str(tmp_path / "elsewhere")]
    )
    summary = svc.reconcile(env.ws.id)
    assert summary.scanned_files == 0
    assert env.storage.list_adrs(env.ws.id) == []


def test_update_file_repo_outside_project_fails(env, tmp_path):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    svc = ADRReconcileService(
        env.storage, project_folders=lambda _pid: [str(tmp_path / "elsewhere")]
    )
    with pytest.raises(ADRReconcileError) as exc:
        svc.update_file(adr.id, "# New\n", dry_run=False)
    assert exc.value.code == "ADR_NO_REPOSITORY"


def test_multiple_authorized_repos_without_choice_fails_closed(env, tmp_path):
    second_root = tmp_path / "repo2"
    second_root.mkdir()
    env.storage.register_repository(
        workspace_id=env.ws.id, name="repo2", path=str(second_root),
        git_root=str(second_root), default_branch="main",
    )
    # BOTH repos are inside the project folders: an ADR without an explicit
    # repository has two candidates — fail closed.
    svc = ADRReconcileService(
        env.storage,
        project_folders=lambda _pid: [str(env.repo_root), str(second_root)],
    )
    adr = env.adr(title="Legacy", slug="legacy-adr", repository_id=None)
    with pytest.raises(ADRReconcileError) as exc:
        svc.materialize(adr.id, dry_run=False)
    assert exc.value.code == "ADR_AMBIGUOUS_REPOSITORY"


# ---------------------------------------------------------------------------
# B. Path containment
# ---------------------------------------------------------------------------


def test_valid_contained_path_works(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    summary = env.svc.reconcile(env.ws.id)
    assert summary.indexed == 1


def test_traversal_canonical_path_rejected(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    env.storage.update_adr_reconcile_meta(
        adr.id, canonical_path="docs/adr/../../escape.md", content_hash="",
    )
    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n")
    assert exc.value.code == "ADR_UNSAFE_PATH"
    # Status reports the row as invalid rather than reading outside.
    statuses = env.svc.status(env.ws.id)
    assert statuses[0].reconcile_state == "invalid"


def test_absolute_canonical_path_rejected(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    env.storage.update_adr_reconcile_meta(
        adr.id, canonical_path=f"{env.outside_root}/escape.md", content_hash="",
    )
    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n")
    assert exc.value.code == "ADR_UNSAFE_PATH"


def test_prefix_confusion_rejected(env, tmp_path):
    # A sibling directory that shares the repo root's prefix.
    evil = tmp_path / "repo-evil"
    evil.mkdir()
    (evil / "x.md").write_text("# Evil\n")
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    # A stored path pointing at the sibling via '..' is rejected outright.
    env.storage.update_adr_reconcile_meta(
        adr.id, canonical_path="docs/adr/../../../repo-evil/x.md", content_hash="",
    )
    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n")
    assert exc.value.code == "ADR_UNSAFE_PATH"
    # The evil file was never touched.
    assert (evil / "x.md").read_text() == "# Evil\n"


def test_symlink_directory_escape_skipped(env, tmp_path):
    outside = tmp_path / "outside-adrs"
    outside.mkdir()
    (outside / "0009-secret.md").write_text("# Secret\n")
    (env.repo_root / "docs").mkdir(parents=True, exist_ok=True)
    os.symlink(str(outside), env.repo_root / "docs" / "adr", target_is_directory=True)

    summary = env.svc.reconcile(env.ws.id)
    # The symlinked dir resolves outside the root — never followed.
    assert summary.scanned_files == 0
    assert env.storage.list_adrs(env.ws.id) == []


def test_destination_symlink_not_read(env, tmp_path):
    outside = tmp_path / "outside-file.md"
    outside.write_text("# Outside\n")
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    # Swap the canonical file for a symlink pointing outside the root.
    (env.repo_root / "docs/adr/0001-use-sqlite.md").unlink()
    os.symlink(str(outside), env.repo_root / "docs/adr/0001-use-sqlite.md")

    statuses = env.svc.status(env.ws.id)
    assert statuses[0].file_exists is False  # symlink destination: not trusted
    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n", dry_run=False)
    assert exc.value.code == "ADR_SANDBOX_DENIED"
    assert (outside).read_text() == "# Outside\n"  # untouched


# ---------------------------------------------------------------------------
# C. Hashing
# ---------------------------------------------------------------------------


def test_newline_change_detected(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    old_hash = adr.content_hash

    # Newline-only change (CRLF) is a byte-level change.
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1.replace("\n", "\r\n"))
    summary = env.svc.reconcile(env.ws.id)
    assert summary.file_changed == 1
    adr = env.storage.list_adrs(env.ws.id)[0]
    assert adr.content_hash != old_hash


def test_identical_bytes_noop(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    # Identical external rewrite (same bytes) is NOT a change.
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    summary = env.svc.reconcile(env.ws.id)
    assert summary.file_changed == 0
    assert summary.conflict == 0


# ---------------------------------------------------------------------------
# D. No-clobber / CAS
# ---------------------------------------------------------------------------


def test_safe_create_and_update(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    result = env.svc.update_file(adr.id, VALID_1.replace("SQLite", "SQLite (v2)"))
    assert result.status == "updated"
    assert "v2" in (env.repo_root / "docs/adr/0001-use-sqlite.md").read_text()


def test_external_modification_conflicts(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]

    # External actor modifies the canonical file.
    external = VALID_1.replace("We chose SQLite", "We chose SQLite (externally)")
    env.write_adr("docs/adr/0001-use-sqlite.md", external)

    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n", dry_run=False)
    assert exc.value.code == "ADR_CONFLICT"
    # External content preserved byte-for-byte.
    assert (env.repo_root / "docs/adr/0001-use-sqlite.md").read_text() == external


def test_unexpected_preexisting_file_not_overwritten(env):
    env.write_adr("docs/adr/0001-legacy-adr.md", VALID_1)
    adr = env.adr(title="Legacy", slug="legacy-adr")
    # Target already exists with different content.
    result = env.svc.materialize(adr.id, dry_run=False)
    assert result.status == "target_exists"
    assert (env.repo_root / "docs/adr/0001-legacy-adr.md").read_text() == VALID_1


def test_both_db_and_file_modified_conflict(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1.replace("SQLite", "Postgres"))
    env.storage.update_adr(adr.id, markdown="# Changed in DB\n")
    env.storage._conn.execute(
        "UPDATE adrs SET updated_at = '2099-01-01' WHERE id = ?", (adr.id,)
    )
    env.storage._conn.commit()

    summary = env.svc.reconcile(env.ws.id)
    assert summary.conflict == 1
    adr = env.storage.list_adrs(env.ws.id)[0]
    assert adr.reconcile_state == "conflict"
    assert adr.last_error == "file_and_db_changed"


# ---------------------------------------------------------------------------
# E. Recovery / failure injection
# ---------------------------------------------------------------------------


def test_write_failure_does_not_mark_success(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]

    # Make the ADR directory read-only so the temp write fails.
    adr_dir = env.repo_root / "docs/adr"
    os.chmod(adr_dir, stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            env.svc.update_file(adr.id, "# New\n", dry_run=False)
    finally:
        os.chmod(adr_dir, stat.S_IRWXU)

    # DB projection unchanged; the canonical file untouched.
    adr = env.storage.list_adrs(env.ws.id)[0]
    assert adr.reconcile_state == "synced"
    assert (env.repo_root / "docs/adr/0001-use-sqlite.md").read_text() == VALID_1


def test_stale_projection_converges_on_reconcile(env):
    """Simulate a crash after file write but before DB update: the next
    reconcile converges deterministically (file wins)."""
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1.replace("SQLite", "SQLite (v2)"))
    # DB still believes the old content (crash between file and DB write).

    summary = env.svc.reconcile(env.ws.id)
    assert summary.file_changed == 1
    adr = env.storage.list_adrs(env.ws.id)[0]
    assert adr.reconcile_state == "synced"
    assert adr.content_hash != ""  # converged to the file's bytes


def test_retry_after_conflict_is_deterministic(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1.replace("SQLite", "SQLite (v2)"))

    with pytest.raises(ADRReconcileError) as exc:
        env.svc.update_file(adr.id, "# New\n")
    assert exc.value.code == "ADR_CONFLICT"

    # Reconcile adopts the external change; the retry then succeeds.
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]
    result = env.svc.update_file(adr.id, "# New\n")
    assert result.status == "updated"


# ---------------------------------------------------------------------------
# F. Concurrent reconciliation
# ---------------------------------------------------------------------------


def test_concurrent_update_file_no_clobber(env):
    env.write_adr("docs/adr/0001-use-sqlite.md", VALID_1)
    env.svc.reconcile(env.ws.id)
    adr = env.storage.list_adrs(env.ws.id)[0]

    results: list = []
    lock = threading.Lock()

    def worker(content: str) -> None:
        try:
            env.svc.update_file(adr.id, content, dry_run=False)
            outcome = "ok"
        except ADRReconcileError as exc:
            outcome = exc.code
        except Exception as exc:  # noqa: BLE001
            outcome = f"unexpected:{type(exc).__name__}"
        with lock:
            results.append(outcome)

    t1 = threading.Thread(target=worker, args=(VALID_1.replace("SQLite", "SQLite (A)"),))
    t2 = threading.Thread(target=worker, args=(VALID_1.replace("SQLite", "SQLite (B)"),))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Exactly one succeeds; the other conflicts — never a silent clobber.
    assert sorted(results) in (["ADR_CONFLICT", "ok"], ["ok", "ADR_CONFLICT"])
    on_disk = (env.repo_root / "docs/adr/0001-use-sqlite.md").read_text()
    assert "(A)" in on_disk or "(B)" in on_disk
    assert on_disk.startswith("---\n") and "# Use SQLite" in on_disk  # not corrupted


# ---------------------------------------------------------------------------
# G. API boundary
# ---------------------------------------------------------------------------


@pytest.fixture
def api_env(tmp_path):
    """Mount the router with a real home-A runtime + scoped client."""
    from contextlib import contextmanager

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )
    from hermes_cli import projects_db
    from plugins.workspace.backend.runtime import (
        get_workspace_runtime,
        pin_workspace_runtime,
        reset_workspace_runtimes,
    )
    from plugins.workspace.dashboard.plugin_api import router

    home = tmp_path / "api-home"
    home.mkdir()
    repo = tmp_path / "api-repo"
    repo.mkdir()
    import subprocess

    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True, check=True, timeout=30,
    )

    reset_workspace_runtimes()
    token = set_hermes_home_override(str(home))
    try:
        rt = get_workspace_runtime()
    finally:
        reset_hermes_home_override(token)
    pin_workspace_runtime(rt)

    with projects_db.connect_closing(home / "projects.db") as conn:
        pid = projects_db.create_project(conn, name="proj", folders=[str(repo)])

    app = FastAPI()
    app.include_router(router)
    inner = TestClient(app)

    class _ScopedClient:
        def _call(self, method: str, *args, **kwargs):
            token = set_hermes_home_override(str(home))
            try:
                return getattr(inner, method)(*args, **kwargs)
            finally:
                reset_hermes_home_override(token)

        def get(self, *a, **kw):
            return self._call("get", *a, **kw)

        def post(self, *a, **kw):
            return self._call("post", *a, **kw)

        def put(self, *a, **kw):
            return self._call("put", *a, **kw)

    c = _ScopedClient()
    r = c.post("/v1/workspaces", json={"name": "api-ws"})
    ws_id = r.json()["workspaces"][0]["id"]
    c.post("/v1/repositories", json={
        "workspace_id": ws_id, "name": "repo", "path": str(repo),
    })
    assert c.put(f"/v1/workspaces/{ws_id}/project", json={"project_id": pid}).status_code == 200

    yield c, ws_id, repo, rt

    reset_workspace_runtimes()


def test_api_cross_workspace_reconcile_rejected(api_env, tmp_path):
    c, ws_id, repo, rt = api_env
    (repo / "docs/adr").mkdir(parents=True)
    (repo / "docs/adr/0001-use-sqlite.md").write_text(VALID_1)
    assert c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False}).status_code == 200

    # Another workspace cannot reconcile this one's ADRs (membership 404).
    r = c.post("/v1/workspaces", json={"name": "other-ws"})
    other_ws = r.json()["workspaces"][0]["id"]
    resp = c.post("/v1/adrs/reconcile", json={"workspace_id": other_ws, "dry_run": False})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "ADR_NO_PROJECT_AUTHORITY"
    adr = rt.storage.list_adrs(ws_id)[0]
    resp = c.post(f"/v1/adrs/{adr.id}/materialize", json={"dry_run": False},
                  params={"workspace_id": other_ws})
    assert resp.status_code == 404


def test_api_external_modification_conflict_409(api_env):
    c, ws_id, repo, rt = api_env
    (repo / "docs/adr").mkdir(parents=True)
    (repo / "docs/adr/0001-use-sqlite.md").write_text(VALID_1)
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    adr = rt.storage.list_adrs(ws_id)[0]

    (repo / "docs/adr/0001-use-sqlite.md").write_text(
        VALID_1.replace("SQLite", "SQLite (external)")
    )
    resp = c.put(
        f"/v1/adrs/{adr.id}/file?workspace_id={ws_id}",
        json={"markdown": "# New\n"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "ADR_CONFLICT"
    assert "external" in (repo / "docs/adr/0001-use-sqlite.md").read_text()


def test_api_unsafe_stored_path_cannot_escape(api_env):
    c, ws_id, repo, rt = api_env
    (repo / "docs/adr").mkdir(parents=True)
    (repo / "docs/adr/0001-use-sqlite.md").write_text(VALID_1)
    c.post("/v1/adrs/reconcile", json={"workspace_id": ws_id, "dry_run": False})
    adr = rt.storage.list_adrs(ws_id)[0]
    # Seed a hostile stored path (would be a corrupt row).
    rt.storage.update_adr_reconcile_meta(
        adr.id, canonical_path="docs/adr/../../../etc/hostname.md", content_hash="",
    )

    statuses = c.get(f"/v1/adrs/reconcile/status?workspace_id={ws_id}")
    assert statuses.status_code == 200
    assert statuses.json()["statuses"][0]["reconcile_state"] == "invalid"

    resp = c.put(
        f"/v1/adrs/{adr.id}/file?workspace_id={ws_id}",
        json={"markdown": "# New\n"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "ADR_UNSAFE_PATH"
