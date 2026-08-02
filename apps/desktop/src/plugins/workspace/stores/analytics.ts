/**
 * Analytics nanostores.
 */

import { atom } from 'nanostores'

export interface Metric {
  label: string
  value: number | string
  unit: string
  trend: string
}

export interface AnalyticsData {
  roadmaps: {
    total: number
    active: number
    completed: number
    avg_progress: number
    total_milestones: number
    milestones_completed: number
    milestones_in_progress: number
    milestones_blocked: number
  }
  tasks: {
    total: number
    open: number
    completed: number
    blocked: number
    overdue: number
    by_priority: Record<string, number>
    by_status: Record<string, number>
  }
  repositories: {
    total: number
    active: number
    most_active: string
    most_active_task_count: number
  }
  adrs: {
    total: number
    recently_added: number
    by_status: Record<string, number>
  }
  journal: {
    entries_this_week: number
    entries_this_month: number
    writing_streak_days: number
  }
  graph_entities: number
  graph_edges: number
  graph_orphans: number
}

export interface TrendPoint {
  date: string
  value: number
}

export interface TrendData {
  task_completion: TrendPoint[]
  milestone_completion: TrendPoint[]
  roadmap_progress: TrendPoint[]
  journal_activity: TrendPoint[]
  adr_growth: TrendPoint[]
  period_days: number
}

export interface AutoInsight {
  type: string
  title: string
  description: string
  entity_type: string
  entity_id: string
}

export const $analytics = atom<AnalyticsData | null>(null)
export const $trends = atom<TrendData | null>(null)
export const $insights = atom<AutoInsight[]>([])
export const $trendPeriod = atom<number>(30)
