"""Idle soak: watch a running Ducky for leaks and needless work over time.

Samples the whole Ducky process tree (UEFN-Ducky.exe, its bridge, its WebView2 and
every child it started) plus the PC's memory every --interval seconds for --minutes,
snapshots Ducky's AppData files every --scan-every seconds, then fails when something
trends the wrong way while the app sits idle:

  * CPU above the idle budget (Oct 10 2026: a per-request PanelApi re-shipped every
    skill pack every 5 s and kept ~13% of a 16-core PC busy)
  * private memory, handles or threads growing per hour (judged on runs of 15+ minutes;
    shorter runs say "run longer", because these counts swing up and down)
  * child processes piling up
  * a Ducky file in AppData rewritten again and again (the same bug rewrote every
    skill pack's LICENSE.txt each time); WebView2's own files are listed, not judged
  * the same Python thread name starting again and again (the re-ship loop started a
    new "ship-newest" thread every 5 s); needs py-spy
  * PC memory owned by no program growing (reported, not failed: it is system-wide)

Process I/O counters are shown but not judged: on Windows they count every kind of
I/O, including the messages WebView2's processes send each other, not just disk.

Leave Ducky idle (no chat running) and do not use it during the run. The first
--warmup minutes are sampled but not judged (start-up work is allowed).

Usage:
  .venv/Scripts/python.exe tests/e2e/soak/idle_soak.py --minutes 30
  .venv/Scripts/python.exe tests/e2e/soak/idle_soak.py --minutes 20 --interval 10 --json soak.json
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover
    print("pip install psutil", file=sys.stderr)
    sys.exit(2)

APP_NAMES = ("UEFN-Ducky.exe", "UEFN-Ducky-Dev.exe")
# Agent workspaces and scratch hold ~100k files and are not touched by an idle app.
SKIP_DIRS = frozenset({"coding_agents", "scratch", "build_kit"})
# Chromium writes its own profile files; they are listed, not judged.
WEBVIEW_DIRS = ("webview", "webview2_browser")
MIN_TREND_MINUTES = 15.0


@dataclass
class Sample:
    t: float
    cpu_pct_of_pc: float
    private_mb: float
    handles: int
    threads: int
    children: int
    io_mb: float
    hidden_gb: float
    python_threads: list[str] = field(default_factory=list)


@dataclass
class Scan:
    t: float
    files: dict[str, tuple[int, int]]


@dataclass
class Limits:
    cpu_pct_of_pc: float = 2.0
    private_mb_per_hour: float = 60.0
    handles_per_hour: float = 150.0
    threads_per_hour: float = 15.0
    children_growth: int = 0
    rewrite_share: float = 0.5
    thread_restarts_per_10min: int = 3
    hidden_gb_per_hour_warn: float = 0.5


def slope_per_hour(times: list[float], values: list[float]) -> float:
    """Least-squares slope of values over time, in units per hour."""
    n = len(times)
    if n < 2:
        return 0.0
    mt, mv = sum(times) / n, sum(values) / n
    den = sum((t - mt) ** 2 for t in times)
    if den == 0:
        return 0.0
    return sum((t - mt) * (v - mv) for t, v in zip(times, values)) / den * 3600.0


def growth_per_hour(times: list[float], values: list[float], min_minutes: float = MIN_TREND_MINUTES) -> float | None:
    """Average of the last third minus average of the first third, per hour. Counts like
    handles swing by dozens between samples (averaging a third evens that out; a median
    picks one side of the swing), and a short run's trend means nothing: None when the
    run is shorter than ``min_minutes``."""
    if len(times) < 6 or (times[-1] - times[0]) / 60.0 < min_minutes:
        return None
    k = len(times) // 3
    dt = statistics.fmean(times[-k:]) - statistics.fmean(times[:k])
    return (statistics.fmean(values[-k:]) - statistics.fmean(values[:k])) / dt * 3600.0 if dt > 0 else None


def thread_restarts(samples: list[Sample]) -> Counter:
    """How many times a thread name appeared with a new id (needs py-spy samples)."""
    ids: dict[str, set[str]] = {}
    for s in samples:
        for entry in s.python_threads:
            tid, _, name = entry.partition(":")
            ids.setdefault(name, set()).add(tid)
    return Counter({name: len(found) - 1 for name, found in ids.items() if len(found) > 1})


def rewritten_files(scans: list[Scan]) -> tuple[dict[str, int], int]:
    """Per file: in how many scan intervals it changed (new, modified or deleted)."""
    changed: Counter = Counter()
    for before, after in zip(scans, scans[1:]):
        for path in set(before.files) | set(after.files):
            if before.files.get(path) != after.files.get(path):
                changed[path] += 1
    return dict(changed), max(len(scans) - 1, 0)


def is_webview_file(path: str) -> bool:
    return path.replace("\\", "/").split("/", 1)[0] in WEBVIEW_DIRS


def analyse(samples: list[Sample], limits: Limits, warmup_s: float, scans: list[Scan] | None = None) -> dict:
    """Judge what was recorded after the warm-up. A check whose value is None needs a longer run."""
    if not samples:
        return {"ok": False, "checks": [], "error": "no samples"}
    start = samples[0].t
    judged = [s for s in samples if s.t - start >= warmup_s] or samples
    times = [s.t for s in judged]
    span_min = max((times[-1] - times[0]) / 60.0, 1e-9)
    checks: list[dict] = []

    def check(name: str, value: float | None, limit: float, unit: str, *, fail: bool = True) -> None:
        over = value is not None and value > limit
        checks.append({"check": name, "value": None if value is None else round(value, 3), "limit": limit,
                       "unit": unit, "ok": not (over and fail), "warn": over and not fail, "skipped": value is None})

    cpu = [s.cpu_pct_of_pc for s in judged[1:]] or [judged[0].cpu_pct_of_pc]
    check("idle CPU (average)", sum(cpu) / len(cpu), limits.cpu_pct_of_pc, "% of PC")
    check("private memory growth", growth_per_hour(times, [s.private_mb for s in judged]), limits.private_mb_per_hour, "MB/h")
    check("handle growth", growth_per_hour(times, [s.handles for s in judged]), limits.handles_per_hour, "/h")
    check("thread growth", growth_per_hour(times, [s.threads for s in judged]), limits.threads_per_hour, "/h")
    check("child process growth", judged[-1].children - judged[0].children, limits.children_growth, "processes")

    restarts = thread_restarts(judged)
    worst = restarts.most_common(1)
    check(f"repeated thread starts ({worst[0][0] if worst else 'none'})",
          (worst[0][1] / span_min * 10.0) if worst else 0.0, limits.thread_restarts_per_10min, "per 10 min")

    churn: dict[str, int] = {}
    webview_churn: dict[str, int] = {}
    intervals = 0
    judged_scans = [s for s in (scans or []) if s.t - start >= warmup_s]
    if len(judged_scans) >= 4:
        changed, intervals = rewritten_files(judged_scans)
        for path, n in changed.items():
            (webview_churn if is_webview_file(path) else churn)[path] = n
        worst_share = max(churn.values(), default=0) / intervals
        check("Ducky file rewritten over and over", worst_share, limits.rewrite_share, "of scans")
    else:
        check("Ducky file rewritten over and over", None, limits.rewrite_share, "of scans")

    check("PC memory owned by no program", growth_per_hour(times, [s.hidden_gb for s in judged]),
          limits.hidden_gb_per_hour_warn, "GB/h", fail=False)
    top = lambda d: dict(sorted(d.items(), key=lambda kv: -kv[1])[:15])  # noqa: E731
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "judged_samples": len(judged),
            "minutes": round(span_min, 1), "thread_restarts": dict(restarts.most_common(10)),
            "scan_intervals": intervals, "ducky_files_changed": top(churn), "webview_files_changed": top(webview_churn)}


# ----------------------------------------------------------------------------- sampling


class _PerfInfo(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "cb", "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable",
        "SystemCache", "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize",
        "HandleCount", "ProcessCount", "ThreadCount")]


def hidden_gb() -> float:
    """Committed memory not charged to any program's private bytes or the kernel pools."""
    if os.name != "nt":
        return 0.0
    info = _PerfInfo()
    info.cb = ctypes.sizeof(info)
    if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
        return 0.0
    page = info.PageSize
    private = 0
    for proc in psutil.process_iter():
        try:
            private += proc.memory_info().private
        except (psutil.Error, AttributeError):
            continue
    return (info.CommitTotal * page - private - (info.KernelPaged + info.KernelNonpaged) * page) / 1024**3


def scan_appdata(root: Path) -> Scan:
    """(mtime, size) of every file in Ducky's AppData outside the agent/scratch folders."""
    files: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if folder != root or entry.name not in SKIP_DIRS:
                        stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat()
                    files[os.path.relpath(entry.path, root)] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return Scan(t=time.time(), files=files)


def find_app(pid: int | None) -> psutil.Process:
    if pid:
        return psutil.Process(pid)
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] in APP_NAMES:
            return proc
    raise SystemExit("Ducky is not running (start it, or pass --pid)")


def tree(root: psutil.Process) -> list[psutil.Process]:
    procs = [root]
    try:
        procs += root.children(recursive=True)
    except psutil.Error:
        pass
    return procs


def py_threads(py_spy: str | None, pid: int) -> list[str]:
    """'<thread id>:<name>' for each Python thread, from one non-blocking py-spy dump."""
    if not py_spy:
        return []
    try:
        out = subprocess.run([py_spy, "dump", "--pid", str(pid), "--nonblocking"], capture_output=True,
                             text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    found = []
    for line in out.splitlines():
        if line.startswith("Thread "):
            name = line.split('"')[1] if '"' in line else "?"
            found.append(f"{line.split()[1]}:{name}")
    return found


def sample(root: psutil.Process, cpu_prev: dict[int, float], wall_prev: float, py_spy: str | None) -> tuple[Sample, float]:
    now = time.time()
    procs = tree(root)
    cpu_now: dict[int, float] = {}
    private = handles = threads = io = 0
    for proc in procs:
        try:
            with proc.oneshot():
                times = proc.cpu_times()
                cpu_now[proc.pid] = times.user + times.system
                private += proc.memory_info().private
                handles += proc.num_handles()
                threads += proc.num_threads()
                io += proc.io_counters().write_bytes
        except (psutil.Error, AttributeError):
            continue
    busy = sum(max(cpu_now[p] - cpu_prev.get(p, cpu_now[p]), 0.0) for p in cpu_now)
    cpu_prev.clear()
    cpu_prev.update(cpu_now)
    pc_pct = busy / max(now - wall_prev, 1e-6) / (psutil.cpu_count() or 1) * 100.0
    return Sample(t=now, cpu_pct_of_pc=pc_pct, private_mb=private / 1024**2, handles=handles, threads=threads,
                  children=len(procs) - 1, io_mb=io / 1024**2, hidden_gb=hidden_gb(),
                  python_threads=py_threads(py_spy, root.pid)), now


def _mark(c: dict) -> str:
    if c["skipped"]:
        return "SKIP"
    if c["warn"]:
        return "WARN"
    return "PASS" if c["ok"] else "FAIL"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--scan-every", type=float, default=120.0, help="seconds between AppData snapshots")
    ap.add_argument("--warmup", type=float, default=3.0, help="minutes sampled but not judged")
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--appdata", default=str(Path(os.environ.get("LOCALAPPDATA", "")) / "UEFN-Ducky"))
    ap.add_argument("--py-spy", default="", help="py-spy path (default: the repo venv's, if present)")
    ap.add_argument("--json", default="", help="write samples and the verdict here")
    args = ap.parse_args()

    root = find_app(args.pid or None)
    venv_spy = Path(__file__).resolve().parents[3] / ".venv" / "Scripts" / "py-spy.exe"
    py_spy = args.py_spy or (str(venv_spy) if venv_spy.is_file() else shutil.which("py-spy") or "")
    appdata = Path(args.appdata)
    print(f"Watching {root.name()} pid {root.pid} for {args.minutes:g} min (every {args.interval:g} s, "
          f"AppData every {args.scan_every:g} s, first {args.warmup:g} min not judged)"
          + ("" if py_spy else "; py-spy not found, thread restarts not checked"))

    cpu_prev: dict[int, float] = {}
    _, wall = sample(root, cpu_prev, time.time(), None)
    samples: list[Sample] = []
    scans: list[Scan] = [scan_appdata(appdata)] if appdata.is_dir() else []
    end = time.time() + args.minutes * 60
    while time.time() < end:
        time.sleep(args.interval)
        if not root.is_running():
            print("Ducky exited during the soak")
            return 1
        s, wall = sample(root, cpu_prev, wall, py_spy)
        samples.append(s)
        if scans and s.t - scans[-1].t >= args.scan_every:
            scans.append(scan_appdata(appdata))
        print(f"{time.strftime('%H:%M:%S')}  cpu {s.cpu_pct_of_pc:5.2f}%  mem {s.private_mb:7.0f} MB  "
              f"handles {s.handles:5d}  threads {s.threads:4d}  children {s.children:2d}  "
              f"I/O {s.io_mb:8.1f} MB  no-program {s.hidden_gb:5.2f} GB", flush=True)

    verdict = analyse(samples, Limits(), warmup_s=args.warmup * 60, scans=scans)
    print()
    for c in verdict["checks"]:
        value = "run longer" if c["skipped"] else f"{c['value']} {c['unit']}"
        print(f"{_mark(c):4}  {c['check']}: {value} (limit {c['limit']})")
    for key, label in (("thread_restarts", "thread restarts"), ("ducky_files_changed", "Ducky files changed (scans)"),
                       ("webview_files_changed", "WebView2 files changed (scans, not judged)")):
        if verdict.get(key):
            print(f"{label}:", json.dumps(verdict[key], indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps({"verdict": verdict, "samples": [asdict(s) for s in samples]}, indent=1),
                                   encoding="utf-8")
    print("\nRESULT:", "PASS" if verdict["ok"] else "FAIL")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
