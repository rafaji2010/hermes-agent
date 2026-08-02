"""Task Service.

Orchestrates task CRUD, validation, dependency management, circular
dependency detection, and statistics.  Depends on ``AbstractStorage``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from ..models import (
    InvalidTaskPriorityError,
    InvalidTaskStatusError,
    Task,
    TaskComment,
    TaskCommentCreate,
    TaskCreate,
    TaskDependencyCreate,
    TaskDependencyList,
    TaskList,
    TaskNotFoundError,
    TaskSearchParams,
    TaskStats,
    TaskUpdate,
    VALID_TASK_PRIORITIES,
    VALID_TASK_STATUSES,
)
from ..storage import AbstractStorage

if TYPE_CHECKING:
    from ..security.authorization import AuthorizationMiddleware
    from ..security.resource_limits import ResourceLimiter

_log = logging.getLogger("hermes.plugins.workspace.service")


class TaskService:
    """Business logic for task management."""

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

    def create_task(self, payload: TaskCreate) -> Task:
        status = payload.status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            raise InvalidTaskStatusError(payload.status)
        priority = payload.priority.strip().lower()
        if priority not in VALID_TASK_PRIORITIES:
            raise InvalidTaskPriorityError(payload.priority)

        if self._authz:
            self._authz.guard("task.create", resource_type="task",
                              resource_id=payload.workspace_id or "")

        if self._limits:
            self._check_limit(self._limits.check_title_length(payload.title.strip()))
            if payload.description:
                self._check_limit(
                    self._limits.check_description_length(payload.description))
            if payload.labels:
                self._check_limit(self._limits.check_label_count(payload.labels))
            if payload.dependency_ids:
                self._check_limit(
                    self._limits.check_dependency_count(payload.dependency_ids))

        with self._storage.transaction():
            return self._storage.create_task(
                title=payload.title.strip(),
                description=payload.description.strip(),
                status=status,
                priority=priority,
                workspace_id=payload.workspace_id,
                repository_id=payload.repository_id,
                roadmap_id=payload.roadmap_id,
                milestone_id=payload.milestone_id,
                adr_id=payload.adr_id,
                journal_id=payload.journal_id,
                labels=payload.labels,
                estimate_hours=payload.estimate_hours,
                actual_hours=payload.actual_hours,
                due_date=payload.due_date,
                dependency_ids=payload.dependency_ids,
            )

    def update_task(self, task_id: str, payload: TaskUpdate) -> Task:
        if self._authz:
            self._authz.guard("task.update", resource_type="task",
                              resource_id=task_id)
        kwargs = {}
        if payload.title is not None:
            kwargs["title"] = payload.title.strip()
        if payload.description is not None:
            kwargs["description"] = payload.description.strip()
        if payload.status is not None:
            kwargs["status"] = payload.status.strip().lower()
        if payload.priority is not None:
            kwargs["priority"] = payload.priority.strip().lower()
        if payload.workspace_id is not None:
            kwargs["workspace_id"] = payload.workspace_id
        if payload.repository_id is not None:
            kwargs["repository_id"] = payload.repository_id
        if payload.roadmap_id is not None:
            kwargs["roadmap_id"] = payload.roadmap_id
        if payload.milestone_id is not None:
            kwargs["milestone_id"] = payload.milestone_id
        if payload.adr_id is not None:
            kwargs["adr_id"] = payload.adr_id
        if payload.journal_id is not None:
            kwargs["journal_id"] = payload.journal_id
        if payload.labels is not None:
            kwargs["labels"] = payload.labels
        if payload.estimate_hours is not None:
            kwargs["estimate_hours"] = payload.estimate_hours
        if payload.actual_hours is not None:
            kwargs["actual_hours"] = payload.actual_hours
        if payload.due_date is not None:
            kwargs["due_date"] = payload.due_date
        if payload.dependency_ids is not None:
            kwargs["dependency_ids"] = payload.dependency_ids

        if self._limits:
            if kwargs.get("title"):
                self._check_limit(self._limits.check_title_length(kwargs["title"]))
            if kwargs.get("labels"):
                self._check_limit(self._limits.check_label_count(kwargs["labels"]))
            if kwargs.get("dependency_ids"):
                self._check_limit(
                    self._limits.check_dependency_count(kwargs["dependency_ids"]))
            if kwargs.get("description"):
                self._check_limit(
                    self._limits.check_description_length(kwargs["description"]))

        with self._storage.transaction():
            return self._storage.update_task(task_id, **kwargs)

    def delete_task(self, task_id: str) -> None:
        if self._authz:
            self._authz.guard("task.delete", resource_type="task",
                              resource_id=task_id)
        with self._storage.transaction():
            self._storage.delete_task(task_id)

    def get_task(self, task_id: str) -> Task:
        t = self._storage.get_task(task_id)
        if t is None:
            raise TaskNotFoundError(task_id)
        return t

    def list_tasks(self, params: TaskSearchParams, limit: Optional[int] = None) -> List[Task]:
        return self._storage.list_tasks(
            workspace_id=params.workspace_id or "",
            status=params.status,
            priority=params.priority,
            label=params.label,
            repository_id=params.repository_id,
            roadmap_id=params.roadmap_id,
            milestone_id=params.milestone_id,
            adr_id=params.adr_id,
            journal_id=params.journal_id,
            q=params.q,
            overdue=params.overdue,
            limit=limit,
        )

    def search_tasks(self, params: TaskSearchParams) -> List[Task]:
        return self.list_tasks(params)

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def add_comment(self, task_id: str, payload: TaskCommentCreate) -> TaskComment:
        if self._authz:
            self._authz.guard("task.create", resource_type="task",
                              resource_id=task_id)
        if self._limits:
            self._check_limit(
                self._limits.check_comment_length(payload.body.strip()))
        with self._storage.transaction():
            return self._storage.add_comment(
                task_id, payload.author.strip(), payload.body.strip(),
            )

    def list_comments(self, task_id: str) -> List[TaskComment]:
        return self._storage.list_comments(task_id)

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------

    def set_dependencies(self, task_id: str, payload: TaskDependencyCreate) -> TaskDependencyList:
        if self._authz:
            self._authz.guard("task.update", resource_type="task",
                              resource_id=task_id)
        if self._limits and payload.depends_on_ids:
            self._check_limit(
                self._limits.check_dependency_count(payload.depends_on_ids))
        with self._storage.transaction():
            self._storage.set_dependencies(task_id, payload.depends_on_ids)
        deps, depends_on = self._storage.get_dependencies(task_id)
        return TaskDependencyList(dependencies=deps, depends_on=depends_on)

    def get_dependencies(self, task_id: str) -> TaskDependencyList:
        deps, depends_on = self._storage.get_dependencies(task_id)
        return TaskDependencyList(dependencies=deps, depends_on=depends_on)

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> TaskStats:
        return self._storage.get_task_stats()

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
