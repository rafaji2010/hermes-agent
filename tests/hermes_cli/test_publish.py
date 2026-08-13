"""Tests for ``hermes publish`` — the publishing glue (Docusaurus markdown + PDF).

Covers the document parsing/frontmatter normalization, the MDX-escaped
markdown renderer (including the box-drawing ascii-guard wrap reused from the
skill-docs generator conventions), and the reportlab PDF renderer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.publish import (
    DOC_TYPES,
    cmd_publish,
    mdx_escape_body,
    parse_document,
    publish_document,
    render_docusaurus_markdown,
    render_pdf,
)

ADR = """\
---
title: "ADR-001: Replace the retry loop"
status: accepted
date: 2026-08-13
---

## Context

The retry loop in `agent/retry.py` double-counts attempts.

{request_id} is logged on every attempt.
"""


def test_parse_document_splits_frontmatter_and_body() -> None:
    parsed = parse_document(ADR)
    assert parsed["frontmatter"]["title"] == "ADR-001: Replace the retry loop"
    assert parsed["frontmatter"]["status"] == "accepted"
    assert "## Context" in parsed["body"]
    assert parsed["body"].startswith("## Context")


def test_parse_document_without_frontmatter() -> None:
    parsed = parse_document("# Just a heading\n\nBody text.\n")
    assert parsed["frontmatter"] == {}
    assert parsed["body"].startswith("# Just a heading")


def test_parse_document_malformed_frontmatter_raises() -> None:
    with pytest.raises(ValueError, match="malformed frontmatter"):
        parse_document("---\n: : :\n---\nbody")


def test_render_docusaurus_markdown_adds_type_and_title() -> None:
    rendered = render_docusaurus_markdown(ADR, doc_type="adr")
    assert rendered.startswith("---\n")
    assert 'title: "ADR-001: Replace the retry loop"' in rendered
    assert 'type: "adr"' in rendered
    assert 'status: "accepted"' in rendered
    assert "## Context" in rendered


def test_render_docusaurus_markdown_escapes_braces_outside_code() -> None:
    rendered = render_docusaurus_markdown(ADR, doc_type="adr")
    # MDX would parse {request_id} as a JSX expression — must be escaped.
    assert "{request_id}" not in rendered
    assert "&#123;request_id&#125;" in rendered
    # The backtick inline code survives untouched.
    assert "`agent/retry.py`" in rendered


def test_render_docusaurus_markdown_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unsupported document type"):
        render_docusaurus_markdown(ADR, doc_type="novel")


def test_mdx_escape_body_wraps_box_drawing_code() -> None:
    body = "Before.\n\n```\n┌─────────┐\n│ diagram │\n└─────────┘\n```\n\nAfter."
    result = mdx_escape_body(body)
    assert "<!-- ascii-guard-ignore -->" in result
    assert "<!-- ascii-guard-ignore-end -->" in result
    assert "┌─────────┐" in result


def test_mdx_escape_body_leaves_plain_code_alone() -> None:
    body = "```bash\npip install foo\n```"
    result = mdx_escape_body(body)
    assert "ascii-guard" not in result
    assert "pip install foo" in result


def test_render_pdf_produces_valid_pdf() -> None:
    pdf_bytes = render_pdf(ADR, doc_type="adr")
    assert pdf_bytes.startswith(b"%PDF")
    assert b"%%EOF" in pdf_bytes
    assert len(pdf_bytes) > 500


def test_publish_document_writes_md_and_pdf(tmp_path: Path) -> None:
    written = publish_document(
        ADR,
        output_dir=tmp_path,
        doc_type="adr",
        stem="adr-001-replace-the-retry-loop",
        formats=("md", "pdf"),
        force=False,
    )
    paths = {p.name: p for p in written}
    assert set(paths) == {"adr-001-replace-the-retry-loop.md", "adr-001-replace-the-retry-loop.pdf"}
    md = paths["adr-001-replace-the-retry-loop.md"].read_text(encoding="utf-8")
    assert md.startswith("---")
    assert "## Context" in md
    assert paths["adr-001-replace-the-retry-loop.pdf"].read_bytes().startswith(b"%PDF")


def test_publish_document_refuses_overwrite_without_force(tmp_path: Path) -> None:
    publish_document(
        ADR,
        output_dir=tmp_path,
        doc_type="adr",
        stem="adr-001",
        formats=("md",),
        force=False,
    )
    with pytest.raises(FileExistsError):
        publish_document(
            ADR,
            output_dir=tmp_path,
            doc_type="adr",
            stem="adr-001",
            formats=("md",),
            force=False,
        )
    # force=True overwrites.
    publish_document(
        ADR,
        output_dir=tmp_path,
        doc_type="adr",
        stem="adr-001",
        formats=("md",),
        force=True,
    )


def test_cmd_publish_missing_document_returns_1(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    class _Args:
        document = str(tmp_path / "nope.md")
        type = None
        output_dir = str(tmp_path)
        out_stem = None
        formats = ["md"]
        force = False

    rc = cmd_publish(_Args())
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_cmd_publish_writes_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    source = tmp_path / "adr-001.md"
    source.write_text(ADR, encoding="utf-8")

    class _Args:
        document = str(source)
        type = "adr"
        output_dir = str(tmp_path)
        out_stem = None
        formats = ["md", "pdf"]
        force = False

    rc = cmd_publish(_Args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "published:" in out
    assert (tmp_path / "adr-001-replace-the-retry-loop.md").exists()
    assert (tmp_path / "adr-001-replace-the-retry-loop.pdf").exists()


def test_doc_types_are_stable() -> None:
    # The CLI help and validation both key off this tuple.
    assert DOC_TYPES == ("adr", "milestone", "journal")
