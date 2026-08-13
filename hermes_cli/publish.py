"""Publishing/export helpers: ADR, milestone, and journal documents.

``hermes publish`` turns a single markdown document (with optional YAML
frontmatter) into either a Docusaurus-ready markdown page (frontmatter
normalized, MDX-escaped body) or a printable PDF built with reportlab.

The two renderers are deliberately small and independent:

* :func:`render_docusaurus_markdown` — frontmatter normalization + the
  MDX-escaping conventions used by ``website/scripts/generate-skill-docs.py``.
* :func:`render_pdf` — a tiny markdown→reportlab flowable converter that
  covers the constructs ADRs/milestones/journals actually use (headings,
  paragraphs, fenced code, lists, blockquotes, tables). It does NOT shell out
  to a headless browser; that keeps the dependency footprint to reportlab
  alone and makes the output deterministic.

Input documents look like::

    ---
    title: "ADR-001: Replace the retry loop"
    type: adr
    status: accepted
    date: 2026-08-13
    ---

    ## Context

    ...
"""

from __future__ import annotations

import argparse
import io
import re
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import Any

import yaml

# Document kinds understood by ``hermes publish``. The type is normalized for
# both the frontmatter ``type`` field and the ``--type`` CLI flag.
DOC_TYPES = ("adr", "milestone", "journal")

# Frontmatter keys rendered first (in this order); anything else is appended
# alphabetically so extra metadata survives the round-trip.
_PREFERRED_KEYS = ("title", "type", "status", "date", "author")

_MDX_BOX_DRAWING_CHARS = frozenset("┌┐└┘─│═║╔╗╚╝╠╣╦╩╬├┤┬┴┼╭╮╯╰▶◀▲▼")


def parse_document(text: str) -> dict[str, Any]:
    """Split ``text`` into ``{"frontmatter": dict, "body": str}``.

    A leading ``---``-fenced YAML block becomes ``frontmatter`` (empty dict
    when absent). The remainder (with the frontmatter separator stripped) is
    the body.
    """
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise ValueError(f"malformed frontmatter: {exc}") from exc
            if not isinstance(fm, dict):
                raise ValueError("frontmatter must be a YAML mapping")
            return {"frontmatter": fm, "body": parts[2].lstrip("\n")}
    return {"frontmatter": {}, "body": text.lstrip("\n")}


def _yaml_scalar(value: Any) -> str:
    """Serialize a value as a YAML scalar (strings double-quoted)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = re.sub(r"\s+", " ", str(value)).strip()
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_frontmatter(frontmatter: dict[str, Any]) -> str:
    lines = ["---"]
    keys = [k for k in _PREFERRED_KEYS if k in frontmatter]
    keys += sorted(k for k in frontmatter if k not in _PREFERRED_KEYS)
    for key in keys:
        value = frontmatter[key]
        if isinstance(value, (list, dict)):
            # Nested metadata (tags, authors, ...) stays raw YAML.
            rendered = yaml.safe_dump(
                {key: value}, default_flow_style=False, sort_keys=False
            ).rstrip()
            lines.extend(rendered.splitlines())
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def mdx_escape_body(body: str) -> str:
    """Escape MDX-dangerous characters outside fenced code blocks.

    Mirrors the conventions in ``website/scripts/generate-skill-docs.py``:
    ``{``/``}`` become HTML entities, bare ``<tag>`` that aren't a
    whitelisted HTML tag get ``&lt;``-escaped, and fenced code blocks are left
    untouched (box-drawing diagrams get wrapped in ``ascii-guard-ignore``
    markers so the docs-site ASCII lint can't reject generated pages).
    """
    # Split into (text|code) segments by ``` and ~~~ fences.
    segments: list[tuple[str, str]] = []
    buf: list[str] = []
    mode = "text"
    fence_char = ""
    fence_len = 0
    for line in body.split("\n"):
        stripped = line.lstrip()
        if mode == "text":
            m = re.match(r"(`{3,}|~{3,})", stripped)
            if m:
                if buf:
                    segments.append(("text", "\n".join(buf)))
                    buf = []
                buf.append(line)
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                mode = "code"
            else:
                buf.append(line)
        else:
            buf.append(line)
            if stripped.startswith(fence_char * fence_len):
                segments.append(("code", "\n".join(buf)))
                buf = []
                mode = "text"
    if buf:
        segments.append((mode, "\n".join(buf)))

    def escape_text(text: str) -> str:
        out: list[str] = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "`":
                # Preserve inline code runs verbatim.
                j = i
                while j < len(text) and text[j] == "`":
                    j += 1
                run = text[i:j]
                end = text.find(run, j)
                if end == -1:
                    out.append(text[i:])
                    break
                out.append(text[i : end + len(run)])
                i = end + len(run)
            elif ch == "{":
                out.append("&#123;")
                i += 1
            elif ch == "}":
                out.append("&#125;")
                i += 1
            elif ch == "<":
                if text[i:].startswith("<!--"):
                    end = text.find("-->", i)
                    if end != -1:
                        out.append(text[i : end + 3])
                        i = end + 3
                        continue
                m = re.match(r"<(/?)([A-Za-z][A-Za-z0-9]*)([^<>]*)>", text[i:])
                if m and m.group(2).lower() in _SAFE_HTML_TAGS:
                    out.append(m.group(0))
                    i += len(m.group(0))
                    continue
                out.append("&lt;")
                i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)

    processed: list[str] = []
    for kind, content in segments:
        if kind == "code":
            processed.append(_wrap_ascii_art(content))
        else:
            processed.append(escape_text(content))
    return "\n".join(processed)


_SAFE_HTML_TAGS = frozenset(
    {
        "br", "hr", "img", "a", "b", "i", "em", "strong", "code", "kbd", "sup",
        "sub", "span", "div", "p", "ul", "ol", "li", "table", "thead", "tbody",
        "tr", "td", "th", "details", "summary", "blockquote", "pre", "mark",
        "small", "u", "s", "del", "ins", "h1", "h2", "h3", "h4", "h5", "h6",
    }
)


def _wrap_ascii_art(code_segment: str) -> str:
    """Wrap a fenced block containing box-drawing chars in ascii-guard markers."""
    if not any(ch in _MDX_BOX_DRAWING_CHARS for ch in code_segment):
        return code_segment
    return (
        "<!-- ascii-guard-ignore -->\n"
        f"{code_segment}\n"
        "<!-- ascii-guard-ignore-end -->"
    )


def render_docusaurus_markdown(text: str, *, doc_type: str) -> str:
    """Render a document as a Docusaurus-ready markdown page.

    Returns a string with normalized frontmatter (``title``/``type``/
    ``status``/``date``/``author`` first, any extra keys preserved) plus the
    MDX-escaped body. ``doc_type`` supplies the ``type`` key when the source
    frontmatter doesn't set one.
    """
    if doc_type not in DOC_TYPES:
        raise ValueError(f"unsupported document type: {doc_type}")
    parsed = parse_document(text)
    frontmatter = dict(parsed["frontmatter"])
    if "type" not in frontmatter:
        frontmatter["type"] = doc_type
    body = parsed["body"].strip()
    title = str(frontmatter.get("title") or "").strip()
    if not title:
        first_heading = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if first_heading:
            title = first_heading.group(1).strip()
    if title and "title" not in frontmatter:
        frontmatter["title"] = title
    return (
        _render_frontmatter(frontmatter)
        + "\n"
        + mdx_escape_body(body)
        + "\n"
    )


# ---------------------------------------------------------------------------
# PDF rendering (reportlab)
# ---------------------------------------------------------------------------


def render_pdf(text: str, *, doc_type: str = "journal", title: str | None = None) -> bytes:
    """Render a document to PDF bytes using reportlab.

    Imported lazily so ``hermes publish --format md`` works even on installs
    where reportlab is missing. Only the pure-Python reportlab layout API is
    used (``platypus``/``pdfbase``), so no C-extension codecs are required.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            Preformatted,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as exc:  # pragma: no cover - dependency missing
        raise RuntimeError(
            "PDF export requires reportlab; run `uv pip install reportlab` "
            "or reinstall hermes with the core dependencies"
        ) from exc

    parsed = parse_document(text)
    fm = parsed["frontmatter"]
    doc_title = (title or str(fm.get("title") or "")).strip() or "Untitled"
    effective_type = str(fm.get("type") or doc_type)

    page = A4
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=page,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=doc_title,
    )

    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("DocH1", parent=styles["Title"], fontSize=20, leading=24, spaceAfter=10)
    h2 = ParagraphStyle("DocH2", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)
    h3 = ParagraphStyle("DocH3", parent=styles["Heading3"], spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle(
        "DocBody",
        parent=styles["BodyText"],
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
        spaceAfter=6,
    )
    code = ParagraphStyle(
        "DocCode",
        parent=styles["Code"],
        fontSize=8,
        leading=10,
        backColor=colors.HexColor("#F6F6F6"),
        borderPadding=6,
        spaceBefore=6,
        spaceAfter=6,
    )
    meta = ParagraphStyle(
        "DocMeta",
        parent=body,
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#555555"),
    )
    quote = ParagraphStyle(
        "DocQuote",
        parent=body,
        leftIndent=14,
        textColor=colors.HexColor("#444444"),
    )

    story: list[Any] = []
    story.append(Paragraph(html_escape(doc_title), h1))

    # Metadata block: type/status/date/author when present.
    meta_rows: list[str] = []
    for key in ("type", "status", "date", "author"):
        value = fm.get(key)
        if value:
            meta_rows.append(
                f"<b>{html_escape(str(key).capitalize())}:</b> {html_escape(str(value))}"
            )
    if meta_rows:
        story.append(Paragraph(" &nbsp;&nbsp; ".join(meta_rows), meta))
        story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#999999")))
    story.append(Spacer(1, 6))

    story.extend(_markdown_to_flowables(parsed["body"], body, code, quote))

    doc.build(story)
    return buffer.getvalue()


def _markdown_to_flowables(body: str, body_style: Any, code_style: Any, quote_style: Any) -> list[Any]:
    """Convert markdown body lines into reportlab flowables.

    Handles the constructs ADR/milestone/journal documents actually use:
    ATX headings, paragraphs, fenced code blocks, ``-``/``*`` lists,
    blockquotes, and pipe tables. Everything else falls back to paragraph
    text with markdown punctuation stripped.
    """
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Preformatted, Spacer, Table, TableStyle

    flowables: list[Any] = []
    lines = body.splitlines()
    i = 0
    n = len(lines)
    table_buf: list[list[str]] = []
    heading_styles: dict[int, Any] = {}

    def flush_table() -> None:
        if not table_buf:
            return
        rows = [row[:] for row in table_buf]
        table_buf.clear()
        if len(rows) < 2:
            return
        data: list[list[str]] = []
        for row in rows:
            data.append([_inline_markup(re.sub(r"\*\*|`", "", cell).strip()) for cell in row])
        table = Table(data, hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#BBBBBB")),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        flowables.append(table)
        flowables.append(Spacer(1, 6))

    def flush_paragraph(buf: list[str]) -> None:
        if not buf:
            return
        flowables.append(Paragraph(_inline_markup(" ".join(buf).strip()), body_style))
        buf.clear()

    def heading_style(level: int) -> Any:
        if level not in heading_styles:
            heading_styles[level] = ParagraphStyle(
                f"DocH{level}",
                parent=body_style,
                fontSize=max(11, 15 - level),
                leading=max(13, 17 - level),
                spaceBefore=12,
                spaceAfter=5,
                textColor=colors.HexColor("#1a1a1a"),
            )
        return heading_styles[level]

    para_buf: list[str] = []
    code_buf: list[str] | None = None

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if code_buf is not None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                flowables.append(Preformatted("\n".join(code_buf).rstrip(), code_style))
                code_buf = None
            else:
                code_buf.append(line)
            i += 1
            continue

        if stripped.startswith("```") or stripped.startswith("~~~"):
            flush_paragraph(para_buf)
            flush_table()
            code_buf = []
            i += 1
            continue

        if not stripped:
            flush_paragraph(para_buf)
            flush_table()
            i += 1
            continue

        # Table row: starts and ends with a pipe.
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph(para_buf)
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Separator row like |---|---| — every cell is dashes (with
            # optional colons for alignment).
            if cells and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells):
                i += 1
                continue
            if len(cells) > 1:
                table_buf.append(cells)
                i += 1
                continue

        flush_table()

        if stripped.startswith("#"):
            flush_paragraph(para_buf)
            level = len(stripped) - len(stripped.lstrip("#"))
            heading_text = stripped[level:].strip()
            flowables.append(Paragraph(html_escape(heading_text), heading_style(level)))
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph(para_buf)
            quote_lines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            flowables.append(Paragraph(_inline_markup(" ".join(quote_lines).strip()), quote_style))
            flowables.append(Spacer(1, 4))
            continue

        m_list = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m_list:
            flush_paragraph(para_buf)
            flowables.append(Paragraph("&bull; " + _inline_markup(m_list.group(1)), body_style))
            i += 1
            continue

        m_num = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if m_num:
            flush_paragraph(para_buf)
            flowables.append(
                Paragraph(
                    f"<b>{m_num.group(1)}.</b> {_inline_markup(m_num.group(2))}",
                    body_style,
                )
            )
            i += 1
            continue

        para_buf.append(line)
        i += 1

    flush_paragraph(para_buf)
    flush_table()
    if code_buf is not None:
        flowables.append(Preformatted("\n".join(code_buf).rstrip(), code_style))
    return flowables


def _inline_markup(text: str) -> str:
    """Convert inline markdown to reportlab mini-markup (escaped first)."""
    text = html_escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", text)
    return text


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-_").lower()
    return slug or "document"


def publish_document(
    text: str,
    *,
    output_dir: Path,
    doc_type: str,
    stem: str,
    formats: tuple[str, ...],
    force: bool,
) -> list[Path]:
    """Write ``text`` as markdown/PDF into ``output_dir``; return written paths.

    ``formats`` is a subset of ``("md", "pdf")``. ``md`` emits the
    Docusaurus-ready page; ``pdf`` the reportlab render. When ``force`` is
    False an existing destination raises ``FileExistsError`` (matching
    ``session_export_md.write_session_markdown``).
    """
    output_dir = Path(output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    if "md" in formats:
        md_path = _out_path(output_dir, stem, "md")
        if md_path.exists() and not force:
            raise FileExistsError(str(md_path))
        md_path.write_text(render_docusaurus_markdown(text, doc_type=doc_type), encoding="utf-8")
        written.append(md_path)

    if "pdf" in formats:
        pdf_path = _out_path(output_dir, stem, "pdf")
        if pdf_path.exists() and not force:
            raise FileExistsError(str(pdf_path))
        pdf_path.write_bytes(render_pdf(text, doc_type=doc_type))
        written.append(pdf_path)

    return written


def _out_path(output_dir: Path, stem: str, ext: str) -> Path:
    return output_dir / f"{stem}.{ext}"


def cmd_publish(args: argparse.Namespace) -> int:
    """``hermes publish`` handler: read a document, write the requested formats."""
    import sys

    source = Path(args.document).expanduser()
    if not source.exists():
        print(f"error: document not found: {source}", file=sys.stderr)
        return 1
    text = source.read_text(encoding="utf-8")

    parsed = parse_document(text)
    fm = parsed["frontmatter"]
    doc_type = (getattr(args, "type", None) or str(fm.get("type") or "")).strip().lower()
    if not doc_type:
        doc_type = "journal"
    if doc_type not in DOC_TYPES:
        print(
            f"error: unsupported document type: {doc_type!r} "
            f"(expected one of: {', '.join(DOC_TYPES)})",
            file=sys.stderr,
        )
        return 1

    formats: list[str] = []
    requested = getattr(args, "formats", None) or []
    for fmt in requested:
        if fmt == "md":
            formats.append("md")
        elif fmt == "pdf":
            formats.append("pdf")
    if not formats:
        formats.append("md")
    formats = [f for f in ("md", "pdf") if f in formats]

    title = str(fm.get("title") or "").strip() or source.stem
    stem = _slug(title)
    if getattr(args, "out_stem", None):
        stem = _slug(args.out_stem)

    output_dir = Path(getattr(args, "output_dir", ".") or ".").expanduser()
    try:
        written = publish_document(
            text,
            output_dir=output_dir,
            doc_type=doc_type,
            stem=stem,
            formats=tuple(formats),
            force=bool(getattr(args, "force", False)),
        )
    except FileExistsError as exc:
        print(f"error: {exc} (use --force to overwrite)", file=sys.stderr)
        return 1

    for path in written:
        print(f"published: {path}")
    return 0
