"""ADR Reconciliation Service.

The canonical authority for ADR CONTENT is the Markdown file inside the
resolved Hermes Project repository (default ``docs/adr/``).  ``workspace.db``
keeps an INDEX/PROJECTION — metadata, a derived markdown cache for
search/API performance, and reconciliation bookkeeping — never a competing
copy of truth.

U1D-E filesystem hardening:

* **Project-root authority.**  All ADR filesystem operations derive their
  allowed root from the mapped Hermes Project (U1D-D authority).  A
  workspace without a valid (non-archived) project link fails closed
  (``ADR_NO_PROJECT_AUTHORITY``) BEFORE any filesystem access.  Only
  repositories whose root lies inside the project's folders are
  authorized — Workspace repository metadata alone never grants
  filesystem authority, and a stored/caller path never overrides the
  project boundary.
* **Canonical path contract.**  ``canonical_path`` must be project-root
  relative, under one of the configured ADR directories, ``.md``-suffixed,
  with no absolute/``.``/``..`` components.  Violations are rejected
  (``ADR_UNSAFE_PATH``), never normalized into acceptance.
* **Path containment.**  Every filesystem access resolves the candidate
  (following symlinks) and verifies containment against the repository
  root via parent-chain checks — prefix checks are never used alone.
  Symlink escapes (file, directory, parent, destination) resolve outside
  the root and are rejected or skipped.
* **Byte-level hashing.**  Reconciliation authority is the SHA-256 of the
  exact file bytes — never mtime, size, or newline-normalized text.
* **No-clobber / compare-and-swap.**  File updates verify the current
  on-disk hash against the hash Workspace believes it owns BEFORE writing,
  and re-verify immediately before the atomic replace.  An external
  modification produces a first-class conflict (``ADR_CONFLICT``) — never
  last-writer-wins.  Materialization creates canonical files with an
  atomic no-clobber link (``O_EXCL`` semantics).
* **DB/filesystem ordering.**  File writes happen first; the DB
  projection follows.  A crash between the two leaves the projection
  stale and the next reconciliation converges (file wins) — explicit,
  recoverable semantics.  Reconciliation scans the filesystem outside any
  DB transaction; projection mutations apply in one transaction.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

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


# project_id -> list of authoritative folder paths | None (missing/archived)
ProjectFolders = Callable[[str], Optional[List[str]]]


def _default_project_folders(project_id: str) -> Optional[List[str]]:
    """Resolve a project's authoritative folder paths from ``projects.db``.

    Returns ``None`` when the project does not exist or is archived —
    the authority is then stale and callers must fail closed.  Lazy
    import: projects_db lives in Hermes Core.
    """
    if not project_id:
        return None
    try:
        from hermes_cli.projects_db import connect_closing, get_project

        with connect_closing() as conn:
            project = get_project(conn, project_id)
    except Exception:
        _log.exception("Failed to read project %s", project_id)
        return None
    if project is None or bool(getattr(project, "archived", False)):
        return None
    folders = [f.path for f in (project.folders or []) if getattr(f, "path", "")]
    if not folders and getattr(project, "primary_path", None):
        folders = [project.primary_path]
    return folders or None


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
        project_folders: Optional[ProjectFolders] = None,
    ):
        self._storage = storage
        self._authz = authz
        self._limits = limits
        self._sandbox = sandbox
        self._adr_dirs = adr_dirs or DEFAULT_ADR_DIRS
        self._project_folders = project_folders or _default_project_folders

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
        legacy rows are never touched destructively.  Filesystem scanning
        happens OUTSIDE any DB transaction; projection mutations apply in
        one transaction afterwards.
        """
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise ADRNotFoundError(workspace_id)

        # U1D-E: project authority is required BEFORE any filesystem access.
        self._project_folders_or_raise(ws)

        summary = ADRReconcileSummary(
            workspace_id=workspace_id,
            project_id=ws.hermes_project_id or "",
            dry_run=dry_run,
        )
        seen_identities: Dict[str, str] = {}
        actions: List[Tuple[str, dict]] = []

        for repo, root in self._authorized_repos(ws):
            self._plan_repository(
                workspace_id, repo, root, summary, seen_identities, actions, dry_run
            )
        self._plan_missing_files(workspace_id, actions, dry_run)

        if not dry_run:
            self._apply_actions(actions, summary)

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
        self._project_folders_or_raise(ws)

        adrs = self._storage.list_adrs(workspace_id)
        if adr_id is not None:
            adrs = [a for a in adrs if a.id == adr_id]

        root_map = self._repo_roots(ws)
        statuses: List[ADRReconcileStatus] = []
        for adr in adrs:
            file_state = self._file_state(adr, root_map)
            state = self._live_state(adr, root_map, file_state)
            statuses.append(self._to_status(adr, state, bool(file_state.get("exists"))))
        statuses.sort(key=lambda s: (s.canonical_path, s.id))
        return statuses

    def materialize(
        self, adr_id: str, *, dry_run: bool = True, session_id: str = ""
    ) -> ADRMaterializeResult:
        """Materialize a legacy DB-only ADR into a canonical file.

        ``dry_run`` (default) returns a preview without writing.  Real mode
        creates the canonical file with atomic no-clobber semantics
        (an unexpected existing file is never overwritten), then promotes
        the DB record to the ``git_file`` projection.
        """
        adr = self._storage.get_adr(adr_id)
        if adr is None:
            raise ADRNotFoundError(adr_id)
        if adr.source != "workspace_db":
            raise ADRReconcileError(
                f"ADR {adr_id} is already canonical (source={adr.source})",
                code="ADR_ALREADY_CANONICAL",
            )

        ws = self._storage.get_workspace(adr.workspace_id)
        if ws is None:
            raise ADRNotFoundError(adr.workspace_id)
        self._project_folders_or_raise(ws)

        repo, root = self._authorized_repo_for(adr)
        if repo is None:
            return ADRMaterializeResult(
                id=adr_id, status="no_repository",
                message="Workspace has no registered repository inside the "
                        "authoritative project.",
            )

        target = self._materialization_target(root, adr.slug)
        if target is None:
            return ADRMaterializeResult(
                id=adr_id, status="invalid",
                message=f"Could not derive a safe materialization target for slug {adr.slug!r}.",
            )

        rel_path = str(target.relative_to(root))

        if target.is_symlink() or target.exists() or self._identity_collision(root, adr.slug):
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
        self._atomic_create_no_clobber(target, content, root)

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

        No-clobber semantics: the on-disk bytes must still match the hash
        Workspace believes it owns (``content_hash``).  An external
        modification raises ``ADR_CONFLICT`` — the external content is
        preserved byte-for-byte and never overwritten.
        """
        adr = self._storage.get_adr(adr_id)
        if adr is None:
            raise ADRNotFoundError(adr_id)
        if adr.source != "git_file" or not adr.canonical_path:
            raise ADRCanonicalUpdateError(adr_id)

        ws = self._storage.get_workspace(adr.workspace_id)
        if ws is None:
            raise ADRNotFoundError(adr.workspace_id)
        self._project_folders_or_raise(ws)

        repo, root = self._authorized_repo_for(adr)
        if repo is None:
            raise ADRReconcileError(
                f"ADR {adr_id} has no repository inside the authoritative project",
                code="ADR_NO_REPOSITORY",
            )

        target = self._contained_target(root, adr.canonical_path, operation="write")
        if not target.is_file():
            raise ADRReconcileError(
                f"Canonical file missing: {adr.canonical_path}",
                code="ADR_MISSING_FILE",
            )

        rel_path = str(adr.canonical_path)

        # Compare-and-swap: refuse destructive overwrite when the on-disk
        # bytes are not what Workspace believes it owns.
        current_hash = _sha256(target.read_bytes())
        if adr.content_hash and current_hash != adr.content_hash:
            self._audit_conflict(adr, "file_externally_modified", session_id)
            raise ADRReconcileError(
                f"ADR {adr_id} canonical file was modified outside Workspace "
                f"(expected hash {adr.content_hash}, found {current_hash}); "
                f"refusing to overwrite.",
                code="ADR_CONFLICT",
            )

        if dry_run:
            return ADRMaterializeResult(
                id=adr_id, status="preview", target_path=rel_path,
                message="Preview — no file written.",
            )

        self._guard_write("adr.reconcile.write", adr)
        # Re-verify the bytes immediately before the replace (narrow
        # TOCTOU window between verification and the atomic swap).
        try:
            self._atomic_write_cas(target, markdown, root, expected_hash=adr.content_hash)
        except ADRReconcileError:
            self._audit_conflict(adr, "file_externally_modified_during_write", session_id)
            raise

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
    # Project-root authority (U1D-D chain)
    # ------------------------------------------------------------------

    def _project_folders_or_raise(self, ws) -> List[str]:
        """Return the workspace's authoritative project folder paths.

        Raises ``ADR_NO_PROJECT_AUTHORITY`` when the mapped project is
        missing or archived — filesystem authority must fail closed
        before any access.
        """
        project_id = ws.hermes_project_id or ""
        folders = self._project_folders(project_id) if project_id else None
        if not folders:
            raise ADRReconcileError(
                f"Workspace {ws.id} has no valid Hermes Project authority "
                "(project missing, archived, or unmapped); refusing filesystem access.",
                code="ADR_NO_PROJECT_AUTHORITY",
            )
        return folders

    def _authorized_repos(self, ws) -> List[Tuple[object, Path]]:
        """Return (repo, root) pairs whose root lies inside the project.

        Repositories outside the authoritative project folders are skipped
        (audit-logged) — Workspace repository metadata alone never grants
        filesystem authority.
        """
        folders = self._project_folders_or_raise(ws)
        folder_roots = []
        for f in folders:
            try:
                folder_roots.append(Path(f).resolve())
            except (OSError, RuntimeError):
                continue

        def _inside_project(root: Path) -> bool:
            for folder in folder_roots:
                if root == folder or folder in root.parents:
                    return True
            return False

        authorized: List[Tuple[object, Path]] = []
        for repo in self._storage.list_repositories(ws.id):
            try:
                root = Path(repo.path).resolve()
            except (OSError, RuntimeError):
                continue
            if _inside_project(root):
                authorized.append((repo, root))
            else:
                _log.warning(
                    "[Workspace Plugin] Repository %s (%s) is outside the "
                    "authoritative project folders — skipped",
                    repo.id, repo.path,
                )
                if self._authz:
                    self._authz.audit.log(
                        action="adr.reconcile.repo_skipped",
                        status="DENY",
                        resource_type="repository",
                        resource_id=repo.id,
                        details={"workspace_id": ws.id, "reason": "outside_project_folders"},
                    )
        return authorized

    def _authorized_repo_for(self, adr: ADR) -> Tuple[Optional[object], Optional[Path]]:
        """Resolve the ADR's repository inside project authority.

        Never falls back to an arbitrary first repository: the ADR's own
        repository (when named) must be authorized; otherwise the ONLY
        authorized repository may be used (deterministic — a single
        candidate is unambiguous).  Multiple authorized repositories
        without an explicit choice fail closed.
        """
        ws = self._storage.get_workspace(adr.workspace_id)
        if ws is None:
            return None, None
        authorized = self._authorized_repos(ws)

        if adr.repository_id:
            for repo, root in authorized:
                if repo.id == adr.repository_id:
                    return repo, root
            return None, None

        if len(authorized) == 0:
            return None, None
        if len(authorized) > 1:
            raise ADRReconcileError(
                f"ADR {adr.id} has no repository and the workspace has "
                "multiple authorized repositories; refusing to choose.",
                code="ADR_AMBIGUOUS_REPOSITORY",
            )
        return authorized[0]

    # ------------------------------------------------------------------
    # Canonical path contract + containment
    # ------------------------------------------------------------------

    def _validate_canonical_rel(self, rel_str: str) -> None:
        """Enforce the canonical ADR path contract.

        Relative, under one of the allowed ADR directories, ``.md``
        suffix, no absolute/``.``/``..``/empty components.  Violations are
        rejected — never normalized into acceptance.
        """
        if not rel_str or rel_str.startswith("/") or "\\" in rel_str:
            raise ADRReconcileError(
                f"Unsafe canonical path: {rel_str!r}", code="ADR_UNSAFE_PATH"
            )
        rel = Path(rel_str)
        parts = rel.parts
        if not parts or any(p in (".", "..", "") for p in parts):
            raise ADRReconcileError(
                f"Unsafe canonical path: {rel_str!r}", code="ADR_UNSAFE_PATH"
            )
        if not any(
            rel_str == d or rel_str.startswith(d.rstrip("/") + "/")
            for d in self._adr_dirs
        ):
            raise ADRReconcileError(
                f"Canonical path outside allowed ADR directories: {rel_str!r}",
                code="ADR_UNSAFE_PATH",
            )
        if not parts[-1].endswith(".md"):
            raise ADRReconcileError(
                f"Canonical path must be a .md file: {rel_str!r}",
                code="ADR_UNSAFE_PATH",
            )

    def _contained_target(self, root: Path, rel_str: str, *, operation: str) -> Path:
        """Resolve a canonical relative path to a contained absolute path.

        ``resolve()`` follows symlinks (file, directory, parent,
        destination) so any escape lands OUTSIDE the root and is rejected
        by the containment check — string-prefix checks are never used
        alone.
        """
        self._validate_canonical_rel(rel_str)
        target = (root / rel_str).resolve()
        self._validate_in_root(target, root, operation=operation)
        return target

    def _validate_in_root(self, path: Path, root: Path, *, operation: str) -> None:
        """Validate that ``path`` stays inside ``root`` (sandbox + parents)."""
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

    # ------------------------------------------------------------------
    # Discovery / planning (no DB writes during scan)
    # ------------------------------------------------------------------

    def _plan_repository(
        self,
        workspace_id: str,
        repo,
        root: Path,
        summary,
        seen_identities: Dict[str, str],
        actions: List[Tuple[str, dict]],
        dry_run: bool,
    ) -> None:
        discovered = self._discover(root)
        summary.scanned_files += len(discovered)

        for rel_str, abs_path in discovered:
            try:
                raw = abs_path.read_bytes()
            except (OSError, RuntimeError):
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                self._plan_mark_invalid_by_path(
                    workspace_id, rel_str, "unreadable", actions, dry_run
                )
                continue
            try:
                text = raw.decode("utf-8")
                parsed = parse_adr_file(text)
            except (UnicodeDecodeError, _MalformedADR) as exc:
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                reason = exc.reason if isinstance(exc, _MalformedADR) else "not_utf8"
                self._plan_mark_invalid_by_path(
                    workspace_id, rel_str, reason, actions, dry_run
                )
                continue

            identity = _identity_from_stem(abs_path.stem)
            content_hash = _sha256(raw)

            if identity in seen_identities:
                summary.invalid += 1
                summary.invalid_paths.append(rel_str)
                self._plan_mark_invalid_by_path(
                    workspace_id, rel_str, "duplicate_identity", actions, dry_run
                )
                continue
            seen_identities[identity] = rel_str

            self._plan_file(
                workspace_id, repo, rel_str, parsed, identity, content_hash,
                summary, actions, dry_run,
            )

    def _plan_file(
        self,
        workspace_id: str,
        repo,
        rel_str: str,
        parsed,
        identity: str,
        content_hash: str,
        summary,
        actions: List[Tuple[str, dict]],
        dry_run: bool,
    ) -> None:
        row = self._storage.find_adr_by_canonical_path(workspace_id, rel_str)

        if row is not None and row.source == "git_file":
            if row.content_hash == content_hash:
                return  # synced — counted in the final pass
            if row.updated_at > row.last_indexed and row.last_indexed:
                summary.conflict += 1
                actions.append(
                    ("mark_conflict", {"adr_id": row.id, "reason": "file_and_db_changed"})
                )
                return
            summary.file_changed += 1
            actions.append(
                ("refresh_projection", {
                    "adr_id": row.id,
                    "title": parsed.title,
                    "status": parsed.status,
                    "category": parsed.category,
                    "markdown": parsed.raw,
                    "tags": parsed.tags,
                    "content_hash": content_hash,
                })
            )
            return

        if row is not None:
            # Projection row exists but not in the git_file state.
            summary.conflict += 1
            actions.append(
                ("mark_conflict", {"adr_id": row.id, "reason": "projection_state_mismatch"})
            )
            return

        linked_by_slug = self._storage.get_adr_by_slug(workspace_id, identity)
        if linked_by_slug is not None and not linked_by_slug.canonical_path:
            cached = (linked_by_slug.markdown or "").strip()
            if not cached or cached == parsed.markdown.strip():
                summary.file_changed += 1  # would promote legacy -> synced
                actions.append(
                    ("promote_legacy", {
                        "adr_id": linked_by_slug.id,
                        "markdown": parsed.raw,
                        "tags": parsed.tags,
                        "canonical_path": rel_str,
                        "content_hash": content_hash,
                    })
                )
                return
            summary.conflict += 1
            actions.append(
                ("mark_conflict", {
                    "adr_id": linked_by_slug.id,
                    "reason": "legacy_db_and_file_differ",
                })
            )
            return

        if linked_by_slug is not None:
            # Same identity already linked to a canonical file.  If its old
            # file is gone, this is a RENAME — re-link.  If the old file
            # still exists, it is a genuine duplicate identity → invalid.
            old_state = self._file_state(linked_by_slug, self._repo_roots(
                self._storage.get_workspace(workspace_id)))
            if not old_state.get("exists"):
                summary.file_changed += 1
                actions.append(
                    ("rename_projection", {
                        "adr_id": linked_by_slug.id,
                        "title": parsed.title,
                        "status": parsed.status,
                        "category": parsed.category,
                        "markdown": parsed.raw,
                        "tags": parsed.tags,
                        "canonical_path": rel_str,
                        "content_hash": content_hash,
                    })
                )
                return
            summary.invalid += 1
            summary.invalid_paths.append(rel_str)
            return

        # No projection row → index the canonical file (new projection row).
        summary.indexed += 1
        actions.append(
            ("create_index", {
                "workspace_id": workspace_id,
                "repository_id": repo.id,
                "title": parsed.title,
                "slug": identity,
                "status": parsed.status,
                "category": parsed.category,
                "markdown": parsed.raw,
                "tags": parsed.tags,
                "canonical_path": rel_str,
                "content_hash": content_hash,
            })
        )

    def _plan_mark_invalid_by_path(
        self,
        workspace_id: str,
        rel_str: str,
        reason: str,
        actions: List[Tuple[str, dict]],
        dry_run: bool,
    ) -> None:
        row = self._storage.find_adr_by_canonical_path(workspace_id, rel_str)
        if row is None:
            return
        if not dry_run and row.reconcile_state != "invalid":
            actions.append(
                ("mark_invalid", {"adr_id": row.id, "reason": reason})
            )

    def _plan_missing_files(
        self,
        workspace_id: str,
        actions: List[Tuple[str, dict]],
        dry_run: bool,
    ) -> None:
        """Plan missing-file marks.

        Rows with a pending re-link (rename/promote) action are skipped —
        their canonical path changes as part of this run, so planning
        against the pre-apply state would falsely mark them missing.
        """
        relinked_ids = {
            kw["adr_id"]
            for kind, kw in actions
            if kind in ("rename_projection", "promote_legacy")
        }
        ws = self._storage.get_workspace(workspace_id)
        root_map = self._repo_roots(ws)
        for adr in self._storage.list_adrs(workspace_id):
            if adr.source != "git_file" or not adr.canonical_path:
                continue
            if adr.id in relinked_ids:
                continue
            if self._file_state(adr, root_map).get("exists"):
                continue
            if not dry_run:
                actions.append(
                    ("mark_missing", {"adr_id": adr.id, "reason": "canonical_file_missing"})
                )

    # ------------------------------------------------------------------
    # Apply phase (single DB transaction)
    # ------------------------------------------------------------------

    def _apply_actions(self, actions: List[Tuple[str, dict]], summary) -> None:
        with self._storage.transaction():
            for kind, kwargs in actions:
                try:
                    self._apply_one(kind, kwargs)
                except DuplicateSlugError:
                    if kind == "create_index":
                        summary.indexed -= 1
                        summary.invalid += 1
                        summary.invalid_paths.append(kwargs["canonical_path"])
                    else:
                        raise

    def _apply_one(self, kind: str, kw: dict) -> None:
        if kind == "refresh_projection":
            self._storage.update_adr(
                kw["adr_id"],
                title=kw["title"],
                status=kw["status"],
                category=kw["category"],
                markdown=kw["markdown"],
                tags=kw["tags"],
            )
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"], content_hash=kw["content_hash"],
                reconcile_state="synced", last_indexed=_utc_now(), last_error="",
            )
        elif kind == "mark_conflict":
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"], reconcile_state="conflict", last_error=kw["reason"],
            )
        elif kind == "promote_legacy":
            self._storage.update_adr(
                kw["adr_id"], markdown=kw["markdown"], tags=kw["tags"]
            )
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"],
                canonical_path=kw["canonical_path"],
                content_hash=kw["content_hash"],
                reconcile_state="synced",
                source="git_file",
                last_indexed=_utc_now(),
                last_error="",
            )
        elif kind == "rename_projection":
            self._storage.update_adr(
                kw["adr_id"],
                title=kw["title"],
                status=kw["status"],
                category=kw["category"],
                markdown=kw["markdown"],
                tags=kw["tags"],
            )
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"],
                canonical_path=kw["canonical_path"],
                content_hash=kw["content_hash"],
                reconcile_state="synced",
                last_indexed=_utc_now(),
                last_error="",
            )
        elif kind == "mark_invalid":
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"], reconcile_state="invalid", last_error=kw["reason"],
            )
        elif kind == "mark_missing":
            self._storage.update_adr_reconcile_meta(
                kw["adr_id"], reconcile_state="missing_file", last_error=kw["reason"],
            )
        elif kind == "create_index":
            self._storage.create_adr(
                workspace_id=kw["workspace_id"],
                repository_id=kw["repository_id"],
                title=kw["title"],
                slug=kw["slug"],
                status=kw["status"],
                category=kw["category"],
                markdown=kw["markdown"],
                tags=kw["tags"],
            )
            row = self._storage.get_adr_by_slug(kw["workspace_id"], kw["slug"])
            if row is not None:
                self._storage.update_adr_reconcile_meta(
                    row.id, canonical_path=kw["canonical_path"],
                    content_hash=kw["content_hash"],
                    reconcile_state="synced", source="git_file",
                    last_indexed=_utc_now(), last_error="",
                )
        else:  # pragma: no cover - defensive
            raise ADRReconcileError(
                f"Unknown reconcile action: {kind}", code="ADR_RECONCILE_ERROR"
            )

    def _finalize_counts(self, summary) -> None:
        """Count stored projection states after the scan.

        Scan-phase counters (``indexed`` / ``file_changed`` / ``invalid`` /
        ``invalid_paths`` / ``scanned_files``) are preserved; stored-row
        states (synced / db_legacy / missing_file / conflict / invalid)
        are counted here so real-mode transitions land exactly once.
        """
        ws = self._storage.get_workspace(summary.workspace_id)
        root_map = self._repo_roots(ws)
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
            file_state = self._file_state(adr, root_map)
            summary.statuses.append(
                self._to_status(adr, state, bool(file_state.get("exists")))
            )
        summary.statuses.sort(key=lambda s: (s.canonical_path, s.id))

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    def _repo_roots(self, ws) -> Dict[str, Path]:
        """Authorized repository roots (project-contained only)."""
        roots: Dict[str, Path] = {}
        try:
            for repo, root in self._authorized_repos(ws):
                roots[repo.id] = root
        except ADRReconcileError:
            return roots
        return roots

    def _file_state(self, adr: ADR, root_map: Dict[str, Path]) -> Dict[str, object]:
        """Read-only byte-hash state of an ADR's canonical file.

        Returns ``{"exists", "hash", "unsafe"}``.  An unsafe (non-contract
        or escaping) stored path yields ``unsafe=True`` and is never read.
        """
        if adr.source != "git_file" or not adr.canonical_path:
            return {"exists": False, "hash": "", "unsafe": False}
        root = root_map.get(adr.repository_id or "")
        if root is None:
            return {"exists": False, "hash": "", "unsafe": False}
        try:
            target = self._contained_target(root, adr.canonical_path, operation="read")
        except ADRReconcileError:
            return {"exists": False, "hash": "", "unsafe": True}
        try:
            if not target.is_file() or target.is_symlink():
                return {"exists": False, "hash": "", "unsafe": False}
            return {"exists": True, "hash": _sha256(target.read_bytes()), "unsafe": False}
        except (OSError, RuntimeError):
            return {"exists": False, "hash": "", "unsafe": False}

    def _live_state(
        self, adr: ADR, root_map: Dict[str, Path], file_state: Dict[str, object]
    ) -> str:
        if adr.source == "workspace_db":
            return "db_legacy"
        if not adr.canonical_path:
            return "db_legacy"
        if file_state.get("unsafe"):
            return "invalid"
        if not file_state.get("exists"):
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

    def _materialization_target(self, root: Path, slug: str) -> Optional[Path]:
        safe_slug = _generate_slug(slug)
        if not safe_slug:
            return None
        adr_dir = root / self._adr_dirs[0]
        next_seq = self._next_sequence(adr_dir)
        target = adr_dir / f"{next_seq:04d}-{safe_slug}.md"
        try:
            self._validate_in_root(target, root, operation="write")
        except ADRReconcileError:
            return None
        return target

    def _identity_collision(self, root: Path, slug: str) -> bool:
        """True when ANY canonical file under the ADR dir shares this identity.

        Uses the same recursive discovery as reconciliation so nested
        collisions are detected (U1D-E).
        """
        target_identity = _identity_from_stem(_generate_slug(slug))
        try:
            for rel_str, _abs in self._discover(root):
                if not rel_str.startswith(self._adr_dirs[0] + "/"):
                    continue
                if _identity_from_stem(Path(rel_str).stem) == target_identity:
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

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _atomic_write_cas(
        self, target: Path, content: str, root: Path, *, expected_hash: str
    ) -> None:
        """Atomically replace ``target`` only while its bytes still match
        ``expected_hash`` (narrow TOCTOU double-check before the swap)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())

            # Re-verify ownership immediately before the atomic swap.
            if expected_hash and _sha256(target.read_bytes()) != expected_hash:
                raise ADRReconcileError(
                    f"Canonical file changed during write: {target}",
                    code="ADR_CONFLICT",
                )
            os.replace(tmp_path, target)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _atomic_create_no_clobber(self, target: Path, content: str, root: Path) -> None:
        """Create ``target`` atomically WITHOUT overwriting an existing file.

        The temporary file is hard-linked to ``target`` — ``os.link``
        fails atomically if the target appeared meanwhile (no-clobber
        create semantics).
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp_path, target)
            except FileExistsError:
                raise ADRReconcileError(
                    f"Canonical file appeared during materialization: {target}",
                    code="MATERIALIZE_TARGET_EXISTS",
                ) from None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Audit / capability
    # ------------------------------------------------------------------

    def _audit_conflict(self, adr: ADR, reason: str, session_id: str = "") -> None:
        if self._authz:
            self._authz.audit.log(
                action="adr.file_conflict",
                status="DENY",
                resource_type="adr",
                resource_id=adr.id,
                details={
                    "workspace_id": adr.workspace_id,
                    "canonical_path": adr.canonical_path,
                    "reason": reason,
                },
                session_id=session_id,
            )

    def _guard_write(self, capability: str, adr: ADR) -> None:
        if self._authz:
            self._authz.guard(
                capability,
                resource_type="adr",
                resource_id=adr.id,
                details={"workspace_id": adr.workspace_id, "source": adr.source},
            )
