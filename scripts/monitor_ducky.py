"""Read-only process sampling; no application imports, chat data or process control.

Use the native launcher's PID to include both Python and WebView2 descendants.
The output is a new JSONL file. Stops when that process exits or duration expires.
CPU percentages use the entire machine (100% = all logical CPUs).
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import time

import psutil


def group(name: str) -> str:
    name = name.lower()
    if name == "ducky-launcher.exe":
        return "launcher"
    if name in {"uefn-ducky.exe", "uefn-ducky-dev.exe"}:
        return "ducky"
    if name == "msedgewebview2.exe":
        return "webview"
    if name.endswith("-bridge.exe"):
        return "bridges"
    if name in {"claude.exe", "codex.exe"}:
        return "agent_cli"
    return "other_children"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration", type=float, default=7200)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.duration, args.interval)):
        parser.error("duration and interval must be finite and positive")
    root = psutil.Process(args.pid)
    if group(root.name()) not in {"launcher", "ducky"}:
        parser.error("pid must identify Ducky or its native launcher")
    identity = root.create_time()
    cpus = psutil.cpu_count() or 1
    previous: dict[int, tuple[float, float]] = {}
    previous_time = None
    started = time.monotonic()
    # Exclusive creation preserves earlier recordings.
    with args.output.open("x", encoding="utf-8", buffering=1) as output:
        reason = "duration_elapsed"
        while True:
            now = time.monotonic()
            if not root.is_running() or root.create_time() != identity:
                reason = "root_exited"
                break
            try:
                processes = [root, *root.children(recursive=True)]
            except psutil.NoSuchProcess:
                reason = "root_exited"
                break
            rows = []
            current = {}
            for proc in processes:
                try:
                    with proc.oneshot():
                        birth = proc.create_time()
                        cpu = proc.cpu_times()
                        seconds = cpu.user + cpu.system
                        mem = proc.memory_info()
                        old = previous.get(proc.pid)
                        percent = None
                        if old and old[0] == birth and previous_time is not None:
                            percent = max(0, (seconds - old[1]) * 100 / (now - previous_time) / cpus)
                        name = proc.name()
                        rows.append({
                            "pid": proc.pid, "parent": proc.ppid(), "name": name,
                            "group": group(name), "started": birth,
                            "rss_bytes": mem.rss, "private_bytes": getattr(mem, "private", None),
                            "cpu_percent_machine": None if percent is None else round(percent, 4),
                            "threads": proc.num_threads(), "handles": proc.num_handles(),
                        })
                        current[proc.pid] = (birth, seconds)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            vm = psutil.virtual_memory()
            output.write(json.dumps({
                "event": "sample", "at": datetime.now().astimezone().isoformat(),
                "root_pid": root.pid, "root_started": identity, "logical_cpus": cpus,
                "cpu_interval_seconds": None if previous_time is None else round(now - previous_time, 3),
                "free_physical_bytes": vm.available, "total_physical_bytes": vm.total,
                "processes": rows,
            }) + "\n")
            previous, previous_time = current, now
            remaining = args.duration - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(args.interval, remaining))
        output.write(json.dumps({"event": "end", "at": datetime.now().astimezone().isoformat(),
                                 "reason": reason}) + "\n")


if __name__ == "__main__":
    main()
