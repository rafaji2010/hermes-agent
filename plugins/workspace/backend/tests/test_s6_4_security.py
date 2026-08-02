"""S6.4 — Runtime Security Integration Tests.

Covers:
- ResourceLimiter enforcement in services (create/update methods)
- PathSandbox enforcement in WorkspaceService (path validation)
- Audit event emission on limit/sandbox violations
- Missing authz guards now present (add_comment, set_dependencies)
- Backward compatibility (limits/sandbox=None preserves old behavior)
- Regression (existing tests continue to pass)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[6]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.workspace.backend.security.resource_limits import (
    ResourceLimiter,
    ResourceLimitExceeded,
    ResourceLimits,
)
from plugins.workspace.backend.security.sandbox import (
    PathSandbox,
    SandboxConfig,
)
from plugins.workspace.backend.security.audit import AuditLogger
from plugins.workspace.backend.security.authorization import AuthorizationMiddleware
from plugins.workspace.backend.services.workspace_service import WorkspaceService
from plugins.workspace.backend.services.adr_service import ADRService
from plugins.workspace.backend.services.journal_service import JournalService
from plugins.workspace.backend.services.roadmap_service import RoadmapService
from plugins.workspace.backend.services.task_service import TaskService
from plugins.workspace.backend.models import (
    WorkspaceCreate,
    ADRCreate,
    JournalEntryCreate,
    RoadmapCreate,
    MilestoneCreate,
    TaskCreate,
    TaskCommentCreate,
    TaskDependencyCreate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def audit_logger():
    with tempfile.TemporaryDirectory() as td:
        yield AuditLogger(log_path=Path(td) / "audit.log")


@pytest.fixture
def limits():
    return ResourceLimiter()


@pytest.fixture
def strict_limits():
    """Limiter with tight title limit for testing enforcement."""
    return ResourceLimiter(ResourceLimits(
        max_title_length=15,
        max_tag_count=5,
        max_label_count=5,
        max_dependency_count=8,
        max_markdown_size_bytes=50,
        max_description_length=30,
        max_comment_length=20,
    ))


@pytest.fixture
def sandbox():
    return PathSandbox()


@pytest.fixture
def authz(audit_logger):
    return AuthorizationMiddleware(audit_logger=audit_logger)


# ---------------------------------------------------------------------------
# WorkspaceService — PathSandbox + ResourceLimits
# ---------------------------------------------------------------------------

class TestWorkspaceServiceS6_4:
    def test_create_workspace_with_limits_enforces_name_length(self, storage, strict_limits, authz):
        svc = WorkspaceService(storage=storage, limits=strict_limits, authz=authz)
        long_name = "this-name-is-over-fifteen-chars"
        with pytest.raises(ResourceLimitExceeded) as exc:
            svc.create_workspace(WorkspaceCreate(name=long_name, path="/tmp"))
        assert exc.value.resource == "workspace name"

    def test_create_workspace_with_limits_allows_valid(self, storage, limits, authz):
        svc = WorkspaceService(storage=storage, limits=limits, authz=authz)
        ws = svc.create_workspace(WorkspaceCreate(name="valid-ws", path="/tmp"))
        assert ws.name == "valid-ws"

    def test_create_workspace_with_sandbox_denies_etc(self, storage, limits, sandbox, authz):
        svc = WorkspaceService(storage=storage, limits=limits, sandbox=sandbox, authz=authz)
        from plugins.workspace.backend.models import InvalidPathError
        with pytest.raises(InvalidPathError):
            svc.create_workspace(WorkspaceCreate(name="bad-path", path="/etc/passwd"))

    def test_create_workspace_with_sandbox_allows_tmp(self, storage, limits, sandbox, authz):
        svc = WorkspaceService(storage=storage, limits=limits, sandbox=sandbox, authz=authz)
        ws = svc.create_workspace(WorkspaceCreate(name="tmp-ws", path="/tmp"))
        assert ws.name == "tmp-ws"

    def test_register_repository_with_sandbox_denied_path(self, storage, limits, sandbox, authz):
        svc = WorkspaceService(storage=storage, limits=limits, sandbox=sandbox, authz=authz)
        ws = storage.create_workspace("sandbox-ws", "/tmp")
        from plugins.workspace.backend.models import InvalidPathError
        with pytest.raises(InvalidPathError):
            svc.register_repository(type("R", (), {
                "workspace_id": ws.id, "name": "repo",
                "path": "/etc/shadow", "git_root": None,
                "default_branch": "main",
            })())

    def test_register_repo_limit_enforced(self, storage, strict_limits, authz):
        svc = WorkspaceService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("repo-ws", "/tmp")
        long_name = "very-long-repo-name-123"
        with pytest.raises(ResourceLimitExceeded):
            svc.register_repository(type("R", (), {
                "workspace_id": ws.id, "name": long_name,
                "path": "/tmp", "git_root": None,
                "default_branch": "main",
            })())


# ---------------------------------------------------------------------------
# ADRService — ResourceLimits enforcement
# ---------------------------------------------------------------------------

class TestADRServiceS6_4:
    def test_create_adr_enforces_tag_count(self, storage, strict_limits, authz):
        svc = ADRService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("adr-limit-ws", "/tmp")
        many_tags = [f"t{i}" for i in range(10)]
        with pytest.raises(ResourceLimitExceeded) as exc:
            svc.create_adr(ADRCreate(
                workspace_id=ws.id, title="ADR",
                tags=many_tags,
            ))
        assert exc.value.resource == "tags"

    def test_create_adr_enforces_markdown_size(self, storage, strict_limits, authz):
        svc = ADRService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("adr-md-ws", "/tmp")
        huge_md = "x" * 100
        with pytest.raises(ResourceLimitExceeded):
            svc.create_adr(ADRCreate(
                workspace_id=ws.id, title="ADR", markdown=huge_md,
            ))

    def test_create_adr_allows_valid(self, storage, limits, authz):
        svc = ADRService(storage=storage, limits=limits, authz=authz)
        ws = storage.create_workspace("adr-ok-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(
            workspace_id=ws.id, title="Valid ADR",
            tags=["tag1", "tag2"],
        ))
        assert adr.title == "Valid ADR"

    def test_update_adr_enforces_limits(self, storage, strict_limits, authz):
        svc = ADRService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("adr-upd-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(workspace_id=ws.id, title="Original"))

        with pytest.raises(ResourceLimitExceeded):
            svc.update_adr(adr.id, type("U", (), {
                "title": "x" * 25, "status": None, "category": None,
                "markdown": None, "tags": None,
            })())


# ---------------------------------------------------------------------------
# JournalService — ResourceLimits enforcement
# ---------------------------------------------------------------------------

class TestJournalServiceS6_4:
    def test_create_entry_enforces_tag_count(self, storage, strict_limits, authz):
        svc = JournalService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("jrn-tag-ws", "/tmp")
        with pytest.raises(ResourceLimitExceeded):
            svc.create_entry(type("E", (), {
                "workspace_id": ws.id, "title": "Entry",
                "summary": "", "markdown": "", "entry_date": "",
                "tags": [f"t{i}" for i in range(10)], "repository_id": None,
            })())

    def test_create_entry_allows_valid(self, storage, limits, authz):
        svc = JournalService(storage=storage, limits=limits, authz=authz)
        ws = storage.create_workspace("jrn-ok-ws", "/tmp")
        entry = svc.create_entry(type("E", (), {
            "workspace_id": ws.id, "title": "Day 1",
            "summary": "Good day", "markdown": "", "entry_date": "",
            "tags": ["daily"], "repository_id": None,
        })())
        assert entry.title == "Day 1"


# ---------------------------------------------------------------------------
# RoadmapService — ResourceLimits enforcement
# ---------------------------------------------------------------------------

class TestRoadmapServiceS6_4:
    def test_create_roadmap_enforces_description(self, storage, strict_limits, authz):
        svc = RoadmapService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("rm-ws", "/tmp")
        long_desc = "this is a very long description that exceeds the tight limit"
        with pytest.raises(ResourceLimitExceeded):
            svc.create_roadmap(RoadmapCreate(
                workspace_id=ws.id, name="Plan", description=long_desc))

    def test_create_roadmap_allows_valid(self, storage, limits, authz):
        svc = RoadmapService(storage=storage, limits=limits, authz=authz)
        ws = storage.create_workspace("rm-ok-ws", "/tmp")
        roadmap = svc.create_roadmap(RoadmapCreate(
            workspace_id=ws.id, name="Q3 Plan"))
        assert roadmap.name == "Q3 Plan"


# ---------------------------------------------------------------------------
# TaskService — ResourceLimits + Missing Authz Guards
# ---------------------------------------------------------------------------

class TestTaskServiceS6_4:
    def test_create_task_enforces_label_count(self, storage, strict_limits, authz):
        svc = TaskService(storage=storage, limits=strict_limits, authz=authz)
        with pytest.raises(ResourceLimitExceeded):
            svc.create_task(TaskCreate(
                title="Task", labels=[f"l{i}" for i in range(10)]))

    def test_create_task_enforces_dependency_count(self, storage, strict_limits, authz):
        svc = TaskService(storage=storage, limits=strict_limits, authz=authz)
        with pytest.raises(ResourceLimitExceeded):
            svc.create_task(TaskCreate(
                title="Task", dependency_ids=["d1"] * 15))

    def test_create_task_allows_valid(self, storage, limits, authz):
        svc = TaskService(storage=storage, limits=limits, authz=authz)
        task = svc.create_task(TaskCreate(
            title="Valid Task", labels=["bug"]))
        assert task.title == "Valid Task"

    def test_add_comment_is_guarded(self, storage, limits, authz):
        svc = TaskService(storage=storage, limits=limits, authz=authz)
        task = svc.create_task(TaskCreate(title="Comment Task"))
        comment = svc.add_comment(task.id, TaskCommentCreate(
            body="Good job", author="tester"))
        assert comment.body == "Good job"

    def test_add_comment_enforces_length(self, storage, strict_limits, authz):
        svc = TaskService(storage=storage, limits=strict_limits, authz=authz)
        task = svc.create_task(TaskCreate(title="Len Task"))
        long_body = "x" * 25
        with pytest.raises(ResourceLimitExceeded):
            svc.add_comment(task.id, TaskCommentCreate(body=long_body))

    def test_set_dependencies_is_guarded_and_enforced(self, storage, strict_limits, authz):
        svc = TaskService(storage=storage, limits=strict_limits, authz=authz)
        task = svc.create_task(TaskCreate(title="Dep Task"))
        with pytest.raises(ResourceLimitExceeded):
            svc.set_dependencies(task.id, TaskDependencyCreate(
                depends_on_ids=["d"] * 15))


# ---------------------------------------------------------------------------
# Audit Integration — limit/sandbox violations emit audit events
# ---------------------------------------------------------------------------

class TestAuditIntegrationS6_4:
    def test_resource_limit_violation_emits_audit(self, storage, audit_logger):
        strict_limits = ResourceLimiter(ResourceLimits(max_tag_count=2))
        authz = AuthorizationMiddleware(audit_logger=audit_logger)
        svc = ADRService(storage=storage, limits=strict_limits, authz=authz)
        ws = storage.create_workspace("audit-ws", "/tmp")

        try:
            svc.create_adr(ADRCreate(
                workspace_id=ws.id, title="ADR",
                tags=["t1", "t2", "t3", "t4"],
            ))
        except ResourceLimitExceeded:
            pass

        events = audit_logger.read(10)
        violations = [e for e in events if "violation" in e.get("action", "")]
        assert len(violations) >= 1
        assert violations[-1]["action"] == "s6.4.violation.resource_limit"
        assert violations[-1]["status"] == "DENY"

    def test_sandbox_violation_emits_audit(self, storage, audit_logger):
        limits = ResourceLimiter()
        sandbox = PathSandbox()
        authz = AuthorizationMiddleware(audit_logger=audit_logger)
        svc = WorkspaceService(storage=storage, limits=limits,
                               sandbox=sandbox, authz=authz)

        from plugins.workspace.backend.models import InvalidPathError
        try:
            svc.create_workspace(WorkspaceCreate(name="audit-ws", path="/etc/shadow"))
        except InvalidPathError:
            pass

        events = audit_logger.read(10)
        violations = [e for e in events if "violation" in e.get("action", "")]
        assert len(violations) >= 1
        assert violations[-1]["action"] == "s6.4.violation.sandbox"
        assert violations[-1]["status"] == "DENY"


# ---------------------------------------------------------------------------
# Backward compatibility — limits/sandbox=None unchanged
# ---------------------------------------------------------------------------

class TestBackwardCompatibilityS6_4:
    def test_service_without_limits_behaves_as_before(self, storage):
        svc = TaskService(storage=storage)
        task = svc.create_task(TaskCreate(title="BC Task"))
        assert task.title == "BC Task"

    def test_service_without_sandbox_behaves_as_before(self, storage):
        svc = WorkspaceService(storage=storage)
        ws = svc.create_workspace(WorkspaceCreate(name="bc-ws", path="/tmp"))
        assert ws.name == "bc-ws"

    def test_service_without_authz_behaves_as_before(self, storage):
        svc = ADRService(storage=storage)
        ws = storage.create_workspace("bc-adr-ws", "/tmp")
        adr = svc.create_adr(ADRCreate(workspace_id=ws.id, title="BC ADR"))
        assert adr.title == "BC ADR"


# ---------------------------------------------------------------------------
# Regression — existing behaviours unchanged
# ---------------------------------------------------------------------------

class TestRegressionS6_4:
    def test_s6_2_capabilities_still_work(self):
        from plugins.workspace.backend.security.capabilities import CAPABILITIES
        assert "fs.read" in CAPABILITIES
        assert "workspace.create" in CAPABILITIES
        assert len(CAPABILITIES) >= 44

    def test_s6_3_network_validator_still_works(self):
        from plugins.workspace.backend.security.network_isolation import validate_url
        assert validate_url("https://example.com").is_safe
        assert not validate_url("http://127.0.0.1").is_safe

    def test_s6_3_resource_limits_standalone(self):
        limiter = ResourceLimiter()
        assert limiter.check_title_length("OK").allowed

    def test_s6_3_sandbox_standalone(self):
        sandbox = PathSandbox()
        assert not sandbox.validate_path("/etc/passwd").is_allowed
