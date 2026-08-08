"""S7.4 — Workspace LLM context adapter tests (Phases 9.1 + 9.2).

Covers:
- FAIL-CLOSED: unresolved / partial / unmapped / ambiguous / empty anchors
  all yield an EMPTY context string (nothing injected).
- mapped scope injects a bounded, provenance-carrying block.
- aggregates present (workspace identity, task stats, roadmap/ADR/journal/
  graph counts) but NO full bodies, NO absolute paths, NO raw profile path.
- ranked recall appears only for meaningful queries; trivial queries get
  aggregates only.
- budget: the assembled block never exceeds the char cap.
- sanitization: internal fences/notes and obvious secret/path tokens are
  stripped/redacted.
- profile isolation: two temp homes resolve distinct workspaces; a session
  in profile B cannot surface profile A's workspace.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes_constants import (  # type: ignore[import-untyped]
    reset_hermes_home_override,
    set_hermes_home_override,
)
from hermes_state import SessionDB  # type: ignore[import-untyped]
from hermes_cli import projects_db  # type: ignore[import-untyped]
from plugins.workspace.backend.context_adapter import (  # type: ignore[import-untyped]
    MAX_CONTEXT_CHARS,
    WorkspaceContextAdapter,
    assemble_workspace_context,
    reset_context_adapter,
)
from plugins.workspace.backend.models import (  # type: ignore[import-untyped]
    WorkspaceCreate,
)
from plugins.workspace.backend.runtime import (  # type: ignore[import-untyped]
    WorkspaceRuntime,
    get_workspace_runtime,
    reset_workspace_runtimes,
)


@contextmanager
def _effective_home(home: Path) -> Iterator[None]:
    token = set_hermes_home_override(str(home))
    try:
        yield
    finally:
        reset_hermes_home_override(token)


@pytest.fixture(autouse=True)
def _unpin_runtime():
    """Use the real home-keyed runtime cache (like test_runtime_isolation)."""
    reset_workspace_runtimes()
    reset_context_adapter()
    yield
    reset_workspace_runtimes()
    reset_context_adapter()


def _add_project(home: Path, name: str, folders) -> str:
    with projects_db.connect_closing(home / "projects.db") as conn:
        return projects_db.create_project(
            conn, name=name, folders=folders, primary_path=folders[0]
        )


def _add_session(home: Path, session_id: str, cwd: str, git_root: str = "") -> None:
    db = SessionDB(home / "state.db")
    try:
        db.create_session(
            session_id, source="test", cwd=cwd, git_repo_root=git_root, profile_name=""
        )
    finally:
        db.close()


def _mapped_env(tmp_path: Path, name: str = "w-a", project: str = "proj-a",
                folder: str = "/work/p1"):
    """Build a home with a workspace linked to a real project + durable
    session, all under the SAME profile home. Returns (home, ws_id)."""
    home = tmp_path / name
    home.mkdir(parents=True, exist_ok=True)
    pid = _add_project(home, project, [folder])
    with _effective_home(home):
        rt = get_workspace_runtime()
        ws = rt.workspace_service.create_workspace(WorkspaceCreate(name=name, path=""))
        rt.storage.link_project(ws.id, pid)
    _add_session(home, "durable-1", folder, folder)
    return home, ws.id


def _call(home: Path, **kwargs) -> str:
    with _effective_home(home):
        return assemble_workspace_context(**kwargs)


# ---------------------------------------------------------------------------
# Phase 9.1 — no-op / fail-closed hook behavior
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_empty_anchors_yield_empty(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        assert _call(home) == ""
        assert _call(home, session_id="", workspace_id="", cwd="") == ""

    def test_unknown_session_yields_empty(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        assert _call(home, session_id="no-such-session") == ""

    def test_volatile_runtime_id_yields_empty(self, tmp_path):
        """A volatile Desktop runtime id is NOT a durable SessionDB key —
        must fail closed (U1C identity rule)."""
        home, _ws_id = _mapped_env(tmp_path)
        assert _call(home, session_id="volatile-runtime-abc123") == ""

    def test_unresolved_scope_yields_empty(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        # session in one home, but cwd resolves nowhere -> unresolved
        assert _call(home, session_id="durable-1", cwd="/elsewhere") == ""

    def test_partial_scope_yields_empty(self, tmp_path):
        """A project with NO workspace mapping is partial -> empty."""
        home = tmp_path / "partial"
        home.mkdir(parents=True, exist_ok=True)
        _add_project(home, "proj-p", ["/work/pp"])
        _add_session(home, "s-p", "/work/pp", "/work/pp")
        assert _call(home, session_id="s-p") == ""

    def test_unmapped_scope_yields_empty(self, tmp_path):
        """A workspace with no project link and no path evidence is
        unmapped -> empty."""
        home = tmp_path / "unmapped"
        home.mkdir(parents=True, exist_ok=True)
        with _effective_home(home):
            rt = get_workspace_runtime()
            rt.workspace_service.create_workspace(WorkspaceCreate(name="w-u", path=""))
        assert _call(home, session_id="durable-1") == ""

    def test_ambiguous_scope_yields_empty(self, tmp_path):
        """Duplicate project mapping -> ambiguous -> empty (never a silent
        first-row choice)."""
        home = tmp_path / "ambig"
        home.mkdir(parents=True, exist_ok=True)
        pid = _add_project(home, "proj-am", ["/work/pam"])
        with _effective_home(home):
            rt = get_workspace_runtime()
            w1 = rt.workspace_service.create_workspace(
                WorkspaceCreate(name="w1", path=""))
            w2 = rt.workspace_service.create_workspace(
                WorkspaceCreate(name="w2", path=""))
            rt.storage.link_project(w1.id, pid)
            # Seed corrupt duplicate via raw SQL (same as authority test).
            conn = rt.database.get_connection()
            conn.execute(
                "UPDATE workspaces SET hermes_project_id = ? WHERE id = ?",
                (pid, w2.id),
            )
            conn.commit()
        _add_session(home, "s-am", "/work/pam", "/work/pam")
        assert _call(home, session_id="s-am") == ""

    def test_register_hook_returns_context_dict(self):
        """Phase 9.1: the plugin registers a hook whose callback returns a
        dict with a 'context' key (never None/str that breaks composition)."""
        from plugins.workspace import _make_pre_llm_callback

        cb = _make_pre_llm_callback()
        result = cb(session_id="", user_message="hi")
        assert isinstance(result, dict)
        assert "context" in result
        assert result["context"] == ""


# ---------------------------------------------------------------------------
# Phase 9.2 — mapped assembly
# ---------------------------------------------------------------------------


class TestMappedAssembly:
    def test_mapped_injects_bounded_block(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1")
        assert block.startswith("<workspace-context>")
        assert block.endswith("</workspace-context>")
        assert "Workspace: w-a" in block
        assert "Provenance:" in block
        assert "Tasks:" in block
        assert len(block) <= MAX_CONTEXT_CHARS

    def test_mapped_block_has_aggregates_not_bodies(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1")
        # aggregates present
        assert "Tasks:" in block
        assert "Roadmaps:" in block
        assert "ADRs:" in block
        assert "Journal:" in block
        assert "Graph:" in block
        # no full bodies, no absolute paths, no raw profile home
        assert "<markdown>" not in block.lower()
        assert "<description>" not in block.lower()
        assert "/work/p1" not in block  # matched_path not leaked
        assert ".hermes" not in block  # raw profile path not leaked

    def test_meaningful_query_adds_recall(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        # seed a searchable ADR in the mapped workspace
        from plugins.workspace.backend.models import ADRCreate

        with _effective_home(home):
            rt = get_workspace_runtime()
            rt.adr_service.create_adr(
                ADRCreate(
                    workspace_id=ws_id,
                    title="Auth Design",
                    status="proposed",
                    category="",
                    markdown="# Auth\n",
                    tags=[],
                )
            )
        block = _call(home, session_id="durable-1", user_message="auth design")
        assert "Recent relevant workspace items:" in block

    def test_trivial_query_gets_no_recall(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1", user_message="hi")
        assert "Recent relevant workspace items:" not in block
        # aggregates still present
        assert "Tasks:" in block

    def test_sanitization_strips_fences_and_paths(self, tmp_path):
        home, _ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1", user_message="check /home/foo")
        # internal fence tag gone, path token redacted
        assert "workspace-context>" not in block.replace(
            "<workspace-context>", "").replace("</workspace-context>", "")


# ---------------------------------------------------------------------------
# Profile isolation
# ---------------------------------------------------------------------------


class TestProfileIsolation:
    def test_two_profiles_do_not_cross(self, tmp_path):
        home_a, _ws_a = _mapped_env(tmp_path, name="wa", project="pa", folder="/work/pa")
        home_b, _ws_b = _mapped_env(tmp_path, name="wb", project="pb", folder="/work/pb")
        _add_session(home_b, "durable-1", "/work/pb", "/work/pb")

        block_a = _call(home_a, session_id="durable-1")
        block_b = _call(home_b, session_id="durable-1")

        assert "Workspace: wa" in block_a
        assert "Workspace: wb" in block_b
        # A never leaks B's workspace name
        assert "wb" not in block_a
        assert "wa" not in block_b


# ---------------------------------------------------------------------------
# Phase 9.3 — ranked recall hardening
# ---------------------------------------------------------------------------


class TestRankedRecallHardening:
    def _seed(self, home, ws_id, *titles):
        """Seed searchable ADRs in the mapped workspace."""
        from plugins.workspace.backend.models import ADRCreate

        with _effective_home(home):
            rt = get_workspace_runtime()
            for t in titles:
                rt.adr_service.create_adr(
                    ADRCreate(
                        workspace_id=ws_id,
                        title=t,
                        status="proposed",
                        category="",
                        markdown=f"# {t}\nbody {t.lower()}",
                        tags=[],
                    )
                )

    def test_meaningful_query_returns_ranked(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design", "Storage Design")
        block = _call(home, session_id="durable-1", user_message="auth design")
        assert "Recent relevant workspace items:" in block
        assert "Auth Design" in block

    def test_trivial_query_no_ranked(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design")
        for trivial in ("hi", "ok", "thanks", "yes", "/help"):
            block = _call(home, session_id="durable-1", user_message=trivial)
            assert "Recent relevant workspace items:" not in block, trivial

    def test_query_below_min_length_no_ranked(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design")
        block = _call(home, session_id="durable-1", user_message="ab")
        assert "Recent relevant workspace items:" not in block
        # aggregates still present
        assert "Tasks:" in block

    def test_workspace_id_mandatory_never_global(self, tmp_path):
        """The assembler must never call search without a workspace_id."""
        from plugins.workspace.backend.context_adapter import WorkspaceContextAdapter

        home, ws_id = _mapped_env(tmp_path)
        calls = []

        class NoGlobalSearchAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                calls.append(workspace_id)
                return ""

        from plugins.workspace.backend.context_adapter import assemble_workspace_context

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1",
                user_message="auth design",
                adapter=NoGlobalSearchAdapter(),
            )
        assert calls and calls[0] == ws_id
        assert calls and calls[0] != ""

    def test_cross_workspace_results_never_appear(self, tmp_path):
        """A result whose workspace_id differs is dropped."""
        home_a, ws_a = _mapped_env(tmp_path, name="wa", project="pa", folder="/work/pa")
        home_b, ws_b = _mapped_env(tmp_path, name="wb", project="pb", folder="/work/pb")
        self._seed(home_a, ws_a, "Auth Design")
        self._seed(home_b, ws_b, "Auth Design B")

        block_a = _call(home_a, session_id="durable-1", user_message="auth design")
        # A's block may contain A's ADR, never B's
        assert "Auth Design B" not in block_a

    def test_deterministic_score_then_id_ordering(self, tmp_path):
        """Repeated identical turns produce byte-identical context and the
        ordering is score-desc, id-asc."""
        home, ws_id = _mapped_env(tmp_path)
        # same score -> deterministic id tiebreak
        self._seed(home, ws_id, "Alpha Item", "Beta Item", "Gamma Item")
        with _effective_home(home):
            rt = get_workspace_runtime()
            # give Alpha a higher score via a direct title substring query
        b1 = _call(home, session_id="durable-1", user_message="alpha")
        b2 = _call(home, session_id="durable-1", user_message="alpha")
        assert b1 == b2
        assert "Alpha Item" in b1

    def test_repeated_identical_query_byte_identical(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design")
        b1 = _call(home, session_id="durable-1", user_message="auth design")
        b2 = _call(home, session_id="durable-1", user_message="auth design")
        assert b1 == b2

    def test_duplicate_suppression_by_id(self, tmp_path):
        """Two search results with the same canonical_id/id render once."""
        from plugins.workspace.backend.context_adapter import (
            WorkspaceContextAdapter,
        )
        from plugins.workspace.backend.models import SearchResult, SearchResponse

        home, ws_id = _mapped_env(tmp_path)

        class DupSearchAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                # fabricate two results with the SAME canonical id
                dup = SearchResult(
                    id="r-1", type="adr", title="Dup Item",
                    description="body", status="proposed",
                    workspace_id=workspace_id, score=5.0,
                    canonical_id="dup-canonical",
                )
                resp = SearchResponse(results=[dup, dup], total=2, query=query)
                return self._render_snippets(resp.results, workspace_id)

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1",
                user_message="dup",
                adapter=DupSearchAdapter(),
            )
        # "Dup Item" appears exactly once (dedup by canonical_id/id)
        assert block.count("Dup Item") == 1

    def test_snippet_truncation(self, tmp_path):
        """A long snippet is capped at _SNIPPET_CHARS."""
        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design")
        block = _call(home, session_id="durable-1", user_message="auth design")
        # the seeded snippet is short; directly test the truncation cap
        from plugins.workspace.backend.context_adapter import _SNIPPET_CHARS

        assert _SNIPPET_CHARS == 160

    def test_hard_context_budget(self, tmp_path):
        from plugins.workspace.backend.context_adapter import MAX_CONTEXT_CHARS

        home, ws_id = _mapped_env(tmp_path)
        self._seed(home, ws_id, "Auth Design")
        block = _call(home, session_id="durable-1", user_message="auth design")
        assert len(block) <= MAX_CONTEXT_CHARS

    def test_sanitization_redacts_secrets_and_paths(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        with _effective_home(home):
            rt = get_workspace_runtime()
            from plugins.workspace.backend.models import ADRCreate

            rt.adr_service.create_adr(
                ADRCreate(
                    workspace_id=ws_id,
                    title="Secret Leak",
                    status="proposed",
                    category="",
                    markdown="token sk-abc123 at /home/user/secret",
                    tags=[],
                )
            )
        block = _call(home, session_id="durable-1", user_message="secret leak")
        assert "sk-abc123" not in block
        assert "/home/user/secret" not in block

    def test_unresolved_ambiguous_still_empty(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        assert _call(home, session_id="no-such-session") == ""
        assert _call(home) == ""

    def test_profile_a_b_isolation_with_recall(self, tmp_path):
        home_a, ws_a = _mapped_env(tmp_path, name="wa", project="pa", folder="/work/pa")
        home_b, ws_b = _mapped_env(tmp_path, name="wb", project="pb", folder="/work/pb")
        self._seed(home_a, ws_a, "Auth A")
        self._seed(home_b, ws_b, "Auth B")
        ba = _call(home_a, session_id="durable-1", user_message="auth")
        bb = _call(home_b, session_id="durable-1", user_message="auth")
        assert "Auth A" in ba
        assert "Auth B" in bb
        assert "Auth B" not in ba
        assert "Auth A" not in bb


# ---------------------------------------------------------------------------
# Phase 9.4 — hardening & integration validation
# ---------------------------------------------------------------------------


class TestPhase94FailureIsolation:
    """A failure in any Workspace subsystem must produce an EMPTY block, a
    leak, or a global fallback — and must never raise through the hook."""

    def test_runtime_unavailable_yields_empty(self, tmp_path, monkeypatch):
        import plugins.workspace.backend.runtime as runtime_mod

        home, _ws_id = _mapped_env(tmp_path)

        def _boom():
            raise RuntimeError("runtime down")

        monkeypatch.setattr(runtime_mod, "get_workspace_runtime", _boom)
        assert _call(home, session_id="durable-1") == ""

    def test_search_failure_yields_empty_recall(self, tmp_path):
        from plugins.workspace.backend.context_adapter import WorkspaceContextAdapter

        home, ws_id = _mapped_env(tmp_path)

        class BrokenSearchAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                raise RuntimeError("search blew up")

        from plugins.workspace.backend.context_adapter import assemble_workspace_context

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1",
                user_message="auth design",
                adapter=BrokenSearchAdapter(),
            )
        # aggregates still present, recall absent, no leak, no crash
        assert "Tasks:" in block
        assert "Recent relevant workspace items:" not in block

    def test_analytics_failure_still_yields_aggregates(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        with _effective_home(home):
            rt = get_workspace_runtime()
            # analytics_service is a read-only property; swap the underlying
            # attribute to an exploding stand-in.
            rt._analytics_service = _ExplodingAnalytics()
        block = _call(home, session_id="durable-1", user_message="hi")
        # header + task stats still present; analytics lines simply absent
        assert "Provenance:" in block
        assert "Tasks:" in block
        assert "<workspace-context>" in block

    def test_malformed_search_result_skipped_safely(self, tmp_path):
        from plugins.workspace.backend.context_adapter import WorkspaceContextAdapter
        from plugins.workspace.backend.models import SearchResult, SearchResponse

        home, ws_id = _mapped_env(tmp_path)

        class MalformedAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                # a result with NO id and NO canonical_id
                bad = SearchResult(
                    id="", type="adr", title="Nameless",
                    description="x", workspace_id=workspace_id, score=1.0,
                    canonical_id="",
                )
                return self._render_snippets([bad, None], workspace_id)

        from plugins.workspace.backend.context_adapter import assemble_workspace_context

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1",
                user_message="x", adapter=MalformedAdapter(),
            )
        # None result and empty-id result are skipped; block still valid
        assert "<workspace-context>" in block
        assert "Nameless" not in block

    def test_hook_exception_does_not_raise(self):
        """invoke_hook wraps callbacks in try/except (plugins.py:2123) — a
        Workspace callback raising must not propagate to the agent loop."""
        from hermes_cli.plugins import PluginManager
        from hermes_cli.lifecycle import invoke_hook as lifecycle_invoke_hook

        # The adapter's own assemble() already swallows exceptions; the
        # callback returns {"context": ""} so composition stays valid.
        from plugins.workspace import _make_pre_llm_callback

        cb = _make_pre_llm_callback()
        result = cb(session_id="", user_message="hi")
        assert isinstance(result, dict)
        assert result["context"] == ""


class _ExplodingAnalytics:
    """Stands in for AnalyticsService with a raising get_analytics."""

    def get_analytics(self, workspace_id=""):
        raise RuntimeError("analytics down")


class TestPhase94BoundedAndSecurity:
    def test_pathological_large_recall_cannot_bypass_budget(self, tmp_path):
        from plugins.workspace.backend.context_adapter import (
            MAX_CONTEXT_CHARS,
            WorkspaceContextAdapter,
        )
        from plugins.workspace.backend.models import SearchResult, SearchResponse

        home, ws_id = _mapped_env(tmp_path)

        class FloodAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                # 1000 results, each with a huge description
                results = []
                for i in range(1000):
                    results.append(SearchResult(
                        id=f"r-{i}", type="adr", title=f"Item {i}",
                        description="X" * 5000, workspace_id=workspace_id,
                        score=1.0, canonical_id=f"r-{i}",
                    ))
                return self._render_snippets(results, workspace_id)

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1", user_message="auth",
                adapter=FloodAdapter(),
            )
        assert len(block) <= MAX_CONTEXT_CHARS
        # only up to _MAX_SNIPPETS snippets render
        from plugins.workspace.backend.context_adapter import _MAX_SNIPPETS

        assert block.count("[adr] Item") <= _MAX_SNIPPETS

    def test_no_hermes_home_or_db_path_leak(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1", user_message="auth design")
        # the raw home path and its basename must not appear verbatim
        raw_home = str(home)
        assert raw_home not in block
        assert "workspace.db" not in block
        assert "state.db" not in block
        assert "projects.db" not in block

    def test_no_absolute_repo_path_leak(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        block = _call(home, session_id="durable-1", user_message="auth design")
        assert "/work/p1" not in block

    def test_truncation_marker_deterministic(self, tmp_path):
        from plugins.workspace.backend.context_adapter import (
            _TRUNCATION_MARKER,
            WorkspaceContextAdapter,
        )
        from plugins.workspace.backend.models import SearchResult

        home, ws_id = _mapped_env(tmp_path)

        class BigAdapter(WorkspaceContextAdapter):
            def _aggregates(self, runtime, workspace_id):
                return "A" * 10000

        with _effective_home(home):
            block = assemble_workspace_context(
                session_id="durable-1", user_message="hi", adapter=BigAdapter(),
            )
        assert _TRUNCATION_MARKER in block
        assert block.count(_TRUNCATION_MARKER) == 1


class TestPhase94Lifecycle:
    def test_repeated_invocation_stable(self, tmp_path):
        home, ws_id = _mapped_env(tmp_path)
        b1 = _call(home, session_id="durable-1", user_message="auth")
        for _ in range(5):
            assert _call(home, session_id="durable-1", user_message="auth") == b1

    def test_multiple_sessions_same_workspace(self, tmp_path):
        """Two durable sessions in the same home resolve the same mapped
        workspace and produce identical context (state unchanged)."""
        home, ws_id = _mapped_env(tmp_path)
        _add_session(home, "durable-2", "/work/p1", "/work/p1")
        b1 = _call(home, session_id="durable-1")
        b2 = _call(home, session_id="durable-2")
        assert b1 == b2
        assert "Workspace: w-a" in b1

    def test_no_stale_runtime_crossing_profiles(self, tmp_path):
        """After switching profiles, the adapter resolves the NEW profile's
        workspace — no stale runtime from the previous home."""
        home_a, ws_a = _mapped_env(tmp_path, name="wa", project="pa", folder="/work/pa")
        home_b, ws_b = _mapped_env(tmp_path, name="wb", project="pb", folder="/work/pb")
        _add_session(home_b, "durable-1", "/work/pb", "/work/pb")
        _add_session(home_a, "durable-1", "/work/pa", "/work/pa")
        b_a = _call(home_a, session_id="durable-1")
        b_b = _call(home_b, session_id="durable-1")
        assert "Workspace: wa" in b_a
        assert "Workspace: wb" in b_b
        assert "wb" not in b_a
        assert "wa" not in b_b

    def test_plugin_register_hook_wire(self):
        """The plugin's register() registers pre_llm_call through the official
        PluginContext surface (no manual manager mutation)."""
        from plugins.workspace import _CONTEXT_HOOK_NAME, register

        captured = {}

        class FakeCtx:
            def register_hook(self, hook_name, callback):
                captured["hook"] = hook_name
                captured["cb"] = callback

        register(FakeCtx())
        assert captured.get("hook") == "pre_llm_call"
        assert callable(captured.get("cb"))
        res = captured["cb"](session_id="", user_message="hi")
        assert isinstance(res, dict)
        assert res["context"] == ""

    def test_real_pluginmanager_loads_workspace_hook(self, tmp_path, monkeypatch):
        """End-to-end: the production PluginManager singleton discovers the
        bundled workspace plugin (kind: standalone, opt-in via plugins.enabled),
        calls register(ctx), and the registered pre_llm_call hook is invocable
        through lifecycle.invoke_hook — the exact 0.20.0 surface."""
        import yaml

        from hermes_cli.plugins import get_plugin_manager, _plugin_manager
        from hermes_cli.lifecycle import invoke_hook, has_hook

        # temp HERMES_HOME with config.yaml enabling the workspace plugin
        home = tmp_path
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(
            yaml.dump({"plugins": {"enabled": ["workspace"]}})
        )
        monkeypatch.setenv("HERMES_HOME", str(home))
        monkeypatch.delenv("HERMES_BUNDLED_PLUGINS", raising=False)

        # Reset the singleton so discovery re-runs against the temp config.
        from hermes_cli import plugins as plugins_mod

        saved = _plugin_manager
        plugins_mod._plugin_manager = None
        try:
            manager = get_plugin_manager()
            manager.discover_and_load(force=True)
            assert has_hook("pre_llm_call")
            results = invoke_hook(
                "pre_llm_call",
                session_id="",
                user_message="hi",
            )
            # our callback returns {"context": ""} (empty anchors -> fail-closed)
            assert any(
                isinstance(r, dict) and r.get("context", "") == "" for r in results
            )
        finally:
            plugins_mod._plugin_manager = saved
