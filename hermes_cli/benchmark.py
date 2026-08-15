"""``hermes workers benchmark`` — benchmark suite for the worker fleet.

Runs a set of small, self-contained tasks through ``worker_backend.run_task()``
on each selected worker and records per-worker/per-task metrics. This is the
EVIDENCE behind routing (§13 — "do not implement automatic routing based on
intuition alone. Use benchmark evidence.").

Categories are the benchmark categories of the architecture doc (§24):

- A. repository    — read a small fixture project and summarize what it does
- B. coding        — write a function and a passing test
- C. long_horizon  — multi-step task (create data, compute, report)
- D. recovery      — find and fix a bug so a test passes
- E. context_heavy — answer a question about the end of a long document
- F. tool_heavy    — drive several terminal tool calls and report the results
- G. multi_file    — create three cooperating modules and run them

Tasks never need the internet or a specific repo: each runs in a temp
workspace created at runtime (``tempfile.mkdtemp``), fixtures are written
inline, and every task is self-verifying (the expected marker/values are
derivable from the prompt alone).

Metrics recorded per worker per task (§25): completion (expected marker
present), correctness (expected value present), tokens consumed (parsed from
the harness output when it reports them), latency (seconds), and failure mode
(``timeout`` / ``error`` / ``wrong-output``). Results persist to
``<HERMES_HOME>/benchmark_results.json`` (see ``save_results`` /
``load_results``) — the routing evidence the ``route_task`` scorer consumes.

Stdlib only. State paths go through ``get_hermes_home()``; never hardcode
``~/.hermes``.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hermes_constants import get_hermes_home

from hermes_cli import worker_backend
from hermes_cli import workers

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Per-task timeout in seconds (§24).
DEFAULT_TIMEOUT = 120

#: Routing-evidence store filename inside HERMES_HOME (§13). Persisted by
#: ``save_results`` and read by the evidence-aware router.
BENCHMARK_STORE = "benchmark_results.json"

#: Default workers for a benchmark run (§24) — dsh is skipped unless
#: explicitly requested (experimental).
DEFAULT_WORKERS: tuple[str, ...] = ("pi", "codex", "opencode", "commandcode")

#: Matches a harness-reported token count: "tokens used: 1,234",
#: "Tokens consumed = 99", "tokens: 42".
_TOKEN_RE = re.compile(r"tokens\s*(?:used|consumed)?\s*[:=]\s*([\d,]+)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Task / category data contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkTask:
    """One small, self-verifying benchmark task.

    ``markers`` gate *completion* ("did it produce the expected marker?"),
    ``values`` gate *correctness* ("is the expected value in the output?") —
    both checked case-insensitively as substrings. ``fixtures`` is a list of
    ``(relative_path, content)`` pairs written into the temp workspace.
    """

    category: str
    name: str
    prompt: str
    markers: tuple[str, ...]
    values: tuple[str, ...]
    fixtures: tuple[tuple[str, str], ...] = ()
    timeout: int = DEFAULT_TIMEOUT


@dataclass(frozen=True)
class BenchmarkCategory:
    """A §24 category: a key (A–G), a name, and 1–3 tasks."""

    key: str
    name: str
    description: str
    tasks: tuple[BenchmarkTask, ...]
    aliases: tuple[str, ...] = ()


def _task(
    category: str,
    name: str,
    prompt: str,
    markers: tuple[str, ...],
    values: tuple[str, ...] | None = None,
    fixtures: tuple[tuple[str, str], ...] = (),
    timeout: int = DEFAULT_TIMEOUT,
) -> BenchmarkTask:
    return BenchmarkTask(
        category=category,
        name=name,
        prompt=prompt,
        markers=markers,
        values=values if values is not None else markers,
        fixtures=fixtures,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Category fixtures + task prompts
# ---------------------------------------------------------------------------

_A_FIXTURES = (
    (
        "README.md",
        "# Acme Weather\n"
        "\n"
        "The Acme Weather project fetches daily weather forecasts for a list\n"
        "of cities and prints a plain-text summary to the terminal.\n",
    ),
    (
        "weather.py",
        "def fetch(city):\n"
        '    return {"city": city, "temp_c": 18}\n',
    ),
    (
        "forecast.py",
        "from weather import fetch\n"
        "\n"
        "\n"
        "def summarize(city):\n"
        '    data = fetch(city)\n'
        '    return f"{data[\'city\']}: {data[\'temp_c\']}C"\n',
    ),
)

_D_FIXTURES = (
    ("buggy.py", "def is_even(n):\n    return n % 2 == 1\n"),
    (
        "test_buggy.py",
        "from buggy import is_even\n"
        "\n"
        "\n"
        "def test_is_even():\n"
        "    assert is_even(2) is True\n"
        "    assert is_even(10) is True\n"
        "    assert is_even(3) is False\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    test_is_even()\n"
        '    print("PASSED")\n',
    ),
)


def _celestial_manual() -> str:
    """A long synthetic document for the context-heavy category (E).

    The unique token ``Zephyros`` appears only in the final paragraph, so the
    worker must actually read the whole document to answer correctly.
    """
    paragraphs = [
        "THE CELESTIAL NAVIGATION SOCIETY\n"
        "Official Field Manual, 12th Edition\n"
        "\n"
        "This manual describes the instruments and procedures used by the "
        "Society's surveyors to record star positions from coastal "
        "observatories. It is organized into numbered sections followed by a "
        "final paragraph of standing instructions.",
    ]
    for i in range(1, 121):
        paragraphs.append(
            f"Section {i}. Record the azimuth and elevation of every "
            f"first-magnitude star visible during the {i % 4 + 1}th quarter. "
            "Instruments must be levelled against the brass meridian ring, "
            "and readings are logged in the seafarer's ledger with the date, "
            "tide, and observer's initials."
        )
    paragraphs.append(
        "Final paragraph. The restored observatory at Zephyros now hosts the "
        "Society's master chronometer. Surveyors are reminded that the "
        "meridian cipher is always green, and that the observatory at "
        "Zephyros must be mentioned in every final report."
    )
    return "\n\n".join(paragraphs)


#: Ordered benchmark categories (§24 A–G).
CATEGORIES: tuple[BenchmarkCategory, ...] = (
    BenchmarkCategory(
        "A",
        "repository",
        "Read a small fixture project and summarize what it does",
        (
            _task(
                "A",
                "summarize_project",
                "Read the files in this workspace (README.md, weather.py, "
                "forecast.py) and summarize what the project does. In your "
                "final answer, mention the project name.",
                markers=("Acme Weather",),
                fixtures=_A_FIXTURES,
            ),
        ),
    ),
    BenchmarkCategory(
        "B",
        "coding",
        "Write a function and a passing test",
        (
            _task(
                "B",
                "is_prime",
                "In this workspace, write a Python function `is_prime(n)` in "
                "`solution.py` that returns True when n is prime and False "
                "otherwise. Also write `test_solution.py` with a test that "
                "asserts is_prime(2) is True, is_prime(4) is False, and "
                "is_prime(7) is True. Run the test (pytest if available, "
                "otherwise a `python3 test_solution.py` that prints PASSED on "
                "success) and confirm it passes. Report the outcome.",
                markers=("passed",),
            ),
            _task(
                "B",
                "reverse_string",
                "In this workspace, write a Python function "
                "`reverse_string(s)` in `solution.py` that returns s "
                "reversed. Write `test_solution.py` with a test that asserts "
                "reverse_string('hello') == 'olleh' and "
                "reverse_string('') == ''. Run the test (pytest if "
                "available, otherwise `python3 test_solution.py` printing "
                "PASSED on success) and confirm it passes. Report the "
                "outcome.",
                markers=("passed",),
            ),
        ),
    ),
    BenchmarkCategory(
        "C",
        "long_horizon",
        "Multi-step data task (create, compute, report)",
        (
            _task(
                "C",
                "sum_of_data",
                "Work through this three-step task in the workspace: "
                "(1) create `data.csv` containing the integers 1 through 10, "
                "one per line, in ascending order; (2) write `sum.py` that "
                "reads `data.csv` and prints the sum of all its numbers; "
                "(3) run `python3 sum.py` and report the sum in your final "
                "answer.",
                markers=("55",),
                timeout=240,
            ),
        ),
    ),
    BenchmarkCategory(
        "D",
        "recovery",
        "Find and fix a bug so a test passes",
        (
            _task(
                "D",
                "fix_is_even",
                "There is a bug in `buggy.py`: the function `is_even(n)` "
                "returns the wrong value. Find the bug, fix it so that "
                "`test_buggy.py` passes, then run the test "
                "(`python3 test_buggy.py`, or pytest) and confirm it passes. "
                "Report the outcome.",
                markers=("passed",),
                fixtures=_D_FIXTURES,
            ),
        ),
    ),
    BenchmarkCategory(
        "E",
        "context_heavy",
        "Answer a question about the end of a long document",
        (
            _task(
                "E",
                "last_paragraph",
                _celestial_manual()
                + "\n\n"
                + "Read the entire document above. In your final answer, "
                "state what the document's final paragraph says about the "
                "observatory at Zephyros.",
                markers=("zephyros",),
                timeout=240,
            ),
        ),
        aliases=("context", "context-heavy"),
    ),
    BenchmarkCategory(
        "F",
        "tool_heavy",
        "Drive several terminal tool calls and report the results",
        (
            _task(
                "F",
                "three_commands",
                "Use the terminal tool to run `python3 -c` three separate "
                "times to compute 2+2, 3*4, and 10-3. Report all three "
                "results in your final answer.",
                markers=("4", "12", "7"),
            ),
        ),
        aliases=("tool", "tool-heavy"),
    ),
    BenchmarkCategory(
        "G",
        "multi_file",
        "Create three cooperating modules and run them",
        (
            _task(
                "G",
                "add_and_mul",
                "In this workspace create three files: `mod_a.py` with "
                "`add(a, b)`, `mod_b.py` with `mul(a, b)`, and `main.py` "
                "that imports both and prints `add(2, 3) + mul(4, 5)`. Run "
                "`python3 main.py` and report the number it prints in your "
                "final answer.",
                markers=("25",),
            ),
        ),
        aliases=("multi-file", "multifile"),
    ),
)

_CATEGORY_KEYS = {cat.key.lower(): cat for cat in CATEGORIES}

#: Category key → canonical name (``"B"`` → ``"coding"``). Used to normalize
#: §25 result records into routing-evidence records.
_CATEGORY_NAMES: dict[str, str] = {cat.key: cat.name for cat in CATEGORIES}


def _match_category(part: str) -> BenchmarkCategory | None:
    """Resolve one ``--category`` token (key, name, or alias)."""
    cat = _CATEGORY_KEYS.get(part)
    if cat is not None:
        return cat
    for candidate in CATEGORIES:
        if part == candidate.name or part in candidate.aliases:
            return candidate
    return None


def resolve_categories(selected: str = "") -> list[BenchmarkCategory]:
    """Map a comma-separated ``--category`` value to ordered categories.

    Tokens may be category keys (``A``–``G``, case-insensitive) or
    names/aliases (``coding``, ``recovery``, ``context``, ...). Empty input
    returns every category. Raises ValueError for unknown tokens.
    """
    text = (selected or "").strip()
    if not text:
        return list(CATEGORIES)
    wanted = [part.strip().lower() for part in text.split(",") if part.strip()]
    resolved: list[BenchmarkCategory] = []
    seen: set[str] = set()
    for part in wanted:
        cat = _match_category(part)
        if cat is None:
            names = ", ".join(c.key for c in CATEGORIES)
            raise ValueError(
                f"unknown benchmark category '{part}' (use {names} or a category name/alias)"
            )
        if cat.key not in seen:
            seen.add(cat.key)
            resolved.append(cat)
    return resolved


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------


def _write_fixtures(fixtures: tuple[tuple[str, str], ...]) -> Path:
    """Create a temp workspace and write the task's fixture files."""
    workspace = Path(tempfile.mkdtemp(prefix="hermes-bench-"))
    for relative, content in fixtures:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return workspace


# ---------------------------------------------------------------------------
# Metrics (§25)
# ---------------------------------------------------------------------------


def _contains_all(needles: tuple[str, ...], haystack: str) -> bool:
    lowered = haystack.lower()
    return all(needle.lower() in lowered for needle in needles)


def _extract_tokens(text: str) -> int | None:
    """Pull the reported token count out of harness output, if any."""
    if not text:
        return None
    matches = _TOKEN_RE.findall(text)
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def evaluate_task(
    worker: str,
    task: BenchmarkTask,
    final: dict,
    latency: float,
    workspace: str = "",
) -> dict:
    """Turn one backend execution dict into a §25 metrics record."""
    status = final.get("status", worker_backend.FAILED)
    output = final.get("result") or ""
    error = final.get("error") or ""

    failure: str | None = None
    if status != worker_backend.DONE:
        failure = "timeout" if "timeout" in error.lower() else "error"
    elif not _contains_all(task.markers, output):
        failure = "wrong-output"

    completed = failure is None
    correct = completed and _contains_all(task.values, output)

    tokens = final.get("tokens")
    if tokens is None:
        tokens = _extract_tokens(output)

    return {
        "worker": worker,
        "category": task.category,
        "task": task.name,
        "status": status,
        "completed": completed,
        "correct": correct,
        "tokens": tokens,
        "latency": round(latency, 3),
        "failure": failure,
        "error": error,
        "workspace": workspace,
        "output": output,
    }


def run_task_benchmark(
    worker: str,
    task: BenchmarkTask,
    timeout: int | None = None,
    workspace: Path | None = None,
) -> dict:
    """Run one task on one worker through ``worker_backend.run_task()``."""
    ws = workspace or _write_fixtures(task.fixtures)
    spec = worker_backend.WorkerSpec(
        worker_type=worker,
        task=task.prompt,
        workspace=str(ws),
        timeout=timeout or task.timeout,
        environment_policy="isolated",
    )
    started = time.monotonic()
    final = worker_backend.run_task(spec)
    latency = time.monotonic() - started
    return evaluate_task(worker, task, final, latency, str(ws))


def run_benchmarks(
    worker_types: list[str],
    categories: list[BenchmarkCategory],
    timeout: int | None = None,
) -> list[dict]:
    """Run every selected category's tasks on every selected worker."""
    results: list[dict] = []
    for worker in worker_types:
        for category in categories:
            for task in category.tasks:
                results.append(run_task_benchmark(worker, task, timeout))
    return results


# ---------------------------------------------------------------------------
# Routing-evidence store (§13)
# ---------------------------------------------------------------------------
#
# Benchmark results persist to <HERMES_HOME>/benchmark_results.json so the
# router can prefer workers with proven success for a task's category instead
# of relying on capability intuition alone. The store is a JSON document:
#
#   {"generated_at": "...", "results": [
#       {"worker": "opencode", "category": "coding", "task": "B/is_prime",
#        "pass": true, "latency_s": 13.0, "tokens": null, "failure_mode": null}
#   ]}


def benchmark_results_path(hermes_home: Path | None = None) -> Path:
    """Path to the routing-evidence store (profile-aware)."""
    return (hermes_home or get_hermes_home()) / BENCHMARK_STORE


def _now_iso() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _evidence_record(record: dict) -> dict:
    """Normalize a §25 benchmark result (or stored record) into evidence shape.

    A §25 result carries ``category`` as the category *key* (``"B"``) and
    ``task`` as the bare task name (``"is_prime"``); the evidence store uses
    the category *name* (``"coding"``) and a ``key/task`` composite
    (``"B/is_prime"``). Already-normalized records pass through unchanged, so
    ``save_results(load_results(...))`` round-trips idempotently.
    """
    category = str(record.get("category") or "")
    task = str(record.get("task") or "")
    if category in _CATEGORY_NAMES:
        normalized_category = _CATEGORY_NAMES[category]
        task = f"{category}/{task}"
    else:
        normalized_category = category
    if "pass" in record:
        passed = bool(record.get("pass"))
    else:
        passed = bool(record.get("completed") and record.get("correct"))
    latency = record.get("latency_s") if "latency_s" in record else record.get("latency")
    failure = record.get("failure_mode") if "failure_mode" in record else record.get("failure")
    return {
        "worker": record.get("worker"),
        "category": normalized_category,
        "task": task,
        "pass": passed,
        "latency_s": latency,
        "tokens": record.get("tokens"),
        "failure_mode": failure,
    }


def _result_key(record: dict) -> tuple[str, str]:
    """The dedupe key for an evidence record: (worker, task)."""
    return (str(record.get("worker") or ""), str(record.get("task") or ""))


def _merge_results(existing: list[dict], new: list[dict]) -> list[dict]:
    """Merge evidence lists — per (worker, task) the newer record wins.

    Order is preserved: existing records keep their position, and brand-new
    (worker, task) pairs append at the end.
    """
    merged: dict[tuple[str, str], dict] = {}
    for record in existing:
        merged[_result_key(record)] = record
    for record in new:
        normalized = _evidence_record(record)
        merged[_result_key(normalized)] = normalized
    return list(merged.values())


def load_results(path: str | Path | None = None) -> list[dict]:
    """Read the routing-evidence store; returns the results list ([] if missing)."""
    store_path = Path(path) if path else benchmark_results_path()
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        return []
    return [record for record in results if isinstance(record, dict)]


def save_results(results: list[dict], path: str | Path | None = None) -> Path:
    """Persist benchmark evidence to ``path`` (default the HERMES_HOME store).

    Merges ``results`` into whatever is already stored — a new record for a
    (worker, task) pair replaces the older one, so re-running a benchmark
    refreshes its evidence instead of accumulating stale entries. Returns the
    path written.
    """
    store_path = Path(path) if path else benchmark_results_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_results(path=store_path)
    payload = {"generated_at": _now_iso(), "results": _merge_results(existing, results)}
    store_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return store_path


# ---------------------------------------------------------------------------
# Aggregation + output
# ---------------------------------------------------------------------------


def _aggregate(results: list[dict]) -> dict:
    """Group per-task results into {worker: {category_key: {stats}}}."""
    aggregated: dict[str, dict] = {}
    for record in results:
        by_category = aggregated.setdefault(record["worker"], {})
        entry = by_category.setdefault(
            record["category"],
            {"pass": True, "tasks": 0, "passed": 0, "latency": 0.0, "tokens": 0},
        )
        entry["tasks"] += 1
        entry["latency"] += record["latency"]
        if record["tokens"] is not None:
            entry["tokens"] += record["tokens"]
        if record["completed"] and record["correct"]:
            entry["passed"] += 1
        else:
            entry["pass"] = False
    return aggregated


def _fmt_duration(seconds: float) -> str:
    return f"{seconds:.1f}s"


def build_payload(
    worker_types: list[str],
    categories: list[BenchmarkCategory],
    timeout: int,
    results: list[dict],
) -> dict:
    """Structured results payload for ``--json`` / ``--out`` (routing evidence)."""
    return {
        "tool": "hermes workers benchmark",
        "workers": worker_types,
        "categories": [
            {
                "key": cat.key,
                "name": cat.name,
                "description": cat.description,
                "tasks": [
                    {"name": task.name, "markers": list(task.markers), "values": list(task.values)}
                    for task in cat.tasks
                ],
            }
            for cat in categories
        ],
        "timeout": timeout,
        "summary": {
            worker: {
                "categories": {
                    key: {
                        "pass": stats["pass"],
                        "tasks": stats["tasks"],
                        "passed": stats["passed"],
                        "latency": round(stats["latency"], 3),
                        "tokens": stats["tokens"],
                    }
                    for key, stats in by_category.items()
                },
                "total_latency": round(
                    sum(stats["latency"] for stats in by_category.values()), 3
                ),
            }
            for worker, by_category in _aggregate(results).items()
        },
        "results": results,
    }


def print_table(
    worker_types: list[str],
    categories: list[BenchmarkCategory],
    results: list[dict],
    timeout: int,
) -> None:
    """Human-readable worker × category pass/fail table + per-task detail."""
    total_tasks = sum(len(cat.tasks) for cat in categories)
    print(
        f"hermes workers benchmark — {len(worker_types)} worker(s), "
        f"{len(categories)} category(ies), {total_tasks} task(s), "
        f"timeout {timeout}s"
    )
    print()

    aggregated = _aggregate(results)
    headers = ["WORKER", *[cat.name.upper() for cat in categories], "LATENCY", "TOKENS"]
    rows: list[list[str]] = []
    for worker in worker_types:
        by_category = aggregated.get(worker, {})
        row = [worker]
        for cat in categories:
            entry = by_category.get(cat.key)
            row.append("PASS" if entry and entry["pass"] else "FAIL")
        row.append(_fmt_duration(sum(e["latency"] for e in by_category.values())))
        total_tokens = sum(e["tokens"] for e in by_category.values())
        row.append(f"{total_tokens:,}" if total_tokens else "—")
        rows.append(row)

    all_rows = [headers, *rows]
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    for index, row in enumerate(all_rows):
        print("  ".join(str(row[j]).ljust(widths[j]) for j in range(len(headers))).rstrip())
        if index == 0:
            print("  ".join("-" * w for w in widths))

    print()
    print("Detail (per task):")
    for record in results:
        status = "PASS" if record["completed"] and record["correct"] else "FAIL"
        tokens = f"{record['tokens']:,}" if record["tokens"] else "—"
        reason = f" ({record['failure']})" if record["failure"] else ""
        print(
            f"  {record['worker']:<12} {record['category']}/{record['task']:<20} "
            f"{status:<5} {_fmt_duration(record['latency']):<8} tokens {tokens}{reason}"
        )

    failures = [r for r in results if r["failure"]]
    if failures:
        print()
        print("Failures:")
        for record in failures:
            print(
                f"  [{record['worker']}] {record['category']}/{record['task']} "
                f"— {record['failure']} (status {record['status']})"
            )
        print()
        passed = sum(1 for r in results if r["completed"] and r["correct"])
        print(f"Result: {passed}/{len(results)} tasks passed")
    else:
        print()
        print(f"Result: all {len(results)} tasks passed")


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------


def resolve_workers(selected: str, hermes_home) -> list[str]:
    """Resolve ``--worker`` (comma-separated) to an ordered worker list.

    Explicit workers are validated (known type + installed). With no
    selection, the default fleet order (pi, codex, opencode, commandcode —
    dsh skipped as experimental) is intersected with what is installed, then
    any registered custom workers are appended.
    """
    text = (selected or "").strip()
    if text:
        names = [name.strip() for name in text.split(",") if name.strip()]
        known = set(worker_backend.BACKENDS) | set(workers._read_custom_workers(hermes_home))
        for name in names:
            if name not in known:
                raise ValueError(
                    f"unknown worker type '{name}' (known: {', '.join(sorted(known))})"
                )
            if name in worker_backend.BACKENDS and worker_backend.resolve_binary(name) is None:
                raise ValueError(f"worker '{name}' is not installed on this machine")
        return names

    installed = set(workers.detect_workers(hermes_home)) | set(
        workers._read_custom_workers(hermes_home)
    )
    names = [name for name in DEFAULT_WORKERS if name in installed]
    for name in sorted(installed):
        if name not in names and name not in worker_backend.BACKENDS:
            names.append(name)
    return names


def run_benchmark_command(args) -> int:
    """``hermes workers benchmark`` handler. Returns the process exit code."""
    hermes_home = get_hermes_home()
    timeout = max(1, int(getattr(args, "timeout", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT))
    as_json = bool(getattr(args, "json", False))
    out_path = getattr(args, "out", "") or ""

    try:
        worker_types = resolve_workers(getattr(args, "worker", "") or "", hermes_home)
        categories = resolve_categories(getattr(args, "category", "") or "")
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not worker_types:
        print(
            "error: no worker harness installed — run `hermes workers` to see what is available",
            file=sys.stderr,
        )
        return 1

    results = run_benchmarks(worker_types, categories, timeout)
    payload = build_payload(worker_types, categories, timeout, results)

    # Routing evidence (§13): always refresh the HERMES_HOME store so the
    # evidence-aware router sees the latest results. Best-effort — a
    # read-only store must not fail the run.
    try:
        save_results(results)
    except OSError as exc:
        print(
            f"warning: cannot write routing evidence to {benchmark_results_path()}: {exc}",
            file=sys.stderr,
        )

    if out_path:
        try:
            Path(out_path).write_text(
                json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            print(f"error: cannot write '{out_path}': {exc}", file=sys.stderr)
            return 1

    if as_json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print_table(worker_types, categories, results, timeout)
    return 0
