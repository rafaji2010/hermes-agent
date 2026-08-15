"""``hermes workers`` subcommand parser.

Follows the pattern of ``hermes_cli/subcommands/self_describe.py``: parser
built here, handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_workers_parser(subparsers, *, cmd_workers: Callable) -> None:
    """Attach the ``workers`` subcommand to ``subparsers``."""
    workers_parser = subparsers.add_parser(
        "workers",
        help="Discover installed coding-agent harnesses and their capabilities",
        description=(
            "Discover the installed external coding-agent harnesses (pi, "
            "codex, opencode, commandcode, dsh, herdr) and report their "
            "version, capabilities, and Herdr integration status, and manage "
            "a registry of user-defined custom workers persisted to "
            "<HERMES_HOME>/workers.yaml."
        ),
    )
    workers_subparsers = workers_parser.add_subparsers(dest="workers_action")

    _list = workers_subparsers.add_parser(
        "list",
        aliases=["ls"],
        help="List detected workers with version, capabilities, and Herdr status",
    )
    _list.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the table",
    )

    _status = workers_subparsers.add_parser(
        "status",
        help="List workers, live executions, and (with --all) recent history",
        description=(
            "Report the installed workers and their Herdr integration status, "
            "then the currently running/blocked executions (§28 — worker, "
            "task, execution id, status, elapsed) from the mirrored live "
            "registry. With --all, also list the most recent completed/failed "
            "executions from the persisted history (§30), --limit N controls "
            "the count (default 10)."
        ),
    )
    _status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the table",
    )
    _status.add_argument(
        "--all",
        action="store_true",
        help="Also show recent completed/failed executions from the persisted history",
    )
    _status.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of history entries to show with --all (default: 10)",
    )

    _add = workers_subparsers.add_parser(
        "add",
        help="Register a custom worker (stored in workers.yaml)",
    )
    _add.add_argument("name", help="Worker name (must not collide with a builtin harness)")
    _add.add_argument(
        "capabilities",
        nargs="+",
        metavar="capability",
        help="Capabilities this worker provides, e.g. coding testing",
    )

    _remove = workers_subparsers.add_parser(
        "remove",
        aliases=["rm"],
        help="Remove a custom worker from the registry",
    )
    _remove.add_argument("name", help="Worker name to remove")

    _run = workers_subparsers.add_parser(
        "run",
        help="Execute a task on an external worker harness",
        description=(
            "Route a task to the best installed worker harness (or force one "
            "with --worker), start it in its workspace, and optionally wait "
            "for completion. Implements the AgentExecutionBackend contract: "
            "spec → execution_id, lifecycle PLANNED→DISPATCHING→RUNNING→"
            "DONE/FAILED, and §29 failure handling (--retry, "
            "--switch-on-failure)."
        ),
    )
    _run.add_argument("task", help="The task/prompt to run on the worker")
    _run.add_argument(
        "--worker",
        help="Force a specific worker type (pi, codex, opencode, commandcode, dsh, or a registered custom worker)",
    )
    _run.add_argument(
        "--capabilities",
        nargs="+",
        metavar="capability",
        help="Required capabilities for routing, e.g. coding testing",
    )
    _run.add_argument(
        "--workspace",
        default="",
        help="Working directory for the worker process (default: current directory)",
    )
    _run.add_argument(
        "--wait",
        action="store_true",
        help="Wait for completion, printing lifecycle transitions and the result",
    )
    _run.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-execution timeout in seconds (default: 600)",
    )
    _run.add_argument(
        "--retry",
        type=int,
        default=0,
        help="On failure, retry up to N times with a fresh execution (default: 0)",
    )
    _run.add_argument(
        "--switch-on-failure",
        action="store_true",
        help="On failure, route the retry to the next-best worker once",
    )
    _run.add_argument(
        "--context",
        default="",
        help="Compact context handoff for the worker (§22)",
    )
    _run.add_argument(
        "--model",
        default="",
        help="Model override for the codex/opencode harnesses",
    )
    _run.add_argument(
        "--provider",
        default="",
        help="Provider override for the codex harness",
    )

    _resume = workers_subparsers.add_parser(
        "resume",
        help="Resume or re-issue a previous worker execution",
        description=(
            "Recover a worker execution (§30): re-attach a still-in-flight "
            "execution whose harness supports a --resume/--continue flag "
            "(pi, codex, opencode, commandcode); mark a vanished execution "
            "FAILED (interrupted) and offer the task text to re-run via "
            "`hermes workers run`; and for harnesses without a resume flag, "
            "honestly re-issue the task as a fresh run, reporting the new "
            "execution id."
        ),
    )
    _resume.add_argument(
        "execution_id",
        help="The execution id to resume (from `hermes workers status` live/history output)",
    )

    _route = workers_subparsers.add_parser(
        "route",
        help="Route a task to the best worker using benchmark evidence",
        description=(
            "Route a task text to the best installed worker using benchmark "
            "evidence (§13): infer the benchmark category, score each "
            "installed worker by its stored pass/fail record (+2 per PASS in "
            "the category, −1 per FAIL, +0.5 per PASS elsewhere, +1 for a "
            "capability match), and print the ranked list plus the chosen "
            "worker — the same selection `hermes workers run` would make. "
            "Falls back to capability-hint routing when no evidence store "
            "exists."
        ),
    )
    _route.add_argument("task", help="The task text to route")
    _route.add_argument(
        "--capabilities",
        nargs="+",
        metavar="capability",
        help="Required capabilities for routing, e.g. coding testing",
    )
    _route.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the table",
    )

    _benchmark = workers_subparsers.add_parser(
        "benchmark",
        help="Run the worker benchmark suite (categories §24, metrics §25)",
        description=(
            "Run the benchmark suite against the installed workers to gather "
            "routing evidence (§13). Each selected category's self-verifying "
            "tasks run through worker_backend.run_task() in a temp workspace; "
            "per worker × task it records completion, correctness, tokens, "
            "latency, and failure mode. Default workers: pi, codex, opencode, "
            "commandcode (dsh is experimental and skipped unless requested)."
        ),
    )
    _benchmark.add_argument(
        "--worker",
        default="",
        help="Comma-separated worker types, e.g. pi,codex,opencode (default: all installed except dsh)",
    )
    _benchmark.add_argument(
        "--category",
        default="",
        help="Comma-separated categories to run — keys A-G or names, e.g. coding,recovery (default: all)",
    )
    _benchmark.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON results (routing evidence)",
    )
    _benchmark.add_argument(
        "--out",
        default="",
        metavar="FILE",
        help="Write structured JSON results to FILE",
    )
    _benchmark.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-task timeout in seconds (default: 120)",
    )

    workers_parser.set_defaults(func=cmd_workers)
