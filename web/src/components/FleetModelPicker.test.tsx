// @vitest-environment jsdom
// Tests for the FleetModelPicker per-harness model picker: loading /
// unavailable states, grouped + searchable catalog, current-selection
// highlighting, per-provider error banners, clear action, and combobox
// keyboard navigation.

import { describe, it, expect, afterEach, vi } from "vitest";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";

import { FleetModelPicker } from "./FleetModelPicker";

vi.mock("@nous-research/ui/ui/components/spinner", () => ({
  Spinner: ({ className }: { className?: string }) => (
    <span data-testid="spinner" className={className} />
  ),
}));

type Props = Parameters<typeof FleetModelPicker>[0];

const CATALOG = {
  providers: {
    "opencode-go": {
      models: ["opencode-go/opus-4", "opencode-go/sonnet-4"],
    },
    commandcode: { models: ["commandcode/mainnet", "commandcode/testnet"] },
    openrouter: {
      models: ["anthropic/claude-opus-4", "deepseek/deepseek-v3"],
    },
  },
};

function baseProps(over: Partial<Props> = {}): Props {
  return {
    worker: "opencode",
    catalog: CATALOG,
    onSelect: vi.fn(),
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

async function render(ui: ReactNode) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(ui));
}

async function openPicker() {
  const toggle = container.querySelector(
    'button[aria-haspopup="listbox"]',
  ) as HTMLButtonElement;
  await act(async () => toggle.click());
}

function searchInput(): HTMLInputElement {
  return container.querySelector('[role="combobox"]') as HTMLInputElement;
}

/** Type into the controlled search input the way testing-library does. */
async function typeSearch(text: string) {
  const input = searchInput();
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, text);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function pressKey(key: string) {
  const input = searchInput();
  await act(async () => {
    input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

function optionElements(): HTMLElement[] {
  return [...container.querySelectorAll<HTMLElement>('[role="option"]')];
}

function activeOptionText(): string | null {
  const id = searchInput().getAttribute("aria-activedescendant");
  const el = id ? container.querySelector(`[id="${id}"]`) : null;
  return el?.textContent ?? null;
}

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.clearAllMocks();
});

describe("FleetModelPicker", () => {
  it("shows a spinner and loading text while loading", async () => {
    await render(<FleetModelPicker {...baseProps({ loading: true })} />);
    expect(container.querySelector('[data-testid="spinner"]')).not.toBeNull();
    expect(container.textContent).toContain("Loading models…");
  });

  it("shows a muted unavailable state with a retry hint when catalog is null", async () => {
    await render(<FleetModelPicker {...baseProps({ catalog: null })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Models unavailable");
    expect(text.toLowerCase()).toContain("refresh");
  });

  it("shows a muted unavailable state when catalog is undefined", async () => {
    await render(<FleetModelPicker {...baseProps({ catalog: undefined })} />);
    expect(container.textContent).toContain("Models unavailable");
  });

  it("groups models under their provider labels", async () => {
    await render(<FleetModelPicker {...baseProps()} />);
    expect(container.querySelector('[role="listbox"]')).toBeNull();
    await openPicker();
    const groups = [
      ...container.querySelectorAll<HTMLElement>('[role="group"]'),
    ];
    expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual([
      "Opencode Go",
      "CommandCode",
      "OpenRouter",
    ]);
    expect(container.textContent).toContain("opencode-go/opus-4");
    expect(optionElements()).toHaveLength(6);
  });

  it("filters models case-insensitively across all providers", async () => {
    await render(<FleetModelPicker {...baseProps()} />);
    await openPicker();
    await typeSearch("CLAUDE");
    const models = optionElements().map((el) => el.textContent);
    expect(models).toEqual(["anthropic/claude-opus-4"]);
    await typeSearch("opus");
    expect(optionElements().map((el) => el.textContent)).toEqual([
      "opencode-go/opus-4",
      "anthropic/claude-opus-4",
    ]);
  });

  it("shows a no-match message for a query with zero results", async () => {
    await render(<FleetModelPicker {...baseProps()} />);
    await openPicker();
    await typeSearch("zzzz-nope");
    expect(optionElements()).toHaveLength(0);
    expect(container.textContent).toContain('No models match “zzzz-nope”.');
  });

  it("shows a no-models message for an empty catalog", async () => {
    await render(
      <FleetModelPicker {...baseProps({ catalog: { providers: {} } })} />,
    );
    await openPicker();
    expect(container.textContent).toContain("No models available.");
  });

  it("highlights the current selection with a checkmark and aria-selected", async () => {
    await render(
      <FleetModelPicker
        {...baseProps({
          current: {
            provider: "commandcode",
            model: "commandcode/mainnet",
          },
        })}
      />,
    );
    await openPicker();
    const selected = optionElements().filter(
      (el) => el.getAttribute("aria-selected") === "true",
    );
    expect(selected).toHaveLength(1);
    expect(selected[0].textContent).toContain("commandcode/mainnet");
    expect(selected[0].textContent).toContain("current");
    // A check icon is rendered inside the selected option.
    expect(selected[0].querySelector("svg")).not.toBeNull();
    const unselected = optionElements().filter(
      (el) => el.getAttribute("aria-selected") === "false",
    );
    expect(unselected).toHaveLength(5);
  });

  it("shows a per-provider error banner above the failing section", async () => {
    await render(
      <FleetModelPicker
        {...baseProps({
          catalog: {
            providers: CATALOG.providers,
            errors: { commandcode: "connection reset" },
          },
        })}
      />,
    );
    await openPicker();
    const banner = container.querySelector(
      '[role="alert"]',
    ) as HTMLElement | null;
    expect(banner).not.toBeNull();
    expect(banner?.textContent).toContain(
      "Failed to load CommandCode models — showing stale/empty.",
    );
    expect(
      banner?.parentElement?.getAttribute("aria-label"),
    ).toBe("CommandCode");
  });

  it("calls onSelect with provider + model and closes on option click", async () => {
    const onSelect = vi.fn();
    await render(<FleetModelPicker {...baseProps({ onSelect })} />);
    await openPicker();
    const option = container.querySelector(
      '[data-testid="fleet-model-option-opencode-go-opencode-go/opus-4"]',
    ) as HTMLElement;
    await act(async () => option.click());
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith(
      "opencode-go",
      "opencode-go/opus-4",
    );
    expect(container.querySelector('[role="listbox"]')).toBeNull();
  });

  it("navigates with arrow keys, selects with enter, closes with escape", async () => {
    const onSelect = vi.fn();
    await render(<FleetModelPicker {...baseProps({ onSelect })} />);
    await openPicker();
    expect(activeOptionText()).toContain("opencode-go/opus-4");

    await pressKey("ArrowDown");
    expect(activeOptionText()).toContain("opencode-go/sonnet-4");
    await pressKey("ArrowDown");
    expect(activeOptionText()).toContain("commandcode/mainnet");

    await pressKey("Enter");
    expect(onSelect).toHaveBeenCalledWith("commandcode", "commandcode/mainnet");
    expect(container.querySelector('[role="listbox"]')).toBeNull();

    // Escape closes the reopened dropdown without selecting.
    await openPicker();
    await pressKey("Escape");
    expect(container.querySelector('[role="listbox"]')).toBeNull();
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("wraps arrow navigation within the option list", async () => {
    await render(<FleetModelPicker {...baseProps()} />);
    await openPicker();
    await pressKey("ArrowUp");
    expect(activeOptionText()).toContain("deepseek/deepseek-v3");
    await pressKey("ArrowDown");
    expect(activeOptionText()).toContain("opencode-go/opus-4");
  });

  it("renders a Use default action that calls onClear", async () => {
    const onClear = vi.fn();
    await render(
      <FleetModelPicker
        {...baseProps({
          onClear,
          current: { provider: "commandcode", model: "commandcode/mainnet" },
        })}
      />,
    );
    const clear = container.querySelector(
      '[data-testid="fleet-model-clear"]',
    ) as HTMLButtonElement;
    expect(clear).not.toBeNull();
    expect(clear.textContent).toContain("Use default");
    await act(async () => clear.click());
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("renders no clear action when onClear is omitted", async () => {
    await render(
      <FleetModelPicker
        {...baseProps({
          current: { provider: "commandcode", model: "commandcode/mainnet" },
        })}
      />,
    );
    expect(container.querySelector('[data-testid="fleet-model-clear"]')).toBe(
      null,
    );
  });

  it("disables the picker when disabled is set", async () => {
    const onSelect = vi.fn();
    await render(
      <FleetModelPicker {...baseProps({ disabled: true, onSelect })} />,
    );
    const toggle = container.querySelector(
      'button[aria-haspopup="listbox"]',
    ) as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
    await act(async () => toggle.click());
    expect(container.querySelector('[role="combobox"]')).toBeNull();
    expect(container.querySelector('[role="listbox"]')).toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
  });
});