"""Soak budgets over the perf log (call floods, UI freezes) and over memory samples.

``py -3 -m frontend.soak_budget --hours 2`` prints every broken budget and exits 1 when any.
``py -3 -m frontend.soak_budget --memory samples.jsonl [--sample-pid PID --minutes 60]
[--results results.json] [--agents N]`` checks per-process private memory (app, agent host,
WebView2 renderer), Ducky's own total apart from what agents start, tool servers per agent,
terminals closing after the last turn, and growth.
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


# Memory budgets: private MB of the largest process in each role, and of Ducky's own processes.
# The tree is split: ``ducky`` (app, agent hosts, WebView2, tool servers, consoles) is budgeted;
# ``workload`` (agent CLIs, their shells and everything under them) is reported, never failed on.
MEMORY_BUDGET_MB = {"app": 550.0, "agent_host": 350.0, "renderer": 500.0}
DUCKY_BUDGET_MB = 1000.0
TOOL_SERVER_MB_PER_AGENT = 25.0
TERMINAL_CLOSE_S = 15.0  # every agent terminal gone this long after the last turn ended
MAX_GROWTH_MB_PER_HOUR = 50.0
GROWTH_FROM_MIN, GROWTH_TO_MIN = 10.0, 60.0
ROLES = ("app", "agent_host", "renderer", "gpu")
_LABEL = {"app": "app", "agent_host": "agent host", "renderer": "renderer", "gpu": "webview gpu",
          "tree": "tree", "ducky": "ducky", "workload": "agent workload", "tool_servers": "tool servers"}
SHELLS = ("powershell", "pwsh", "bash", "cmd", "sh")
CONSOLES = ("conhost", "openconsole")
AGENT_CLIS = ("claude", "codex", "stub_claude")


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


def _stem(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower().rsplit(".", 1)[0]


def kind_of(name: str, cmdline: list[str]) -> str:
    """``tool_server``, ``console``, ``agent`` (an agent CLI or its launcher), ``terminal``
    (a shell) or ``ducky`` for any other process, by name and command line."""
    stem = _stem(name)
    args = [a.lower() for a in cmdline[1:]]
    if stem.endswith("-bridge") or "bridge" in args:
        return "tool_server"
    if stem in CONSOLES:
        return "console"
    if stem in AGENT_CLIS or any(_stem(a) in AGENT_CLIS for a in args):
        return "agent"
    if stem in SHELLS:
        return "terminal"
    return "ducky"


def _psutil_cmdline(pid: int) -> list[str]:
    import psutil

    try:
        return list(psutil.Process(pid).cmdline())
    except (psutil.Error, OSError):
        return []


def memory_sample(procs: dict[int, Any], root: int, cmdline_of: Callable[[int], list[str]] | None = None,
                  now: float | None = None) -> dict[str, Any]:
    """One sample of ``root``'s process tree from a ``proc_table`` table, in private MB: the
    largest process per role, ``tree`` (the sum), ``ducky`` (Ducky's own processes),
    ``workload`` (agent CLIs, shells and everything under them), ``tool_servers`` (MB) and
    the counts ``agents``, ``terminals`` and ``tool_server_count``."""
    from backend.util import proc_table

    cmdline_of = cmdline_of or _psutil_cmdline
    out: dict[str, Any] = {"t": time.time() if now is None else now, **{r: 0.0 for r in ROLES}, "tree": 0.0,
                           "ducky": 0.0, "workload": 0.0, "tool_servers": 0.0}
    counts = {"agents": 0, "terminals": 0, "tool_server_count": 0}
    in_workload: set[int] = set()
    for pid in proc_table.tree(procs, root):  # parents come before their children
        proc = procs[pid]
        mb = proc.private / (1 << 20)
        out["tree"] += mb
        cmd = cmdline_of(pid)
        role = "app" if pid == root else role_of(proc.name, cmd)
        if role in ROLES:
            out[role] = max(out[role], mb)
        kind = "ducky" if pid == root else kind_of(proc.name, cmd)
        if kind == "tool_server":  # Ducky's, even when an agent CLI started it
            out["tool_servers"] += mb
            counts["tool_server_count"] += 1
        elif kind in ("agent", "terminal") or (kind != "console" and proc.ppid in in_workload):
            in_workload.add(pid)
            if kind == "agent":
                counts["agents"] += 1
            elif kind == "terminal":
                counts["terminals"] += 1
        out["workload" if pid in in_workload else "ducky"] += mb
    return {**{k: round(v, 1) if k != "t" else v for k, v in out.items()}, **counts}


def _slope_mb_per_hour(points: list[tuple[float, float]]) -> float:
    """Least-squares slope of ``(minute, MB)`` points, in MB per hour."""
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    den = sum((p[0] - mx) ** 2 for p in points)
    return 0.0 if den <= 0 else sum((p[0] - mx) * (p[1] - my) for p in points) / den * 60


def agents_ended_at(samples: list[dict[str, Any]], results: dict[str, Any] | None = None) -> float | None:
    """When the last agent turn ended: the harness ``results.json`` (``last_turn_end``, or its
    samples' ``agents_running`` dropping to 0), else the first memory sample with no agent CLI
    left after one that had some. None when the agents never finished."""
    results = results or {}
    if results.get("last_turn_end"):
        return float(results["last_turn_end"])
    for rows, key in ((results.get("samples") or [], "agents_running"), (samples, "agents")):
        seen, ended = False, None
        for s in sorted((r for r in rows if r.get(key) is not None), key=lambda r: float(r.get("t") or 0)):
            if s[key] > 0:
                seen, ended = True, None
            elif seen and ended is None:
                ended = float(s["t"])
        if ended is not None:
            return ended
    return None


def check_memory(samples: list[dict[str, Any]], agents: int | None = None,
                 ended_at: float | None = None) -> list[str]:
    """Broken memory budgets in ``samples`` (``memory_sample`` dicts, oldest first): the last
    sample against each budget and Ducky's own total, the peak tool-server MB against
    ``agents`` seats, terminals left ``TERMINAL_CLOSE_S`` after ``ended_at`` (the last turn's
    end, see ``agents_ended_at``), and growth between minute 10 and 60 of the soak."""
    if not samples:
        return ["memory: no samples"]
    samples = sorted(samples, key=lambda s: float(s.get("t") or 0))
    broken: list[str] = []
    last = samples[-1]
    for role, budget in MEMORY_BUDGET_MB.items():
        mb = float(last.get(role) or 0)
        if mb > budget:
            broken.append(f"{_LABEL[role]}: {mb:.0f} MB > {budget:.0f} MB budget")
    # ponytail: samples written before the split have no "ducky": their whole tree counts.
    ducky = float(last.get("ducky", last.get("tree")) or 0)
    if ducky > DUCKY_BUDGET_MB:
        broken.append(f"ducky: {ducky:.0f} MB > {DUCKY_BUDGET_MB:.0f} MB budget")
    if agents:
        peak = max(float(s.get("tool_servers") or 0) for s in samples)
        limit = TOOL_SERVER_MB_PER_AGENT * agents
        if peak > limit:
            broken.append(f"tool servers: {peak:.0f} MB peak > {limit:.0f} MB"
                          f" ({TOOL_SERVER_MB_PER_AGENT:.0f} MB × {agents} agents)")
    if ended_at is not None:
        after = [s for s in samples if float(s["t"]) >= ended_at + TERMINAL_CLOSE_S and "terminals" in s]
        if after and after[0]["terminals"]:
            broken.append(f"terminals: {after[0]['terminals']} still open {TERMINAL_CLOSE_S:.0f} s"
                          f" after the agents finished")
    t0 = float(samples[0].get("t") or 0)
    window = [s for s in samples if GROWTH_FROM_MIN <= (float(s.get("t") or 0) - t0) / 60 <= GROWTH_TO_MIN]
    if len(window) >= 2 and (float(window[-1]["t"]) - float(window[0]["t"])) >= 5 * 60:
        for key in (*ROLES, "ducky" if "ducky" in last else "tree"):
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
    ap.add_argument("--results", help="the soak harness results.json (agents, when the last turn ended)")
    ap.add_argument("--agents", type=int, help="seats in the soak (default: results.json summary.agents)")
    args = ap.parse_args(argv)
    if args.memory:
        if args.sample_pid:
            collect(args.sample_pid, args.minutes, args.every, args.memory)
        samples = load_samples(args.memory)
        results: dict[str, Any] = {}
        if args.results and Path(args.results).is_file():
            results = json.loads(Path(args.results).read_text(encoding="utf-8")) or {}
        agents = args.agents or (results.get("summary") or {}).get("agents")
        ended = agents_ended_at(samples, results)
        broken = check_memory(samples, agents=agents, ended_at=ended)
        last = samples[-1] if samples else {}
        keys = (*ROLES, "ducky", "workload", "tool_servers", "tree")
        what = ", ".join(f"{_LABEL[k]} {float(last.get(k) or 0):.0f} MB" for k in keys) if last else ""
        if last:  # agent workload is reported, never failed on
            print(f"memory: {what}; terminals {last.get('terminals', '?')}, tool servers"
                  f" {last.get('tool_server_count', '?')}")
        if ended is None:
            print("terminals: close check skipped (the agents never finished in these samples)")
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
