// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AnalyticsDailyEntry, AnalyticsModelEntry } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  getAnalytics: vi.fn(),
  getUsageProviders: vi.fn(),
  fetchJSON: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    getAnalytics: apiMocks.getAnalytics,
    getUsageProviders: apiMocks.getUsageProviders,
  },
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

function emptyAnalytics() {
  return {
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
  };
}

const PROVIDER_FIXTURE = {
  providers: [
    {
      provider: "openrouter",
      spend_usd: 7.83,
      credits_remaining: 35.0,
      period: "billing-month",
      source: "api",
      tokens: { input: 2292, output: 5843, cache_read: 0 },
      models: [
        {
          model: "gemini-3.6-flash",
          requests: 2,
          input: 2292,
          output: 5843,
          cost: 7.828827,
        },
      ],
      note: "per-model split estimated from local sessions — OpenRouter exposes no usage listing API",
    },
    {
      provider: "opencode",
      spend_usd: 0.0,
      sessions: 3,
      tokens: { input: 418900, output: 55800, cache_read: 15700000 },
      models: [
        {
          model: "deepseek-v4-flash-free",
          requests: 177,
          input: 418900,
          output: 123000,
          cache_read: 15700000,
          cost: 0.0,
        },
      ],
      source: "cli",
    },
    {
      provider: "commandcode",
      spend_usd: null,
      sessions: 15,
      models: [
        { model: "deepseek-v4-flash", requests: 325 },
        { model: "muse-spark-1.2-contributor", requests: 149 },
      ],
      note: "server-side only — plan/credits not exposed via API",
      source: "local-transcripts",
    },
  ],
  requests_by_model: [
    { model: "deepseek-v4-flash", requests: 325, provider: "commandcode" },
    { model: "deepseek-v4-flash-free", requests: 177, provider: "opencode" },
    { model: "muse-spark-1.2-contributor", requests: 149, provider: "commandcode" },
    { model: "gemini-3.6-flash", requests: 2, provider: "openrouter" },
  ],
};

beforeEach(() => {
  apiMocks.getAnalytics.mockReset();
  apiMocks.getUsageProviders.mockReset();
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
  it("renders real provider cards, the requests-by-model bar chart and the model breakdown", async () => {
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
    apiMocks.getUsageProviders.mockResolvedValue(PROVIDER_FIXTURE);

    const { default: UsagePage } = await import("./UsagePage");
    await render(<UsagePage />);

    await act(async () => {
      await vi.waitFor(() => expect(apiMocks.getUsageProviders).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 0));
    });

    // Provider cards — real OpenRouter spend + credits.
    expect(container.textContent).toContain("Provider usage");
    expect(container.textContent).toContain("$7.83");
    expect(container.textContent).toContain("credits remaining");
    expect(container.textContent).toContain("$35.00");
    expect(container.textContent).toContain("Primary");
    // opencode — real zero spend renders as $0.00, per-model token bars show.
    expect(container.textContent).toContain("$0.00");
    expect(container.textContent).toContain("deepseek-v4-flash-free");
    expect(container.textContent).toContain("3 sessions");
    // commandcode — no spend, muted server-side note.
    expect(container.textContent).toContain("server-side only");
    expect(container.textContent).toContain("15 sessions");
    // Bar chart + merged table sections render.
    expect(container.textContent).toContain("Requests by model");
    expect(container.textContent).toContain("Spend over time");
    expect(container.textContent).toContain("Model & cost breakdown");
    expect(container.textContent).toContain("gemini-3.6-flash");
    expect(container.textContent).toContain("By task");
  });

  it("renders the provider unavailable notice when the endpoint fails", async () => {
    apiMocks.getAnalytics.mockResolvedValue(emptyAnalytics());
    apiMocks.getUsageProviders.mockRejectedValue(new Error("404: Not Found"));

    const { default: UsagePage } = await import("./UsagePage");
    await render(<UsagePage />);

    await act(async () => {
      await vi.waitFor(() => expect(apiMocks.getUsageProviders).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 0));
    });

    // Graceful degradation: the provider section shows a subtle notice
    // instead of crashing the page when the endpoint is unavailable.
    expect(container.textContent).toContain("Provider usage");
    expect(container.textContent).toContain("Provider data unavailable");
    expect(container.textContent).toContain("No usage data for this period");
  });

  it("does not fetch or render the fake budget/limit endpoint", async () => {
    apiMocks.getAnalytics.mockResolvedValue(emptyAnalytics());
    apiMocks.getUsageProviders.mockResolvedValue({ providers: [], requests_by_model: [] });

    const { default: UsagePage } = await import("./UsagePage");
    await render(<UsagePage />);

    await act(async () => {
      await vi.waitFor(() => expect(apiMocks.getAnalytics).toHaveBeenCalled());
      await new Promise((r) => setTimeout(r, 0));
    });

    // The invented monthly-cap / 5-hour / weekly limits are gone from the UI.
    expect(apiMocks.fetchJSON).not.toHaveBeenCalledWith("/api/usage/budget");
    expect(container.textContent).not.toContain("Monthly usage");
    expect(container.textContent).not.toContain("5-hour limit");
    expect(container.textContent).not.toContain("Weekly limit");
    expect(container.textContent).not.toContain("Total runs");
  });
});
