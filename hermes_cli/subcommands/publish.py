"""``hermes publish`` subcommand parser.

Exports a single document (an ADR, a milestone, or a journal entry) as a
Docusaurus-ready markdown page and/or a printable reportlab PDF. See
``hermes_cli/publish.py`` for the renderers.
"""

from __future__ import annotations

import argparse
from typing import Callable

from hermes_cli.publish import DOC_TYPES


def build_publish_parser(subparsers, *, cmd_publish: Callable) -> None:
    """Attach the ``publish`` subcommand to ``subparsers``."""
    parser = subparsers.add_parser(
        "publish",
        help="Export a document (ADR / milestone / journal) as Docusaurus markdown or PDF",
        description=(
            "Publishing glue: turn a single markdown document with YAML "
            "frontmatter into a Docusaurus-ready page (frontmatter normalized, "
            "MDX-escaped body) and/or a printable PDF rendered with reportlab. "
            "The document kind is taken from the frontmatter `type` key or the "
            "`--type` flag."
        ),
    )
    parser.add_argument(
        "document",
        help="Path to the markdown document to publish (e.g. docs/adr-001.md)",
    )
    parser.add_argument(
        "--type",
        choices=DOC_TYPES,
        default=None,
        help=f"Document kind (default: from frontmatter, else {DOC_TYPES[2]})",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Directory to write the exported files into (default: current directory)",
    )
    parser.add_argument(
        "--out-stem",
        default=None,
        help="Output filename stem (default: slugified document title)",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        nargs="+",
        choices=("md", "pdf"),
        default=None,
        help="Formats to write: md, pdf, or both (default: md)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing output files",
    )
    parser.set_defaults(func=cmd_publish)
