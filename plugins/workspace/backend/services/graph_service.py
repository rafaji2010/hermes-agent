"""Knowledge Graph Service.

Builds an in-memory graph from entity relationships, supports
related-item queries and shortest-path traversal via BFS.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from ..models import (
    GraphEdge,
    GraphNode,
    GraphResponse,
    GraphStats,
    RelatedEntity,
    RelatedItems,
    ShortestPathResponse,
    WorkspaceNotFoundError,
)
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.graph")


class GraphService:
    """Knowledge graph powered by entity relationships."""

    def __init__(self, storage: AbstractStorage):
        self._storage = storage

    # ------------------------------------------------------------------
    # Related Items
    # ------------------------------------------------------------------

    def entity_workspace_id(self, entity_type: str, entity_id: str) -> Optional[str]:
        """Resolve the workspace that OWNS an entity, or ``None`` if the
        entity does not exist.  U1D-C: the ownership boundary for every
        related-item traversal — possession of an entity id never grants
        access from another workspace.
        """
        try:
            if entity_type == "workspace":
                ws = self._storage.get_workspace(entity_id)
                return ws.id if ws else None
            if entity_type == "repository":
                repo = self._storage.get_repository(entity_id)
                return repo.workspace_id if repo else None
            if entity_type == "roadmap":
                r = self._storage.get_roadmap(entity_id)
                return r.workspace_id if r else None
            if entity_type == "milestone":
                m = self._storage.get_milestone(entity_id)
                if m is None:
                    return None
                r = self._storage.get_roadmap(m.roadmap_id)
                return r.workspace_id if r else None
            if entity_type == "adr":
                adr = self._storage.get_adr(entity_id)
                return adr.workspace_id if adr else None
            if entity_type == "journal":
                je = self._storage.get_journal_entry(entity_id)
                return je.workspace_id if je else None
            if entity_type == "task":
                t = self._storage.get_task(entity_id)
                return t.workspace_id if t else None
        except Exception:
            _log.exception("Error resolving workspace for %s:%s", entity_type, entity_id)
        return None

    def get_related(
        self,
        entity_type: str,
        entity_id: str,
        workspace_id: str = "",
    ) -> RelatedItems:
        """Return all entities related to the given entity.

        U1D-C: the entity must exist AND belong to the effective Workspace
        scope; otherwise ``WorkspaceNotFoundError`` (404) is raised — no
        existence leak and no cross-workspace traversal.
        """
        entity_ws = self.entity_workspace_id(entity_type, entity_id)
        if entity_ws is None:
            raise WorkspaceNotFoundError(entity_id)
        if workspace_id and entity_ws != workspace_id:
            raise WorkspaceNotFoundError(entity_id)

        items: List[RelatedEntity] = []

        try:
            if entity_type == "workspace":
                items.extend(self._related_workspace(entity_id))
            elif entity_type == "repository":
                items.extend(self._related_repository(entity_id))
            elif entity_type == "roadmap":
                items.extend(self._related_roadmap(entity_id))
            elif entity_type == "milestone":
                items.extend(self._related_milestone(entity_id))
            elif entity_type == "adr":
                items.extend(self._related_adr(entity_id))
            elif entity_type == "journal":
                items.extend(self._related_journal(entity_id))
            elif entity_type == "task":
                items.extend(self._related_task(entity_id))
        except Exception:
            _log.exception("Error getting related items for %s:%s", entity_type, entity_id)

        return RelatedItems(
            entity_type=entity_type,
            entity_id=entity_id,
            items=items,
        )

    # ------------------------------------------------------------------
    # Graph
    # ------------------------------------------------------------------

    def get_graph(self, workspace_id: str = "") -> GraphResponse:
        """Build the full graph, optionally scoped to a workspace."""
        nodes: Dict[str, GraphNode] = {}
        edges: List[GraphEdge] = []

        try:
            workspaces = self._storage.list_workspaces()
            if workspace_id:
                workspaces = [w for w in workspaces if w.id == workspace_id]

            for ws in workspaces:
                _add_node(nodes, ws.id, "workspace", ws.name)
                repos = self._storage.list_repositories(ws.id)
                for repo in repos:
                    _add_node(nodes, repo.id, "repository", repo.name)
                    edges.append(GraphEdge(
                        source_id=repo.id, source_type="repository",
                        target_id=ws.id, target_type="workspace",
                        relationship="belongs_to",
                    ))
                self._add_roadmap_graph(ws.id, nodes, edges)
                self._add_adr_graph(ws.id, nodes, edges)
                self._add_journal_graph(ws.id, nodes, edges)
                self._add_task_graph(ws.id, nodes, edges)
        except Exception:
            _log.exception("Error building graph")

        return GraphResponse(nodes=list(nodes.values()), edges=edges)

    # ------------------------------------------------------------------
    # Shortest Path
    # ------------------------------------------------------------------

    def shortest_path(self, source_type: str, source_id: str,
                      target_type: str, target_id: str,
                      workspace_id: str = "") -> ShortestPathResponse:
        """BFS shortest-path between two entities.

        ``workspace_id`` (S7.3A) scopes the traversed graph; an empty
        scope means "all workspaces" at the service level — the API layer
        guarantees an explicit scope for project-scoped callers.
        """
        graph = self.get_graph(workspace_id)
        adj: Dict[str, List[Tuple[str, str, str]]] = {}
        node_map: Dict[str, GraphNode] = {}

        for n in graph.nodes:
            key = f"{n.type}:{n.id}"
            node_map[key] = n
            adj.setdefault(key, [])

        edge_map: Dict[Tuple[str, str], GraphEdge] = {}
        for e in graph.edges:
            sk = f"{e.source_type}:{e.source_id}"
            tk = f"{e.target_type}:{e.target_id}"
            adj[sk].append((tk, e.relationship, e.source_id))
            adj[tk].append((sk, "reverse", e.target_id))
            edge_map[(sk, tk)] = e
            edge_map[(tk, sk)] = e

        src_key = f"{source_type}:{source_id}"
        tgt_key = f"{target_type}:{target_id}"

        if src_key not in node_map or tgt_key not in node_map:
            return ShortestPathResponse(
                path=[], edges=[], distance=-1,
            )

        visited: Set[str] = set()
        queue: deque[Tuple[str, List[str]]] = deque()
        queue.append((src_key, [src_key]))
        visited.add(src_key)

        while queue:
            current, path = queue.popleft()
            if current == tgt_key:
                nodes_path = [node_map[k] for k in path if k in node_map]
                path_edges: List[GraphEdge] = []
                for i in range(len(path) - 1):
                    ek = (path[i], path[i + 1])
                    if ek in edge_map:
                        path_edges.append(edge_map[ek])
                return ShortestPathResponse(
                    path=nodes_path,
                    edges=path_edges,
                    distance=len(path) - 1,
                )

            for neighbor, rel, _ in adj.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))

        return ShortestPathResponse(
            path=[], edges=[], distance=-1,
        )

    # ------------------------------------------------------------------
    # Graph Stats
    # ------------------------------------------------------------------

    def get_graph_stats(self, workspace_id: str = "") -> GraphStats:
        """Return aggregate graph statistics for a scope.

        ``workspace_id`` (S7.3A) scopes the statistics to one workspace;
        an empty scope means "all workspaces" at the service level — the
        API layer guarantees an explicit scope for project-scoped callers.
        """
        graph = self.get_graph(workspace_id)
        total_entities = len(graph.nodes)
        total_edges = len(graph.edges)

        node_types = set(n.type for n in graph.nodes)
        edge_source_ids = {e.source_id for e in graph.edges}
        edge_target_ids = {e.target_id for e in graph.edges}
        linked_ids = edge_source_ids | edge_target_ids

        # Orphan entities: nodes with no incoming or outgoing edges
        orphan_count = sum(
            1 for n in graph.nodes
            if n.id not in linked_ids
        )

        # Compute domain-specific stats (scoped where possible)
        try:
            tasks = self._storage.list_tasks(workspace_id=workspace_id)
            tasks_wo_ms = sum(1 for t in tasks if not t.milestone_id)
        except Exception:
            tasks_wo_ms = 0

        try:
            ms_wo_tasks = 0
            scoped = self._storage.list_workspaces()
            if workspace_id:
                scoped = [w for w in scoped if w.id == workspace_id]
            for ws in scoped:
                for r in self._storage.list_roadmaps(ws.id):
                    m_count = r.milestone_count
                    ms_wo_tasks += sum(
                        1 for m in r.milestones
                        if not self._storage.list_tasks(
                            ws.id, milestone_id=m.id,
                        )
                    )
        except Exception:
            ms_wo_tasks = 0

        try:
            roads_wo_ms = 0
            scoped = self._storage.list_workspaces()
            if workspace_id:
                scoped = [w for w in scoped if w.id == workspace_id]
            for ws in scoped:
                for r in self._storage.list_roadmaps(ws.id):
                    if r.milestone_count == 0:
                        roads_wo_ms += 1
        except Exception:
            roads_wo_ms = 0

        return GraphStats(
            total_entities=total_entities,
            total_edges=total_edges,
            orphan_entities=orphan_count,
            tasks_without_milestones=tasks_wo_ms,
            milestones_without_tasks=ms_wo_tasks,
            roadmaps_without_milestones=roads_wo_ms,
        )

    # ------------------------------------------------------------------
    # Per-type relationship queries
    # ------------------------------------------------------------------

    def _related_workspace(self, workspace_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            repos = self._storage.list_repositories(workspace_id)
            for r in repos:
                items.append(RelatedEntity(id=r.id, type="repository", title=r.name, relationship="contains"))
            maps = self._storage.list_roadmaps(workspace_id)
            for r in maps:
                items.append(RelatedEntity(id=r.id, type="roadmap", title=r.name, relationship="contains"))
                for m in r.milestones:
                    items.append(RelatedEntity(id=m.id, type="milestone", title=m.title, relationship="contains", status=m.status))
            adrs = self._storage.list_adrs(workspace_id)
            for a in adrs:
                items.append(RelatedEntity(id=a.id, type="adr", title=a.title, relationship="contains", status=a.status))
            entries = self._storage.list_journal_entries(workspace_id)
            for je in entries:
                items.append(RelatedEntity(id=je.id, type="journal", title=je.title, relationship="contains"))
            tasks = self._storage.list_tasks(workspace_id)
            for t in tasks:
                items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="contains", status=t.status))
        except Exception:
            pass
        return items

    def _related_repository(self, repo_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            repo = self._storage.get_repository(repo_id)
            if repo:
                items.append(RelatedEntity(id=repo.workspace_id, type="workspace", title="parent", relationship="belongs_to"))
            tasks = self._storage.list_tasks(workspace_id="", repository_id=repo_id)
            for t in tasks:
                items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="task_in_repo", status=t.status))
        except Exception:
            pass
        return items

    def _related_roadmap(self, roadmap_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            r = self._storage.get_roadmap(roadmap_id)
            if r:
                ws = self._storage.get_workspace(r.workspace_id)
                if ws:
                    items.append(RelatedEntity(id=ws.id, type="workspace", title=ws.name, relationship="belongs_to"))
                for m in r.milestones:
                    items.append(RelatedEntity(id=m.id, type="milestone", title=m.title, relationship="has_milestone", status=m.status))
                tasks = self._storage.list_tasks(r.workspace_id, roadmap_id=roadmap_id)
                for t in tasks:
                    items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="has_task", status=t.status))
        except Exception:
            pass
        return items

    def _related_milestone(self, milestone_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            m = self._storage.get_milestone(milestone_id)
            if m:
                r = self._storage.get_roadmap(m.roadmap_id)
                if r:
                    items.append(RelatedEntity(id=r.id, type="roadmap", title=r.name, relationship="milestone_of"))
                    ws = self._storage.get_workspace(r.workspace_id)
                    if ws:
                        items.append(RelatedEntity(id=ws.id, type="workspace", title=ws.name, relationship="in_workspace"))
                tasks = self._storage.list_tasks(r.workspace_id if r else "", milestone_id=milestone_id)
                for t in tasks:
                    items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="has_task", status=t.status))
        except Exception:
            pass
        return items

    def _related_adr(self, adr_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            adr = self._storage.get_adr(adr_id)
            if adr:
                ws = self._storage.get_workspace(adr.workspace_id)
                if ws:
                    items.append(RelatedEntity(id=ws.id, type="workspace", title=ws.name, relationship="belongs_to"))
                tasks = self._storage.list_tasks(adr.workspace_id, adr_id=adr_id)
                for t in tasks:
                    items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="implements_adr", status=t.status))
        except Exception:
            pass
        return items

    def _related_journal(self, journal_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            je = self._storage.get_journal_entry(journal_id)
            if je is None:
                return items
            ws = self._storage.get_workspace(je.workspace_id)
            if ws:
                items.append(RelatedEntity(id=ws.id, type="workspace", title=ws.name, relationship="belongs_to"))
            tasks = self._storage.list_tasks(je.workspace_id, journal_id=journal_id)
            for t in tasks:
                items.append(RelatedEntity(id=t.id, type="task", title=t.title, relationship="logged_in", status=t.status))
        except Exception:
            pass
        return items

    def _related_task(self, task_id: str) -> List[RelatedEntity]:
        items: List[RelatedEntity] = []
        try:
            t = self._storage.get_task(task_id)
            if t:
                if t.workspace_id:
                    ws = self._storage.get_workspace(t.workspace_id)
                    if ws:
                        items.append(RelatedEntity(id=ws.id, type="workspace", title=ws.name, relationship="belongs_to"))
                if t.repository_id:
                    repo = self._storage.get_repository(t.repository_id)
                    if repo:
                        items.append(RelatedEntity(id=repo.id, type="repository", title=repo.name, relationship="in_repository"))
                if t.roadmap_id:
                    r = self._storage.get_roadmap(t.roadmap_id)
                    if r:
                        items.append(RelatedEntity(id=r.id, type="roadmap", title=r.name, relationship="part_of"))
                if t.milestone_id:
                    m = self._storage.get_milestone(t.milestone_id)
                    if m:
                        items.append(RelatedEntity(id=m.id, type="milestone", title=m.title, relationship="contributes_to"))
                        r = self._storage.get_roadmap(m.roadmap_id)
                        if r:
                            items.append(RelatedEntity(id=r.id, type="roadmap", title=r.name, relationship="roadmap_of_milestone"))
                if t.adr_id:
                    adr = self._storage.get_adr(t.adr_id)
                    if adr:
                        items.append(RelatedEntity(id=adr.id, type="adr", title=adr.title, relationship="implements"))
                if t.journal_id:
                    entries = self._storage.list_journal_entries(t.workspace_id or "")
                    for je in entries:
                        if je.id == t.journal_id:
                            items.append(RelatedEntity(id=je.id, type="journal", title=je.title, relationship="logged_in"))
                            break
                deps, depends_on = self._storage.get_dependencies(task_id)
                for d in deps:
                    items.append(RelatedEntity(id=d.id, type="task", title=d.title, relationship="dependency_of"))
                for d in depends_on:
                    items.append(RelatedEntity(id=d.id, type="task", title=d.title, relationship="depends_on"))
        except Exception:
            pass
        return items

    # ------------------------------------------------------------------
    # Graph building helpers
    # ------------------------------------------------------------------

    def _add_roadmap_graph(self, workspace_id: str, nodes: Dict[str, GraphNode],
                           edges: List[GraphEdge]) -> None:
        for r in self._storage.list_roadmaps(workspace_id):
            _add_node(nodes, r.id, "roadmap", r.name)
            edges.append(GraphEdge(
                source_id=r.id, source_type="roadmap",
                target_id=workspace_id, target_type="workspace",
                relationship="belongs_to",
            ))
            for m in r.milestones:
                _add_node(nodes, m.id, "milestone", m.title)
                edges.append(GraphEdge(
                    source_id=m.id, source_type="milestone",
                    target_id=r.id, target_type="roadmap",
                    relationship="milestone_of",
                ))

    def _add_adr_graph(self, workspace_id: str, nodes: Dict[str, GraphNode],
                        edges: List[GraphEdge]) -> None:
        for a in self._storage.list_adrs(workspace_id):
            _add_node(nodes, a.id, "adr", a.title)
            edges.append(GraphEdge(
                source_id=a.id, source_type="adr",
                target_id=workspace_id, target_type="workspace",
                relationship="belongs_to",
            ))
            if a.repository_id:
                _add_node(nodes, a.repository_id, "repository", "repo")
                edges.append(GraphEdge(
                    source_id=a.id, source_type="adr",
                    target_id=a.repository_id, target_type="repository",
                    relationship="in_repository",
                ))

    def _add_journal_graph(self, workspace_id: str, nodes: Dict[str, GraphNode],
                           edges: List[GraphEdge]) -> None:
        for je in self._storage.list_journal_entries(workspace_id):
            _add_node(nodes, je.id, "journal", je.title)
            edges.append(GraphEdge(
                source_id=je.id, source_type="journal",
                target_id=workspace_id, target_type="workspace",
                relationship="belongs_to",
            ))

    def _add_task_graph(self, workspace_id: str, nodes: Dict[str, GraphNode],
                        edges: List[GraphEdge]) -> None:
        for t in self._storage.list_tasks(workspace_id):
            _add_node(nodes, t.id, "task", t.title)
            edges.append(GraphEdge(
                source_id=t.id, source_type="task",
                target_id=workspace_id, target_type="workspace",
                relationship="belongs_to",
            ))
            if t.repository_id:
                edges.append(GraphEdge(
                    source_id=t.id, source_type="task",
                    target_id=t.repository_id, target_type="repository",
                    relationship="in_repository",
                ))
            if t.roadmap_id:
                edges.append(GraphEdge(
                    source_id=t.id, source_type="task",
                    target_id=t.roadmap_id, target_type="roadmap",
                    relationship="in_roadmap",
                ))
            if t.milestone_id:
                edges.append(GraphEdge(
                    source_id=t.id, source_type="task",
                    target_id=t.milestone_id, target_type="milestone",
                    relationship="contributes_to",
                ))
            if t.adr_id:
                edges.append(GraphEdge(
                    source_id=t.id, source_type="task",
                    target_id=t.adr_id, target_type="adr",
                    relationship="implements",
                ))
            if t.journal_id:
                edges.append(GraphEdge(
                    source_id=t.id, source_type="task",
                    target_id=t.journal_id, target_type="journal",
                    relationship="logged_in",
                ))


def _add_node(nodes: Dict[str, GraphNode], id_: str, type_: str,
              title: str) -> None:
    key = id_
    if key not in nodes:
        nodes[key] = GraphNode(id=id_, type=type_, title=title)
