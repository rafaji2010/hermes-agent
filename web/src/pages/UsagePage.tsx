import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  CalendarDays,
  Cpu,
  Layers,
  RefreshCw,
  Timer,
  TrendingUp,
  Wallet,
} from "lucide-react";
import * as Plot from "@observablehq/plot";
import { api, fetchJSON } from "@/lib/api";
import type {
  AnalyticsResponse,
  AnalyticsModelEntry,
  UsageProvider,
  UsageProvidersResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useI18n } from "@/i18n";
import { PluginSlot } from "@/plugins";

const PERIODS = [
  { label: "12h", days: 1 },
  { label: "24h", days: 1 },
  { label: "7d", days: 7 },
  { label: "30d", days: 30 },
] as const;

const CHART_HEIGHT_PX = 220;
const MAX_MODEL_SERIES = 6;

// Categorical palette for the per-model request series (browser-safe hex;
// theme tokens don't resolve reliably inside SVG presentation attributes).
const MODEL_SERIES_COLORS = [
  "#f59e0b",
  "#38bdf8",
  "#22c55e",
  "#a855f7",
  "#ef4444",
  "#ec4899",
  "#64748b",
];

const PLOT_STYLE: Plot.PlotOptions["style"] = {
  background: "transparent",
  color: "var(--color-text-secondary)",
  fontFamily: "inherit",
};

// ---------------------------------------------------------------------------
// Types — the usage endpoint also returns `by_task`/`tools` beyond the base
// `AnalyticsResponse` shape, and `/api/usage/budget` is built in parallel.
// ---------------------------------------------------------------------------

interface UsageByTaskEntry {
  task: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  api_calls: number;
  models: string[];
}

interface UsageAnalyticsResponse extends AnalyticsResponse {
  by_task?: UsageByTaskEntry[];
}

interface UsageBudgetResponse {
  monthly: {
    spend_usd: number;
    cap_usd: number;
    period_start: string;
    period_end: string;
    resets_in: string;
  };
  limits: {
    five_hour: { used: number; cap: number; pct: number; resets_in: string };
    weekly: { used: number; cap: number; pct: number; resets_in: string };
  };
  runs: { total: number; last_24h: number };
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatCost(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.01) return `$${n.toFixed(3)}`;
  if (n > 0) return `$${n.toFixed(4)}`;
  return "$0";
}

function formatDay(day: string): string {
  try {
    const d = new Date(day + "T00:00:00");
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return day;
  }
}

/** Short model name: strip vendor prefix like "openrouter/" or "anthropic/". */
function shortModelName(model: string): string {
  const slashIdx = model.indexOf("/");
  if (slashIdx > 0) return model.slice(slashIdx + 1);
  return model;
}

/** Spend-vs-cap progress color: green → amber → red at 60% / 90%. */
function usageColor(pct: number): string {
  if (pct >= 90) return "var(--color-destructive)";
  if (pct >= 60) return "var(--color-warning)";
  return "var(--color-success)";
}

/** Resolve a theme CSS variable for chart fills, with a hex fallback. */
function resolveSeriesColor(cssVarName: string, fallback: string): string {
  if (typeof window === "undefined" || typeof getComputedStyle === "undefined") {
    return fallback;
  }
  try {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(cssVarName)
      .trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

function fmtUsdTick(d: number): string {
  if (d >= 100) return `$${Math.round(d)}`;
  if (d >= 1) return `$${d.toFixed(1)}`;
  return `$${d.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Sorting (mirrors AnalyticsPage)
// ---------------------------------------------------------------------------

function useTableSort<T>(
  data: T[],
  defaultKey: keyof T & string,
  defaultDir: "asc" | "desc" = "desc",
) {
  const [sortKey, setSortKey] = useState<string>(defaultKey);
  const [sortDir, setSortDir] = useState<"asc" | "desc">(defaultDir);

  const sorted = useMemo(() => {
    return [...data].sort((a, b) => {
      const aVal = a[sortKey as keyof T];
      const bVal = b[sortKey as keyof T];
      if (aVal === null || aVal === undefined) return 1;
      if (bVal === null || bVal === undefined) return -1;
      if (aVal === bVal) return 0;
      const cmp = aVal > bVal ? 1 : -1;
      return sortDir === "asc" ? cmp : -cmp;
    });
  }, [data, sortKey, sortDir]);

  const toggle = useCallback(
    (key: string) => {
      if (key === sortKey) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
      } else {
        setSortKey(key);
        setSortDir("desc");
      }
    },
    [sortKey],
  );

  return { sorted, sortKey, sortDir, toggle };
}

function SortHeader({
  label,
  col,
  sortKey,
  sortDir,
  toggle,
  className,
}: {
  label: string;
  col: string;
  sortKey: string;
  sortDir: "asc" | "desc";
  toggle: (key: string) => void;
  className?: string;
}) {
  const active = col === sortKey;
  return (
    <th onClick={() => toggle(col)} className={`cursor-pointer select-none ${className ?? ""}`}>
      <span className="inline-flex items-center gap-1.5 rounded px-1 -mx-1 py-0.5 hover:bg-muted/40 transition-colors">
        {label}
        {active ? (
          sortDir === "asc" ? (
            <ArrowUp className="h-3.5 w-3.5 text-foreground/80 shrink-0" />
          ) : (
            <ArrowDown className="h-3.5 w-3.5 text-foreground/80 shrink-0" />
          )
        ) : (
          <ArrowUpDown className="h-3 w-3 text-text-tertiary shrink-0" />
        )}
      </span>
    </th>
  );
}

// ---------------------------------------------------------------------------
// Plot chart wrapper (Observable Plot integration, same style as the desktop
// analytics-charts.tsx)
// ---------------------------------------------------------------------------

function PlotChart({ className, options }: { className?: string; options: Plot.PlotOptions }) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let width = Math.max(container.clientWidth, 1);
    container.replaceChildren(Plot.plot({ ...options, width }));

    const observer = new ResizeObserver(() => {
      const next = Math.max(container.clientWidth, 1);
      if (next === width) return;
      width = next;
      container.replaceChildren(Plot.plot({ ...options, width }));
    });
    observer.observe(container);

    return () => observer.disconnect();
  }, [options]);

  return <div ref={containerRef} className={cn("w-full", className)} />;
}

function ChartEmpty() {
  return (
    <div className="flex h-24 items-center justify-center text-xs text-text-tertiary">
      No data to chart
    </div>
  );
}

// ---------------------------------------------------------------------------
// Section 2 — Activity charts
// ---------------------------------------------------------------------------

function RequestsByModelChart({ data }: { data: UsageAnalyticsResponse }) {
  const rows = useMemo(() => {
    const totalCalls = data.by_model.reduce((s, m) => s + (m.api_calls || 0), 0);
    if (totalCalls <= 0) return [];
    const sorted = [...data.by_model].sort((a, b) => (b.api_calls || 0) - (a.api_calls || 0));
    const top = sorted.slice(0, MAX_MODEL_SERIES);
    const otherCalls = sorted
      .slice(MAX_MODEL_SERIES)
      .reduce((s, m) => s + (m.api_calls || 0), 0);
    const series = top.map((m) => ({ model: m.model, api_calls: m.api_calls || 0 }));
    if (otherCalls > 0) series.push({ model: "Other", api_calls: otherCalls });
    // The API only reports per-day totals and per-model totals, so split each
    // day's calls across models proportionally to their period-wide share.
    return data.daily.flatMap((d) => {
      const dayCalls = d.api_calls || 0;
      if (dayCalls <= 0) return [];
      return series.map((s) => ({
        day: new Date(`${d.day}T00:00:00Z`),
        model: s.model,
        api_calls: (dayCalls * s.api_calls) / totalCalls,
      }));
    });
  }, [data]);

  const options = useMemo<Plot.PlotOptions>(() => {
    const models = Array.from(new Set(rows.map((r) => r.model)));
    return {
      height: CHART_HEIGHT_PX,
      marginLeft: 44,
      marginBottom: 32,
      color: {
        legend: true,
        domain: models,
        range: models.map((_, i) => MODEL_SERIES_COLORS[i % MODEL_SERIES_COLORS.length]),
      },
      marks: [
        Plot.areaY(rows, {
          x: "day",
          y: "api_calls",
          z: "model",
          fill: "model",
          curve: "natural",
          tip: true,
        }),
        Plot.ruleY([0]),
      ],
      x: { type: "utc", label: null },
      y: { grid: true, label: null, tickFormat: "~s" },
      style: PLOT_STYLE,
    };
  }, [rows]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <BarChart3 className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">Requests by model</CardTitle>
        </div>
        <div className="font-mondwest normal-case text-xs text-muted-foreground">
          Daily API calls split by model
        </div>
      </CardHeader>
      <CardContent>{rows.length > 0 ? <PlotChart options={options} /> : <ChartEmpty />}</CardContent>
    </Card>
  );
}

function SpendOverTimeChart({ data }: { data: UsageAnalyticsResponse }) {
  const { t } = useI18n();
  const rows = useMemo(() => {
    const costs = data.daily.map((d) => d.estimated_cost ?? 0);
    const cumulative = costs.reduce<number[]>(
      (acc, c) => [...acc, (acc[acc.length - 1] ?? 0) + c],
      [],
    );
    return data.daily.map((d, i) => ({
      day: new Date(`${d.day}T00:00:00Z`),
      cost: costs[i],
      cumulative: cumulative[i],
    }));
  }, [data]);

  const options = useMemo<Plot.PlotOptions>(() => {
    const inputColor = resolveSeriesColor("--series-input-token", "#fbbf24");
    const outputColor = resolveSeriesColor("--series-output-token", "#34d399");
    return {
      height: CHART_HEIGHT_PX,
      marginLeft: 52,
      marginBottom: 32,
      marks: [
        Plot.areaY(rows, {
          x: "day",
          y: "cost",
          curve: "natural",
          fillOpacity: 0.35,
          fill: inputColor,
          tip: true,
        }),
        Plot.lineY(rows, {
          x: "day",
          y: "cumulative",
          curve: "natural",
          stroke: outputColor,
          strokeWidth: 2,
          tip: true,
        }),
        Plot.ruleY([0]),
      ],
      x: { type: "utc", label: null },
      y: { grid: true, label: null, tickFormat: fmtUsdTick },
      style: PLOT_STYLE,
    };
  }, [rows]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <TrendingUp className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">Spend over time</CardTitle>
        </div>
        <div className="flex items-center gap-4 font-mondwest normal-case text-xs text-muted-foreground">
          <div className="flex items-center gap-1.5">
            <div
              className="h-2.5 w-2.5"
              style={{ backgroundColor: "var(--series-input-token)" }}
            />
            {t.analytics.total} daily spend
          </div>
          <div className="flex items-center gap-1.5">
            <div
              className="h-2.5 w-2.5"
              style={{ backgroundColor: "var(--series-output-token)" }}
            />
            Cumulative
          </div>
        </div>
      </CardHeader>
      <CardContent>{rows.length > 0 ? <PlotChart options={options} /> : <ChartEmpty />}</CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 3 — Model & cost breakdown
// ---------------------------------------------------------------------------

function ModelCostTable({ models }: { models: AnalyticsModelEntry[] }) {
  const { t } = useI18n();
  const { sorted, sortKey, sortDir, toggle } = useTableSort(models, "estimated_cost", "desc");

  if (models.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">Model & cost breakdown</CardTitle>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full font-mondwest normal-case text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground text-xs">
                <SortHeader label={t.analytics.model} col="model" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-left py-2 pr-4 font-medium" />
                <SortHeader label={t.analytics.input} col="input_tokens" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.analytics.output} col="output_tokens" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.models.estimatedCost} col="estimated_cost" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.analytics.apiCalls} col="api_calls" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.sessions.title} col="sessions" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 pl-4 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => (
                <tr
                  key={m.model}
                  className="border-b border-border/50 hover:bg-secondary/20 transition-colors"
                >
                  <td className="py-2 pr-4">
                    <span className="font-mono-ui text-xs">{shortModelName(m.model)}</span>
                  </td>
                  <td className="text-right py-2 px-4">
                    <span style={{ color: "var(--series-input-token)" }}>
                      {formatTokens(m.input_tokens)}
                    </span>
                  </td>
                  <td className="text-right py-2 px-4">
                    <span style={{ color: "var(--series-output-token)" }}>
                      {formatTokens(m.output_tokens)}
                    </span>
                  </td>
                  <td className="text-right py-2 px-4 font-medium">{formatCost(m.estimated_cost)}</td>
                  <td className="text-right py-2 px-4 text-muted-foreground">{m.api_calls}</td>
                  <td className="text-right py-2 pl-4 text-muted-foreground">{m.sessions}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function ByTaskList({ tasks }: { tasks: UsageByTaskEntry[] | undefined }) {
  if (!tasks || tasks.length === 0) return null;
  const sorted = [...tasks].sort((a, b) => b.estimated_cost - a.estimated_cost);
  const maxCost = Math.max(...sorted.map((x) => x.estimated_cost), 1);
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">By task</CardTitle>
        </div>
        <div className="font-mondwest normal-case text-xs text-muted-foreground">
          Auxiliary tasks (compression, vision, title gen, …)
        </div>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {sorted.map((x) => (
            <li key={x.task} className="flex items-center gap-3">
              <span className="w-36 shrink-0 truncate font-mono-ui text-xs">
                {x.task.replace(/_/g, " ")}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden bg-secondary">
                <div
                  className="h-full"
                  style={{
                    width: `${(x.estimated_cost / maxCost) * 100}%`,
                    backgroundColor: "var(--color-text-tertiary)",
                  }}
                />
              </div>
              <span className="w-16 shrink-0 text-right text-xs text-muted-foreground">
                {formatTokens(x.input_tokens + x.output_tokens)}
              </span>
              <span className="w-14 shrink-0 text-right text-xs font-medium">
                {formatCost(x.estimated_cost)}
              </span>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Section 1 — Budget cards
// ---------------------------------------------------------------------------

function ProgressBar({ pct, color }: { pct: number; color: string }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="h-1.5 w-full overflow-hidden bg-secondary">
      <div
        className="h-full transition-all duration-300"
        style={{ width: `${clamped}%`, backgroundColor: color }}
      />
    </div>
  );
}

function UsageLimitCard({
  icon,
  title,
  used,
  cap,
  pct,
  footer,
}: {
  icon: ReactNode;
  title: string;
  used: number;
  cap: number | null;
  pct: number;
  footer: string;
}) {
  const color = usageColor(pct);
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">{icon}</span>
          <CardTitle className="text-sm">{title}</CardTitle>
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-mono font-semibold">{formatCost(used)}</span>
          <span className="text-sm text-muted-foreground">
            of {cap != null ? formatCost(cap) : "—"}
          </span>
          {cap != null && (
            <span className="ml-auto text-xs font-medium" style={{ color }}>
              {pct.toFixed(0)}%
            </span>
          )}
        </div>
        <ProgressBar pct={cap != null ? pct : 0} color={color} />
        {footer && <div className="text-xs text-text-tertiary">{footer}</div>}
      </CardContent>
    </Card>
  );
}

function BudgetSection({ budget }: { budget: UsageBudgetResponse | null }) {
  const m = budget?.monthly;
  const fh = budget?.limits?.five_hour;
  const wk = budget?.limits?.weekly;
  const monthlyPct = m && m.cap_usd > 0 ? (m.spend_usd / m.cap_usd) * 100 : 0;
  const monthlyFooter = m
    ? m.period_end
      ? `resets ${formatDay(m.period_end)}`
      : m.resets_in
        ? `resets in ${m.resets_in}`
        : ""
    : "";

  return (
    <section className="flex flex-col gap-3">
      {budget === null && (
        <p className="text-xs text-text-tertiary">
          Budget limits endpoint not available — showing placeholders.
        </p>
      )}
      <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-4">
        <UsageLimitCard
          icon={<Wallet className="h-4 w-4" />}
          title="Monthly usage"
          used={m?.spend_usd ?? 0}
          cap={m && m.cap_usd > 0 ? m.cap_usd : null}
          pct={monthlyPct}
          footer={monthlyFooter}
        />
        <UsageLimitCard
          icon={<Timer className="h-4 w-4" />}
          title="5-hour limit"
          used={fh?.used ?? 0}
          cap={fh && fh.cap > 0 ? fh.cap : null}
          pct={fh?.pct ?? 0}
          footer={fh?.resets_in ? `resets in ${fh.resets_in}` : ""}
        />
        <UsageLimitCard
          icon={<CalendarDays className="h-4 w-4" />}
          title="Weekly limit"
          used={wk?.used ?? 0}
          cap={wk && wk.cap > 0 ? wk.cap : null}
          pct={wk?.pct ?? 0}
          footer={wk?.resets_in ? `resets in ${wk.resets_in}` : ""}
        />
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Activity className="h-4 w-4 text-muted-foreground" />
              <CardTitle className="text-sm">Total runs</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-1">
            <div className="text-2xl font-mono font-semibold">{budget?.runs.total ?? "—"}</div>
            <div className="text-xs text-text-tertiary">
              {budget?.runs.last_24h != null
                ? `${budget.runs.last_24h} in the last 24h`
                : "last 24h —"}
            </div>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Section 2 — Provider usage (real spend from the LLM providers)
// ---------------------------------------------------------------------------

function ProviderCard({
  provider,
  highlight,
}: {
  provider: UsageProvider;
  highlight?: boolean;
}) {
  const { provider: name, spend_usd, credits_remaining, tokens, sessions, note, error } = provider;

  // A failed collector for one provider must not kill the whole section.
  if (error) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">{name}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="text-xs text-text-tertiary">{error}</div>
        </CardContent>
      </Card>
    );
  }

  const spend = spend_usd != null ? formatCost(spend_usd) : null;
  const credits = credits_remaining != null ? formatCost(credits_remaining) : null;
  // used vs credits → progress; only meaningful when both sides are present.
  const pct =
    spend_usd != null && credits_remaining != null && spend_usd + credits_remaining > 0
      ? (spend_usd / (spend_usd + credits_remaining)) * 100
      : 0;

  return (
    <Card className={highlight ? "border-primary/40" : undefined}>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <CardTitle className="text-sm">{name}</CardTitle>
          {highlight && (
            <span className="ml-auto rounded bg-primary/10 px-1.5 py-0.5 text-display text-[10px] font-medium tracking-wider text-primary">
              Primary
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-mono font-semibold">
            {spend_usd === 0 ? "—" : (spend ?? "—")}
          </span>
          <span className="text-sm text-muted-foreground">spent</span>
        </div>
        {credits != null && (
          <>
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-mono font-semibold">{credits}</span>
              <span className="text-sm text-muted-foreground">credits remaining</span>
            </div>
            {spend_usd != null && (
              <>
                <ProgressBar pct={pct} color={usageColor(pct)} />
                <div className="text-xs text-text-tertiary">
                  {pct.toFixed(0)}% of available credits used
                </div>
              </>
            )}
          </>
        )}
        {tokens && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mondwest normal-case text-xs text-muted-foreground">
            <span>
              <span style={{ color: "var(--series-input-token)" }}>{formatTokens(tokens.input)}</span>{" "}
              in
            </span>
            <span>
              <span style={{ color: "var(--series-output-token)" }}>{formatTokens(tokens.output)}</span>{" "}
              out
            </span>
            <span>cache {formatTokens(tokens.cache_read)}</span>
          </div>
        )}
        {sessions != null && (
          <div className="text-xs text-text-tertiary">
            {sessions} session{sessions === 1 ? "" : "s"}
          </div>
        )}
        {spend == null && !note && (
          <div className="text-xs text-text-tertiary">
            Spend not exposed for this provider
          </div>
        )}
        {note && <div className="text-xs text-text-tertiary">{note}</div>}
      </CardContent>
    </Card>
  );
}

function ProviderUsageSection({
  providers,
  unavailable,
}: {
  providers: UsageProvider[] | null;
  unavailable: boolean;
}) {
  if (unavailable || providers == null) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <Wallet className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-sm">Provider usage</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="text-xs text-text-tertiary">
            Provider data unavailable
          </div>
        </CardContent>
      </Card>
    );
  }

  if (providers.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Wallet className="h-4 w-4 text-muted-foreground" />
        <h2 className="font-mondwest text-display text-sm tracking-wider text-foreground">
          Provider usage
        </h2>
      </div>
      <div className="grid gap-6 sm:grid-cols-2 xl:grid-cols-3">
        {providers.map((p) => (
          <ProviderCard
            key={p.provider}
            provider={p}
            highlight={p.provider === "openrouter"}
          />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function UsagePage() {
  const [period, setPeriod] = useState<string>("30d");
  const [data, setData] = useState<UsageAnalyticsResponse | null>(null);
  const [budget, setBudget] = useState<UsageBudgetResponse | null>(null);
  const [providers, setProviders] = useState<UsageProvidersResponse | null>(null);
  const [providersLoaded, setProvidersLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();

  const days = PERIODS.find((p) => p.label === period)?.days ?? 30;

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    setProvidersLoaded(false);
    Promise.all([
      api.getAnalytics(days),
      // Budget endpoint is built in parallel — a 404 just means "no budget
      // limits configured"; the section renders placeholders instead.
      fetchJSON<UsageBudgetResponse>("/api/usage/budget").catch(() => null),
      // Provider spend endpoint is built in parallel — a 404/failure renders
      // the section with a subtle "unavailable" notice instead of failing.
      api
        .getUsageProviders()
        .then(setProviders)
        .catch(() => setProviders(null))
        .finally(() => setProvidersLoaded(true)),
    ])
      .then(([usage, budgetData]) => {
        setData(usage);
        setBudget(budgetData);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [days]);

  useLayoutEffect(() => {
    // Period selector + refresh both live in afterTitle so the controls sit
    // immediately next to the page title. The active period is conveyed by
    // the filled (non-outlined) button.
    setAfterTitle(
      <div className="flex flex-wrap items-center gap-1.5">
        {PERIODS.map((p) => (
          <Button
            key={p.label}
            type="button"
            size="sm"
            outlined={period !== p.label}
            onClick={() => setPeriod(p.label)}
          >
            {p.label}
          </Button>
        ))}
        <Button
          type="button"
          ghost
          size="icon"
          className="text-muted-foreground hover:text-foreground"
          onClick={load}
          disabled={loading}
          aria-label={t.common.refresh}
        >
          {loading ? <Spinner /> : <RefreshCw />}
        </Button>
      </div>,
    );
    setEnd(null);
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [period, loading, load, setAfterTitle, setEnd, t.common.refresh]);

  useEffect(() => {
    load();
  }, [load]);

  const hasUsage = data && (data.daily.length > 0 || data.by_model.length > 0);

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="usage:top" />

      <BudgetSection budget={budget} />

      <ProviderUsageSection
        providers={providers?.providers ?? null}
        unavailable={providersLoaded && providers == null}
      />

      {loading && !data && (
        <div className="flex items-center justify-center py-24">
          <Spinner className="text-2xl text-primary" />
        </div>
      )}

      {error && (
        <Card>
          <CardContent className="py-6">
            <p className="text-center text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      )}

      {data && hasUsage && (
        <>
          <div className="grid gap-6 lg:grid-cols-2">
            <RequestsByModelChart data={data} />
            <SpendOverTimeChart data={data} />
          </div>

          <ModelCostTable models={data.by_model} />

          <ByTaskList tasks={data.by_task} />
        </>
      )}

      {data && !hasUsage && (
        <Card>
          <CardContent className="py-12">
            <div className="flex flex-col items-center text-muted-foreground">
              <BarChart3 className="mb-3 h-8 w-8 opacity-40" />
              <p className="text-sm font-medium">{t.analytics.noUsageData}</p>
              <p className="mt-1 text-xs text-text-tertiary">{t.analytics.startSession}</p>
            </div>
          </CardContent>
        </Card>
      )}

      <PluginSlot name="usage:bottom" />
    </div>
  );
}
