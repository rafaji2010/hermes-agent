"""ADR Service.

Business logic for Architecture Decision Records — slug generation,
validation, and persistence through the storage layer.
"""

from __future__ import annotations

import logging
import re
from typing import List, Optional

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

    def __init__(self, storage: AbstractStorage):
        self._storage = storage

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_adr(self, payload: ADRCreate) -> ADR:
        title = payload.title.strip()
        if not title:
            raise ADRError("Title must not be empty.", code="EMPTY_TITLE")

        status = (payload.status or "proposed").strip().lower()
        if status not in VALID_ADR_STATUSES:
            raise InvalidADRStatusError(payload.status)

        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)

        base_slug = _generate_slug(title)
        slug = _unique_slug(self._storage, payload.workspace_id, base_slug)

        tags = [t.strip().lower() for t in (payload.tags or []) if t.strip()]

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
