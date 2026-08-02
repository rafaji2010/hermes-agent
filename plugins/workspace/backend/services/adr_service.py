"""ADR Service.

Business logic for Architecture Decision Records — slug generation,
validation, and persistence through the storage layer.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, List, Optional

from ..models import (
    ADR,
    ADRCreate,
    ADRError,
    ADRNotFoundError,
    ADRUpdate,
    InvalidADRStatusError,
    VALID_ADR_STATUSES,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter

_log = logging.getLogger("hermes.plugins.workspace.adr_service")

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_SLUG_HYPHEN_RE = re.compile(r"[-\s]+")


def _generate_slug(title: str) -> str:
    """Generate a URL-safe slug from a title."""
    slug = title.lower().strip()
    slug = _SLUG_STRIP_RE.sub("", slug)
    slug = _SLUG_HYPHEN_RE.sub("-", slug)
    return slug.strip("-")


def _unique_slug(storage: AbstractStorage, workspace_id: str, base: str) -> str:
    """Return *base* if available, otherwise ``base-2``, ``base-3``, …"""
    slug = base
    n = 2
    while storage.get_adr_by_slug(workspace_id, slug) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


class ADRService:
    """Business logic for ADR management."""

    def __init__(
        self,
        storage: AbstractStorage,
        authz: "AuthorizationMiddleware | None" = None,
        limits: "ResourceLimiter | None" = None,
    ):
        self._storage = storage
        self._authz = authz
        self._limits = limits

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_adr(self, payload: ADRCreate) -> ADR:
        title = payload.title.strip()
        if not title:
            raise ADRError("Title must not be empty.", code="EMPTY_TITLE")

        if self._authz:
            self._authz.guard("adr.create", resource_type="adr",
                              resource_id=payload.workspace_id)

        status = (payload.status or "proposed").strip().lower()
        if status not in VALID_ADR_STATUSES:
            raise InvalidADRStatusError(payload.status)

        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)

        base_slug = _generate_slug(title)
        slug = _unique_slug(self._storage, payload.workspace_id, base_slug)

        tags = [t.strip().lower() for t in (payload.tags or []) if t.strip()]

        if self._limits:
            self._check_limit(self._limits.check_title_length(title))
            self._check_limit(self._limits.check_tag_count(tags))
            if payload.markdown:
                self._check_limit(self._limits.check_markdown_size(payload.markdown))

        with self._storage.transaction():
            return self._storage.create_adr(
                workspace_id=payload.workspace_id,
                repository_id=payload.repository_id,
                title=title,
                slug=slug,
                status=status,
                category=(payload.category or "").strip(),
                markdown=payload.markdown or "",
                tags=tags,
            )

    def update_adr(self, adr_id: str, payload: ADRUpdate) -> ADR:
        existing = self._storage.get_adr(adr_id)
        if existing is None:
            raise ADRNotFoundError(adr_id)

        # S7.3A: the canonical file is the authority for content/metadata of
        # git_file ADRs.  Edits must go through the file endpoint so the
        # projection never silently diverges from the file.
        if existing.source == "git_file":
            from ..models import ADRCanonicalUpdateError

            raise ADRCanonicalUpdateError(adr_id)

        if self._authz:
            self._authz.guard("adr.update", resource_type="adr",
                              resource_id=adr_id)

        status = payload.status
        if status is not None:
            status = status.strip().lower()
            if status not in VALID_ADR_STATUSES:
                raise InvalidADRStatusError(payload.status)

        title = payload.title.strip() if payload.title is not None else None
        if title == "":
            raise ADRError("Title must not be empty.", code="EMPTY_TITLE")

        slug = None
        if title is not None:
            base_slug = _generate_slug(title)
            slug = _unique_slug(self._storage, existing.workspace_id, base_slug)

        tags = None
        if payload.tags is not None:
            tags = [t.strip().lower() for t in payload.tags if t.strip()]

        if self._limits:
            if title is not None:
                self._check_limit(self._limits.check_title_length(title))
            if tags is not None:
                self._check_limit(self._limits.check_tag_count(tags))
            if payload.markdown is not None:
                self._check_limit(self._limits.check_markdown_size(payload.markdown))

        with self._storage.transaction():
            return self._storage.update_adr(
                adr_id=adr_id,
                title=title,
                slug=slug,
                status=status,
                category=payload.category.strip() if payload.category is not None else None,
                markdown=payload.markdown,
                tags=tags,
            )

    def delete_adr(self, adr_id: str) -> None:
        existing = self._storage.get_adr(adr_id)
        if existing is None:
            raise ADRNotFoundError(adr_id)

        # S7.3A: never silently delete a canonical file's projection — the
        # file stays authoritative.  Delete the file in git, then reconcile.
        if existing.source == "git_file":
            from ..models import ADRCanonicalDeleteError

            raise ADRCanonicalDeleteError(adr_id)

        if self._authz:
            self._authz.guard("adr.delete", resource_type="adr",
                              resource_id=adr_id)
        with self._storage.transaction():
            self._storage.delete_adr(adr_id)

    def get_adr(self, adr_id: str) -> ADR:
        adr = self._storage.get_adr(adr_id)
        if adr is None:
            raise ADRNotFoundError(adr_id)
        return adr

    def list_adrs(
        self,
        workspace_id: str,
        *,
        status: Optional[str] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        query: Optional[str] = None,
    ) -> List[ADR]:
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return self._storage.list_adrs(
            workspace_id,
            status=status,
            category=category,
            tag=tag,
            query=query,
        )

    def get_tags(self, workspace_id: str) -> List[str]:
        return self._storage.get_distinct_tags(workspace_id)

    def get_categories(self, workspace_id: str) -> List[str]:
        return self._storage.get_distinct_categories(workspace_id)

    # ------------------------------------------------------------------
    # Security helpers
    # ------------------------------------------------------------------

    def _check_limit(self, result) -> None:
        if not result.allowed:
            from ..security.resource_limits import ResourceLimitExceeded
            if self._authz:
                self._authz.audit.log(
                    action="s6.4.violation.resource_limit",
                    status="DENY",
                    details={"resource": result.resource, "reason": result.reason},
                )
            raise ResourceLimitExceeded(result.resource, result.limit, result.actual)
