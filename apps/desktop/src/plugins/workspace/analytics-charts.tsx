/**
 * Analytics charts — Observable Plot visualisations for the Workspace
 * Analytics page. The data is already shaped by the analytics API layer
 * (`./analytics-api`); these components only render it.
 */

import { cn } from '@hermes/plugin-sdk'
import * as Plot from '@observablehq/plot'
import { useEffect, useMemo, useRef } from 'react'

import type { TrendData } from './analytics'

// ---------------------------------------------------------------------------
// Shared pieces
// ---------------------------------------------------------------------------

const PLOT_STYLE: Plot.PlotOptions['style'] = {
  background: 'transparent',
  color: 'var(--ui-text-secondary)',
  fontFamily: 'inherit',
}

const DEFAULT_BAR_COLOR = '#38bdf8'

const STATUS_COLORS: Record<string, string> = {
  blocked: '#ef4444',
  cancelled: '#6b7280',
  done: '#22c55e',
  in_progress: '#3b82f6',
  review: '#a855f7',
  todo: '#f59e0b',
}

interface CountDatum {
  color: string
  count: number
  label: string
}

function toCountData(record: Record<string, number>, colorOf: (label: string) => string): CountDatum[] {
  return Object.entries(record)
    .map(([label, count]) => ({ color: colorOf(label), count, label }))
    .sort((a, b) => b.count - a.count)
}

/** Renders an Observable Plot into a responsive container. */
function PlotChart({ className, options }: { className?: string; options: Plot.PlotOptions }) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const container = containerRef.current

    if (!container) {return}

    let width = Math.max(container.clientWidth, 1)
    container.replaceChildren(Plot.plot({ ...options, width }))

    const observer = new ResizeObserver(() => {
      const next = Math.max(container.clientWidth, 1)

      if (next === width) {return}
      width = next
      container.replaceChildren(Plot.plot({ ...options, width }))
    })

    observer.observe(container)

    return () => observer.disconnect()
  }, [options])

  return <div className={cn('w-full', className)} ref={containerRef} />
}

function ChartEmpty() {
  return (
    <div className="flex h-24 items-center justify-center text-xs text-(--ui-text-tertiary)">
      No data to chart.
    </div>
  )
}

// ---------------------------------------------------------------------------
// Bar charts
// ---------------------------------------------------------------------------

export function TaskStatusChart({ byStatus }: { byStatus: Record<string, number> }) {
  const rows = useMemo(
    () => toCountData(byStatus, label => STATUS_COLORS[label] ?? DEFAULT_BAR_COLOR),
    [byStatus],
  )

  const options = useMemo<Plot.PlotOptions>(
    () => ({
      height: 220,
      marginBottom: 48,
      marginLeft: 36,
      marks: [
        Plot.barY(rows, { fill: 'color', inset: 6, tip: true, x: 'label', y: 'count' }),
        Plot.ruleY([0]),
      ],
      style: PLOT_STYLE,
      x: { label: null, tickRotate: -25, tickSize: 0 },
      y: { grid: true, label: null, tickFormat: '~s' },
    }),
    [rows],
  )

  if (rows.length === 0) {return <ChartEmpty />}

  return <PlotChart options={options} />
}

export function TaskPriorityChart({ byPriority }: { byPriority: Record<string, number> }) {
  const rows = useMemo(() => toCountData(byPriority, () => DEFAULT_BAR_COLOR), [byPriority])

  const options = useMemo<Plot.PlotOptions>(
    () => ({
      height: Math.max(140, rows.length * 30 + 40),
      marginLeft: 68,
      marks: [
        Plot.barX(rows, { fill: 'color', inset: 2, tip: true, x: 'count', y: 'label' }),
        Plot.ruleX([0]),
      ],
      style: PLOT_STYLE,
      x: { grid: true, label: null, tickFormat: '~s' },
      y: { label: null, tickSize: 0 },
    }),
    [rows],
  )

  if (rows.length === 0) {return <ChartEmpty />}

  return <PlotChart options={options} />
}

export function MilestoneStatusChart({
  blocked,
  completed,
  inProgress,
}: {
  blocked: number
  completed: number
  inProgress: number
}) {
  const rows = useMemo<CountDatum[]>(
    () => [
      { color: STATUS_COLORS.done, count: completed, label: 'completed' },
      { color: STATUS_COLORS.in_progress, count: inProgress, label: 'in_progress' },
      { color: STATUS_COLORS.blocked, count: blocked, label: 'blocked' },
    ],
    [blocked, completed, inProgress],
  )

  const options = useMemo<Plot.PlotOptions>(
    () => ({
      height: 180,
      marginBottom: 44,
      marginLeft: 36,
      marks: [
        Plot.barY(rows, { fill: 'color', inset: 6, tip: true, x: 'label', y: 'count' }),
        Plot.ruleY([0]),
      ],
      style: PLOT_STYLE,
      x: { label: null, tickRotate: -25, tickSize: 0 },
      y: { grid: true, label: null, tickFormat: '~s' },
    }),
    [rows],
  )

  if (rows.every(row => row.count === 0)) {return <ChartEmpty />}

  return <PlotChart options={options} />
}

export function AdrStatusChart({ byStatus }: { byStatus: Record<string, number> }) {
  const rows = useMemo(() => toCountData(byStatus, () => '#8b5cf6'), [byStatus])

  const options = useMemo<Plot.PlotOptions>(
    () => ({
      height: 180,
      marginBottom: 44,
      marginLeft: 36,
      marks: [
        Plot.barY(rows, { fill: 'color', inset: 6, tip: true, x: 'label', y: 'count' }),
        Plot.ruleY([0]),
      ],
      style: PLOT_STYLE,
      x: { label: null, tickRotate: -25, tickSize: 0 },
      y: { grid: true, label: null, tickFormat: '~s' },
    }),
    [rows],
  )

  if (rows.length === 0) {return <ChartEmpty />}

  return <PlotChart options={options} />
}

// ---------------------------------------------------------------------------
// Trends
// ---------------------------------------------------------------------------

type TrendSeriesKey = 'adr_growth' | 'journal_activity' | 'milestone_completion' | 'task_completion'

const TREND_SERIES: { color: string; key: TrendSeriesKey; name: string }[] = [
  { color: '#22c55e', key: 'task_completion', name: 'Tasks completed' },
  { color: '#3b82f6', key: 'milestone_completion', name: 'Milestones completed' },
  { color: '#ec4899', key: 'journal_activity', name: 'Journal entries' },
  { color: '#f59e0b', key: 'adr_growth', name: 'ADRs (cumulative)' },
]

interface TrendDatum {
  date: Date
  series: string
  value: number
}

export function ActivityTrendChart({ trends }: { trends: TrendData }) {
  const rows = useMemo<TrendDatum[]>(() => {
    const out: TrendDatum[] = []

    for (const series of TREND_SERIES) {
      const points = trends[series.key]

      if (!points.some(point => point.value > 0)) {continue}

      for (const point of points) {
        out.push({ date: new Date(`${point.date}T00:00:00Z`), series: series.name, value: point.value })
      }
    }

    return out
  }, [trends])

  const activeSeries = useMemo(
    () => TREND_SERIES.filter(series => rows.some(row => row.series === series.name)),
    [rows],
  )

  const options = useMemo<Plot.PlotOptions>(
    () => ({
      height: 260,
      marginBottom: 40,
      marginLeft: 40,
      marks: [
        Plot.line(rows, { curve: 'natural', stroke: 'series', tip: true, x: 'date', y: 'value' }),
        Plot.dot(rows, { r: 2, stroke: 'series', x: 'date', y: 'value' }),
        Plot.ruleY([0]),
      ],
      color: {
        domain: activeSeries.map(series => series.name),
        legend: true,
        range: activeSeries.map(series => series.color),
      },
      style: PLOT_STYLE,
      x: { label: null, ticks: 6, type: 'utc' },
      y: { grid: true, label: null, tickFormat: '~s' },
    }),
    [activeSeries, rows],
  )

  if (rows.length === 0) {return <ChartEmpty />}

  return <PlotChart options={options} />
}