import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Send, Zap } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api } from "@/lib/api";
import type { FleetHistoryEntry, FleetLiveExecution, FleetWorker } from "@/lib/api";
import { FleetModelPicker } from "@/components/FleetModelPicker";

const WORKER_NAMES = ["pi", "codex", "opencode", "commandcode", "dsh"] as const;

const STATUS_TONE: Record<string, string> = {
  PLANNED: "bg-amber-500/20 text-amber-600 border-amber-500/30",
  DISPATCHING: "bg-sky-500/20 text-sky-600 border-sky-500/30",
  RUNNING: "bg-emerald-500/20 text-emerald-600 border-emerald-500/30",
  BLOCKED: "bg-orange-500/20 text-orange-600 border-orange-500/30",
  DONE: "bg-success/20 text-success border-success/30",
  FAILED: "bg-destructive/20 text-destructive border-destructive/30",
  CANCELLED: "bg-muted text-muted-foreground border-border",
};

function statusClass(status: string): string {
  return STATUS_TONE[status] ?? "bg-muted text-muted-foreground border-border";
}

function elapsedSec(startedAt: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const sec = s % 60;
  if (m < 60) return `${m}m${String(sec).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h${String(m % 60).padStart(2, "0")}m`;
}

function FleetDiagram({ hotNodes }: { hotNodes: Set<string> }) {
  const hot = (id: string) => (hotNodes.has(id) ? "hot" : "");
  return (
    <div className="fleet-diagram-wrap overflow-x-auto rounded-lg border bg-muted/20 p-3">
      <style>{`
        .fleet-svg .fleet-node { transition: filter 0.3s, stroke 0.3s, fill 0.3s; }
        .fleet-svg .fleet-node.hot { filter: drop-shadow(0 0 8px rgba(99,102,241,0.9)); stroke: #6366f1 !important; stroke-width: 2.2 !important; }
        .fleet-svg .fleet-node.hot rect { fill: rgba(99,102,241,0.12) !important; }
        .fleet-svg .fleet-edge.hot { stroke: #6366f1 !important; stroke-width: 2 !important; filter: drop-shadow(0 0 6px rgba(99,102,241,0.6)); }
      `}</style>
      <svg viewBox="0 0 800 340" className="fleet-svg min-w-[640px] w-full" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Hermes worker fleet diagram">
        {/* USER */}
        <g id="node-user" className={`fleet-node ${hot("node-user")}`}>
          <rect x="320" y="8" width="160" height="36" rx="6" fill="rgba(122,131,153,0.10)" stroke="#7a8399" />
          <text x="400" y="30" textAnchor="middle" fontSize="11" fontWeight="600" fill="#2d3142">USER</text>
        </g>
        <line x1="400" y1="44" x2="400" y2="64" stroke="#4f5d75" strokeWidth="1.2" markerEnd="url(#fleet-arrow)" className={`fleet-edge ${hot("node-hermes")}`} />
        {/* HERMES */}
        <g id="node-hermes" className={`fleet-node ${hot("node-hermes")}`}>
          <rect x="280" y="64" width="240" height="44" rx="6" fill="rgba(235,108,54,0.08)" stroke="#eb6c36" />
          <text x="400" y="82" textAnchor="middle" fontSize="12" fontWeight="600" fill="#2d3142">HERMES</text>
          <text x="400" y="98" textAnchor="middle" fontSize="8" fill="#4f5d75">plans · routes · synthesizes</text>
        </g>
        {/* Tooling row */}
        <line x1="220" y1="108" x2="220" y2="132" stroke="#4f5d75" strokeWidth="1" className={`fleet-edge ${hot("node-workers-list")}`} />
        <line x1="400" y1="108" x2="400" y2="132" stroke="#eb6c36" strokeWidth="1.6" className={`fleet-edge ${hot("node-workers-run")}`} />
        <line x1="580" y1="108" x2="580" y2="132" stroke="#4f5d75" strokeWidth="1" className={`fleet-edge ${hot("node-workers-benchmark")}`} />
        <g id="node-workers-list" className={`fleet-node ${hot("node-workers-list")}`}>
          <rect x="120" y="132" width="200" height="40" rx="6" fill="rgba(45,49,66,0.05)" stroke="#4f5d75" />
          <text x="220" y="150" textAnchor="middle" fontSize="10" fontWeight="600" fill="#2d3142">workers list</text>
          <text x="220" y="162" textAnchor="middle" fontSize="7" fill="#4f5d75">registry</text>
        </g>
        <g id="node-workers-run" className={`fleet-node ${hot("node-workers-run")}`}>
          <rect x="300" y="132" width="200" height="40" rx="6" fill="#fff" stroke="#2d3142" />
          <text x="400" y="150" textAnchor="middle" fontSize="10" fontWeight="600" fill="#2d3142">workers run</text>
          <text x="400" y="162" textAnchor="middle" fontSize="7" fill="#4f5d75">router</text>
        </g>
        <g id="node-workers-benchmark" className={`fleet-node ${hot("node-workers-benchmark")}`}>
          <rect x="520" y="132" width="160" height="40" rx="6" fill="rgba(45,49,66,0.05)" stroke="#4f5d75" />
          <text x="600" y="150" textAnchor="middle" fontSize="10" fontWeight="600" fill="#2d3142">workers benchmark</text>
          <text x="600" y="162" textAnchor="middle" fontSize="7" fill="#4f5d75">evidence</text>
        </g>
        {/* Benchmark → run evidence edge */}
        <line x1="520" y1="152" x2="500" y2="152" stroke="#4f5d75" strokeWidth="1" markerEnd="url(#fleet-arrow)" />
        <line x1="400" y1="172" x2="400" y2="196" stroke="#4f5d75" strokeWidth="1.2" markerEnd="url(#fleet-arrow)" className={`fleet-edge ${hot("node-herdr")}`} />
        {/* HERDR */}
        <g id="node-herdr" className={`fleet-node ${hot("node-herdr")}`}>
          <rect x="280" y="196" width="240" height="36" rx="6" fill="#fff" stroke="#2d3142" />
          <text x="400" y="218" textAnchor="middle" fontSize="12" fontWeight="600" fill="#2d3142">HERDR</text>
        </g>
        {/* HERDR → workers */}
        {WORKER_NAMES.map((w, i) => {
          const x = 56 + i * 144;
          return <line key={w} x1={400 + (x + 56 - 400) * 0.18} y1={232} x2={x + 56} y2={256} stroke="#4f5d75" strokeWidth="1" className={`fleet-edge ${hot(`node-${w}`)}`} markerEnd="url(#fleet-arrow)" />;
        })}
        {/* Worker nodes */}
        {WORKER_NAMES.map((w, i) => {
          const x = 56 + i * 144;
          const label = w === "dsh" ? "dsh" : w.charAt(0).toUpperCase() + w.slice(1);
          return (
            <g key={w} id={`node-${w}`} className={`fleet-node ${hot(`node-${w}`)}`}>
              <rect x={x} y={256} width={112} height={40} rx={6} fill="rgba(45,49,66,0.03)" stroke="rgba(45,49,66,0.3)" />
              <text x={x + 56} y={278} textAnchor="middle" fontSize="10" fontWeight="600" fill="#2d3142">{label}</text>
              <text x={x + 56} y={290} textAnchor="middle" fontSize="7" fill="#4f5d75">worker</text>
            </g>
          );
        })}
        {/* Results dashed return */}
        <path d="M 720 276 H 760 Q 768 276 768 268 V 40 Q 768 32 760 32 H 560" fill="none" stroke="#4f5d75" strokeWidth="1" strokeDasharray="4,3" markerEnd="url(#fleet-arrow)" />
        <defs>
          <marker id="fleet-arrow" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="#4f5d75" /></marker>
        </defs>
      </svg>
    </div>
  );
}

function LiveCard({ exec, tick }: { exec: FleetLiveExecution; tick: number }) {
  void tick;
  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 rounded-md border bg-card px-3 py-2 text-sm shadow-sm transition-all">
      <div className="flex items-center gap-2">
        <Badge className={`shrink-0 border text-xs ${statusClass(exec.status)}`}>{exec.status}</Badge>
        <span className="truncate text-xs" title={exec.task}>{exec.task || "(no task)"}</span>
      </div>
      <div className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
        <span className="truncate font-mono text-[11px]">{exec.execution_id.slice(0, 8)}</span>
        <span>{elapsedSec(exec.started_at)}</span>
      </div>
    </div>
  );
}

function HistoryRow({ entry }: { entry: FleetHistoryEntry }) {
  const detail = entry.error || entry.result_tail || "";
  return (
    <tr className="border-b border-border/50 hover:bg-secondary/20">
      <td className="px-3 py-2 font-mono text-xs">{entry.execution_id.slice(0, 8)}</td>
      <td className="px-3 py-2 text-xs">{entry.worker_type}</td>
      <td className="px-3 py-2"><Badge className={`border text-xs ${statusClass(entry.status)}`}>{entry.status}</Badge></td>
      <td className="max-w-[320px] truncate px-3 py-2 text-xs" title={entry.task}>{entry.task}</td>
      <td className="max-w-[220px] truncate px-3 py-2 text-xs text-muted-foreground" title={detail}>{detail}</td>
    </tr>
  );
}

export default function FleetPage() {
  const { setEnd } = usePageHeader();
  const [live, setLive] = useState<FleetLiveExecution[] | null>(null);
  const [history, setHistory] = useState<FleetHistoryEntry[]>([]);
  const [workers, setWorkers] = useState<FleetWorker[]>([]);
  const [workerModels, setWorkerModels] = useState<Record<string, { provider: string; model: string }>>({});
  const [catalog, setCatalog] = useState<{ providers: Record<string, { models: string[] }>; errors?: Record<string, string> } | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [modelToast, setModelToast] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [hotNodes, setHotNodes] = useState<Set<string>>(new Set());
  const [taskInput, setTaskInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const cursorRef = useRef(0);

  const flashNodes = useCallback((ids: string[]) => {
    setHotNodes((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.add(id));
      return next;
    });
    setTimeout(() => {
      setHotNodes((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }, 1500);
  }, []);

  const loadStatus = useCallback(async () => {
    try {
      const res = await api.getFleetStatus();
      setLive(res.live);
      setHistory(res.history);
      setWorkers(res.workers);
      if (res.worker_models) setWorkerModels(res.worker_models);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const refreshCatalog = useCallback(async (opts?: { refresh?: boolean }) => {
    try {
      setCatalogLoading(true);
      const c = await api.getFleetModels(opts);
      setCatalog(c);
      setCatalogError(null);
    } catch (e) {
      setCatalogError(String(e));
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    let cancelled = false;
    (async () => {
      try {
        setCatalogLoading(true);
        const c = await api.getFleetModels();
        if (!cancelled) {
          setCatalog(c);
          setCatalogError(null);
        }
      } catch (e) {
        if (!cancelled) setCatalogError(String(e));
      } finally {
        if (!cancelled) setCatalogLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadStatus]);

  // Elapsed timer tick
  useEffect(() => {
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Header refresh
  useEffect(() => {
    setEnd(
      <Button ghost size="icon" type="button" onClick={() => void loadStatus()} aria-label="Refresh fleet">
        <RefreshCw />
      </Button>,
    );
    return () => setEnd(null);
  }, [loadStatus, setEnd]);

  // Events polling every 1.5s (waku pattern)
  useEffect(() => {
    const poll = async () => {
      try {
        const res = await api.getFleetEvents(cursorRef.current);
        if (res.events.length > 0) {
          cursorRef.current = res.next_cursor;
          setCursor(res.next_cursor);
          // Also refresh status to show new live/history data
          void loadStatus();
          for (const ev of res.events) {
            const w = ev.worker_type;
            const ids = ["node-hermes", "node-workers-run", "node-herdr"];
            if (w) ids.push(`node-${w}`);
            flashNodes(ids);
          }
        } else if (res.next_cursor !== cursorRef.current) {
          cursorRef.current = res.next_cursor;
          setCursor(res.next_cursor);
        }
      } catch {
        // polling errors are non-fatal
      }
    };
    // Initial cursor from status: set to 0 and let events feed populate
    const id = setInterval(() => void poll(), 1500);
    // Also poll once shortly after mount to seed events
    const t = setTimeout(() => void poll(), 800);
    return () => {
      clearInterval(id);
      clearTimeout(t);
    };
  }, [flashNodes, loadStatus]);

  // Keep ref in sync when cursor state changes via other paths
  useEffect(() => {
    cursorRef.current = cursor;
  }, [cursor]);

  const byWorker = (() => {
    const m = new Map<string, FleetLiveExecution[]>();
    for (const w of WORKER_NAMES) m.set(w, []);
    for (const e of live ?? []) {
      const k = e.worker_type;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(e);
    }
    return m;
  })();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const t = taskInput.trim();
    if (!t) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await api.runFleetTask(t);
      setTaskInput("");
      flashNodes(["node-user", "node-hermes", "node-workers-run"]);
      // Status will update via next events poll; also refresh eagerly
      setTimeout(() => void loadStatus(), 600);
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="container mx-auto max-w-6xl space-y-6 py-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-semibold">
          <Zap className="h-6 w-6 text-primary" /> Fleet
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">Realtime view of the Hermes worker fleet — tasks flow from HERMES through HERDR to harnesses.</p>
      </div>

      <FleetDiagram hotNodes={hotNodes} />
      {modelToast && (
        <div className="rounded-md border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">{modelToast}</div>
      )}
      {catalogError && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">{catalogError}</div>
      )}

      <div className="flex justify-end">
        <Button
          ghost
          size="sm"
          onClick={() => void refreshCatalog({ refresh: true })}
          disabled={catalogLoading}
          aria-label="Refresh model catalog"
          data-testid="fleet-models-refresh"
          className="h-7 px-2 text-xs"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${catalogLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {error ? (
        <Card><CardContent className="py-4 text-sm text-muted-foreground">{error}</CardContent></Card>
      ) : live === null ? (
        <div className="flex min-h-[120px] items-center justify-center"><Spinner /></div>
      ) : null}

      {/* Worker columns with live task cards */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        {WORKER_NAMES.map((w) => {
          const label = w === "dsh" ? "dsh" : w.charAt(0).toUpperCase() + w.slice(1);
          const info = workers.find((x) => x.name === w);
          const tasks = byWorker.get(w) ?? [];
          return (
            <Card key={w} className="flex flex-col">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">{label}</CardTitle>
                {info ? (
                  <p className="text-xs text-muted-foreground">{info.version ?? "—"} · {(info.capabilities ?? []).slice(0, 3).join(", ")}</p>
                ) : (
                  <p className="text-xs text-muted-foreground">not installed</p>
                )}
              </CardHeader>
              <CardContent className="flex flex-1 flex-col gap-2">
                <FleetModelPicker
                  worker={w}
                  current={workerModels[w] ?? null}
                  catalog={catalog}
                  loading={catalogLoading}
                  onSelect={async (provider, model) => {
                    const prev = workerModels[w] ?? null;
                    // Optimistic update
                    setWorkerModels((m) => ({ ...m, [w]: { provider, model } }));
                    try {
                      const updated = await api.setFleetModel(w, provider, model);
                      setWorkerModels((m) => ({ ...m, [w]: { provider: updated.provider, model: updated.model } }));
                      setModelToast(`Model for ${w} set to ${provider} · ${model}`);
                      setTimeout(() => setModelToast(null), 3000);
                      // Re-fetch status to stay consistent, don't race with catalog
                      void loadStatus();
                    } catch (e) {
                      // Revert
                      setWorkerModels((m) => {
                        const copy = { ...m };
                        if (prev) copy[w] = prev;
                        else delete copy[w];
                        return copy;
                      });
                      setCatalogError(String(e));
                      setTimeout(() => setCatalogError(null), 4000);
                    }
                  }}
                  onClear={async () => {
                    const prev = workerModels[w] ?? null;
                    const copy = { ...workerModels };
                    delete copy[w];
                    setWorkerModels(copy);
                    try {
                      await api.clearFleetModel(w);
                      setModelToast(`Model for ${w} reset to default`);
                      setTimeout(() => setModelToast(null), 3000);
                      void loadStatus();
                    } catch (e) {
                      if (prev) setWorkerModels((m) => ({ ...m, [w]: prev }));
                      setCatalogError(String(e));
                      setTimeout(() => setCatalogError(null), 4000);
                    }
                  }}
                />
                <p className="text-[11px] text-muted-foreground">Model changes apply to future fleet runs.</p>
                {tasks.length === 0 ? (
                  <p className="py-2 text-center text-xs text-muted-foreground">idle</p>
                ) : (
                  tasks.map((ex) => <LiveCard key={ex.execution_id} exec={ex} tick={tick} />)
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Prompt box (waku chat-dock style) */}
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">Dispatch a task</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex gap-2">
            <input
              value={taskInput}
              onChange={(e) => setTaskInput(e.target.value)}
              placeholder="Type a task — it routes and executes through the fleet…"
              className="flex-1 rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
            />
            <Button type="submit" disabled={submitting || !taskInput.trim()} className="shrink-0">
              {submitting ? <Spinner className="h-4 w-4" /> : <Send className="h-4 w-4" />}
              <span className="ml-1 hidden sm:inline">Run</span>
            </Button>
          </form>
          {submitError && <p className="mt-2 text-xs text-destructive">{submitError}</p>}
        </CardContent>
      </Card>

      {/* Recent execution history */}
      <Card>
        <CardHeader className="pb-2"><CardTitle className="text-sm">Recent executions</CardTitle></CardHeader>
        <CardContent className="p-0">
          {history.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">No completed executions yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="px-3 py-2 font-medium">ID</th>
                    <th className="px-3 py-2 font-medium">Worker</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Task</th>
                    <th className="px-3 py-2 font-medium">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h) => <HistoryRow key={h.execution_id} entry={h} />)}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
