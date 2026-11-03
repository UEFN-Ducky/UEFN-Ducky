"""Soak budgets over the perf log: fail on call floods and UI freezes.

``py -3 -m frontend.soak_budget --hours 2`` prints every broken budget and exits 1 when any.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fail when the perf log breaks the soak budgets.")
    ap.add_argument("--hours", type=float, default=2.0)
    args = ap.parse_args(argv)
    broken = check(args.hours)
    for line in broken:
        print(line)
    if not broken:
        print(f"soak budgets ok over {args.hours:g} h")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
