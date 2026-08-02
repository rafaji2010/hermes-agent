"""Unit tests for the ProjectScopeResolver."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    ScopeResolveRequest,
    WorkspaceNotFoundError,
)
from plugins.workspace.backend.services.scope_resolver import (  # type: ignore[import-untyped]
    ProjectScopeResolver,
)
from plugins.workspace.backend.storage.sqlite_storage import SQLiteStorage  # type: ignore[import-untyped]


class FakeProjectStore:
    """Fake Hermes projects.db: folder-prefix matching like project_for_path."""

    def __init__(self, folders):
        # folders: {folder_path: (project_id, slug)}
        self.folders = folders

    def lookup(self, path: str) -> Optional[tuple]:
        if not path:
            return None
        best = None
        best_len = -1
        for folder, proj in self.folders.items():
            if path == folder or path.startswith(folder.rstrip("/") + "/"):
                if len(folder) > best_len:
                    best_len = len(folder)
                    best = proj
        return best

    def slug(self, project_id: str) -> Optional[str]:
        for _folder, (pid, slug) in self.folders.items():
            if pid == project_id:
                return slug
        return None


SESSIONS: Dict[str, dict] = {}


def fake_session_meta(session_id: str) -> Optional[dict]:
    return SESSIONS.get(session_id)


def make_resolver(storage: SQLiteStorage, project_store: FakeProjectStore):
    return ProjectScopeResolver(
        storage=storage,
        project_lookup=project_store.lookup,
        session_meta=fake_session_meta,
        project_slug=project_store.slug,
    )


@pytest.fixture
def ws(storage: SQLiteStorage):
    return storage.create_workspace("resolver-ws", "/tmp/resolver")


def test_mapped_workspace_wins(storage, ws):
    """Explicit mapping is precedence 1 — even when path evidence exists."""
    storage.link_project(ws.id, "p_abc")
    r = make_resolver(storage, FakeProjectStore({"/tmp/resolver": ("p_abc", "proj-a")}))
    result = r.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.state == "mapped"
    assert result.project_id == "p_abc"
    assert result.project_slug == "proj-a"
    assert result.match_source == "mapping"


def test_mapped_workspace_without_path_evidence(storage, ws):
    """A mapping alone is sufficient — no session needed (project must exist)."""
    storage.link_project(ws.id, "p_abc")
    resolver = make_resolver(storage, FakeProjectStore({"/proj": ("p_abc", "proj-a")}))
    result = resolver.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.state == "mapped"
    assert result.project_id == "p_abc"


def test_mapping_to_missing_project_is_stale(storage, ws):
    """U1D-D: a mapping to a project that no longer exists is stale →
    unmapped (never revived as authority)."""
    storage.link_project(ws.id, "p_ghost")
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.state == "unmapped"
    assert result.project_id is None


def test_unmapped_workspace_no_evidence(storage, ws):
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.state == "unmapped"
    assert result.workspace_id == ws.id


def test_unmapped_workspace_with_path_evidence_partial(storage, ws):
    """Workspace known, mapping missing, but path resolves → partial."""
    store = FakeProjectStore({"/tmp/resolver": ("p_xyz", "proj-x")})
    resolver = make_resolver(storage, store)
    result = resolver.resolve(
        ScopeResolveRequest(workspace_id=ws.id, cwd="/tmp/resolver/sub")
    )
    assert result.state == "partial"
    assert result.project_id == "p_xyz"
    assert result.match_source == "explicit_cwd"
    assert result.matched_path == "/tmp/resolver/sub"


def test_session_cwd_resolves_to_mapped_workspace(storage):
    """Session cwd → project → reverse mapping → mapped."""
    ws = storage.create_workspace("other-ws", "/tmp/other")
    storage.link_project(ws.id, "p_abc")
    store = FakeProjectStore({"/work/proj-a": ("p_abc", "proj-a")})
    SESSIONS["s1"] = {"cwd": "/work/proj-a/src", "git_repo_root": "/work/proj-a"}
    resolver = make_resolver(storage, store)
    result = resolver.resolve(ScopeResolveRequest(session_id="s1"))
    assert result.state == "mapped"
    assert result.workspace_id == ws.id
    assert result.project_id == "p_abc"
    assert result.match_source == "session_cwd"


def test_session_cwd_resolves_but_no_workspace_mapped(storage):
    """Project identified but not linked to any workspace → partial."""
    store = FakeProjectStore({"/work/proj-b": ("p_b", "proj-b")})
    SESSIONS["s2"] = {"cwd": "/work/proj-b", "git_repo_root": "/work/proj-b"}
    resolver = make_resolver(storage, store)
    result = resolver.resolve(ScopeResolveRequest(session_id="s2"))
    assert result.state == "partial"
    assert result.project_id == "p_b"
    assert result.workspace_id == ""


def test_session_git_root_fallback(storage):
    """cwd unresolved, git root resolves → session_git_root source."""
    store = FakeProjectStore({"/git/repo": ("p_g", "proj-g")})
    SESSIONS["s3"] = {"cwd": "/somewhere/else", "git_repo_root": "/git/repo"}
    resolver = make_resolver(storage, store)
    result = resolver.resolve(ScopeResolveRequest(session_id="s3"))
    assert result.state == "partial"
    assert result.project_id == "p_g"
    assert result.match_source == "session_git_root"
    assert result.matched_path == "/git/repo"


def test_session_without_evidence_unresolved(storage):
    """No cwd/git evidence and no mapping → unresolved, never global."""
    SESSIONS["s4"] = {"cwd": "", "git_repo_root": ""}
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest(session_id="s4"))
    assert result.state == "unresolved"


def test_no_anchors_unresolved(storage):
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest())
    assert result.state == "unresolved"


def test_missing_session_unresolved(storage):
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest(session_id="ghost"))
    assert result.state == "unresolved"


def test_explicit_cwd_overrides_session(storage):
    """Caller-supplied cwd wins over the session's recorded cwd."""
    store = FakeProjectStore({"/override": ("p_o", "proj-o")})
    SESSIONS["s5"] = {"cwd": "/work/other", "git_repo_root": ""}
    resolver = make_resolver(storage, store)
    result = resolver.resolve(
        ScopeResolveRequest(session_id="s5", cwd="/override")
    )
    assert result.project_id == "p_o"
    assert result.match_source == "explicit_cwd"


def test_missing_workspace_raises(storage):
    resolver = make_resolver(storage, FakeProjectStore({}))
    with pytest.raises(WorkspaceNotFoundError):
        resolver.resolve(ScopeResolveRequest(workspace_id="missing"))


def test_mapped_project_slug_resolution(storage, ws):
    """Slug is resolved from the project store even without path evidence."""
    storage.link_project(ws.id, "p_abc")
    store = FakeProjectStore({"/x": ("p_abc", "proj-a")})
    resolver = make_resolver(storage, store)
    result = resolver.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.project_slug == "proj-a"


def test_unlink_then_resolve_returns_unmapped(storage, ws):
    storage.link_project(ws.id, "p_abc")
    storage.unlink_project(ws.id)
    resolver = make_resolver(storage, FakeProjectStore({}))
    result = resolver.resolve(ScopeResolveRequest(workspace_id=ws.id))
    assert result.state == "unmapped"
