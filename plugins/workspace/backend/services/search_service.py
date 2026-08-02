"""Search Service.

Unified full-text search across all workspace entity types with
support for filter syntax (``key:value``) and ranked results.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..models import SearchResponse, SearchResult
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.search")

FILTER_RE = re.compile(r"(\w+):([\w\-._ ]+)")

ENTITY_TYPES = ["workspace", "repository", "roadmap", "milestone", "adr",
                "journal", "task"]


class SearchService:
    """Search across all entity types with filter syntax."""

    def __init__(self, storage: AbstractStorage):
        self._storage = storage

    def search(self, q: str = "", filters: Dict[str, str] | None = None,
               workspace_id: str = "", limit: int = 50) -> SearchResponse:
        """Perform a unified search.

        ``q`` may contain bare search terms AND ``key:value`` filters
        (e.g. ``status:blocked priority:high type:task label:backend``).
        Explicit ``filters`` parameter overrides any inline filters.
        """
        merged_filters: Dict[str, str] = {}
        bare_query = q
        raw_filters = filters if filters is not None else {}

        # Extract inline filter syntax
        matches = FILTER_RE.findall(q)
        for key, val in matches:
            merged_filters[key.lower()] = val.strip()
        bare_query = FILTER_RE.sub("", q).strip()

        # Explicit filters override inline
        merged_filters.update(raw_filters)

        # Determine which entity types to search
        types = _type_list(merged_filters.get("type"))

        all_results: List[SearchResult] = []

        if not types or "adr" in types:
            all_results.extend(self._search_adrs(workspace_id, merged_filters, bare_query))
        if not types or "journal" in types:
            all_results.extend(self._search_journals(workspace_id, merged_filters, bare_query))
        if not types or "roadmap" in types:
            all_results.extend(self._search_roadmaps(workspace_id, merged_filters, bare_query))
        if not types or "milestone" in types:
            all_results.extend(self._search_milestones(workspace_id, merged_filters, bare_query))
        if not types or "task" in types:
            all_results.extend(self._search_tasks(workspace_id, merged_filters, bare_query))
        if not types or "repository" in types:
            all_results.extend(self._search_repositories(workspace_id, merged_filters, bare_query))
        if not types or "workspace" in types:
            all_results.extend(self._search_workspaces(workspace_id, merged_filters, bare_query))

        # Rank results by score descending
        all_results.sort(key=lambda r: r.score, reverse=True)
        sliced = all_results[:limit]

        return SearchResponse(
            results=sliced,
            total=len(all_results),
            query=q,
            filters=merged_filters,
        )

    # ------------------------------------------------------------------
    # Per-type search
    # ------------------------------------------------------------------

    def _search_workspaces(self, workspace_id: str, filters: Dict[str, str],
                           q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            workspaces = self._storage.list_workspaces()
            for ws in workspaces:
                if workspace_id and ws.id != workspace_id:
                    continue
                score = _match_score(q, ws.name)
                if q and score == 0:
                    continue
                if filters.get("workspace") and filters["workspace"] not in ws.name:
                    continue
                results.append(SearchResult(
                    id=ws.id, type="workspace", title=ws.name,
                    description=ws.path, status="active",
                    workspace_id=ws.id, workspace_name=ws.name,
                    created_at=ws.created_at, score=score,
                ))
        except Exception:
            pass
        return results

    def _search_repositories(self, workspace_id: str, filters: Dict[str, str],
                             q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            repos = self._storage.list_repositories(workspace_id) if workspace_id else []
            if not workspace_id:
                for ws in self._storage.list_workspaces():
                    repos.extend(self._storage.list_repositories(ws.id))
            for repo in repos:
                score = _match_score(q, repo.name + " " + repo.path)
                if q and score == 0:
                    continue
                if filters.get("repository") and filters["repository"].lower() not in repo.name.lower():
                    continue
                results.append(SearchResult(
                    id=repo.id, type="repository", title=repo.name,
                    description=repo.path, status="active",
                    workspace_id=repo.workspace_id, workspace_name="",
                    created_at=repo.created_at, score=score,
                ))
        except Exception:
            pass
        return results

    def _search_roadmaps(self, workspace_id: str, filters: Dict[str, str],
                         q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            roadmaps = self._storage.list_roadmaps(workspace_id) if workspace_id else []
            if not workspace_id:
                for ws in self._storage.list_workspaces():
                    roadmaps.extend(self._storage.list_roadmaps(ws.id))
            for r in roadmaps:
                score = _match_score(q, r.name + " " + r.description)
                if q and score == 0:
                    continue
                if filters.get("roadmap") and filters["roadmap"].lower() not in r.name.lower():
                    continue
                status = "has_milestones" if r.milestone_count > 0 else "empty"
                results.append(SearchResult(
                    id=r.id, type="roadmap", title=r.name,
                    description=r.description, status=status,
                    workspace_id=r.workspace_id, workspace_name="",
                    created_at=r.created_at, score=score,
                ))
        except Exception:
            pass
        return results

    def _search_milestones(self, workspace_id: str, filters: Dict[str, str],
                           q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            roadmaps = self._storage.list_roadmaps(workspace_id) if workspace_id else []
            if not workspace_id:
                for ws in self._storage.list_workspaces():
                    roadmaps.extend(self._storage.list_roadmaps(ws.id))
            for r in roadmaps:
                for m in r.milestones:
                    score = _match_score(q, m.title + " " + m.description)
                    if q and score == 0:
                        continue
                    if filters.get("status") and filters["status"] != m.status:
                        continue
                    if filters.get("roadmap") and filters["roadmap"].lower() not in r.name.lower():
                        continue
                    results.append(SearchResult(
                        id=m.id, type="milestone", title=m.title,
                        description=m.description, status=m.status,
                        workspace_id=r.workspace_id, workspace_name=r.name,
                        created_at=m.created_at, score=score,
                    ))
        except Exception:
            pass
        return results

    def _search_adrs(self, workspace_id: str, filters: Dict[str, str],
                     q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            if workspace_id:
                adrs = self._storage.list_adrs(workspace_id)
            else:
                adrs = []
                for ws in self._storage.list_workspaces():
                    adrs.extend(self._storage.list_adrs(ws.id))
            for a in adrs:
                text = a.title + " " + a.markdown
                score = _match_score(q, text)
                if q and score == 0:
                    continue
                if filters.get("status") and filters["status"] != a.status:
                    continue
                if filters.get("label"):
                    lbl = filters["label"].lower()
                    if lbl not in [t.lower() for t in a.tags]:
                        continue
                results.append(SearchResult(
                    id=a.id, type="adr", title=a.title,
                    description=a.markdown[:200] if a.markdown else "",
                    status=a.status, labels=a.tags,
                    workspace_id=a.workspace_id, workspace_name="",
                    created_at=a.created_at, score=score,
                    # S7.3A — canonical provenance: git_file ADRs are
                    # projections of a canonical repository file.
                    source_type=(
                        "git_adr" if a.source == "git_file" else "workspace_adr"
                    ),
                    canonical_id=(
                        a.canonical_path or a.slug
                    ),
                ))
        except Exception:
            pass
        return results

    def _search_journals(self, workspace_id: str, filters: Dict[str, str],
                         q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            if workspace_id:
                entries = self._storage.list_journal_entries(workspace_id)
            else:
                entries = []
                for ws in self._storage.list_workspaces():
                    entries.extend(self._storage.list_journal_entries(ws.id))
            for je in entries:
                text = je.title + " " + je.markdown
                score = _match_score(q, text)
                if q and score == 0:
                    continue
                if filters.get("label"):
                    lbl = filters["label"].lower()
                    if lbl not in [t.lower() for t in je.tags]:
                        continue
                results.append(SearchResult(
                    id=je.id, type="journal", title=je.title,
                    description=je.summary or (je.markdown[:200] if je.markdown else ""),
                    status="", labels=je.tags,
                    workspace_id=je.workspace_id, workspace_name="",
                    created_at=je.created_at, score=score,
                ))
        except Exception:
            pass
        return results

    def _search_tasks(self, workspace_id: str, filters: Dict[str, str],
                      q: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        try:
            tasks = self._storage.list_tasks(
                workspace_id,
                status=filters.get("status"),
                priority=filters.get("priority"),
                label=filters.get("label"),
                repository_id=filters.get("repository"),
                roadmap_id=filters.get("roadmap"),
                q=q,
                limit=100,
            )
            for t in tasks:
                score = _match_score(q, t.title + " " + t.description) or 1.0
                results.append(SearchResult(
                    id=t.id, type="task", title=t.title,
                    description=t.description, status=t.status,
                    priority=t.priority, labels=t.labels,
                    workspace_id=t.workspace_id, workspace_name="",
                    created_at=t.created_at, score=score,
                ))
        except Exception:
            pass
        return results


def _type_list(type_str: Any) -> List[str]:
    """Parse a type filter into a list of valid entity types."""
    if not type_str:
        return []
    items = [t.strip().lower() for t in str(type_str).split(",")]
    return [t for t in items if t in ENTITY_TYPES]


def _match_score(query: str, text: str) -> float:
    """Compute a relevance score.

    Title/exact matches score higher than body substring matches.
    """
    if not query or not text:
        return 1.0 if not query else 0.0
    q = query.lower()
    t = text.lower()
    if q == t:
        return 10.0
    if t.startswith(q):
        return 5.0
    if q in t:
        return 2.0
    words = q.split()
    if len(words) > 1 and all(w in t for w in words):
        return 3.0
    return 0.0
