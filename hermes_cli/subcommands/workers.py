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
        help="List workers plus their Herdr integration status",
    )
    _status.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the table",
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

    workers_parser.set_defaults(func=cmd_workers)
