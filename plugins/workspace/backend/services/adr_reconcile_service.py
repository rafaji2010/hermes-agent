"""ADR Reconciliation Service.

The canonical authority for ADR CONTENT is the Markdown file inside the
resolved Hermes Project repository (default ``docs/adr/``).  ``workspace.db``
keeps an INDEX/PROJECTION — metadata, a derived markdown cache for
search/API performance, and reconciliation bookkeeping — never a competing
copy of truth.

This service:

* discovers canonical ADR files under each registered repository of a
  workspace (project-relative paths only);
* parses YAML frontmatter + the H1 title;
* classifies drift between canonical files and the DB projection;
* refreshes the projection transactionally (files win; conflicts stay
  visible — never "latest wins");
* materializes legacy DB-only ADRs into canonical files through an
  explicit, previewable operation;
* updates canonical file content through an explicit, atomic write.

All filesystem access is confined to the registered repository roots via
a per-repo ``PathSandbox``.  The service never touches files outside the
resolved workspace's repositories.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import yaml

from ..models import (
    ADR,
    ADRCanonicalUpdateError,
    ADRMaterializeResult,
    ADRReconcileError,
    ADRReconcileStatus,
    ADRReconcileSummary,
    ADRNotFoundError,
    DuplicateSlugError,
    VALID_ADR_STATUSES,
)
from ..storage import AbstractStorage
from .adr_service import _generate_slug

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter
    from ..security.sandbox import PathSandbox

_log = logging.getLogger("hermes.plugins.workspace.adr_reconcile")

# Canonical ADR directories under a repository root (project-relative).
DEFAULT_ADR_DIRS = ("docs/adr",)

# Optional ordering prefix: NNNN-slug.md — the prefix is a hint, NOT identity.
_SEQ_PREFIX_RE = re.compile(r"^\d{4}-")

_FRONTMATTER_FENCE = "---\n"


def _utc_now() -> str:
    """Profile-safe UTC timestamp following repo convention."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_from_stem(stem: str) -> str:
    """Canonical identity from a file stem.

    ``0001-use-sqlite`` → ``use-sqlite`` (the numeric prefix is an ordering
    hint, not identity).  Bare stems pass through unchanged.
    """
    s = stem.strip()
    m = _SEQ_PREFIX_RE.match(s)
    if m:
        s = s[m.end():]
    return s or stem.strip()


class _ParsedADR:
    """Parsed representation of a canonical ADR file."""

    __slots__ = ("title", "status", "category", "tags", "markdown", "raw")

    def __init__(self, title, status, category, tags, markdown, raw):
        self.title = title
        self.status = status
        self.category = category
        self.tags = tags
        self.markdown = markdown
        self.raw = raw


class _MalformedADR(Exception):
    """Raised when a canonical ADR file cannot be parsed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def parse_adr_file(text: str) -> _ParsedADR:
    """Parse a canonical ADR file.

    Recognizes an optional YAML frontmatter block (``---\\n...\\n---\\n``)
    carrying ``status`` / ``category`` / ``tags``, then a required ``# H1``
    title.  Raises ``_MalformedADR`` when the file is not a valid ADR.
    """
    if not text or not text.strip():
        raise _MalformedADR("empty file")

    meta: Dict[str, object] = {}
    rest = text
    if text.startswith(_FRONTMATTER_FENCE):
        end = text.find("\n---", 4)
        if end == -1:
            raise _MalformedADR("unterminated frontmatter")
        fence_end = end + 4
        raw_meta = text[4:end]
        rest = text[fence_end:].lstrip("\n")
        try:
            parsed = yaml.safe_load(raw_meta) or {}
            if not isinstance(parsed, dict):
                raise _MalformedADR("frontmatter is not a mapping")
            meta = parsed
        except yaml.YAMLError as exc:
            raise _MalformedADR(f"invalid frontmatter: {exc}") from exc

    title: Optional[str] = None
    for line in rest.splitlines():
        line = line.rstrip()
        if line.startswith("# "):
            title = line[2:].strip()
            break
    if not title:
        raise _MalformedADR("missing '# Title' heading")

    status = str(meta.get("status") or "proposed").strip().lower()
    if status not in VALID_ADR_STATUSES:
        raise _MalformedADR(f"invalid frontmatter status: {status!r}")

    category = str(meta.get("category") or "").strip()
    raw_tags = meta.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    tags = [
        str(t).strip().lower()
        for t in raw_tags
        if isinstance(t, (str, int, float)) and str(t).strip()
    ]

    return _ParsedADR(
        title=title,
        status=status,
        category=category,
        tags=tags,
        markdown=rest,
        raw=text,
    )


def build_canonical_markdown(adr: ADR) -> str:
    """Build the canonical file content for a legacy ADR materialization.

    Adds YAML frontmatter (status/category/tags + provenance marker) when
    the stored markdown does not already start with a frontmatter block.
    """
    body = adr.markdown or ""
    if body.startswith(_FRONTMATTER_FENCE):
        return body
    lines = [_FRONTMATTER_FENCE, f"status: {adr.status}"]
    if adr.category:
        lines.append(f"category: {adr.category}")
    if adr.tags:
        lines.append("tags:")
        for t in adr.tags:
            lines.append(f"  - {t}")
    lines.append("source: workspace_db  # legacy Workspace record")
    lines.append(_FRONTMATTER_FENCE)
    return "\n".join(lines) + ("\n" if body and not body.endswith("\n") else "") + body


class ADRReconcileService:
    """Reconcile canonical ADR files with the Workspace ADR projection."""

    def __init__(
        self,
        storage: AbstractStorage,
        authz: "AuthorizationMiddleware | None" = None,
        limits: "ResourceLimiter | None" = None,
        sandbox: "PathSandbox | None" = None,
        adr_dirs: Tuple[str, ...] = DEFAULT_ADR_DIRS,
    ):
        self._storage = storage
        self._authz = authz
        self._limits = limits
        self._sandbox = sandbox
        self._adr_dirs = adr_dirs or DEFAULT_ADR_DIRS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reconcile(
        self,
        workspace_id: str,
        *,
        dry_run: bool = False,
        session_id: str = "",
    ) -> ADRReconcileSummary:
        """Run reconciliation for a workspace.

        ``dry_run`` previews every transition without writing anything.
        Real mode: canonical files win for the projection; conflicts and
        legacy rows are never touched destructively.
        """
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise ADRNotFoundError(workspace_id)

        summary = ADRReconcileSummary(
            workspace_id=workspace_id,
            project_id=ws.hermes_project_id or "",
            dry_run=dry_run,
        )
        seen_identities: Dict[str, str] = {}

        with self._storage.transaction():
            for repo in self._storage.list_repositories(workspace_id):
                self._reconcile_repository(
                    workspace_id, repo, summary, seen_identities, dry_run
                )
            self._mark_missing_files(workspace_id, summary, dry_run)
        self._finalize_counts(summary)

        if self._authz and not dry_run:
            self._authz.audit.log(
                action="adr.reconcile.run",
                status="ALLOW",
                resource_type="workspace",
                resource_id=workspace_id,
                details={
                    "project_id": ws.hermes_project_id or "",
                    "dry_run": dry_run,
                    "scanned_files": summary.scanned_files,
                    "indexed": summary.indexed,
                    "conflict": summary.conflict,
                    "invalid": summary.invalid,
                },
                session_id=session_id,
            )
        return summary

    def status(
        self, workspace_id: str, adr_id: Optional[str] = None
    ) -> List[ADRReconcileStatus]:
        """Return live reconciliation status for a workspace's ADRs.

        Reads the filesystem where needed but never writes.
        """
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise ADRNotFoundError(workspace_id)

        adrs = self._storage.list_adrs(workspace_id)
        if adr_id is not None:
            adrs = [a for a in adrs if a.id == adr_id]

        root_map = self._repo_roots(workspace_id)
        statuses: List[ADRReconcileStatus] = []
        for adr in adrs:
            file_exists = self._file_state(adr, root_map)["exists"]
            state = self._live_state(adr, root_map, file_exists)
            statuses.append(self._to_status(adr, state, file_exists))
        statuses.sort(key=lambda s: (s.canonical_path, s.id))
        return statuses

    def materialize(
        self, adr_id: str, *, dry_run: bool = True, session_id: str = ""
    ) -> ADRMaterializeResult:
        """Materialize a legacy DB-only ADR into a canonical file.

        ``dry_run`` (default) returns a preview without writing.  Real mode
        writes the canonical file atomically (frontmatter + provenance),
        then promotes the DB record to the ``git_file`` projection.
        """
        adr = self._storage.get_adr(adr_id)
        if adr is None:
            raise ADRNotFoundError(adr_id)
        if adr.source != "workspace_db":
            raise ADRReconcileError(
                f"ADR {adr_id} is already canonical (source={adr.source})",
                code="ADR_ALREADY_CANONICAL",
            )

        repo = self._repo_for_materialize(adr)
        if repo is None:
            return ADRMaterializeResult(
                id=adr_id, status="no_repository",
                message="Workspace has no registered repository to materialize into.",
            )

        target = self._materialization_target(repo, adr.slug)
        if target is None:
            return ADRMaterializeResult(
                id=adr_id, status="invalid",
                message=f"Could not derive a safe materialization target for slug {adr.slug!r}.",
            )

        rel_path = str(target.relative_to(Path(repo.path).resolve()))

        if target.exists() or self._identity_collision(repo, adr.slug):
            return ADRMaterializeResult(
                id=adr_id, status="target_exists",
                target_path=rel_path,
                message="A canonical file with this ADR identity already exists; resolve the conflict first.",
            )

        if dry_run:
            return ADRMaterializeResult(
                id=adr_id, status="preview", target_path=rel_path,
                message="Preview — no file written.",
            )

        self._guard_write("adr.reconcile.write", adr)
        content = build_canonical_markdown(adr)
        self._atomic_write(target, content, repo)

        content_hash = _sha256(content.encode("utf-8"))
        with self._storage.transaction():
            self._storage.update_adr(
                adr_id,
                markdown=content,
                tags=adr.tags,
            )
            self._storage.update_adr_reconcile_meta(
                adr_id,
                canonical_path=rel_path,
                content_hash=content_hash,
                reconcile_state="synced",
                source="git_file",
                last_indexed=_utc_now(),
                last_error="",
            )

        if self._authz:
            self._authz.audit.log(
                action="adr.materialize",
                status="ALLOW",
                resource_type="adr",
                resource_id=adr_id,
                details={
                    "workspace_id": adr.workspace_id,
                    "target_path": rel_path,
                    "source_before": "workspace_db",
                    "dry_run": dry_run,
                },
                session_id=session_id,
            )
        _log.info("[Workspace Plugin] ADR %s materialized -> %s", adr_id, rel_path)
        return ADRMaterializeResult(
            id=adr_id, status="materialized", target_path=rel_path,
            message=f"Materialized to {rel_path}.",
        )

    def update_file(
        self, adr_id: str, markdown: str, *, dry_run: bool = False,
        session_id: str = "",
    ) -> ADRMaterializeResult:
        """Update the canonical file content of a git_file ADR.

        Writes the file atomically, then refreshes the projection.
        """
        adr = self._storage.get_adr(adr_id)
        if adr is None:
            raise ADRNotFoundError(adr_id)
        if adr.source != "git_file" or not adr.canonical_path:
            raise ADRCanonicalUpdateError(adr_id)

        repo = self._repo_for_canonical(adr)
        if repo is None:
            raise ADRReconcileError(
                f"ADR {adr_id} has no repository for its canonical file",
                code="ADR_NO_REPOSITORY",
            )

        root = Path(repo.path).resolve()
        rel = Path(adr.canonical_path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ADRReconcileError(
                f"Unsafe canonical path: {adr.canonical_path}",
                code="ADR_UNSAFE_PATH",
            )
        target = (root / rel).resolve()
        self._validate_in_root(target, root, operation="write")
        if not target.is_file():
            raise ADRReconcileError(
                f"Canonical file missing: {adr.canonical_path}",
                code="ADR_MISSING_FILE",
            )

        rel_path = str(rel)
        if dry_run:
            return ADRMaterializeResult(
                id=adr_id, status="preview", target_path=rel_path,
                message="Preview — no file written.",
            )

        self._guard_write("adr.reconcile.write", adr)
        self._atomic_write(target, markdown, repo)

        content_hash = _sha256(markdown.encode("utf-8"))
        with self._storage.transaction():
            self._storage.update_adr(adr_id, markdown=markdown)
            self._storage.update_adr_reconcile_meta(
                adr_id,
                content_hash=content_hash,
                reconcile_state="synced",
                last_indexed=_utc_now(),
                last_error="",
            )

        if self._authz:
            self._authz.audit.log(
                action="adr.file_updated",
                status="ALLOW",
                resource_type="adr",
                resource_id=adr_id,
                details={
                    "workspace_id": adr.workspace_id,
                    "canonical_path": rel_path,
                    "dry_run": dry_run,
                },
                session_id=session_id,
            )
        _log.info("[Workspace Plugin] ADR %s canonical file updated: %s", adr_id, rel_path)
        return ADRMaterializeResult(
            id=adr_id, status="updated", target_path=rel_path,
            message=f"Canonical file updated: {rel_path}.",
        )

    # ------------------------------------------------------------------
    # Discovery / classification
    # ------------------------------------------------------------------

    def _reconcile_repository(
        self, workspace_id, repo, summary, seen_identities, dry_run
    ):
        root = Path(repo.path).resolve()
        discovered = self._discover(root)
        summary.scanned_files += len(discovered)

        for rel_str, abs_path in discovered:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                self._mark_row_invalid_by_path(workspace_id, rel_str, "unreadable", dry_run)
                continue

            try:
                parsed = parse_adr_file(text)
            except _MalformedADR as exc:
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                self._mark_row_invalid_by_path(workspace_id, rel_str, exc.reason, dry_run)
                continue

            identity = _identity_from_stem(abs_path.stem)
            content_hash = _sha256(text.encode("utf-8"))

            if identity in seen_identities:
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                self._mark_row_invalid_by_path(
                    workspace_id, rel_str, "duplicate_identity", dry_run
                )
                continue
            seen_identities[identity] = rel_str

            self._classify_file(
                workspace_id, repo, rel_str, abs_path,
                parsed, identity, content_hash, summary, dry_run,
            )

    def _classify_file(
        self, workspace_id, repo, rel_str, abs_path,
        parsed, identity, content_hash, summary, dry_run,
    ):
        row = self._storage.find_adr_by_canonical_path(workspace_id, rel_str)

        if row is not None and row.source == "git_file":
            if row.content_hash == content_hash:
                return  # synced — counted in the final pass
            if row.updated_at > row.last_indexed and row.last_indexed:
                if dry_run:
                    summary.conflict += 1
                else:
                    self._storage.update_adr_reconcile_meta(
                        row.id, reconcile_state="conflict",
                        last_error="file_and_db_changed",
                    )
                return
            summary.file_changed += 1
            if not dry_run:
                # The file is the authority: refresh ALL projection metadata,
                # including the title/status/category derived from it.  The
                # slug stays the stable canonical identity (not regenerated).
                self._storage.update_adr(
                    row.id,
                    title=parsed.title,
                    status=parsed.status,
                    category=parsed.category,
                    markdown=parsed.raw,
                    tags=parsed.tags,
                )
                self._storage.update_adr_reconcile_meta(
                    row.id, content_hash=content_hash,
                    reconcile_state="synced", last_indexed=_utc_now(),
                    last_error="",
                )
            return

        if row is not None:
            # Projection row exists but not in the git_file state (legacy
            # promoted by hand, or invalid) — treat as conflict: visible.
            if dry_run:
                summary.conflict += 1
            else:
                self._storage.update_adr_reconcile_meta(
                    row.id, reconcile_state="conflict",
                    last_error="projection_state_mismatch",
                )
            return

        linked_by_slug = self._storage.get_adr_by_slug(workspace_id, identity)
        if linked_by_slug is not None and not linked_by_slug.canonical_path:
            cached = (linked_by_slug.markdown or "").strip()
            if not cached or cached == parsed.markdown.strip():
                summary.file_changed += 1  # would promote legacy -> synced
                if not dry_run:
                    self._storage.update_adr(
                        linked_by_slug.id, markdown=parsed.raw, tags=parsed.tags
                    )
                    self._storage.update_adr_reconcile_meta(
                        linked_by_slug.id,
                        canonical_path=rel_str,
                        content_hash=content_hash,
                        reconcile_state="synced",
                        source="git_file",
                        last_indexed=_utc_now(),
                        last_error="",
                    )
                return
            if dry_run:
                summary.conflict += 1
            else:
                self._storage.update_adr_reconcile_meta(
                    linked_by_slug.id, reconcile_state="conflict",
                    last_error="legacy_db_and_file_differ",
                )
            return

        if linked_by_slug is not None:
            # A projection row with the SAME identity is already linked to a
            # canonical file.  If its old file is gone, this is a RENAME —
            # re-link the projection to the new location (the file is the
            # authority; nothing on disk is overwritten).  If the old file
            # still exists, it is a genuine duplicate identity → invalid.
            old_exists = self._file_state(
                linked_by_slug, self._repo_roots(workspace_id)
            )["exists"]
            if not old_exists:
                summary.file_changed += 1
                if not dry_run:
                    self._storage.update_adr(
                        linked_by_slug.id,
                        title=parsed.title,
                        status=parsed.status,
                        category=parsed.category,
                        markdown=parsed.raw,
                        tags=parsed.tags,
                    )
                    self._storage.update_adr_reconcile_meta(
                        linked_by_slug.id,
                        canonical_path=rel_str,
                        content_hash=content_hash,
                        reconcile_state="synced",
                        last_indexed=_utc_now(),
                        last_error="",
                    )
                return
            summary.invalid += 1
            summary.invalid_paths.append(rel_str)
            return

        # No projection row → index the canonical file (new projection row).
        summary.indexed += 1
        if dry_run:
            return
        try:
            with self._storage.transaction():
                self._storage.create_adr(
                    workspace_id=workspace_id,
                    repository_id=repo.id,
                    title=parsed.title,
                    slug=identity,
                    status=parsed.status,
                    category=parsed.category,
                    markdown=parsed.raw,
                    tags=parsed.tags,
                )
        except DuplicateSlugError:
            summary.indexed -= 1
            summary.invalid += 1
            summary.invalid_paths.append(rel_str)
            return
        # The freshly-created row has no canonical_path yet — look it up by
        # its stable identity slug (slug == identity for canonical files).
        row = self._storage.get_adr_by_slug(workspace_id, identity)
        if row is not None:
            self._storage.update_adr_reconcile_meta(
                row.id, canonical_path=rel_str, content_hash=content_hash,
                reconcile_state="synced", source="git_file",
                last_indexed=_utc_now(), last_error="",
            )

    def _mark_missing_files(self, workspace_id, summary, dry_run):
        root_map = self._repo_roots(workspace_id)
        for adr in self._storage.list_adrs(workspace_id):
            if adr.source != "git_file" or not adr.canonical_path:
                continue
            if self._file_state(adr, root_map)["exists"]:
                continue
            if dry_run:
                summary.missing_file += 1
            else:
                self._storage.update_adr_reconcile_meta(
                    adr.id, reconcile_state="missing_file",
                    last_error="canonical_file_missing",
                )

    def _finalize_counts(self, summary) -> None:
        """Count stored projection states after the scan.

        Scan-phase counters (``indexed`` / ``file_changed`` / ``invalid`` /
        ``invalid_paths`` / ``scanned_files``) are preserved; stored-row
        states (synced / db_legacy / missing_file / conflict / invalid)
        are counted here so real-mode transitions land exactly once.
        """
        root_map = self._repo_roots(summary.workspace_id)
        summary.synced = 0
        summary.db_legacy = 0
        summary.missing_file = 0
        summary.conflict = 0
        for adr in self._storage.list_adrs(summary.workspace_id):
            state = adr.reconcile_state or "db_legacy"
            if state == "synced":
                summary.synced += 1
            elif state == "db_legacy":
                summary.db_legacy += 1
            elif state == "missing_file":
                summary.missing_file += 1
            elif state == "conflict":
                summary.conflict += 1
            elif state == "invalid":
                # Files reported invalid by the scan are already counted;
                # only add rows invalidated by an earlier run.
                if adr.canonical_path not in summary.invalid_paths:
                    summary.invalid += 1
            file_exists = self._file_state(adr, root_map)["exists"]
            summary.statuses.append(self._to_status(adr, state, file_exists))
        summary.statuses.sort(key=lambda s: (s.canonical_path, s.id))

    def _mark_row_invalid_by_path(self, workspace_id, rel_str, reason, dry_run):
        row = self._storage.find_adr_by_canonical_path(workspace_id, rel_str)
        if row is None:
            return
        if not dry_run and row.reconcile_state != "invalid":
            self._storage.update_adr_reconcile_meta(
                row.id, reconcile_state="invalid", last_error=reason
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _repo_roots(self, workspace_id) -> Dict[str, Path]:
        roots: Dict[str, Path] = {}
        for repo in self._storage.list_repositories(workspace_id):
            try:
                roots[repo.id] = Path(repo.path).resolve()
            except (OSError, RuntimeError):
                continue
        return roots

    def _file_state(self, adr: ADR, root_map: Dict[str, Path]) -> Dict[str, object]:
        if adr.source != "git_file" or not adr.canonical_path:
            return {"exists": False, "hash": ""}
        root = root_map.get(adr.repository_id or "")
        if root is None and root_map:
            root = next(iter(root_map.values()))
        if root is None:
            return {"exists": False, "hash": ""}
        try:
            target = (root / adr.canonical_path).resolve()
            if not target.is_file():
                return {"exists": False, "hash": ""}
            return {"exists": True, "hash": _sha256(target.read_bytes())}
        except (OSError, RuntimeError):
            return {"exists": False, "hash": ""}

    def _live_state(self, adr: ADR, root_map: Dict[str, Path], file_exists: bool) -> str:
        if adr.source == "workspace_db":
            return "db_legacy"
        if not adr.canonical_path:
            return "db_legacy"
        if not file_exists:
            return "missing_file"
        st = self._file_state(adr, root_map)
        if st["hash"] and st["hash"] == adr.content_hash:
            return "synced"
        if adr.updated_at > adr.last_indexed and adr.last_indexed:
            return "conflict"
        return "file_changed"

    def _to_status(self, adr: ADR, state: str, file_exists: bool) -> ADRReconcileStatus:
        canonical_id = ""
        if adr.canonical_path:
            canonical_id = _identity_from_stem(Path(adr.canonical_path).stem)
        return ADRReconcileStatus(
            id=adr.id,
            workspace_id=adr.workspace_id,
            title=adr.title,
            slug=adr.slug,
            status=adr.status,
            reconcile_state=state,
            source=adr.source,
            canonical_path=adr.canonical_path,
            canonical_id=canonical_id,
            content_hash=adr.content_hash,
            last_indexed=adr.last_indexed,
            last_error=adr.last_error,
            file_exists=file_exists,
        )

    def _discover(self, root: Path) -> List[Tuple[str, Path]]:
        """Discover canonical ADR files under ``root`` (project-relative).

        Out-of-root / symlink-escape paths are SKIPPED, never followed —
        a single hostile or broken entry must not abort reconciliation.
        """
        found: List[Tuple[str, Path]] = []
        for adr_dir in self._adr_dirs:
            try:
                base = (root / adr_dir).resolve()
                self._validate_in_root(base, root, operation="read")
            except ADRReconcileError:
                continue
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fname in sorted(filenames):
                    if not fname.endswith(".md"):
                        continue
                    abs_path = Path(dirpath) / fname
                    try:
                        abs_resolved = abs_path.resolve()
                    except (OSError, RuntimeError):
                        continue
                    try:
                        self._validate_in_root(abs_resolved, root, operation="read")
                    except ADRReconcileError:
                        continue  # escape/symlink — skip, never follow
                    rel = abs_resolved.relative_to(root)
                    found.append((str(rel), abs_resolved))
        return found

    def _repo_for_materialize(self, adr: ADR):
        if adr.repository_id:
            repo = self._storage.get_repository(adr.repository_id)
            if repo is not None:
                return repo
        repos = self._storage.list_repositories(adr.workspace_id)
        return repos[0] if repos else None

    def _repo_for_canonical(self, adr: ADR):
        if adr.repository_id:
            return self._storage.get_repository(adr.repository_id)
        repos = self._storage.list_repositories(adr.workspace_id)
        return repos[0] if repos else None

    def _materialization_target(self, repo, slug: str) -> Optional[Path]:
        root = Path(repo.path).resolve()
        safe_slug = _generate_slug(slug)
        if not safe_slug:
            return None
        adr_dir = root / self._adr_dirs[0]
        next_seq = self._next_sequence(adr_dir)
        target = adr_dir / f"{next_seq:04d}-{safe_slug}.md"
        self._validate_in_root(target, root, operation="write")
        return target

    def _identity_collision(self, repo, slug: str) -> bool:
        """True when any canonical file in the ADR dir shares this identity.

        Prevents materialization from silently creating a second canonical
        file for a slug that already exists as a file.
        """
        root = Path(repo.path).resolve()
        adr_dir = root / self._adr_dirs[0]
        if not adr_dir.is_dir():
            return False
        target_identity = _identity_from_stem(_generate_slug(slug))
        try:
            for p in adr_dir.glob("*.md"):
                if _identity_from_stem(p.stem) == target_identity:
                    return True
        except OSError:
            return False
        return False

    def _next_sequence(self, adr_dir: Path) -> int:
        if not adr_dir.is_dir():
            return 1
        highest = 0
        try:
            for p in adr_dir.glob("*.md"):
                m = _SEQ_PREFIX_RE.match(p.stem)
                if m:
                    try:
                        highest = max(highest, int(p.stem[:4]))
                    except ValueError:
                        continue
        except OSError:
            pass
        return highest + 1

    def _validate_in_root(self, path: Path, root: Path, *, operation: str) -> None:
        """Validate that ``path`` stays inside ``root`` (sandbox + prefix)."""
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as exc:
            raise ADRReconcileError(
                f"Path resolution failed: {path}", code="ADR_SANDBOX_DENIED"
            ) from exc
        if resolved != root and root not in resolved.parents:
            raise ADRReconcileError(
                f"Path escapes the repository root: {resolved}",
                code="ADR_SANDBOX_DENIED",
            )
        if self._sandbox is not None:
            result = self._sandbox.validate_path(str(resolved), operation=operation)
            if not result.is_allowed:
                raise ADRReconcileError(
                    f"Sandbox denied {resolved}: {result.reason}",
                    code="ADR_SANDBOX_DENIED",
                )

    def _atomic_write(self, target: Path, content: str, repo) -> None:
        """Atomically write ``content`` to ``target`` (temp + os.replace)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _guard_write(self, capability: str, adr: ADR) -> None:
        if self._authz:
            self._authz.guard(
                capability,
                resource_type="adr",
                resource_id=adr.id,
                details={"workspace_id": adr.workspace_id, "source": adr.source},
            )
