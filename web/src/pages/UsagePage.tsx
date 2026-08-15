import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  BarChart3,
  Cpu,
  Layers,
  RefreshCw,
  TrendingUp,
  Wallet,
} from "lucide-react";
import * as Plot from "@observablehq/plot";
import { api } from "@/lib/api";
import type {
  AnalyticsResponse,
  UsageProvider,
  UsageProviderModel,
  UsageProvidersResponse,
  UsageRequestModelEntry,
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

// Per-provider palette for the requests-by-model bar chart and card dots.
// Browser-safe hex (theme tokens don't resolve reliably inside SVG
// presentation attributes). OpenRouter amber, opencode sky, commandcode violet.
const PROVIDER_COLORS: Record<string, string> = {
  openrouter: "#f59e0b",
  opencode: "#38bdf8",
  commandcode: "#a855f7",
};

const PLOT_STYLE: Plot.PlotOptions["style"] = {
  background: "transparent",
  color: "var(--color-text-secondary)",
  fontFamily: "inherit",
};

// ---------------------------------------------------------------------------
// Types — the usage endpoint also returns `by_task`/`tools` beyond the base
// `AnalyticsResponse` shape; provider models are merged for the cost table.
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

interface ProviderModelRow {
  key: string;
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost: number;
  requests: number;
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
  return "$0.00";
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
// Requests by model — BAR chart colored per provider (OpenRouter-style)
// ---------------------------------------------------------------------------

function RequestsByModelBarChart({ data }: { data: UsageRequestModelEntry[] }) {
  const rows = useMemo(() => {
    return [...data].sort((a, b) => b.requests - a.requests).slice(0, MAX_MODEL_SERIES);
  }, [data]);

  const options = useMemo<Plot.PlotOptions>(() => {
    const providers = Array.from(new Set(rows.map((r) => r.provider)));
    return {
      height: CHART_HEIGHT_PX,
      marginLeft: 44,
      marginBottom: 56,
      color: {
        legend: true,
        domain: providers,
        range: providers.map((p) => PROVIDER_COLORS[p] ?? "#64748b"),
      },
      marks: [
        Plot.barY(rows, {
          x: "model",
          y: "requests",
          fill: "provider",
          tip: true,
        }),
        Plot.ruleY([0]),
      ],
      x: { type: "band", label: null, tickRotate: -24 },
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
          Real request counts across providers, colored by provider
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
// Model & cost breakdown — merged from the real per-provider model data
// ---------------------------------------------------------------------------

function mergeProviderModels(providers: UsageProvider[]): ProviderModelRow[] {
  const rows: ProviderModelRow[] = [];
  for (const p of providers) {
    if (p.error || !p.models) continue;
    for (const m of p.models) {
      rows.push({
        key: `${p.provider}:${m.model}`,
        model: m.model,
        provider: p.provider,
        input_tokens: m.input ?? 0,
        output_tokens: m.output ?? 0,
        estimated_cost: m.cost ?? 0,
        requests: m.requests ?? 0,
      });
    }
  }
  return rows;
}

function ModelCostTable({ models }: { models: ProviderModelRow[] }) {
  const { t } = useI18n();
  const { sorted, sortKey, sortDir, toggle } = useTableSort(models, "requests", "desc");

  if (models.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <Cpu className="h-5 w-5 text-muted-foreground" />
          <CardTitle className="text-base">Model & cost breakdown</CardTitle>
        </div>
        <div className="font-mondwest normal-case text-xs text-muted-foreground">
          Merged across OpenRouter, opencode and commandcode
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full font-mondwest normal-case text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground text-xs">
                <SortHeader label={t.analytics.model} col="model" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-left py-2 pr-4 font-medium" />
                <SortHeader label="Provider" col="provider" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-left py-2 px-4 font-medium" />
                <SortHeader label={t.analytics.input} col="input_tokens" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.analytics.output} col="output_tokens" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.models.estimatedCost} col="estimated_cost" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 px-4 font-medium" />
                <SortHeader label={t.analytics.apiCalls} col="requests" sortKey={sortKey} sortDir={sortDir} toggle={toggle} className="text-right py-2 pl-4 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((m) => (
                <tr
                  key={m.key}
                  className="border-b border-border/50 hover:bg-secondary/20 transition-colors"
                >
                  <td className="py-2 pr-4">
                    <span className="font-mono-ui text-xs">{shortModelName(m.model)}</span>
                  </td>
                  <td className="py-2 px-4">
                    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
                      <span
                        className="inline-block h-2 w-2 rounded-full"
                        style={{ backgroundColor: PROVIDER_COLORS[m.provider] ?? "#64748b" }}
                      />
                      {m.provider}
                    </span>
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
                  <td className="text-right py-2 pl-4 text-muted-foreground">{m.requests}</td>
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
// Provider cards — real spend + credits + per-model token bars (OpenRouter
// style), one card per LLM provider.
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

/** Per-model "All Tokens" mini-bars, like OpenRouter's usage page: a stacked
 * bar per model scaled to the largest row, with input/output/cache segments. */
function ModelTokenBars({ models }: { models: UsageProviderModel[] }) {
  if (!models || models.length === 0) return null;
  const maxTokens = Math.max(
    ...models.map((m) => (m.input ?? 0) + (m.output ?? 0) + (m.cache_read ?? 0)),
    1,
  );
  return (
    <div className="space-y-2">
      {models.map((m) => {
        const input = m.input ?? 0;
        const output = m.output ?? 0;
        const cacheRead = m.cache_read ?? 0;
        const total = input + output + cacheRead;
        if (total <= 0) return null;
        const segments: Array<{ value: number; color: string; label: string }> = [
          { value: cacheRead, color: "#60a5fa", label: "cache" },
          { value: input, color: "var(--series-input-token)", label: "input" },
          { value: output, color: "var(--series-output-token)", label: "output" },
        ].filter((s) => s.value > 0);
        return (
          <div key={m.model} className="space-y-0.5">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="truncate font-mono-ui text-xs text-muted-foreground">
                {shortModelName(m.model)}
              </span>
              <span className="shrink-0 font-mono text-xs text-text-tertiary">
                {formatTokens(total)}
              </span>
            </div>
            <div className="flex h-1.5 w-full overflow-hidden bg-secondary">
              {segments.map((s, i) => (
                <div
                  key={i}
                  className="h-full"
                  style={{
                    backgroundColor: s.color,
                    width: `${(s.value / maxTokens) * 100}%`,
                  }}
                />
              ))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-text-tertiary">
              {segments.map((s, i) => (
                <span key={i}>
                  <span style={{ color: s.color }}>{formatTokens(s.value)}</span> {s.label}
                </span>
              ))}
              {m.cost != null && m.cost > 0 && (
                <span className="font-medium text-foreground/70">{formatCost(m.cost)}</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ProviderCard({
  provider,
  highlight,
}: {
  provider: UsageProvider;
  highlight?: boolean;
}) {
  const {
    provider: name,
    spend_usd,
    credits_remaining,
    tokens,
    models,
    sessions,
    note,
    error,
  } = provider;

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
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ backgroundColor: PROVIDER_COLORS[name] ?? "#64748b" }}
          />
          <CardTitle className="text-sm">{name}</CardTitle>
          {highlight && (
            <span className="ml-auto rounded bg-primary/10 px-1.5 py-0.5 text-display text-[10px] font-medium tracking-wider text-primary">
              Primary
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-mono font-semibold">{spend ?? "—"}</span>
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
        {models && models.length > 0 ? (
          <ModelTokenBars models={models} />
        ) : tokens && (tokens.input || tokens.output || tokens.cache_read) ? (
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
        ) : null}
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
      // Provider spend endpoint is built in parallel — a 404/failure renders
      // the section with a subtle "unavailable" notice instead of failing.
      api
        .getUsageProviders()
        .then(setProviders)
        .catch(() => setProviders(null))
        .finally(() => setProvidersLoaded(true)),
    ])
      .then(([usage]) => {
        setData(usage);
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
  const providerModels = useMemo(
    () => mergeProviderModels(providers?.providers ?? []),
    [providers],
  );

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="usage:top" />

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
            <RequestsByModelBarChart data={providers?.requests_by_model ?? []} />
            <SpendOverTimeChart data={data} />
          </div>

          <ModelCostTable models={providerModels} />

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
