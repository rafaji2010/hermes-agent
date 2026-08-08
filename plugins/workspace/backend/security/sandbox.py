"""Path Sandbox.

Implements ADR-SEC-007 Layers 1-2 — filesystem path isolation,
allow/deny lists, working directory validation, and sandbox
configuration.

Usage::

    sandbox = PathSandbox(workspace_root="/home/user/projects/my-workspace")
    result = sandbox.validate_path("/home/user/projects/my-workspace/src/main.py")
    assert result.is_allowed
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class PathValidationResult:
    """Result of a path validation check."""

    is_allowed: bool
    path: str
    canonical_path: str = ""
    reason: str = ""
    category: str = ""  # allowed, denied_absolute, denied_system, denied_hidden, denied_symlink


@dataclass
class SandboxConfig:
    """Configuration for path sandbox isolation.

    Defines the allowed scopes and denied patterns for filesystem access.
    """

    # Allowed write scopes
    workspace_root: str = ""
    temp_root: str = "/tmp/hermes-agent"

    # Denied path prefixes
    denied_prefixes: tuple = (
        "/etc/", "/usr/", "/boot/", "/proc/", "/sys/", "/dev/",
        "/var/log/", "/var/run/",
    )

    # Denied hidden directories under HOME
    denied_home_dirs: tuple = (
        ".ssh", ".aws", ".gnupg", ".docker", ".kube",
        ".config/gcloud", ".config/gh",
    )

    # Operations allowed
    allow_read_outside_workspace: bool = True
    allow_write: bool = True
    allow_delete: bool = True
    allow_execute: bool = False
    allow_symlinks: bool = False
    allow_hidden_files: bool = False


class PathSandbox:
    """Validate and restrict filesystem paths based on sandbox configuration.

    Workspace paths are safe.  System paths are denied.  Temp paths are
    allowed within the session scope.
    """

    def __init__(self, config: Optional[SandboxConfig] = None):
        self._config = config or SandboxConfig()
        self._workspace_root: Optional[Path] = None

        if self._config.workspace_root:
            self._workspace_root = Path(self._config.workspace_root).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_path(
        self,
        path: str,
        *,
        operation: str = "read",
    ) -> PathValidationResult:
        """Validate that a filesystem path is safe for the given operation.

        Parameters
        ----------
        path:
            The path to validate.
        operation:
            ``"read"``, ``"write"``, ``"delete"``, or ``"execute"``.
        """
        if not path:
            return PathValidationResult(
                is_allowed=False, path=path,
                reason="Empty path", category="denied_empty",
            )

        try:
            p = Path(path).expanduser()
        except (ValueError, RuntimeError):
            return PathValidationResult(
                is_allowed=False, path=path,
                reason="Invalid path characters", category="denied_invalid",
            )

        # Resolve to canonical absolute path
        try:
            canonical = p.resolve()
        except (OSError, RuntimeError):
            return PathValidationResult(
                is_allowed=False, path=path,
                reason="Path resolution failed", category="denied_resolve",
            )

        canonical_str = str(canonical)

        # Check absolute system paths
        if any(canonical_str.startswith(prefix)
               for prefix in self._config.denied_prefixes):
            return PathValidationResult(
                is_allowed=False, path=path,
                canonical_path=canonical_str,
                reason=f"Path '{canonical_str}' is in a denied system directory",
                category="denied_system",
            )

        # Check hidden directories inside HOME
        home = Path.home()
        if canonical_str.startswith(str(home)):
            relative = str(canonical.relative_to(home))
            parts = relative.split(os.sep)
            for denied_dir in self._config.denied_home_dirs:
                denied_parts = denied_dir.split(os.sep)
                if parts[:len(denied_parts)] == denied_parts:
                    return PathValidationResult(
                        is_allowed=False, path=path,
                        canonical_path=canonical_str,
                        reason=f"Path accesses denied hidden directory '{denied_dir}'",
                        category="denied_hidden",
                    )

        # Check symlinks
        if not self._config.allow_symlinks:
            if p.is_symlink() or (canonical != p and canonical.is_symlink()):
                return PathValidationResult(
                    is_allowed=False, path=path,
                    canonical_path=canonical_str,
                    reason="Symlinks are not allowed",
                    category="denied_symlink",
                )

        # Operation-specific checks
        if operation == "write" and not self._config.allow_write:
            return PathValidationResult(
                is_allowed=False, path=path,
                canonical_path=canonical_str,
                reason="Write operations are disabled",
                category="denied_write",
            )

        if operation == "delete" and not self._config.allow_delete:
            return PathValidationResult(
                is_allowed=False, path=path,
                canonical_path=canonical_str,
                reason="Delete operations are disabled",
                category="denied_delete",
            )

        if operation == "execute" and not self._config.allow_execute:
            return PathValidationResult(
                is_allowed=False, path=path,
                canonical_path=canonical_str,
                reason="Execute operations are disabled",
                category="denied_execute",
            )

        # If workspace root is set, check for write/delete in workspace scope
        if self._workspace_root and operation in ("write", "delete", "execute"):
            if not (canonical_str.startswith(str(self._workspace_root)) or
                    canonical_str.startswith(self._config.temp_root)):
                if not self._config.allow_read_outside_workspace:
                    return PathValidationResult(
                        is_allowed=False, path=path,
                        canonical_path=canonical_str,
                        reason=f"Path outside workspace root: {self._workspace_root}",
                        category="denied_scope",
                    )

        return PathValidationResult(
            is_allowed=True, path=path,
            canonical_path=canonical_str,
            category="allowed",
        )

    def validate_paths(
        self,
        paths: List[str],
        operation: str = "read",
    ) -> dict[str, PathValidationResult]:
        """Validate multiple paths.  Returns ``{path: result, …}``."""
        return {p: self.validate_path(p, operation=operation) for p in paths}

    def is_in_workspace(self, path: str) -> bool:
        """Return True if the path is within the workspace root."""
        if not self._workspace_root:
            return False
        try:
            resolved = Path(path).expanduser().resolve()
            return str(resolved).startswith(str(self._workspace_root))
        except (OSError, RuntimeError, ValueError):
            return False

    def is_safe_temp(self, path: str) -> bool:
        """Return True if the path is within the configured temp root."""
        try:
            resolved = Path(path).expanduser().resolve()
            return str(resolved).startswith(self._config.temp_root)
        except (OSError, RuntimeError, ValueError):
            return False

    @property
    def config(self) -> SandboxConfig:
        return self._config

    @property
    def workspace_root(self) -> Optional[Path]:
        return self._workspace_root
