// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalyticsDailyEntry, AnalyticsModelEntry } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  getAnalytics: vi.fn(),
  fetchJSON: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { getAnalytics: apiMocks.getAnalytics },
  fetchJSON: apiMocks.fetchJSON,
}));
vi.mock("@observablehq/plot", () => ({
  plot: () => document.createElementNS("http://www.w3.org/2000/svg", "svg"),
  areaY: () => ({}),
  lineY: () => ({}),
  barY: () => ({}),
  ruleY: () => ({}),
}));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setAfterTitle: vi.fn(), setEnd: vi.fn() }),
}));
vi.mock("@/plugins", () => ({
  PluginSlot: () => null,
}));
vi.mock("@/i18n", () => ({
  useI18n: () => ({
    t: {
      common: { refresh: "Refresh" },
      analytics: {
        model: "Model",
        input: "Input",
        output: "Output",
        apiCalls: "API Calls",
        noUsageData: "No usage data for this period",
        startSession: "Start a session to see analytics here",
        total: "Total",
      },
      sessions: { title: "Sessions" },
      models: { estimatedCost: "Est. Cost" },
    },
  }),
}));

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => {
    root.render(ui);
  });
}

beforeEach(() => {
  apiMocks.getAnalytics.mockReset();
  apiMocks.fetchJSON.mockReset();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      disconnect() {}
      observe() {}
      unobserve() {}
    },
  );
  Object.defineProperty(window, "__HERMES_SESSION_TOKEN__", {
    configurable: true,
    value: "test-token",
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.unstubAllGlobals();
});

describe("UsagePage", () => {
  it("renders budget cards, activity charts and the model breakdown", async () => {
    const daily: AnalyticsDailyEntry[] = [
      {
        day: "2026-08-12",
        input_tokens: 1000,
        output_tokens: 500,
        cache_read_tokens: 0,
        reasoning_tokens: 0,
        estimated_cost: 0.05,
        actual_cost: 0.04,
        sessions: 2,
        api_calls: 3,
      },
    ];
    const by_model: AnalyticsModelEntry[] = [
      {
        model: "openrouter/deepseek/deepseek-chat",
        input_tokens: 1000,
        output_tokens: 500,
        estimated_cost: 0.05,
        sessions: 2,
        api_calls: 3,
      },
    ];
    apiMocks.getAnalytics.mockResolvedValue({
      daily,
      by_model,
      by_task: [
        {
          task: "compression",
          input_tokens: 10,
          output_tokens: 5,
          estimated_cost: 0.001,
          api_calls: 1,
          models: ["x"],
        },
      ],
      totals: {
        total_input: 1000,
        total_output: 500,
        total_cache_read: 0,
        total_reasoning: 0,
        total_estimated_cost: 0.05,
        total_actual_cost: 0.04,
        total_sessions: 2,
        total_api_calls: 3,
      },
      skills: {
        summary: {
          total_skill_loads: 0,
          total_skill_edits: 0,
          total_skill_actions: 0,
          distinct_skills_used: 0,
        },
        top_skills: [],
      },
    });
    apiMocks.fetchJSON.mockResolvedValue({
      monthly: {
        spend_usd: 1.5,
        cap_usd: 10,
        period_start: "2026-08-01",
        period_end: "2026-08-31",
        resets_in: "18d",
      },
      limits: {
        five_hour: { used: 0.2, cap: 2, pct: 10, resets_in: "3h" },
        weekly: { used: 1.2, cap: 5, pct: 24, resets_in: "4d" },
      },
      runs: { total: 42, last_24h: 7 },
    });

    const { default: UsagePage } = await import("./UsagePage");
    await render(<UsagePage />);

    await act(async () => {
      await vi.waitFor(() => expect(apiMocks.getAnalytics).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(container.textContent).toContain("Monthly usage");
    expect(container.textContent).toContain("5-hour limit");
    expect(container.textContent).toContain("Weekly limit");
    expect(container.textContent).toContain("Total runs");
    expect(container.textContent).toContain("42");
    expect(container.textContent).toContain("Requests by model");
    expect(container.textContent).toContain("Spend over time");
    expect(container.textContent).toContain("Model & cost breakdown");
    expect(container.textContent).toContain("By task");
    expect(container.textContent).toContain("deepseek-chat");
  });

  it("renders budget placeholders when /api/usage/budget 404s", async () => {
    apiMocks.getAnalytics.mockResolvedValue({
      daily: [],
      by_model: [],
      totals: {
        total_input: 0,
        total_output: 0,
        total_cache_read: 0,
        total_reasoning: 0,
        total_estimated_cost: 0,
        total_actual_cost: 0,
        total_sessions: 0,
        total_api_calls: 0,
      },
      skills: {
        summary: {
          total_skill_loads: 0,
          total_skill_edits: 0,
          total_skill_actions: 0,
          distinct_skills_used: 0,
        },
        top_skills: [],
      },
    });
    apiMocks.fetchJSON.mockRejectedValue(new Error("404: Not Found"));

    const { default: UsagePage } = await import("./UsagePage");
    await render(<UsagePage />);

    await act(async () => {
      await vi.waitFor(() => expect(apiMocks.fetchJSON).toHaveBeenCalledWith("/api/usage/budget"));
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(apiMocks.fetchJSON).toHaveBeenCalledWith("/api/usage/budget");
    expect(container.textContent).toContain("Monthly usage");
    expect(container.textContent).toContain("Total runs");
    expect(container.textContent).toContain("No usage data for this period");
  });
});