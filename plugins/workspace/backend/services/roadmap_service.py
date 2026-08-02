"""Roadmap Service.

Orchestrates storage, validation, and progress calculation for roadmaps
and milestones.  Depends on ``AbstractStorage`` — never on a concrete
storage implementation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from ..models import (
    InvalidMilestoneStatusError,
    MilestoneCreate,
    MilestoneReorder,
    MilestoneUpdate,
    Roadmap,
    RoadmapCreate,
    RoadmapMilestone,
    RoadmapNotFoundError,
    RoadmapUpdate,
    VALID_MILESTONE_STATUSES,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter

_log = logging.getLogger("hermes.plugins.workspace.service")


class RoadmapService:
    """Business logic for roadmap and milestone management."""

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
    # Roadmaps
    # ------------------------------------------------------------------

    def create_roadmap(self, payload: RoadmapCreate) -> Roadmap:
        ws = self._storage.get_workspace(payload.workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(payload.workspace_id)
        name = payload.name.strip()
        if not name:
            from ..models import InvalidPathError
            raise InvalidPathError("", "Roadmap name must not be empty.")

        if self._authz:
            self._authz.guard("roadmap.create", resource_type="roadmap",
                              resource_id=payload.workspace_id)

        if self._limits:
            self._check_limit(self._limits.check_title_length(name))
            if payload.description:
                self._check_limit(
                    self._limits.check_description_length(payload.description))

        with self._storage.transaction():
            return self._storage.create_roadmap(
                payload.workspace_id, name, payload.description.strip()
            )

    def update_roadmap(self, roadmap_id: str, payload: RoadmapUpdate) -> Roadmap:
        if self._authz:
            self._authz.guard("roadmap.update", resource_type="roadmap",
                              resource_id=roadmap_id)
        with self._storage.transaction():
            return self._storage.update_roadmap(
                roadmap_id,
                name=payload.name,
                description=payload.description,
            )

    def delete_roadmap(self, roadmap_id: str) -> None:
        if self._authz:
            self._authz.guard("roadmap.delete", resource_type="roadmap",
                              resource_id=roadmap_id)
        with self._storage.transaction():
            self._storage.delete_roadmap(roadmap_id)

    def get_roadmap(self, roadmap_id: str) -> Roadmap:
        r = self._storage.get_roadmap(roadmap_id)
        if r is None:
            raise RoadmapNotFoundError(roadmap_id)
        return r

    def list_roadmaps(self, workspace_id: str) -> List[Roadmap]:
        ws = self._storage.get_workspace(workspace_id)
        if ws is None:
            raise WorkspaceNotFoundError(workspace_id)
        return self._storage.list_roadmaps(workspace_id)

    # ------------------------------------------------------------------
    # Milestones
    # ------------------------------------------------------------------

    def create_milestone(self, roadmap_id: str, payload: MilestoneCreate) -> RoadmapMilestone:
        status = payload.status.strip().lower()
        if status not in VALID_MILESTONE_STATUSES:
            raise InvalidMilestoneStatusError(payload.status)

        if self._authz:
            self._authz.guard("milestone.create", resource_type="milestone",
                              resource_id=roadmap_id)

        if self._limits:
            self._check_limit(self._limits.check_title_length(payload.title.strip()))
            if payload.description:
                self._check_limit(
                    self._limits.check_description_length(payload.description))

        with self._storage.transaction():
            return self._storage.create_milestone(
                roadmap_id,
                payload.title.strip(),
                payload.description.strip(),
                status,
                payload.target_date.strip(),
            )

    def update_milestone(self, milestone_id: str, payload: MilestoneUpdate) -> RoadmapMilestone:
        status = payload.status
        if status is not None:
            status = status.strip().lower()
            if status not in VALID_MILESTONE_STATUSES:
                raise InvalidMilestoneStatusError(payload.status or "")

        if self._authz:
            self._authz.guard("milestone.update", resource_type="milestone",
                              resource_id=milestone_id)
        with self._storage.transaction():
            return self._storage.update_milestone(
                milestone_id,
                title=payload.title,
                description=payload.description,
                status=status,
                target_date=payload.target_date,
                sort_order=payload.sort_order,
            )

    def delete_milestone(self, milestone_id: str) -> None:
        if self._authz:
            self._authz.guard("milestone.delete", resource_type="milestone",
                              resource_id=milestone_id)
        with self._storage.transaction():
            self._storage.delete_milestone(milestone_id)

    def list_milestones(self, roadmap_id: str) -> List[RoadmapMilestone]:
        return self._storage.list_milestones(roadmap_id)

    def reorder_milestones(self, roadmap_id: str, payload: MilestoneReorder) -> List[RoadmapMilestone]:
        existing = self._storage.list_milestones(roadmap_id)
        existing_ids = {m.id for m in existing}
        sent_ids = set(payload.ids)
        if sent_ids != existing_ids:
            raise InvalidMilestoneStatusError(
                "Reorder IDs must include exactly all existing milestones"
            )
        if self._limits:
            self._check_limit(self._limits.check_list_size(payload.ids, "milestone IDs"))
        with self._storage.transaction():
            return self._storage.reorder_milestones(roadmap_id, payload.ids)

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
