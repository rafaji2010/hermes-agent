import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type JSX,
  type KeyboardEvent,
} from "react";
import { Check, ChevronDown, RotateCcw, Search } from "lucide-react";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

type FleetModelPickerProps = {
  worker: string; // e.g. "opencode" | "commandcode"
  current?: { provider: string; model: string } | null;
  catalog?: {
    providers: Record<string, { models: string[] }>;
    errors?: Record<string, string>;
  } | null;
  loading?: boolean;
  onSelect: (provider: string, model: string) => void;
  onClear?: () => void;
  disabled?: boolean;
};

const PROVIDER_LABELS: Record<string, string> = {
  "opencode-go": "Opencode Go",
  commandcode: "CommandCode",
  openrouter: "OpenRouter",
};

function providerLabel(provider: string): string {
  return (
    PROVIDER_LABELS[provider] ??
    provider
      .split(/[-_]/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

interface ModelOption {
  provider: string;
  model: string;
}

interface ModelGroup {
  provider: string;
  label: string;
  start: number;
  models: ModelOption[];
  error?: string;
}

/**
 * Per-harness model picker for the Fleet page.
 *
 * Pure component: the caller owns catalog fetching (the model-picker build's
 * GET /api/fleet/models) and passes the result in via `catalog`. This picker
 * never fetches and never renders secrets — provider error banners carry a
 * fixed message, not the underlying error string.
 */
export function FleetModelPicker(props: FleetModelPickerProps): JSX.Element {
  const {
    worker,
    current,
    catalog,
    loading = false,
    onSelect,
    onClear,
    disabled = false,
  } = props;

  const providers = useMemo(() => catalog?.providers ?? {}, [catalog]);
  const errors = catalog?.errors;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const listboxRef = useRef<HTMLDivElement | null>(null);
  const baseId = useId();
  const listboxId = `${baseId}-listbox`;

  const trimmedQuery = query.trim();

  // Group matching models by provider (case-insensitive substring match),
  // preserving catalog order and skipping providers with no matches.
  const groups = useMemo((): ModelGroup[] => {
    const q = trimmedQuery.toLowerCase();
    const out: ModelGroup[] = [];
    let start = 0;
    for (const [provider, entry] of Object.entries(providers)) {
      const error = errors?.[provider];
      const models: ModelOption[] = [];
      for (const model of entry?.models ?? []) {
        if (!q || model.toLowerCase().includes(q)) {
          models.push({ provider, model });
        }
      }
      // Show provider group if it has matching models OR has an error to surface.
      // This fixes cold-cache invisibility: a 403-era empty catalog still shows
      // its banner ("Failed to load …") instead of vanishing behind "No models available."
      if (models.length === 0 && !error) continue;
      // When search filters out all models for a provider that has an error, keep
      // the group so the banner remains visible even with 0 filtered models.
      out.push({
        provider,
        label: providerLabel(provider),
        start,
        models,
        error,
      });
      start += models.length;
    }
    return out;
  }, [providers, errors, trimmedQuery]);

  const filtered = useMemo(() => groups.flatMap((g) => g.models), [groups]);

  const totalModels = useMemo(
    () =>
      Object.values(providers).reduce(
        (n, p) => n + (p?.models?.length ?? 0),
        0,
      ),
    [providers],
  );

  const active =
    filtered.length > 0
      ? Math.min(Math.max(activeIndex, 0), filtered.length - 1)
      : -1;

  const selectedKey = current
    ? `${current.provider}\u0000${current.model}`
    : null;
  const isCurrent = (opt: ModelOption) =>
    `${opt.provider}\u0000${opt.model}` === selectedKey;

  const showList = open && !loading && catalog != null && !disabled;

  // Keep the keyboard-highlighted option in view while arrowing.
  useEffect(() => {
    if (!showList || active < 0 || !listboxRef.current) return;
    listboxRef.current
      .querySelector<HTMLElement>(`[id="${baseId}-opt-${active}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [showList, active, baseId]);

  // Close the dropdown when the user clicks outside the card.
  useEffect(() => {
    if (!showList) return;
    const onPointerDown = (e: globalThis.MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [showList]);

  const selectOption = (opt: ModelOption) => {
    if (disabled) return;
    onSelect(opt.provider, opt.model);
    setOpen(false);
    setQuery("");
  };

  const clearSelection = () => {
    if (disabled || !onClear) return;
    onClear();
    setQuery("");
  };

  const toggle = () => {
    if (disabled) return;
    if (!open) setActiveIndex(0);
    setOpen((v) => !v);
  };

  const handleQueryChange = (e: ChangeEvent<HTMLInputElement>) => {
    setQuery(e.target.value);
    setActiveIndex(0);
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (disabled) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        if (filtered.length > 0) {
          setActiveIndex((i) => (i + 1) % filtered.length);
        }
        break;
      case "ArrowUp":
        e.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        if (filtered.length > 0) {
          setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
        }
        break;
      case "Home":
        e.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        e.preventDefault();
        setActiveIndex(Math.max(0, filtered.length - 1));
        break;
      case "Enter":
        e.preventDefault();
        if (!open) {
          setOpen(true);
          return;
        }
        if (active >= 0) selectOption(filtered[active]);
        break;
      case "Escape":
        e.preventDefault();
        setOpen(false);
        break;
    }
  };

  const optionId = (i: number) => `${baseId}-opt-${i}`;

  const summary =
    current?.provider && current?.model
      ? `${current.provider} · ${current.model}`
      : null;

  return (
    <div
      ref={rootRef}
      data-testid="fleet-model-picker"
      className="relative rounded-md border bg-card px-2 py-1.5 text-sm shadow-sm"
    >
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={toggle}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={showList}
          aria-controls={showList ? listboxId : undefined}
          title={
            summary
              ? `Model for ${providerLabel(worker)}: ${summary}`
              : `Select a model for ${providerLabel(worker)}`
          }
          className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-1 py-0.5 text-left outline-none hover:bg-secondary/40 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
        >
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
              {providerLabel(worker)} model
            </span>
            <span
              title={summary ?? "Use default"}
              className={`block break-all font-mono text-[11px] leading-snug ${
                summary ? "text-foreground" : "text-muted-foreground"
              }`}
            >
              {summary ?? "Use default"}
            </span>
          </span>
          <ChevronDown
            aria-hidden="true"
            className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
              showList ? "rotate-180" : ""
            }`}
          />
        </button>

        {onClear && (
          <button
            type="button"
            data-testid="fleet-model-clear"
            onClick={clearSelection}
            disabled={disabled || !current}
            title="Reset this harness to its default model"
            className="flex shrink-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground outline-none hover:bg-secondary/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <RotateCcw aria-hidden="true" className="h-3 w-3" />
            Use default
          </button>
        )}
      </div>

      {loading ? (
        <div className="mt-1.5 flex items-center gap-2 border-t border-border/60 pb-1 pt-2 text-xs text-muted-foreground">
          <Spinner className="h-3.5 w-3.5" />
          Loading models…
        </div>
      ) : catalog == null ? (
        <div className="mt-1.5 border-t border-border/60 pb-1 pt-2 text-xs text-muted-foreground">
          <p>Models unavailable</p>
          <p className="mt-0.5 text-[11px]">
            Check the connection and refresh to retry.
          </p>
        </div>
      ) : showList ? (
        <div className="absolute left-0 top-full z-30 mt-2 w-[min(560px,90vw)] min-w-[min(480px,100%)] overflow-hidden rounded-md border bg-popover shadow-lg">
          <div className="p-2">
            <div className="relative">
              <Search
                aria-hidden="true"
                className="absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
              />
              <input
                data-testid="fleet-model-search"
                type="text"
                role="combobox"
                aria-expanded="true"
                aria-controls={listboxId}
                aria-autocomplete="list"
                aria-activedescendant={
                  active >= 0 ? optionId(active) : undefined
                }
                aria-label={`Search models for ${providerLabel(worker)}`}
                value={query}
                onChange={handleQueryChange}
                onKeyDown={handleKeyDown}
                placeholder="Search models…"
                className="w-full rounded-md border bg-background py-1.5 pl-7 pr-2 text-xs outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          <div
            ref={listboxRef}
            id={listboxId}
            role="listbox"
            data-testid="fleet-model-listbox"
            aria-label={`Models for ${providerLabel(worker)}`}
            className="max-h-56 overflow-y-auto border-t"
          >
            {filtered.length === 0 ? (
              <div
                role="presentation"
                className="px-3 py-3 text-xs italic text-muted-foreground"
              >
                {totalModels === 0
                  ? "No models available."
                  : `No models match “${query}”.`}
              </div>
            ) : (
              groups.map((g) => (
                <div key={g.provider} role="group" aria-label={g.label}>
                  {g.error && (
                    <div
                      role="alert"
                      data-testid={`fleet-model-error-${g.provider}`}
                      className="mx-2 mt-2 flex items-start gap-1 rounded-md border border-destructive/30 bg-destructive/10 px-2 py-1.5 text-[11px] text-destructive"
                    >
                      Failed to load {g.label} models — showing stale/empty.
                    </div>
                  )}
                  <div className="sticky top-0 z-10 bg-muted/80 px-3 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground backdrop-blur-sm">
                    {g.label}
                  </div>
                  {g.models.map((opt, j) => {
                    const i = g.start + j;
                    const cur = isCurrent(opt);
                    const act = i === active;
                    return (
                      <div
                        key={`${g.provider}\u0000${opt.model}`}
                        id={optionId(i)}
                        role="option"
                        aria-selected={cur}
                        data-testid={`fleet-model-option-${g.provider}-${opt.model}`}
                        title={`${opt.provider} · ${opt.model}`}
                        onClick={() => selectOption(opt)}
                        onMouseEnter={() => setActiveIndex(i)}
                        className={`flex cursor-pointer select-none items-start gap-2 px-3 py-1.5 text-xs ${
                          act
                            ? "bg-accent text-accent-foreground"
                            : cur
                              ? "text-primary"
                              : ""
                        }`}
                      >
                        <Check
                          aria-hidden="true"
                          className={`mt-0.5 h-3 w-3 shrink-0 ${
                            cur ? "opacity-100 text-primary" : "opacity-0"
                          }`}
                        />
                        <span
                          title={`${opt.provider} · ${opt.model}`}
                          className="min-w-0 flex-1 break-all font-mono text-xs leading-snug"
                        >
                          {opt.model}
                        </span>
                        {cur && (
                          <span className="shrink-0 text-[10px] text-muted-foreground">
                            current
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
