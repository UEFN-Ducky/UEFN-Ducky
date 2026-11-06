"""Soak budgets over the perf log (call floods, UI freezes) and over memory samples.

``py -3 -m frontend.soak_budget --hours 2`` prints every broken budget and exits 1 when any.
``py -3 -m frontend.soak_budget --memory samples.jsonl [--sample-pid PID --minutes 60]``
checks per-process private memory (app, agent host, WebView2 renderer) and growth the same way.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from frontend import perf_trace

MAX_CALLS_PER_HOUR = 600  # any single bridge method
MAX_STALLS_PER_HOUR = 120  # ui_stall rows per process
MAX_STALL_MS = 2000.0  # any single stall


def _load_rows(hours: float) -> list[dict[str, Any]]:
    since = time.time() - hours * 3600
    return perf_trace._events().newest("perf", limit=perf_trace.PER_PROCESS_ROW_CAP, since=since)


def check_rows(rows: list[dict[str, Any]], hours: float) -> list[str]:
    """Broken budgets in ``rows`` (perf events, ``payload`` = the traced entry) over ``hours``."""
    hours = max(float(hours), 1e-6)
    by_pid: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_pid.setdefault(perf_trace._pid_of(row.get("source", "")), []).append(row)
    broken: list[str] = []
    for pid, mine in sorted(by_pid.items()):
        tag = f"[pid {pid}] " if len(by_pid) > 1 else ""
        block = perf_trace.per_process_block(mine, hours * 60)
        calls: dict[str, int] = {}
        worst = 0.0
        for row in mine:
            e = row.get("payload") or {}
            if e.get("kind") == "ui_js":
                src = str(e.get("source") or e.get("name") or "?")
                calls[src] = calls.get(src, 0) + 1
            elif e.get("kind") == "ui_stall":
                worst = max(worst, float(e.get("duration_ms") or 0))
        for method, n in sorted(calls.items(), key=lambda kv: -kv[1]):
            rate = n / hours
            if rate > MAX_CALLS_PER_HOUR:
                broken.append(f"{tag}{method}: {rate:.0f} calls/h > {MAX_CALLS_PER_HOUR}")
        stall_rate = block["ui_stalls"] / hours
        if stall_rate > MAX_STALLS_PER_HOUR:
            broken.append(f"{tag}ui_stalls: {stall_rate:.0f}/h > {MAX_STALLS_PER_HOUR}"
                          f" (p95 {block['ui_stall_p95_ms']} ms)")
        if worst > MAX_STALL_MS:
            broken.append(f"{tag}worst ui_stall: {worst:.0f} ms > {MAX_STALL_MS:.0f} ms")
    return broken


def check(hours: float = 2.0) -> list[str]:
    """Broken budgets in the last ``hours`` of the perf log (empty list = within budget)."""
    return check_rows(_load_rows(hours), hours)


# Memory budgets: private MB of the largest process in each role, and of Ducky's whole tree.
MEMORY_BUDGET_MB = {"app": 550.0, "agent_host": 350.0, "renderer": 500.0}
TREE_BUDGET_MB = 1600.0
MAX_GROWTH_MB_PER_HOUR = 50.0
GROWTH_FROM_MIN, GROWTH_TO_MIN = 10.0, 60.0
ROLES = ("app", "agent_host", "renderer", "gpu")
_LABEL = {"app": "app", "agent_host": "agent host", "renderer": "renderer", "gpu": "webview gpu", "tree": "tree"}


def role_of(name: str, cmdline: list[str]) -> str:
    """``agent_host``, ``renderer``, ``gpu`` or ``other`` for a process under the app."""
    args = " ".join(cmdline[1:]).lower()
    if name.lower().startswith("msedgewebview2"):
        if "--type=renderer" in args:
            return "renderer"
        if "--type=gpu-process" in args:
            return "gpu"
        return "other"
    if " agent-host" in f" {args}":
        return "agent_host"
    return "other"


def _psutil_cmdline(pid: int) -> list[str]:
    import psutil

    try:
        return list(psutil.Process(pid).cmdline())
    except (psutil.Error, OSError):
        return []


def memory_sample(procs: dict[int, Any], root: int, cmdline_of: Callable[[int], list[str]] | None = None,
                  now: float | None = None) -> dict[str, Any]:
    """One sample of ``root``'s process tree from a ``proc_table`` table: ``{t, app, agent_host,
    renderer, gpu, tree}`` in private MB (largest process per role; ``tree`` = the sum)."""
    from backend.util import proc_table

    cmdline_of = cmdline_of or _psutil_cmdline
    out: dict[str, Any] = {"t": time.time() if now is None else now, **{r: 0.0 for r in ROLES}, "tree": 0.0}
    for pid in proc_table.tree(procs, root):
        proc = procs[pid]
        mb = proc.private / (1 << 20)
        out["tree"] += mb
        role = "app" if pid == root else role_of(proc.name, cmdline_of(pid))
        if role in ROLES:
            out[role] = max(out[role], mb)
    return {k: round(v, 1) if k != "t" else v for k, v in out.items()}


def _slope_mb_per_hour(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of ``(minute, MB)`` points, in MB per hour."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return 0.0 if den <= 0 else sum((p[0] - mx) * (p[1] - my) for p in points) / den * 60


def check_memory(samples: list[dict[str, Any]]) -> list[str]:
    """Broken memory budgets in ``samples`` (``memory_sample`` dicts, oldest first): the last
    sample against each budget, and growth between minute 10 and 60 of the soak."""
    if not samples:
        return ["memory: no samples"]
    samples = sorted(samples, key=lambda s: float(s.get("t") or 0))
    broken: list[str] = []
    last = samples[-1]
    for role, budget in MEMORY_BUDGET_MB.items():
        mb = float(last.get(role) or 0)
        if mb > budget:
            broken.append(f"{_LABEL[role]}: {mb:.0f} MB > {budget:.0f} MB budget")
    tree = float(last.get("tree") or 0)
    if tree > TREE_BUDGET_MB:
        broken.append(f"tree: {tree:.0f} MB > {TREE_BUDGET_MB:.0f} MB budget")
    t0 = float(samples[0].get("t") or 0)
    window = [s for s in samples if GROWTH_FROM_MIN <= (float(s.get("t") or 0) - t0) / 60 <= GROWTH_TO_MIN]
    if len(window) >= 2 and (float(window[-1]["t"]) - float(window[0]["t"])) >= 5 * 60:
        for key in (*ROLES, "tree"):
            pts = [((float(s["t"]) - t0) / 60, float(s.get(key) or 0)) for s in window]
            rate = _slope_mb_per_hour(pts)
            if rate > MAX_GROWTH_MB_PER_HOUR:
                broken.append(f"{_LABEL[key]} growth: {rate:.0f} MB/h > {MAX_GROWTH_MB_PER_HOUR:.0f} MB/h"
                              f" (minute {GROWTH_FROM_MIN:g}-{GROWTH_TO_MIN:g})")
    return broken


def load_samples(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def collect(root: int, minutes: float, every_s: float, out: str | Path,
            table: Callable[[], dict[int, Any]] | None = None, sleep: Callable[[float], None] = time.sleep,
            cmdline_of: Callable[[int], list[str]] | None = None) -> list[dict[str, Any]]:
    """Sample ``root``'s tree every ``every_s`` for ``minutes``, appending JSON lines to ``out``;
    stops early when ``root`` exits."""
    if table is None:
        from backend.util import proc_table

        table = proc_table.table
    samples: list[dict[str, Any]] = []
    path = Path(out)
    count = max(1, int(minutes * 60 // max(every_s, 1e-3)) + 1)
    for i in range(count):
        procs = table()
        if root not in procs:
            break
        s = memory_sample(procs, root, cmdline_of)
        samples.append(s)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(s) + "\n")
        if i + 1 < count:
            sleep(every_s)
    return samples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail when the perf log or memory samples break the soak budgets.")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--memory", help="check memory samples (JSON lines) instead of the perf log")
    ap.add_argument("--sample-pid", type=int, help="sample this app's process tree into --memory first")
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--every", type=float, default=60.0, help="seconds between memory samples")
    args = ap.parse_args(argv)
    if args.memory:
        if args.sample_pid:
            collect(args.sample_pid, args.minutes, args.every, args.memory)
        samples = load_samples(args.memory)
        broken = check_memory(samples)
        last = samples[-1] if samples else {}
        what = ", ".join(f"{_LABEL[k]} {float(last.get(k) or 0):.0f} MB" for k in (*ROLES, "tree")) if last else ""
    else:
        broken = check(args.hours)
        what = f"over {args.hours:g} h"
    for line in broken:
        print(line)
    if not broken:
        print(f"soak budgets ok {what}")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
