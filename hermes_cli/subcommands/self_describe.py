"""``hermes self-describe`` subcommand parser.

Follows the pattern of ``hermes_cli/subcommands/dump.py``: parser built
here, handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_self_describe_parser(subparsers, *, cmd_self_describe: Callable) -> None:
    """Attach the ``self-describe`` subcommand to ``subparsers``."""
    self_desc_parser = subparsers.add_parser(
        "self-describe",
        help="Emit a machine-readable description of this Hermes instance",
        description=(
            "Describe the running Hermes installation as JSON: version / build "
            "identity, active model and provider, per-platform enabled toolsets "
            "and the tools they ship, installed plugins, skills, and a redacted "
            "config summary. Secrets are never emitted raw — API-key status is "
            "reported as set/not set, and ``--show-keys`` reveals only a "
            "head/tail-masked form. Suitable for other tooling to consume."
        ),
    )
    self_desc_parser.add_argument(
        "--platform",
        default="cli",
        help="Platform whose toolset configuration to report (default: cli)",
    )
    self_desc_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default output format; accepted for explicitness)",
    )
    self_desc_parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact single-line JSON (no pretty-printing)",
    )
    self_desc_parser.add_argument(
        "--show-keys",
        action="store_true",
        help="Show redacted API key prefixes (first/last 4 chars) instead of "
        "just set/not set",
    )
    self_desc_parser.set_defaults(func=cmd_self_describe)
