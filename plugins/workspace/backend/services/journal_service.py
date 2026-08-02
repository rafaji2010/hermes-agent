"""Journal Service.

Business logic for engineering journal entries — validation and
persistence through the storage layer.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from ..models import (
    JournalEntry,
    JournalEntryCreate,
    JournalEntryNotFoundError,
    JournalEntryUpdate,
    JournalError,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter

_log = logging.getLogger("hermes.plugins.workspace.journal_service")


class JournalService:
    """Business logic for engineering journal management."""

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

    def create_entry(self, payload: JournalEntryCreate) -> JournalEntry:
        title = payload.title.strip()
        if not title:
            raise JournalError("Title must not be empty.", code="EMPTY_TITLE")

        if self._authz:
            self._authz.guard("journal.create", resource_type="journal",
                              resource_id=payload.workspace_id)

        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)

        tags = [t.strip().lower() for t in (payload.tags or []) if t.strip()]

        if self._limits:
            self._check_limit(self._limits.check_title_length(title))
            self._check_limit(self._limits.check_tag_count(tags))
            if payload.markdown:
                self._check_limit(self._limits.check_markdown_size(payload.markdown))
            if payload.summary:
                self._check_limit(self._limits.check_description_length(payload.summary))

        with self._storage.transaction():
            return self._storage.create_journal_entry(
                workspace_id=payload.workspace_id,
                repository_id=payload.repository_id,
                title=title,
                summary=(payload.summary or "").strip(),
                markdown=payload.markdown or "",
                entry_date=(payload.entry_date or "").strip(),
                tags=tags,
            )

    def update_entry(
        self, entry_id: str, payload: JournalEntryUpdate
    ) -> JournalEntry:
        existing = self._storage.get_journal_entry(entry_id)
        if existing is None:
            raise JournalEntryNotFoundError(entry_id)

        if self._authz:
            self._authz.guard("journal.update", resource_type="journal",
                              resource_id=entry_id)

        title = payload.title.strip() if payload.title is not None else None
        if title == "":
            raise JournalError("Title must not be empty.", code="EMPTY_TITLE")

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
            return self._storage.update_journal_entry(
                entry_id=entry_id,
                title=title,
                summary=payload.summary.strip() if payload.summary is not None else None,
                markdown=payload.markdown,
                entry_date=payload.entry_date.strip() if payload.entry_date is not None else None,
                repository_id=payload.repository_id,
                tags=tags,
            )

    def delete_entry(self, entry_id: str) -> None:
        if self._authz:
            self._authz.guard("journal.delete", resource_type="journal",
                              resource_id=entry_id)
        with self._storage.transaction():
            self._storage.delete_journal_entry(entry_id)

    def get_entry(self, entry_id: str) -> JournalEntry:
        entry = self._storage.get_journal_entry(entry_id)
        if entry is None:
            raise JournalEntryNotFoundError(entry_id)
        return entry

    def list_entries(
        self,
        workspace_id: str,
        *,
        repository_id: Optional[str] = None,
        tag: Optional[str] = None,
        entry_date: Optional[str] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[JournalEntry]:
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return self._storage.list_journal_entries(
            workspace_id,
            repository_id=repository_id,
            tag=tag,
            entry_date=entry_date,
            query=query,
            limit=limit,
        )

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
