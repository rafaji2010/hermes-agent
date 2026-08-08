"""Resource Limits.

Implements ADR-SEC-007 Layer 6 — centralized resource limit definitions
and enforcement for workspace operations.

Usage::

    limiter = ResourceLimiter()
    limiter.check_content_size(content, "ADR body")
    limiter.check_tag_count(tags, "ADR tags")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ResourceLimits:
    """Resource limit configuration.

    All limits can be overridden per-operation.  Defaults match the
    sandbox specification in ADR-SEC-007.
    """

    max_content_size_bytes: int = 10 * 1024 * 1024       # 10 MB
    max_file_size_bytes: int = 10 * 1024 * 1024           # 10 MB
    max_temp_space_bytes: int = 500 * 1024 * 1024         # 500 MB
    max_tag_count: int = 50
    max_label_count: int = 50
    max_markdown_size_bytes: int = 2 * 1024 * 1024        # 2 MB
    max_title_length: int = 256
    max_description_length: int = 4096
    max_comment_length: int = 8192
    max_path_length: int = 4096
    max_workspace_name_length: int = 128
    max_repository_name_length: int = 256
    max_list_size: int = 1000
    max_dependency_count: int = 100
    max_concurrent_operations: int = 10


@dataclass
class LimitCheckResult:
    """Result of a resource limit check."""

    allowed: bool
    resource: str = ""
    limit: int = 0
    actual: int = 0
    reason: str = ""


class ResourceLimitExceeded(Exception):
    """Raised when a resource limit is exceeded."""

    def __init__(self, resource: str, limit: int, actual: int):
        msg = (
            f"Resource limit exceeded for '{resource}': "
            f"max={limit}, actual={actual}"
        )
        super().__init__(msg)
        self.resource = resource
        self.limit = limit
        self.actual = actual


class ResourceLimiter:
    """Enforce resource limits for workspace operations.

    Thread-safe — all state is read-only after construction.
    """

    def __init__(self, limits: Optional[ResourceLimits] = None):
        self._limits = limits or ResourceLimits()

    # ------------------------------------------------------------------
    # Content size checks
    # ------------------------------------------------------------------

    def check_content_size(
        self, content: str, resource_name: str = "content",
        *, max_bytes: Optional[int] = None,
    ) -> LimitCheckResult:
        """Check that content does not exceed the size limit."""
        limit = max_bytes if max_bytes is not None else self._limits.max_content_size_bytes
        actual = len(content.encode("utf-8"))
        return self._check(actual, limit, resource_name)

    def check_markdown_size(
        self, content: str, resource_name: str = "markdown",
    ) -> LimitCheckResult:
        """Check that markdown content size is within limits."""
        return self.check_content_size(
            content, resource_name,
            max_bytes=self._limits.max_markdown_size_bytes,
        )

    # ------------------------------------------------------------------
    # String length checks
    # ------------------------------------------------------------------

    def check_title_length(self, title: str, resource_name: str = "title") -> LimitCheckResult:
        return self._check(
            len(title), self._limits.max_title_length, resource_name,
        )

    def check_description_length(
        self, desc: str, resource_name: str = "description",
    ) -> LimitCheckResult:
        return self._check(
            len(desc), self._limits.max_description_length, resource_name,
        )

    def check_comment_length(
        self, comment: str, resource_name: str = "comment",
    ) -> LimitCheckResult:
        return self._check(
            len(comment), self._limits.max_comment_length, resource_name,
        )

    def check_path_length(self, path: str, resource_name: str = "path") -> LimitCheckResult:
        return self._check(
            len(path), self._limits.max_path_length, resource_name,
        )

    # ------------------------------------------------------------------
    # Collection size checks
    # ------------------------------------------------------------------

    def check_tag_count(
        self, tags: List[str], resource_name: str = "tags",
    ) -> LimitCheckResult:
        return self._check(
            len(tags), self._limits.max_tag_count, resource_name,
        )

    def check_label_count(
        self, labels: List[str], resource_name: str = "labels",
    ) -> LimitCheckResult:
        return self._check(
            len(labels), self._limits.max_label_count, resource_name,
        )

    def check_list_size(
        self, items: list, resource_name: str = "list",
    ) -> LimitCheckResult:
        return self._check(
            len(items), self._limits.max_list_size, resource_name,
        )

    def check_dependency_count(
        self, deps: List[str], resource_name: str = "dependencies",
    ) -> LimitCheckResult:
        return self._check(
            len(deps), self._limits.max_dependency_count, resource_name,
        )

    # ------------------------------------------------------------------
    # Composite validation
    # ------------------------------------------------------------------

    def validate_workspace_name(self, name: str) -> LimitCheckResult:
        return self._check(
            len(name), self._limits.max_workspace_name_length, "workspace name",
        )

    def validate_repository_name(self, name: str) -> LimitCheckResult:
        return self._check(
            len(name), self._limits.max_repository_name_length, "repository name",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check(self, actual: int, limit: int, resource_name: str) -> LimitCheckResult:
        allowed = actual <= limit
        reason = ""
        if not allowed:
            reason = (
                f"'{resource_name}' exceeds limit: "
                f"{actual} > {limit}"
            )
        return LimitCheckResult(
            allowed=allowed,
            resource=resource_name,
            limit=limit,
            actual=actual,
            reason=reason,
        )

    @property
    def limits(self) -> ResourceLimits:
        return self._limits
