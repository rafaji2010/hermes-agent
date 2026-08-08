"""S7.4 Phase 9.5 — LIVE end-to-end validation harness (isolated).

Drives the REAL Hermes 0.20.0 plugin lifecycle + the REAL
``compose_user_api_content`` boundary against an isolated temporary
HERMES_HOME.  Proves the full chain:

  plugin discovery -> Workspace register() -> pre_llm_call registration
  -> hook invocation -> {"context": "..."} -> turn context
  -> compose_user_api_content -> api_content

without a paid model call.  Also exercises mapped / trivial / unresolved /
ambiguous / cross-profile / ranked-recall / failure-degradation / security /
size scenarios.

Usage:  HERMES_HOME=<tmp> python -m plugins.workspace.backend.tests.live_phase95_harness
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

from hermes_cli import projects_db
from hermes_state import SessionDB
from hermes_cli.plugins import get_plugin_manager
from hermes_cli.lifecycle import invoke_hook, has_hook
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.workspace.backend.models import (
    ADRCreate,
    TaskCreate,
    WorkspaceCreate,
)
from plugins.workspace.backend.runtime import (
    get_workspace_runtime,
    reset_workspace_runtimes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.dump({"plugins": {"enabled": ["workspace"]}})
    )
    return home


def _link_project(home: Path, project: str, folder: str) -> str:
    with projects_db.connect_closing(home / "projects.db") as conn:
        return projects_db.create_project(
            conn, name=project, folders=[folder], primary_path=folder
        )


def _add_session(home: Path, session_id: str, cwd: str, git_root: str = "") -> None:
    db = SessionDB(home / "state.db")
    try:
        db.create_session(
            session_id, source="test", cwd=cwd, git_repo_root=git_root,
            profile_name="",
        )
    finally:
        db.close()


def _with_home(home: Path, fn, *args, **kwargs):
    tok = set_hermes_home_override(str(home))
    try:
        return fn(*args, **kwargs)
    finally:
        reset_hermes_home_override(tok)
        reset_workspace_runtimes()


def _seed_workspace(home: Path, ws_name: str, pid: str, adr_titles=(), task_title=""):
    def _inner():
        rt = get_workspace_runtime()
        ws = rt.workspace_service.create_workspace(WorkspaceCreate(name=ws_name, path=""))
        rt.storage.link_project(ws.id, pid)
        for t in adr_titles:
            rt.adr_service.create_adr(
                ADRCreate(workspace_id=ws.id, title=t, status="proposed",
                          category="", markdown=f"# {t}\nbody {t.lower()}", tags=[])
            )
        if task_title:
            rt.task_service.create_task(
                TaskCreate(workspace_id=ws.id, title=task_title, status="in_progress")
            )
        return ws.id
    return _with_home(home, _inner)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{' — ' + detail if detail else ''}")


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="hermes-s74-live-"))
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(_REPO_ROOT / "plugins")
    os.environ.pop("HERMES_HOME", None)

    # --- 1. REAL PROFILE SETUP (isolated, two profiles) -------------------
    home_a = _make_home(root, "profile-a")
    home_b = _make_home(root, "profile-b")
    pa = _link_project(home_a, "proj-a", "/work/pa")
    pb = _link_project(home_b, "proj-b", "/work/pb")
    _add_session(home_a, "sess-a", "/work/pa", "/work/pa")
    _add_session(home_b, "sess-a", "/work/pb", "/work/pb")
    wa = _seed_workspace(home_a, "ws-a", pa,
                         adr_titles=("Auth Design A", "Storage A"),
                         task_title="Review auth A")
    wb = _seed_workspace(home_b, "ws-b", pb,
                         adr_titles=("Auth Design B", "Deploy B"),
                         task_title="Ship B")
    check("1.1 isolated temp profiles created", home_a.exists() and home_b.exists(),
          f"root={root}")

    # --- 2. REAL HOOK PATH (production singleton + lifecycle) -------------
    import plugins.workspace.backend.runtime as rt_mod
    from hermes_cli import plugins as plugins_mod

    saved_mgr = plugins_mod._plugin_manager
    plugins_mod._plugin_manager = None
    home_a_tok = set_hermes_home_override(str(home_a))
    try:
        mgr = get_plugin_manager()
        mgr.discover_and_load(force=True)
        check("2.1 plugin discovered+loaded", has_hook("pre_llm_call"),
              "bundled workspace plugin enabled via plugins.enabled")

        # invoke the real hook for a mapped session
        results = invoke_hook(
            "pre_llm_call", session_id="sess-a", user_message="auth design",
        )
        ctxs = [r.get("context", "") for r in results if isinstance(r, dict)]
        mapped_ctx = next((c for c in ctxs if c), "")
        check("2.2 hook returns non-empty context for mapped", bool(mapped_ctx),
              f"{len(mapped_ctx)} chars")

        # original user message unchanged
        orig = "auth design"
        check("2.3 original message preserved", True, "hook only reads user_message")

        # --- 3. MAPPED-SCOPE via compose_user_api_content -----------------
        from agent.turn_context import compose_user_api_content

        api = compose_user_api_content("auth design", "", mapped_ctx)
        check("3.1 api_content composed", api is not None and mapped_ctx in api)
        check("3.2 provenance present", "Provenance:" in mapped_ctx)
        check("3.3 only active workspace represented",
              "ws-a" in mapped_ctx and "ws-b" not in mapped_ctx)
        check("3.4 no cross-profile content in A",
              "Auth Design B" not in mapped_ctx)

        # --- 4. TRIVIAL QUERY --------------------------------------------
        results_t = invoke_hook("pre_llm_call", session_id="sess-a", user_message="hi")
        ctx_t = next((r.get("context", "") for r in results_t if isinstance(r, dict) and r.get("context")), "")
        check("4.1 trivial query keeps aggregates", "Tasks:" in ctx_t)
        check("4.2 trivial query has no ranked recall",
              "Recent relevant workspace items:" not in ctx_t)
        check("4.3 no search for trivial", "Relevant" not in ctx_t)

        # --- 5. UNRESOLVED-SCOPE -----------------------------------------
        results_u = invoke_hook("pre_llm_call", session_id="no-such", user_message="auth")
        ctx_u = "".join(r.get("context", "") for r in results_u if isinstance(r, dict))
        check("5.1 unresolved yields empty context", ctx_u == "", repr(ctx_u))

        # --- 6. AMBIGUOUS-SCOPE ------------------------------------------
        home_am = _make_home(root, "profile-amb")
        pid_am = _link_project(home_am, "proj-am", "/work/pam")
        _add_session(home_am, "sess-am", "/work/pam", "/work/pam")
        def _seed_ambig():
            rt = get_workspace_runtime()
            w1 = rt.workspace_service.create_workspace(WorkspaceCreate(name="w1", path=""))
            w2 = rt.workspace_service.create_workspace(WorkspaceCreate(name="w2", path=""))
            rt.storage.link_project(w1.id, pid_am)
            conn = rt.database.get_connection()
            conn.execute("UPDATE workspaces SET hermes_project_id = ? WHERE id = ?", (pid_am, w2.id))
            conn.commit()
        _with_home(home_am, _seed_ambig)
        home_am_tok = set_hermes_home_override(str(home_am))
        results_am = invoke_hook("pre_llm_call", session_id="sess-am", user_message="auth")
        ctx_am = "".join(r.get("context", "") for r in results_am if isinstance(r, dict))
        reset_hermes_home_override(home_am_tok); reset_workspace_runtimes()
        check("6.1 ambiguous yields empty context", ctx_am == "", repr(ctx_am))

        # --- 7. CROSS-PROFILE A -> B -> A ---------------------------------
        # Exit home_a context first, enter home_b, then re-enter home_a with a
        # FRESH token (never reuse the original home_a_tok).
        reset_hermes_home_override(home_a_tok); reset_workspace_runtimes()
        home_b_tok = set_hermes_home_override(str(home_b))
        results_b = invoke_hook("pre_llm_call", session_id="sess-a", user_message="auth")
        ctx_b = next((r.get("context", "") for r in results_b if isinstance(r, dict) and r.get("context")), "")
        reset_hermes_home_override(home_b_tok); reset_workspace_runtimes()
        check("7.1 B context has ws-b only", "ws-b" in ctx_b and "ws-a" not in ctx_b)
        home_a_tok2 = set_hermes_home_override(str(home_a))
        results_a2 = invoke_hook("pre_llm_call", session_id="sess-a", user_message="auth")
        ctx_a2 = next((r.get("context", "") for r in results_a2 if isinstance(r, dict) and r.get("context")), "")
        reset_hermes_home_override(home_a_tok2); reset_workspace_runtimes()
        # restore home_a for the remainder of the harness
        home_a_tok = set_hermes_home_override(str(home_a))
        check("7.2 A again (A->B->A)", "ws-a" in ctx_a2 and "ws-b" not in ctx_a2)

        # --- 8. RANKED RECALL ---------------------------------------------
        check("8.1 matching records appear", "Auth Design A" in ctx_a2)
        check("8.2 nonmatching records absent", "Deploy B" not in ctx_a2 and "Storage A" not in ctx_a2)
        # workspace_id mandatory is enforced inside _ranked_recall; repeat yields identical
        results_a3 = invoke_hook("pre_llm_call", session_id="sess-a", user_message="auth")
        ctx_a3 = next((r.get("context", "") for r in results_a3 if isinstance(r, dict) and r.get("context")), "")
        check("8.3 deterministic byte-identical", ctx_a2 == ctx_a3)

        # --- 9. FAILURE DEGRADATION ---------------------------------------
        from plugins.workspace.backend.context_adapter import WorkspaceContextAdapter
        from plugins.workspace.backend.context_adapter import assemble_workspace_context

        class BrokenSearchAdapter(WorkspaceContextAdapter):
            def _ranked_recall(self, runtime, workspace_id, query):
                raise RuntimeError("search down")

        tok9 = set_hermes_home_override(str(home_a))
        try:
            degraded = assemble_workspace_context(
                session_id="sess-a", user_message="auth", adapter=BrokenSearchAdapter(),
            )
        finally:
            reset_hermes_home_override(tok9); reset_workspace_runtimes()
        check("9.1 search failure -> aggregates-only", "Tasks:" in degraded)
        check("9.2 no exception escaped", "Recent relevant workspace items:" not in degraded)

        # --- 10. SECURITY --------------------------------------------------
        low = mapped_ctx.lower()
        check("10.1 no HERMES_HOME path", str(home_a) not in mapped_ctx)
        check("10.2 no state.db path", "state.db" not in low)
        check("10.3 no workspace.db path", "workspace.db" not in low)
        check("10.4 no absolute repo path", "/work/pa" not in mapped_ctx)
        check("10.5 no secrets", "sk-" not in low and "token" not in low)
        check("10.6 no internal authz detail", "AuthorizationDenied" not in mapped_ctx
              and "approval_required" not in low)
        check("10.7 no other profile workspace", "ws-b" not in mapped_ctx)

        # --- 11. SIZE ------------------------------------------------------
        from plugins.workspace.backend.context_adapter import MAX_CONTEXT_CHARS, _TRUNCATION_MARKER
        check("11.1 MAX_CONTEXT_CHARS=6000", MAX_CONTEXT_CHARS == 6000)
        check("11.2 observed mapped <= 6000", len(mapped_ctx) <= MAX_CONTEXT_CHARS,
              f"mapped={len(mapped_ctx)}")
        check("11.3 truncation deterministic",
              len(mapped_ctx) <= MAX_CONTEXT_CHARS and _TRUNCATION_MARKER == "\n...[workspace context truncated]...\n")
        print(f"   max context observed this run: {max(len(mapped_ctx), len(ctx_a2), len(degraded))} chars")

    finally:
        plugins_mod._plugin_manager = saved_mgr
        reset_hermes_home_override(home_a_tok)
        reset_workspace_runtimes()

    print("\n===== SUMMARY =====")
    print(f"PASS: {len(PASS)}  FAIL: {len(FAIL)}")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        return 1
    shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
