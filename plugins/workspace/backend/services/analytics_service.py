"""Analytics Service.

Computes engineering metrics, trends, and auto-generated insights
from the workspace storage layer.  Results are cached for performance.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Dict, List

from ..models import (
    ADRAnalytics,
    AnalyticsResponse,
    AutoInsight,
    InsightsResponse,
    JournalAnalytics,
    RepositoryAnalytics,
    RoadmapAnalytics,
    TaskAnalytics,
    TrendPoint,
    TrendsResponse,
)
from ..storage import AbstractStorage

_log = logging.getLogger("hermes.plugins.workspace.analytics")

_CACHE: Dict[str, tuple] = {}
_CACHE_TTL = 30  # seconds


def reset_analytics_cache() -> None:
    """Clear the analytics cache. Useful for testing."""
    _CACHE.clear()


def _cached(key: str, compute_fn):
    now = time.time()
    if key in _CACHE:
        val, ts = _CACHE[key]
        if now - ts < _CACHE_TTL:
            return val
    val = compute_fn()
    _CACHE[key] = (val, now)
    return val


class AnalyticsService:
    """Engineering analytics and insights."""

    def __init__(self, storage: AbstractStorage):
        self._storage = storage

    # ------------------------------------------------------------------
    # Full Analytics
    # ------------------------------------------------------------------

    def get_analytics(self, workspace_id: str = "") -> AnalyticsResponse:
        cache_key = f"analytics_{workspace_id or 'all'}"
        return _cached(cache_key, lambda: self._compute_analytics(workspace_id))

    def _compute_analytics(self, workspace_id: str = "") -> AnalyticsResponse:
        roadmaps = self._roadmap_analytics(workspace_id)
        tasks = self._task_analytics(workspace_id)
        repos = self._repository_analytics(workspace_id)
        adrs = self._adr_analytics(workspace_id)
        journal = self._journal_analytics(workspace_id)

        try:
            graph = self._storage.get_task_stats(workspace_id)
            graph_entities = graph.total + roadmaps.total + roadmaps.total_milestones
            graph_edges = graph.total + roadmaps.total_milestones
            graph_orphans = 0
        except Exception:
            graph_entities = 0
            graph_edges = 0
            graph_orphans = 0

        return AnalyticsResponse(
            roadmaps=roadmaps,
            tasks=tasks,
            repositories=repos,
            adrs=adrs,
            journal=journal,
            graph_entities=graph_entities,
            graph_edges=graph_edges,
            graph_orphans=graph_orphans,
        )

    # ------------------------------------------------------------------
    # Trends
    # ------------------------------------------------------------------

    def get_trends(self, period_days: int = 30, workspace_id: str = "") -> TrendsResponse:
        cache_key = f"trends_{period_days}_{workspace_id or 'all'}"
        return _cached(
            cache_key, lambda: self._compute_trends(period_days, workspace_id)
        )

    def _compute_trends(self, period_days: int, workspace_id: str = "") -> TrendsResponse:
        today = dt.date.today()

        def daily_range(days: int):
            dates = [today - dt.timedelta(days=i) for i in range(days - 1, -1, -1)]
            return {d.isoformat(): 0 for d in dates}

        task_comp = daily_range(period_days)
        ms_comp = daily_range(period_days)
        journal_act = daily_range(period_days)
        adr_growth = daily_range(period_days)
        roadmap_prog = daily_range(period_days)
        cumulative_adrs = 0

        try:
            for ws in self._scoped_workspaces(workspace_id):
                tasks = self._storage.list_tasks(ws.id)
                for t in tasks:
                    if t.completed_at and t.status == "done":
                        cd = t.completed_at[:10]
                        if cd in task_comp:
                            task_comp[cd] += 1
                    cd = t.created_at[:10]
                    if cd in adr_growth:
                        pass
                for r in self._storage.list_roadmaps(ws.id):
                    for m in r.milestones:
                        if m.status == "completed":
                            md = m.updated_at[:10]
                            if md in ms_comp:
                                ms_comp[md] += 1
                entries = self._storage.list_journal_entries(ws.id)
                for je in entries:
                    ed = je.created_at[:10]
                    if ed in journal_act:
                        journal_act[ed] += 1

            for wsd in adr_growth:
                try:
                    all_adrs_before = self._storage.list_adrs(
                        workspace_id if workspace_id else ""
                    )
                    count = sum(1 for a in all_adrs_before if a.created_at[:10] <= wsd)
                    cumulative_adrs = max(cumulative_adrs, count)
                    adr_growth[wsd] = cumulative_adrs
                except Exception:
                    adr_growth[wsd] = cumulative_adrs
        except Exception:
            pass

        return TrendsResponse(
            task_completion=[TrendPoint(date=k, value=v) for k, v in task_comp.items()],
            milestone_completion=[TrendPoint(date=k, value=v) for k, v in ms_comp.items()],
            roadmap_progress=[TrendPoint(date=k, value=0) for k in roadmap_prog],
            journal_activity=[TrendPoint(date=k, value=v) for k, v in journal_act.items()],
            adr_growth=[TrendPoint(date=k, value=v) for k, v in adr_growth.items()],
            period_days=period_days,
        )

    # ------------------------------------------------------------------
    # Insights
    # ------------------------------------------------------------------

    def get_insights(self, workspace_id: str = "") -> InsightsResponse:
        cache_key = f"insights_{workspace_id or 'all'}"
        return _cached(cache_key, lambda: self._compute_insights(workspace_id))

    def _compute_insights(self, workspace_id: str = "") -> InsightsResponse:
        insights: List[AutoInsight] = []

        try:
            all_tasks: list = []
            all_roadmaps: list = []
            for ws in self._scoped_workspaces(workspace_id):
                tasks = self._storage.list_tasks(ws.id)
                all_tasks.extend(tasks)
                roadmaps = self._storage.list_roadmaps(ws.id)
                all_roadmaps.extend(roadmaps)

                # Roadmaps with no milestones
                for r in roadmaps:
                    if r.milestone_count == 0:
                        insights.append(AutoInsight(
                            type="warning",
                            title="Roadmap has no milestones",
                            description=f"Roadmap '{r.name}' has no milestones defined.",
                            entity_type="roadmap",
                            entity_id=r.id,
                        ))

                # Milestones with no tasks
                for r in roadmaps:
                    for m in r.milestones:
                        ms_tasks = self._storage.list_tasks(ws.id, milestone_id=m.id)
                        if not ms_tasks:
                            insights.append(AutoInsight(
                                type="info",
                                title="Milestone has no tasks",
                                description=f"Milestone '{m.title}' in roadmap '{r.name}' has no tasks.",
                                entity_type="milestone",
                                entity_id=m.id,
                            ))

                # Blocked milestones
                for r in roadmaps:
                    for m in r.milestones:
                        if m.status == "blocked":
                            insights.append(AutoInsight(
                                type="danger",
                                title="Blocked milestone",
                                description=f"Milestone '{m.title}' in roadmap '{r.name}' is blocked.",
                                entity_type="milestone",
                                entity_id=m.id,
                            ))

            # Overdue tasks
            today = dt.date.today().isoformat()
            for t in all_tasks:
                if t.due_date and t.due_date < today and t.status not in ("done", "cancelled"):
                    insights.append(AutoInsight(
                        type="danger",
                        title="Overdue task",
                        description=f"Task '{t.title}' is overdue (due: {t.due_date}).",
                        entity_type="task",
                        entity_id=t.id,
                    ))
                if t.status == "blocked":
                    insights.append(AutoInsight(
                        type="warning",
                        title="Blocked task",
                        description=f"Task '{t.title}' is blocked.",
                        entity_type="task",
                        entity_id=t.id,
                    ))

            # Most active repository
            repo_counts: Dict[str, tuple] = {}
            for t in all_tasks:
                if t.repository_id:
                    if t.repository_id not in repo_counts:
                        repo = self._storage.get_repository(t.repository_id)
                        repo_counts[t.repository_id] = (repo.name if repo else "unknown", 0)
                    name, count = repo_counts[t.repository_id]
                    repo_counts[t.repository_id] = (name, count + 1)
            if repo_counts:
                best = max(repo_counts.items(), key=lambda x: x[1][1])
                insights.append(AutoInsight(
                    type="info",
                    title="Most active repository",
                    description=f"Repository '{best[1][0]}' has {best[1][1]} tasks.",
                    entity_type="repository",
                    entity_id=best[0],
                ))

            # Largest roadmap
            if all_roadmaps:
                largest = max(all_roadmaps, key=lambda r: r.milestone_count)
                if largest.milestone_count > 0:
                    insights.append(AutoInsight(
                        type="info",
                        title="Largest roadmap",
                        description=f"Roadmap '{largest.name}' has {largest.milestone_count} milestones.",
                        entity_type="roadmap",
                        entity_id=largest.id,
                    ))

            # Success: completed ratio
            if all_tasks:
                completed = sum(1 for t in all_tasks if t.status == "done")
                ratio = round(completed / max(len(all_tasks), 1) * 100, 1)
                insights.append(AutoInsight(
                    type="success",
                    title="Task completion rate",
                    description=f"{completed}/{len(all_tasks)} tasks completed ({ratio}%).",
                ))

        except Exception:
            _log.exception("Error computing insights")

        return InsightsResponse(insights=insights)

    # ------------------------------------------------------------------
    # Per-section analytics
    # ------------------------------------------------------------------

    def _scoped_workspaces(self, workspace_id: str = ""):
        """Return the workspaces to aggregate over for a scope.

        An empty scope means "all workspaces" at the storage level — the
        API layer guarantees this only happens for explicitly global
        requests; the resolver never degrades to it.
        """
        all_ws = self._storage.list_workspaces()
        if workspace_id:
            return [w for w in all_ws if w.id == workspace_id]
        return all_ws

    def _roadmap_analytics(self, workspace_id: str = "") -> RoadmapAnalytics:
        total = 0
        total_ms = 0
        ms_completed = 0
        ms_in_progress = 0
        ms_blocked = 0
        active = 0
        completed = 0
        progress_sum = 0.0

        try:
            for ws in self._scoped_workspaces(workspace_id):
                for r in self._storage.list_roadmaps(ws.id):
                    total += 1
                    if r.milestone_count == 0:
                        continue
                    total_ms += r.milestone_count
                    progress_sum += r.progress
                    if r.progress == 100.0:
                        completed += 1
                    else:
                        active += 1
                    for m in r.milestones:
                        if m.status == "completed":
                            ms_completed += 1
                        elif m.status == "in_progress":
                            ms_in_progress += 1
                        elif m.status == "blocked":
                            ms_blocked += 1
        except Exception:
            pass

        return RoadmapAnalytics(
            total=total,
            active=active,
            completed=completed,
            avg_progress=round(progress_sum / max(total, 1), 1),
            total_milestones=total_ms,
            milestones_completed=ms_completed,
            milestones_in_progress=ms_in_progress,
            milestones_blocked=ms_blocked,
        )

    def _task_analytics(self, workspace_id: str = "") -> TaskAnalytics:
        by_priority: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        total = 0
        open_count = 0
        completed = 0
        blocked = 0
        overdue = 0
        today = dt.date.today().isoformat()

        try:
            for ws in self._scoped_workspaces(workspace_id):
                for t in self._storage.list_tasks(ws.id):
                    total += 1
                    by_priority[t.priority] = by_priority.get(t.priority, 0) + 1
                    by_status[t.status] = by_status.get(t.status, 0) + 1
                    if t.status == "done":
                        completed += 1
                    elif t.status == "blocked":
                        blocked += 1
                    if t.status not in ("done", "cancelled"):
                        open_count += 1
                        if t.due_date and t.due_date < today:
                            overdue += 1
        except Exception:
            pass

        return TaskAnalytics(
            total=total, open=open_count, completed=completed,
            blocked=blocked, overdue=overdue,
            by_priority=by_priority, by_status=by_status,
        )

    def _repository_analytics(self, workspace_id: str = "") -> RepositoryAnalytics:
        total = 0
        active = 0
        most_name = ""
        most_count = 0
        repo_task_counts: Dict[str, tuple] = {}

        try:
            for ws in self._scoped_workspaces(workspace_id):
                repos = self._storage.list_repositories(ws.id)
                total += len(repos)
                for repo in repos:
                    tasks = self._storage.list_tasks(ws.id, repository_id=repo.id)
                    count = len(tasks)
                    if count > 0:
                        active += 1
                    if count > most_count:
                        most_count = count
                        most_name = repo.name
        except Exception:
            pass

        return RepositoryAnalytics(
            total=total, active=active,
            most_active=most_name, most_active_task_count=most_count,
        )

    def _adr_analytics(self, workspace_id: str = "") -> ADRAnalytics:
        total = 0
        recent = 0
        by_status: Dict[str, int] = {}
        cutoff = (dt.date.today() - dt.timedelta(days=30)).isoformat()

        try:
            for ws in self._scoped_workspaces(workspace_id):
                for a in self._storage.list_adrs(ws.id):
                    total += 1
                    by_status[a.status] = by_status.get(a.status, 0) + 1
                    if a.created_at[:10] >= cutoff:
                        recent += 1
        except Exception:
            pass

        return ADRAnalytics(
            total=total, recently_added=recent, by_status=by_status,
        )

    def _journal_analytics(self, workspace_id: str = "") -> JournalAnalytics:
        week = 0
        month = 0
        today = dt.date.today()
        week_ago = (today - dt.timedelta(days=7)).isoformat()
        month_ago = (today - dt.timedelta(days=30)).isoformat()
        dates: List[str] = []

        try:
            for ws in self._scoped_workspaces(workspace_id):
                for je in self._storage.list_journal_entries(ws.id):
                    d = je.created_at[:10]
                    dates.append(d)
                    if d >= week_ago:
                        week += 1
                    if d >= month_ago:
                        month += 1
            streak = self._compute_streak(sorted(set(dates), reverse=True))
        except Exception:
            streak = 0

        return JournalAnalytics(
            entries_this_week=week,
            entries_this_month=month,
            writing_streak_days=streak,
        )

    @staticmethod
    def _compute_streak(sorted_dates: List[str]) -> int:
        if not sorted_dates:
            return 0
        today_str = dt.date.today().isoformat()
        streak = 0
        cursor = dt.date.today()
        for d in sorted_dates:
            if d == cursor.isoformat():
                streak += 1
                cursor -= dt.timedelta(days=1)
            elif d < cursor.isoformat():
                break
        return streak
