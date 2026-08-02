"""Unit tests for the ADRReconcileService (S7.3A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ADRCanonicalUpdateError,
    ADRReconcileError,
    ADRNotFoundError,
)
from plugins.workspace.backend.services.adr_reconcile_service import (  # type: ignore[import-untyped]
    ADRReconcileService,
    _identity_from_stem,
    build_canonical_markdown,
    parse_adr_file,
)
from plugins.workspace.backend.services.workspace_service import (  # type: ignore[import-untyped]
    WorkspaceService,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


def _svc(storage: SQLiteStorage, git_root: Path | None = None) -> ADRReconcileService:
    """Service with injectable project authority (U1D-E)."""
    if git_root is None:
        folders = lambda _pid: ["/tmp/workspace-project"]  # noqa: E731
    else:
        folders = lambda _pid: [str(git_root)]  # noqa: E731
    return ADRReconcileService(storage, project_folders=folders)


def _setup(storage: SQLiteStorage, git_root: Path):
    """Create a workspace + register the temp git repo, linked to a project.

    U1D-E: ADR filesystem authority derives from the mapped project.
    """
    ws = storage.create_workspace("reconcile-ws", str(git_root))
    repo = storage.register_repository(
        workspace_id=ws.id,
        name="recon-repo",
        path=str(git_root),
        git_root=str(git_root),
        default_branch="main",
    )
    storage.link_project(ws.id, "p_recon_test")
    return ws, repo


def _write_adr(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


VALID_1 = (
    "---\n"
    "status: accepted\n"
    "category: Architecture\n"
    "tags:\n"
    "  - database\n"
    "  - storage\n"
    "---\n"
    "# Use SQLite\n\n"
    "We chose SQLite for storage.\n"
)

VALID_2 = (
    "---\n"
    "status: proposed\n"
    "---\n"
    "# Use Postgres\n\n"
    "Evaluating Postgres.\n"
)

NO_FRONTMATTER = "# Simple Decision\n\nBody without frontmatter.\n"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_adr_with_frontmatter():
    parsed = parse_adr_file(VALID_1)
    assert parsed.title == "Use SQLite"
    assert parsed.status == "accepted"
    assert parsed.category == "Architecture"
    assert parsed.tags == ["database", "storage"]
    assert "We chose SQLite" in parsed.markdown


def test_parse_adr_without_frontmatter():
    parsed = parse_adr_file(NO_FRONTMATTER)
    assert parsed.title == "Simple Decision"
    assert parsed.status == "proposed"
    assert parsed.category == ""
    assert parsed.tags == []


def test_parse_missing_h1_invalid():
    from plugins.workspace.backend.services.adr_reconcile_service import _MalformedADR

    with pytest.raises(_MalformedADR):
        parse_adr_file("no heading here\n")


def test_parse_bad_frontmatter_invalid():
    from plugins.workspace.backend.services.adr_reconcile_service import _MalformedADR

    with pytest.raises(_MalformedADR):
        parse_adr_file("---\nstatus: [unclosed\n---\n# T\n")


def test_parse_invalid_status_invalid():
    from plugins.workspace.backend.services.adr_reconcile_service import _MalformedADR

    with pytest.raises(_MalformedADR):
        parse_adr_file("---\nstatus: bogus\n---\n# T\n")


def test_identity_from_stem():
    assert _identity_from_stem("0001-use-sqlite") == "use-sqlite"
    assert _identity_from_stem("use-sqlite") == "use-sqlite"
    assert _identity_from_stem("0001") == "0001"


def test_build_canonical_markdown_adds_frontmatter(storage):
    ws = storage.create_workspace("mw", "")
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Legacy",
        slug="legacy", status="accepted", category="Arch",
        markdown="# Legacy\n\nBody.", tags=["x"],
    )
    content = build_canonical_markdown(adr)
    assert content.startswith("---\n")
    assert "status: accepted" in content
    assert "category: Arch" in content
    assert "  - x" in content
    assert "source: workspace_db" in content
    assert "# Legacy" in content


def test_build_canonical_markdown_preserves_existing_frontmatter(storage):
    ws = storage.create_workspace("mw2", "")
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Has FM",
        slug="has-fm", status="proposed", category="",
        markdown="---\nstatus: proposed\n---\n# Has FM\n", tags=[],
    )
    content = build_canonical_markdown(adr)
    assert content == "---\nstatus: proposed\n---\n# Has FM\n"


# ---------------------------------------------------------------------------
# Discovery + reconciliation
# ---------------------------------------------------------------------------


def test_discover_and_index_new_file(storage, temp_git_repo):
    ws, repo = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    # Unrelated markdown OUTSIDE docs/adr/ must be ignored.
    _write_adr(temp_git_repo, "docs/other.md", "# Other\n")
    _write_adr(temp_git_repo, "README.md", "# Repo\n")

    svc = _svc(storage, temp_git_repo)
    summary = svc.reconcile(ws.id)

    assert summary.scanned_files == 1  # only docs/adr/*.md
    assert summary.indexed == 1
    adrs = storage.list_adrs(ws.id)
    assert len(adrs) == 1
    adr = adrs[0]
    assert adr.source == "git_file"
    assert adr.reconcile_state == "synced"
    assert adr.canonical_path == "docs/adr/0001-use-sqlite.md"
    assert adr.title == "Use SQLite"
    assert adr.slug == "use-sqlite"
    assert adr.status == "accepted"
    assert adr.category == "Architecture"
    assert set(adr.tags) == {"database", "storage"}
    assert "We chose SQLite" in adr.markdown


def test_reconcile_idempotent(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)

    svc = _svc(storage, temp_git_repo)
    s1 = svc.reconcile(ws.id)
    assert s1.indexed == 1
    adr = storage.list_adrs(ws.id)[0]
    indexed_at = adr.last_indexed

    s2 = svc.reconcile(ws.id)
    assert s2.indexed == 0
    assert s2.synced == 1
    assert s2.scanned_files == 1
    adr2 = storage.list_adrs(ws.id)[0]
    assert adr2.last_indexed == indexed_at  # no refresh when unchanged
    assert adr2.reconcile_state == "synced"


def test_changed_canonical_file_refreshes(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)

    # External edit: new title + content.
    _write_adr(
        temp_git_repo, "docs/adr/0001-use-sqlite.md",
        VALID_1.replace("# Use SQLite", "# Use SQLite Now").replace(
            "We chose SQLite for storage.", "Updated rationale."
        ),
    )
    s2 = svc.reconcile(ws.id)
    assert s2.file_changed == 1
    adr = storage.list_adrs(ws.id)[0]
    assert adr.reconcile_state == "synced"
    assert adr.title == "Use SQLite Now"
    assert "Updated rationale." in adr.markdown


def test_dry_run_previews_without_writing(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)

    s = svc.reconcile(ws.id, dry_run=True)
    assert s.indexed == 1
    assert s.dry_run is True
    assert storage.list_adrs(ws.id) == []  # nothing written


def test_legacy_adr_stays_db_legacy(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    # DB-only legacy ADR, no canonical file.
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Old",
        slug="old-decision", status="proposed", category="",
        markdown="# Old\n", tags=[],
    )
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.db_legacy == 1
    adr = storage.list_adrs(ws.id)[0]
    assert adr.reconcile_state == "db_legacy"
    assert adr.source == "workspace_db"
    assert adr.markdown == "# Old\n"  # untouched


def test_legacy_adr_matches_file_promoted(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Use SQLite",
        slug="use-sqlite", status="accepted", category="Architecture",
        markdown="# Use SQLite\n\nWe chose SQLite for storage.\n",
        tags=["database"],
    )
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.synced == 1
    adr = storage.list_adrs(ws.id)[0]
    assert adr.source == "git_file"
    assert adr.reconcile_state == "synced"
    assert adr.canonical_path == "docs/adr/0001-use-sqlite.md"
    # Content replaced by the canonical file (file wins; contents agreed).
    assert "We chose SQLite for storage." in adr.markdown


def test_legacy_adr_conflicts_with_file(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Use SQLite",
        slug="use-sqlite", status="accepted", category="",
        markdown="# Use SQLite\n\nCOMPLETELY DIFFERENT DB content.\n",
        tags=[],
    )
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.conflict == 1
    adr = storage.list_adrs(ws.id)[0]
    assert adr.reconcile_state == "conflict"
    assert adr.source == "workspace_db"
    assert "COMPLETELY DIFFERENT" in adr.markdown  # not overwritten


def test_missing_canonical_file(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)

    # Delete the canonical file externally.
    (temp_git_repo / "docs/adr/0001-use-sqlite.md").unlink()
    s = svc.reconcile(ws.id)
    assert s.missing_file == 1
    adr = storage.list_adrs(ws.id)[0]
    assert adr.reconcile_state == "missing_file"
    assert storage.list_adrs(ws.id)  # row kept — never auto-deleted


def test_malformed_file_invalid(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-broken.md", "no heading, just prose\n")
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.invalid == 1
    assert "docs/adr/0001-broken.md" in s.invalid_paths
    assert storage.list_adrs(ws.id) == []  # no projection row for invalid files


def test_duplicate_identity_invalid(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-same.md", VALID_1)
    _write_adr(
        temp_git_repo, "docs/adr/0002-same.md",
        "# Use SQLite\n\nSecond.\n",
    )
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.invalid == 1
    assert s.indexed == 1  # first one indexed
    assert any("0002-same" in p for p in s.invalid_paths)


def test_unrelated_markdown_ignored(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    _write_adr(temp_git_repo, "docs/README.md", "# Docs\n")
    _write_adr(temp_git_repo, "README.md", "# Repo\n")
    _write_adr(temp_git_repo, "docs/adr/sub/0002-nested.md", VALID_2)
    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    assert s.scanned_files == 2
    assert s.indexed == 2


def test_rename_relinks_projection(storage, temp_git_repo):
    """A canonical file renamed within the ADR dir keeps identity + re-links."""
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)
    adr = storage.list_adrs(ws.id)[0]
    assert adr.canonical_path == "docs/adr/0001-use-sqlite.md"

    # Rename: same identity (stem slug unchanged), new ordering prefix.
    (temp_git_repo / "docs/adr/0001-use-sqlite.md").rename(
        temp_git_repo / "docs/adr/0004-use-sqlite.md"
    )
    s = svc.reconcile(ws.id)
    assert s.synced == 1, s
    assert s.indexed == 0
    adr2 = storage.list_adrs(ws.id)[0]
    assert adr2.canonical_path == "docs/adr/0004-use-sqlite.md"
    assert adr2.reconcile_state == "synced"
    assert len(storage.list_adrs(ws.id)) == 1  # no duplicate projection


def test_status_live_states(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="Legacy",
        slug="legacy-only", status="proposed", category="", markdown="# L\n", tags=[],
    )
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)

    statuses = svc.status(ws.id)
    by_slug = {s.slug: s for s in statuses}
    assert by_slug["use-sqlite"].reconcile_state == "synced"
    assert by_slug["use-sqlite"].canonical_id == "use-sqlite"
    assert by_slug["use-sqlite"].file_exists is True
    assert by_slug["legacy-only"].reconcile_state == "db_legacy"


def test_no_repositories_legacy_only(storage):
    ws = storage.create_workspace("norepo-ws", "")
    storage.link_project(ws.id, "p_recon_test")
    storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="L",
        slug="l", status="proposed", category="", markdown="# L\n", tags=[],
    )
    svc = _svc(storage)
    s = svc.reconcile(ws.id)
    assert s.scanned_files == 0
    assert s.db_legacy == 1


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_materialize_preview(storage, temp_git_repo):
    ws, repo = _setup(storage, temp_git_repo)
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=repo.id, title="Legacy",
        slug="legacy-adr", status="accepted", category="Arch",
        markdown="# Legacy\n\nBody.", tags=["x"],
    )
    svc = _svc(storage, temp_git_repo)
    result = svc.materialize(adr.id, dry_run=True)
    assert result.status == "preview"
    assert result.target_path == "docs/adr/0001-legacy-adr.md"
    assert not (temp_git_repo / "docs/adr").exists()  # nothing written
    adr2 = storage.get_adr(adr.id)
    assert adr2.source == "workspace_db"  # unchanged


def test_materialize_success(storage, temp_git_repo):
    ws, repo = _setup(storage, temp_git_repo)
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=repo.id, title="Legacy",
        slug="legacy-adr", status="accepted", category="Arch",
        markdown="# Legacy\n\nBody.", tags=["x"],
    )
    svc = _svc(storage, temp_git_repo)
    result = svc.materialize(adr.id, dry_run=False)
    assert result.status == "materialized"
    assert result.target_path == "docs/adr/0001-legacy-adr.md"

    target = temp_git_repo / "docs/adr/0001-legacy-adr.md"
    assert target.is_file()
    content = target.read_text(encoding="utf-8")
    assert "status: accepted" in content
    assert "source: workspace_db" in content
    assert "# Legacy" in content

    adr2 = storage.get_adr(adr.id)
    assert adr2.source == "git_file"
    assert adr2.reconcile_state == "synced"
    assert adr2.canonical_path == "docs/adr/0001-legacy-adr.md"
    assert adr2.content_hash

    # Reconcile afterwards: file is canonical, synced, no new rows.
    s = svc.reconcile(ws.id)
    assert s.synced == 1


def test_materialize_target_exists(storage, temp_git_repo):
    ws, repo = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-legacy-adr.md", VALID_1)
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=repo.id, title="Legacy",
        slug="legacy-adr", status="accepted", category="",
        markdown="# Legacy\n\nBody.", tags=[],
    )
    svc = _svc(storage, temp_git_repo)
    result = svc.materialize(adr.id, dry_run=False)
    assert result.status == "target_exists"
    adr2 = storage.get_adr(adr.id)
    assert adr2.source == "workspace_db"  # untouched


def test_materialize_no_repository(storage):
    ws = storage.create_workspace("norepo2-ws", "")
    storage.link_project(ws.id, "p_recon_test")
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="L",
        slug="l", status="proposed", category="", markdown="# L\n", tags=[],
    )
    svc = _svc(storage)
    result = svc.materialize(adr.id, dry_run=False)
    assert result.status == "no_repository"


def test_materialize_already_canonical_raises(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)
    adr = storage.list_adrs(ws.id)[0]
    with pytest.raises(ADRReconcileError):
        svc.materialize(adr.id, dry_run=True)


def test_materialize_sequence_numbering(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0003-existing.md", VALID_2)
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="New",
        slug="new-adr", status="proposed", category="", markdown="# New\n", tags=[],
    )
    svc = _svc(storage, temp_git_repo)
    result = svc.materialize(adr.id, dry_run=True)
    assert result.target_path == "docs/adr/0004-new-adr.md"


# ---------------------------------------------------------------------------
# Canonical file updates
# ---------------------------------------------------------------------------


def test_update_file_success(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)
    adr = storage.list_adrs(ws.id)[0]

    new_content = VALID_1.replace("# Use SQLite", "# Use SQLite (Updated)")
    result = svc.update_file(adr.id, new_content, dry_run=False)
    assert result.status == "updated"
    assert "Updated" in (temp_git_repo / "docs/adr/0001-use-sqlite.md").read_text()

    adr2 = storage.get_adr(adr.id)
    assert adr2.reconcile_state == "synced"
    assert "Updated" in adr2.markdown
    assert adr2.content_hash

    # Reconcile: still synced, zero writes.
    s = svc.reconcile(ws.id)
    assert s.synced == 1
    assert s.file_changed == 0


def test_update_file_dry_run(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)
    adr = storage.list_adrs(ws.id)[0]

    result = svc.update_file(adr.id, VALID_1.replace("# Use SQLite", "# Changed"), dry_run=True)
    assert result.status == "preview"
    assert "# Use SQLite" in (temp_git_repo / "docs/adr/0001-use-sqlite.md").read_text()


def test_update_file_rejects_legacy(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    adr = storage.create_adr(
        workspace_id=ws.id, repository_id=None, title="L",
        slug="l", status="proposed", category="", markdown="# L\n", tags=[],
    )
    svc = _svc(storage, temp_git_repo)
    with pytest.raises(ADRCanonicalUpdateError):
        svc.update_file(adr.id, "# New\n")


# ---------------------------------------------------------------------------
# Sandbox / path safety
# ---------------------------------------------------------------------------


def test_path_traversal_rejected_on_discovery(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    # A symlink escape inside the ADR dir must be skipped, not followed.
    docs = temp_git_repo / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "adr").mkdir(exist_ok=True)
    outside = temp_git_repo.parent / "outside-file.md"
    outside.write_text(VALID_1)
    (docs / "adr" / "escape.md").symlink_to(outside)

    svc = _svc(storage, temp_git_repo)
    s = svc.reconcile(ws.id)
    # The escape is skipped — nothing outside the repo root is scanned.
    assert s.scanned_files == 0
    assert s.indexed == 0
    assert storage.list_adrs(ws.id) == []


def test_canonical_path_traversal_rejected_on_update(storage, temp_git_repo):
    ws, _ = _setup(storage, temp_git_repo)
    _write_adr(temp_git_repo, "docs/adr/0001-use-sqlite.md", VALID_1)
    svc = _svc(storage, temp_git_repo)
    svc.reconcile(ws.id)
    adr = storage.list_adrs(ws.id)[0]
    # Tamper with the projection path directly (simulating corruption).
    storage.update_adr_reconcile_meta(
        adr.id, canonical_path="../../escape.md"
    )
    with pytest.raises(ADRReconcileError):
        svc.update_file(adr.id, "# Evil\n")


def test_missing_workspace_raises(storage):
    svc = _svc(storage)
    with pytest.raises(ADRNotFoundError):
        svc.reconcile("nope")


def test_status_missing_workspace_raises(storage):
    svc = _svc(storage)
    with pytest.raises(ADRNotFoundError):
        svc.status("nope")
