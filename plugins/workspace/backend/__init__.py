"""Workspace Plugin — Backend Module.

This package provides the storage layer, business services, and REST API
for the Workspace plugin.

Architecture::

    REST API (api/v1.py)
         ↓
    WorkspaceService (services/workspace_service.py)
         ↓
    AbstractStorage (storage/__init__.py)  ←  interface
         ↓
    SQLiteStorage (storage/sqlite_storage.py)  ←  implementation
         ↓
    SQLite (workspace.db)

The rest of the plugin must NEVER access SQLite directly.  All data
access goes through the ``AbstractStorage`` interface so that future
backends (Postgres, remote sync, etc.) can be substituted without
changing business logic.
"""

from .database import DatabaseManager, get_database
from .models import (
    ADR,
    ADRCreate,
    ADRUpdate,
    Repository,
    RepositoryRegister,
    Workspace,
    WorkspaceCreate,
    WorkspaceError,
    WorkspaceNotFoundError,
    DuplicateWorkspaceError,
    RepositoryNotFoundError,
    DuplicateRepositoryError,
    InvalidPathError,
    NotAGitRepositoryError,
    ADRError,
    ADRNotFoundError,
    DuplicateSlugError,
    InvalidADRStatusError,
)
from .storage import AbstractStorage
from .storage.sqlite_storage import SQLiteStorage
from .services.workspace_service import WorkspaceService
from .services.adr_service import ADRService

__all__ = [
    # Database
    "DatabaseManager",
    "get_database",
    # Storage
    "AbstractStorage",
    "SQLiteStorage",
    # Services
    "WorkspaceService",
    "ADRService",
    # Models
    "Workspace",
    "WorkspaceCreate",
    "Repository",
    "RepositoryRegister",
    "ADR",
    "ADRCreate",
    "ADRUpdate",
    # Errors
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "DuplicateWorkspaceError",
    "RepositoryNotFoundError",
    "DuplicateRepositoryError",
    "InvalidPathError",
    "NotAGitRepositoryError",
    "ADRError",
    "ADRNotFoundError",
    "DuplicateSlugError",
    "InvalidADRStatusError",
]
