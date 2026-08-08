"""Capability Registry.

Implements the 25-capability registry from ADR-SEC-003.  Each capability
defines its identifier, description, default tier, approval requirement,
and audit requirement.

No enforcement — this is the registry that later milestones will query.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .models import CapabilityDef


# ── 25 Capability Registry ──────────────────────────────────────────────────

CAPABILITIES: Dict[str, CapabilityDef] = {
    # ── Filesystem ──
    "fs.read": CapabilityDef(
        identifier="fs.read",
        description="Read files within workspace and configured repositories",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "fs.write": CapabilityDef(
        identifier="fs.write",
        description="Write/create files in workspace and temp directories",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "fs.delete": CapabilityDef(
        identifier="fs.delete",
        description="Delete files within workspace directory",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "fs.execute": CapabilityDef(
        identifier="fs.execute",
        description="Execute scripts within workspace",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Shell ──
    "shell.exec": CapabilityDef(
        identifier="shell.exec",
        description="Execute shell commands",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "shell.background": CapabilityDef(
        identifier="shell.background",
        description="Execute commands in background (async)",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "shell.sudo": CapabilityDef(
        identifier="shell.sudo",
        description="Execute commands with elevated permissions",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Git ──
    "git.read": CapabilityDef(
        identifier="git.read",
        description="Read git status, log, diff",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "git.commit": CapabilityDef(
        identifier="git.commit",
        description="Create git commits",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "git.push": CapabilityDef(
        identifier="git.push",
        description="Push commits to remote",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "git.force_push": CapabilityDef(
        identifier="git.force_push",
        description="Force push to remote",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Network ──
    "network.http": CapabilityDef(
        identifier="network.http",
        description="Make HTTP/HTTPS requests to public internet",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "network.internal": CapabilityDef(
        identifier="network.internal",
        description="Access private/internal network addresses",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),
    "network.file": CapabilityDef(
        identifier="network.file",
        description="Access file:// protocol URLs",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Browser ──
    "browser.navigate": CapabilityDef(
        identifier="browser.navigate",
        description="Navigate headless browser to URLs",
        tier=2,
        approval_required=True,
        audit_required=False,
        scope="tool",
    ),

    # ── Search ──
    "search.internal": CapabilityDef(
        identifier="search.internal",
        description="Search local files and content",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "search.external": CapabilityDef(
        identifier="search.external",
        description="Search the web",
        tier=2,
        approval_required=True,
        audit_required=False,
        scope="tool",
    ),

    # ── Vision ──
    "vision.analyze": CapabilityDef(
        identifier="vision.analyze",
        description="Process and analyze images",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),

    # ── Delegation ──
    "delegate.spawn": CapabilityDef(
        identifier="delegate.spawn",
        description="Create subagent for parallel work",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Plugins ──
    "plugin.call": CapabilityDef(
        identifier="plugin.call",
        description="Invoke plugin tool",
        tier=2,
        approval_required=False,  # per-plugin gate handles this
        audit_required=False,
        scope="plugin",
    ),

    # ── Memory ──
    "memory.read": CapabilityDef(
        identifier="memory.read",
        description="Recall stored memories",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "memory.write": CapabilityDef(
        identifier="memory.write",
        description="Store new memories",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),

    # ── Configuration ──
    "config.read": CapabilityDef(
        identifier="config.read",
        description="Read configuration values",
        tier=1,
        approval_required=False,
        audit_required=False,
        scope="tool",
    ),
    "config.write": CapabilityDef(
        identifier="config.write",
        description="Modify configuration",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Cron ──
    "cron.schedule": CapabilityDef(
        identifier="cron.schedule",
        description="Schedule recurring jobs",
        tier=3,
        approval_required=True,
        audit_required=True,
        scope="tool",
    ),

    # ── Workspace Domain ──
    "workspace.create": CapabilityDef(
        identifier="workspace.create",
        description="Create a new workspace",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "workspace.update": CapabilityDef(
        identifier="workspace.update",
        description="Update an existing workspace",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "workspace.delete": CapabilityDef(
        identifier="workspace.delete",
        description="Delete a workspace and its contents",
        tier=2,
        approval_required=True,
        audit_required=True,
        scope="workspace",
    ),

    "workspace.scope.read": CapabilityDef(
        identifier="workspace.scope.read",
        description="Read workspace ↔ Hermes Project mappings and resolve scope",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "workspace.scope.link": CapabilityDef(
        identifier="workspace.scope.link",
        description="Link or unlink a workspace to/from a Hermes Project",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "repository.register": CapabilityDef(
        identifier="repository.register",
        description="Register a repository under a workspace",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "adr.create": CapabilityDef(
        identifier="adr.create",
        description="Create an architecture decision record",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "adr.update": CapabilityDef(
        identifier="adr.update",
        description="Update an existing ADR",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "adr.delete": CapabilityDef(
        identifier="adr.delete",
        description="Delete an ADR",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "adr.reconcile.read": CapabilityDef(
        identifier="adr.reconcile.read",
        description="Inspect canonical ADR reconciliation state and preview operations",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "adr.reconcile.write": CapabilityDef(
        identifier="adr.reconcile.write",
        description="Reconcile, materialize, or update canonical ADR files",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "journal.create": CapabilityDef(
        identifier="journal.create",
        description="Create an engineering journal entry",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "journal.update": CapabilityDef(
        identifier="journal.update",
        description="Update an existing journal entry",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "journal.delete": CapabilityDef(
        identifier="journal.delete",
        description="Delete a journal entry",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "roadmap.create": CapabilityDef(
        identifier="roadmap.create",
        description="Create a roadmap",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "roadmap.update": CapabilityDef(
        identifier="roadmap.update",
        description="Update an existing roadmap",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "roadmap.delete": CapabilityDef(
        identifier="roadmap.delete",
        description="Delete a roadmap and its milestones",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "milestone.create": CapabilityDef(
        identifier="milestone.create",
        description="Create a roadmap milestone",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "milestone.update": CapabilityDef(
        identifier="milestone.update",
        description="Update an existing milestone",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "milestone.delete": CapabilityDef(
        identifier="milestone.delete",
        description="Delete a milestone",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),

    "task.create": CapabilityDef(
        identifier="task.create",
        description="Create a task",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "task.update": CapabilityDef(
        identifier="task.update",
        description="Update an existing task",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
    "task.delete": CapabilityDef(
        identifier="task.delete",
        description="Delete a task and its comments/dependencies",
        tier=1,
        approval_required=False,
        audit_required=True,
        scope="workspace",
    ),
}


class CapabilityRegistry:
    """Registry of security capabilities.

    Provides lookup, listing, and filtering.  Later milestones will add
    enforcement logic here.
    """

    def __init__(self, capabilities: Dict[str, CapabilityDef] | None = None):
        self._caps = dict(capabilities) if capabilities else dict(CAPABILITIES)

    def get(self, identifier: str) -> CapabilityDef | None:
        """Return a capability by identifier, or None."""
        return self._caps.get(identifier)

    def list_all(self) -> List[CapabilityDef]:
        """Return all registered capabilities."""
        return list(self._caps.values())

    def list_by_tier(self, tier: int) -> List[CapabilityDef]:
        """Return capabilities at a specific tier."""
        return [c for c in self._caps.values() if c.tier == tier]

    def list_by_scope(self, scope: str) -> List[CapabilityDef]:
        """Return capabilities for a given scope."""
        return [c for c in self._caps.values() if c.scope == scope]

    def requires_approval(self, identifier: str) -> bool:
        """Return True if this capability requires user approval."""
        cap = self._caps.get(identifier)
        return cap.approval_required if cap else True  # deny by default

    def requires_audit(self, identifier: str) -> bool:
        """Return True if this capability requires audit logging."""
        cap = self._caps.get(identifier)
        return cap.audit_required if cap else True

    def register(self, cap: CapabilityDef) -> None:
        """Register or override a capability."""
        self._caps[cap.identifier] = cap

    @property
    def count(self) -> int:
        return len(self._caps)
