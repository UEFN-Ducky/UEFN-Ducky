"""Ducky looking at itself: what uses this PC's CPU and memory, which of it is Ducky, why
the app may feel slow, and the safe fixes (``ducky_health`` / ``ducky_self_care``).

Runs in the Ducky window's process (agents' tool servers ask over the panel API), so the
numbers are the app's own. One snapshot reads the whole process table twice, one second
apart (CPU), and asks only Ducky's own few processes for their command lines: a busy PC
answers in about a second. The one thing kept is the core-wait meter: a thread that
sleeps 10 ms at a time and records how late Python lets it wake up (the GIL queue).
"""

from __future__ import annotations

import collections
import gc
import json
import math
import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

_MB = 1024 * 1024
_GB = 1024 * _MB
_DUCKY_NAMES = ("uefn-ducky",)
_UEFN_NAMES = ("unrealeditorfortnite", "unrealeditor")


def _mb(value: float) -> float:
    return round(float(value) / _MB, 1)


def _in_stdlib(filename: str) -> bool:
    import sysconfig

    stdlib = os.path.normcase(sysconfig.get_paths()["stdlib"])
    return os.path.normcase(filename).startswith(stdlib + os.sep)


def _app_dir() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir()


def _folder_mb(path: Path, cap_files: int = 60_000) -> float:
    from backend.sandbox.paths import dir_size

    return _mb(dir_size(path, cap_files=cap_files)) if path.exists() else 0.0


def _role(cmdline: list[str]) -> str:
    args = " ".join(cmdline[1:]).lower()
    if "--shared-daemon" in args:
        return "shared tool server"
    if "--adapter" in args:
        return "IDE adapter"
    if " agent-host" in f" {args}":
        return "agent host"
    if " bridge" in f" {args}" or args.startswith("bridge"):
        return "tool server (bridge)"
    return "app"


def _cmdline(pid: int) -> tuple[list[str], str]:
    """A process's command line and exe; only ever asked for Ducky's own few processes."""
    import psutil

    try:
        p = psutil.Process(pid)
        return list(p.cmdline()), str(p.exe() or "")
    except (psutil.Error, OSError):
        return [], ""


def _terminate(pid: int, name: str) -> bool:
    import psutil

    try:
        p = psutil.Process(pid)
        if p.name().lower() != name.lower():  # the pid went to another program meanwhile
            return False
        p.terminate()
        return True
    except psutil.Error:
        return False


def _ducky_named(proc: Any) -> bool:
    return proc.name.lower().startswith(_DUCKY_NAMES)


class _CoreWait:
    """How long Python work waits for the core: lateness of a 10 ms sleep beyond the idle
    baseline (median of the first 200 wakes), last ~60 s."""

    samples: collections.deque[float] = collections.deque(maxlen=6000)
    baseline: float | None = None
    thread: threading.Thread | None = None
    start_lock = threading.Lock()


def _core_wait_loop() -> None:
    # No logging, no locks: one deque append per wake, so the meter never adds to the wait.
    perf, sleep, push = time.perf_counter, time.sleep, _CoreWait.samples.append
    first: list[float] = []
    while True:
        t = perf()
        sleep(0.010)
        late = (perf() - t - 0.010) * 1000
        if _CoreWait.baseline is None:
            first.append(late)
            if len(first) >= 200:
                _CoreWait.baseline = statistics.median(first)
            continue
        push(max(0.0, late - _CoreWait.baseline))


def start_core_wait() -> None:
    """Start the core-wait meter (one daemon thread per process; later calls do nothing)."""
    with _CoreWait.start_lock:
        if _CoreWait.thread is None or not _CoreWait.thread.is_alive():
            _CoreWait.thread = threading.Thread(target=_core_wait_loop, name="core-wait", daemon=True)
            _CoreWait.thread.start()


def core_wait_stats(samples: list[float] | None = None) -> dict[str, Any]:
    """p50 / p95 / max lateness in ms over the meter's window (or the samples given)."""
    vals = sorted(list(_CoreWait.samples) if samples is None else samples)
    if not vals:
        return {"p50": None, "p95": None, "max": None, "samples": 0}

    def pct(q: float) -> float:
        return round(vals[max(0, math.ceil(q * len(vals)) - 1)], 1)

    return {"p50": pct(0.50), "p95": pct(0.95), "max": round(vals[-1], 1), "samples": len(vals)}


_HOT_THREAD_SHARE = 0.2  # of one core: named from here
_SPINNING_SHARE = 0.5  # a finding of its own from here


def _thread_times() -> dict[int, float]:
    import psutil

    try:
        return {t.id: t.user_time + t.system_time for t in psutil.Process().threads()}
    except (psutil.Error, OSError):
        return {}


def hot_threads(before: dict[int, float], after: dict[int, float], seconds: float) -> list[dict[str, Any]]:
    """The (at most 5) busiest threads of this process above a fifth of a core between two
    :func:`_thread_times` reads: ``{name, share, where, stack}``, busiest first."""
    import sys
    import threading

    by_native = {t.native_id: t for t in threading.enumerate() if t.native_id is not None}
    frames = sys._current_frames()
    out: list[dict[str, Any]] = []
    for tid, total in after.items():
        share = (total - before.get(tid, total)) / max(seconds, 1e-3)
        if share < _HOT_THREAD_SHARE:
            continue
        thread = by_native.get(tid)
        stack: list[str] = []
        frame = frames.get(thread.ident) if thread is not None and thread.ident is not None else None
        # Start at the caller, not the stdlib helper it is inside (Event.is_set, queue.get, ...).
        while frame is not None and frame.f_back is not None and _in_stdlib(frame.f_code.co_filename):
            frame = frame.f_back
        where = f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}" if frame is not None else "native code"
        while frame is not None and len(stack) < 8:
            stack.append(f"{frame.f_code.co_name} ({Path(frame.f_code.co_filename).name}:{frame.f_lineno})")
            frame = frame.f_back
        out.append({"name": thread.name if thread is not None else f"native thread {tid}",
                    "share": round(share, 2), "where": where, "stack": stack})
    return sorted(out, key=lambda r: -r["share"])[:5]


def snapshot(detail: bool = False) -> dict[str, Any]:
    import psutil

    from backend.util import proc_table

    start_core_wait()
    started, threads_before = time.monotonic(), _thread_times()
    procs, cpu = proc_table.sample(1.0)
    hot = hot_threads(threads_before, _thread_times(), time.monotonic() - started)
    ncpu = psutil.cpu_count() or 1
    me_pid = os.getpid()
    me = procs.get(me_pid) or proc_table.Proc(me_pid, 0, "", 0, 0, 0, 0, 0.0, time.time())

    def info(proc: Any) -> dict[str, Any]:
        return {"pid": proc.pid, "name": proc.name, "memory_mb": _mb(proc.rss), "private_mb": _mb(proc.private),
                "cpu_percent": cpu.get(proc.pid, 0.0)}

    app = {
        "pid": me_pid,
        "memory_mb": _mb(me.rss),
        "private_mb": _mb(me.private),
        "threads": me.threads,
        "handles": me.handles or None,
        "cpu_percent": cpu.get(me_pid, 0.0),
        "uptime_min": round((time.time() - me.started) / 60, 1) if me.started else None,
        "hot_threads": hot,
        "core_wait_ms": core_wait_stats(),
        **_inflight(),
    }
    by_name: dict[str, dict[str, Any]] = {}
    for pid in proc_table.tree(procs, me_pid)[1:]:
        row = info(procs[pid])
        slot = by_name.setdefault(row["name"], {"name": row["name"], "count": 0, "memory_mb": 0.0, "cpu_percent": 0.0})
        slot["count"] += 1
        slot["memory_mb"] = round(slot["memory_mb"] + row["memory_mb"], 1)
        slot["cpu_percent"] = round(slot["cpu_percent"] + row["cpu_percent"], 1)
    helpers = sorted(by_name.values(), key=lambda r: -r["memory_mb"])

    ducky_rows: list[dict[str, Any]] = []
    for proc in sorted((p for p in procs.values() if _ducky_named(p) and p.pid != me_pid), key=lambda p: -p.rss)[:20]:
        cmd, exe = _cmdline(proc.pid)
        role = _role(cmd)
        row = info(proc)
        row.update({"role": role, "exe": exe,
                    "orphan": role not in ("app", "shared tool server", "agent host") and proc_table.orphan(procs, proc.pid)})
        ducky_rows.append(row)

    merged: dict[str, float] = {}
    for proc in procs.values():
        if proc.pid:
            merged[proc.name or "?"] = merged.get(proc.name or "?", 0.0) + proc.rss
    top_apps = [{"name": k, "memory_mb": _mb(v)} for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:8]]
    busy = sum(v for pid, v in cpu.items() if pid)  # pid 0 is the idle process
    vm = psutil.virtual_memory()
    uefn = sorted((p for p in procs.values() if p.name.lower().startswith(_UEFN_NAMES)), key=lambda p: -p.private)

    out: dict[str, Any] = {
        "ok": True,
        "app": app,
        "helpers": helpers[:12],
        "other_ducky_processes": ducky_rows,
        "uefn": [info(p) for p in uefn[:3]],
        "pc": {"memory_used_percent": vm.percent, "memory_total_gb": round(vm.total / _GB, 1),
               "memory_free_gb": round(vm.available / _GB, 1), "cpu_percent": round(min(100.0, busy / ncpu), 1),
               "cpus": ncpu, "processes": len(procs), "top_apps": top_apps},
        "sandboxes": _sandboxes(procs, cpu),
        "terminals": _terminals(),
        "storage": _storage(),
        "running_agents": _running_agents(),
        "agent_hosts": agent_hosts(procs, cpu),
        "agents_in_window": agents_in_window(),
        "ui_last_hour": _ui_last_hour(),
    }
    if detail:
        out["perf"] = _perf()
    out["findings"] = findings(out)
    return out


def agent_hosts(procs: dict[int, Any], cpu: dict[int, float], sup: Any = None) -> list[dict[str, Any]]:
    """One row per agent host the app's supervisor runs (slot, pid, agents, connected, alive),
    with the host process's CPU and memory; none started yet gives an empty list."""
    if sup is None:
        try:
            from frontend.agent_host import supervisor

            sup = supervisor.get()
        except Exception:  # noqa: BLE001
            sup = None
    if sup is None:
        return []
    rows: list[dict[str, Any]] = []
    for row in sup.snapshot():
        proc = procs.get(row.get("pid"))
        rows.append({**row, "cpu_percent": cpu.get(row.get("pid"), 0.0),
                     "memory_mb": _mb(proc.rss) if proc is not None else None})
    return rows


def agents_in_window(threads: list[threading.Thread] | None = None, hosts_on: bool | None = None) -> list[str]:
    """Chats (first 8 characters) whose CLI output is read in this process although agent
    hosts are on: their turns fell back to the window."""
    if hosts_on is None:
        try:
            from frontend.agent_host import turns

            hosts_on = turns.enabled()
        except Exception:  # noqa: BLE001
            hosts_on = False
    if not hosts_on:
        return []
    names = [t.name for t in (threading.enumerate() if threads is None else threads)]
    return sorted({n[len("ca-stdout-"):] for n in names if n.startswith("ca-stdout-")})


def _inflight() -> dict[str, Any]:
    try:
        from frontend.ui_web import inflight

        return {"inflight": inflight.top(5), "inflight_total": inflight.total()}
    except Exception as exc:  # noqa: BLE001
        return {"inflight": [], "inflight_total": 0, "inflight_error": str(exc)}


def _sandboxes(procs: Any = None, cpu: dict[int, float] | None = None) -> dict[str, Any]:
    try:
        from backend.sandbox import paths, service

        progs = service.all_programs(procs=procs, cpu=cpu)
        return {"count": len(paths.list_sandboxes()), "programs": progs[:20],
                "programs_memory_mb": round(sum(float(p.get("memory_mb") or 0) for p in progs), 1)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _terminals() -> dict[str, Any]:
    try:
        from frontend.ui_web.terminal import get_terminal_manager

        rows = get_terminal_manager().list_sessions()
        return {"open": len(rows), "agent_owned": sum(1 for r in rows if r.get("conv_id") or r.get("chat"))}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _running_agents() -> dict[str, Any]:
    try:
        from frontend.ui_web.agent_state import running_agents

        rows = running_agents()
        return {"count": len(rows), "agents": rows}
    except Exception as exc:  # noqa: BLE001
        return {"count": 0, "agents": [], "error": str(exc)}


_STORAGE_TTL_S = 600.0
_storage_seen: dict[str, Any] = {"at": 0.0, "folders": {}}


def _storage() -> dict[str, Any]:
    """Folder sizes (walked at most every ten minutes: tens of thousands of chat files take
    seconds on a busy disk) and the database files (one stat each, always fresh)."""
    base = _app_dir()
    now = time.monotonic()
    if not _storage_seen["folders"] or now - _storage_seen["at"] > _STORAGE_TTL_S:
        _storage_seen["folders"] = {f"{name}_mb": _folder_mb(base / name)
                                    for name in ("chats", "sandboxes", "cache", "perf", "logs", "tools")}
        _storage_seen["at"] = now
    out: dict[str, Any] = dict(_storage_seen["folders"])
    for name in ("ducky.db", "ducky.db-wal"):
        try:
            out[name.replace(".", "_").replace("-", "_") + "_mb"] = _mb((base / name).stat().st_size)
        except OSError:
            pass
    return out


def _perf() -> Any:
    try:
        from frontend.perf_trace import read_latest_report

        report = read_latest_report() or {}
        text = json.dumps(report, default=str)
        if len(text) < 6000:
            return report
        return {"note": "perf report trimmed (ducky_perf_report has all of it)", "excerpt": text[:6000]}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


UI_STALLS_PER_HOUR = 120  # two frozen frames a minute, all hour
UI_CALLS_PER_HOUR = 600  # one bridge method every 6 s, all hour: a loop, not a person clicking


def _ui_last_hour() -> dict[str, Any]:
    """The app window's frozen frames and busiest bridge calls over the last hour (the perf log),
    so a flood that started before this check still shows: the live ring only holds seconds."""
    try:
        from frontend.perf_trace import per_process_report

        rep = per_process_report(hours=1.0)
        return next((p for p in rep.get("processes") or [] if p.get("role") == "app window"), {})
    except Exception:  # noqa: BLE001
        return {}


def findings(snap: dict[str, Any]) -> list[dict[str, str]]:
    """Plain-words causes, most important first, each with the fix (a self-care action or a step)."""
    out: list[dict[str, str]] = []
    app = snap.get("app") or {}
    pc = snap.get("pc") or {}
    ncpu = int(pc.get("cpus") or 1)
    app_cpu_pc = float(app.get("cpu_percent") or 0) / ncpu
    wait_p95 = float((app.get("core_wait_ms") or {}).get("p95") or 0)
    if wait_p95 > 20:
        out.append({"severity": "high" if wait_p95 > 50 else "medium",
                    "what": f"Ducky's Python core is overloaded: work waits {wait_p95:.0f} ms",
                    "why": "Too many threads want Python at once (the GIL)",
                    "fix": "ducky_perf_report names the busiest calls; fewer running agents"})
    busy_threads = app.get("hot_threads") or []
    for hot in (h for h in busy_threads if float(h.get("share") or 0) > _SPINNING_SHARE):
        where = (hot.get("stack") or [hot.get("where") or "native code"])[0]
        out.append({"severity": "high", "what": f"Ducky's thread {hot['name']} used {round(float(hot['share']) * 100)}% of a core in {where}.",
                    "why": "One thread busy all the time: work repeated per event, or a loop that never waits.",
                    "fix": "Stack, innermost first: " + " < ".join(hot.get("stack") or ["(no Python frames)"])})
    warm = [h for h in busy_threads if float(h.get("share") or 0) <= _SPINNING_SHARE]
    if warm:
        names = ", ".join(f"{h['name']} {round(float(h['share']) * 100)}% in {h.get('where')}" for h in warm)
        out.append({"severity": "medium", "what": f"Busy Ducky threads: {names}.",
                    "why": "Each takes a fifth of a core or more; together they make other work wait for Python.",
                    "fix": "ducky_perf_report names the busiest calls; fewer running agents"})
    in_window = snap.get("agents_in_window") or []
    if in_window:
        out.append({"severity": "medium",
                    "what": "An agent is running inside the window process, not an agent host: chat "
                            + ", ".join(in_window) + ".",
                    "why": "Its turn could not reach an agent host and fell back to the window, where it competes "
                           "with the UI for Python.",
                    "fix": "ducky_health's agent_hosts shows whether the hosts started; the next turn tries a host again."})
    piled = [r for r in app.get("inflight") or [] if int(r.get("count") or 0) > 20 or float(r.get("oldest_s") or 0) > 10]
    if piled:
        names = ", ".join(f"{r['method']} ×{r['count']}" for r in piled)
        out.append({"severity": "medium", "what": f"Window calls are piling up: {names}",
                    "why": "The window asks faster than Python answers (or one call hangs), so the UI shows Loading.",
                    "fix": "ducky_perf_report names the slow calls; fewer running agents"})
    ui = snap.get("ui_last_hour") or {}
    stalls = int(ui.get("ui_stalls") or 0)
    if stalls > UI_STALLS_PER_HOUR or (stalls >= 20 and float(ui.get("ui_stall_p95_ms") or 0) >= 1000):
        out.append({"severity": "high" if stalls > 5 * UI_STALLS_PER_HOUR else "medium",
                    "what": f"Ducky's window froze {stalls} times in the last hour (p95 {ui.get('ui_stall_p95_ms', 0):.0f} ms).",
                    "why": "The UI thread is kept busy: a loop or a flood of bridge replies.",
                    "fix": "ducky_perf_report: per_process names the busiest calls and slowest scripts."})
    for method, n in ui.get("top_ui_calls") or []:
        if int(n) > UI_CALLS_PER_HOUR:
            out.append({"severity": "high",
                        "what": f"The window called {method} {n} times in the last hour.",
                        "why": "Something re-asks it all the time (a per-frame loop, a retry that never backs off); "
                               "every answer costs the UI thread.",
                        "fix": f"Find the caller of {method} in the web UI and make it send only on change."})
    if app_cpu_pc > 15:
        out.append({"severity": "high", "what": f"Ducky's window process uses {app_cpu_pc:.0f}% of the PC's CPU.",
                    "why": "Something in the app is busy while it should idle (a stuck loop, a flood of UI updates).",
                    "fix": "ducky_perf_report names the slowest UI work; ducky_memory_report(start_trace=true) shows what grows."})
    if float(app.get("private_mb") or app.get("memory_mb") or 0) > 2048:
        out.append({"severity": "high", "what": f"Ducky's window process holds {app.get('private_mb') or app.get('memory_mb')} MB.",
                    "why": "Normal is a few hundred MB; this much means something keeps growing.",
                    "fix": "ducky_memory_report(start_trace=true), wait a minute, call it again: 'grew' names the cause."})
    if int(app.get("handles") or 0) > 15000:
        out.append({"severity": "medium", "what": f"{app['handles']} open handles in Ducky's window process.",
                    "why": "Handles that only climb are a leak (files, sockets, threads not closed).",
                    "fix": "ducky_memory_report and ducky_perf_report; restart Ducky if it keeps climbing."})
    helpers = snap.get("helpers") or []
    web = next((h for h in helpers if "webview2" in h["name"].lower()), None)
    if web and float(web["memory_mb"]) > 3000:
        out.append({"severity": "medium", "what": f"Ducky's browser engine (WebView2) uses {web['memory_mb']} MB in {web['count']} processes.",
                    "why": "Many open tabs, a heavy Browser-tab page, or a long chat with many pictures.",
                    "fix": "Close Browser tabs and chats you are not using."})
    orphans = [p for p in snap.get("other_ducky_processes") or [] if p.get("orphan")]
    if orphans:
        out.append({"severity": "medium", "what": f"{len(orphans)} leftover Ducky tool servers whose IDE or agent is gone "
                                                 f"({round(sum(float(p.get('memory_mb') or 0) for p in orphans))} MB).",
                    "why": "An IDE or agent closed without stopping its Ducky bridge.",
                    "fix": "ducky_self_care(action='stop_orphan_bridges')"})
    sb = snap.get("sandboxes") or {}
    heavy = [p for p in sb.get("programs") or [] if float(p.get("memory_mb") or 0) > 1024 or float(p.get("cpu_percent") or 0) / ncpu > 25]
    if heavy:
        names = ", ".join(f"{p['name']} ({p.get('memory_mb')} MB, {p.get('cpu_percent')}% CPU)" for p in heavy[:4])
        out.append({"severity": "medium", "what": f"Sandbox programs are heavy: {names}.",
                    "why": "A program an agent started in a sandbox is still running.",
                    "fix": "ducky_self_care(action='stop_sandbox', target='<chat id or all>')"})
    terms = snap.get("terminals") or {}
    if int(terms.get("agent_owned") or 0) > 6:
        out.append({"severity": "low", "what": f"{terms['agent_owned']} agent terminals are open.",
                    "why": "Each keeps a shell and its console alive.",
                    "fix": "ducky_self_care(action='close_idle_terminals')"})
    storage = snap.get("storage") or {}
    if float(storage.get("ducky_db_wal_mb") or 0) > 256:
        out.append({"severity": "low", "what": f"The database's write-ahead log is {storage['ducky_db_wal_mb']} MB.",
                    "why": "It is folded back into ducky.db when nothing holds it; a long-running reader stops that.",
                    "fix": "Restart Ducky when convenient."})
    if float(storage.get("cache_mb") or 0) > 1024 or float(storage.get("sandboxes_mb") or 0) > 10240:
        out.append({"severity": "low", "what": f"Caches {storage.get('cache_mb')} MB, sandboxes {storage.get('sandboxes_mb')} MB on disk.",
                    "why": "Downloads and sandboxes that are no longer needed.",
                    "fix": "ducky_self_care(action='trim_caches') and ducky_self_care(action='prune_sandboxes')"})
    for u in snap.get("uefn") or []:
        if float(u.get("private_mb") or u.get("memory_mb") or 0) > 30 * 1024:
            out.append({"severity": "high", "what": f"UEFN uses {round(float(u.get('private_mb') or u['memory_mb']) / 1024, 1)} GB.",
                        "why": "UEFN's editor memory only grows during a long session (garbage collections then stall it "
                               "for seconds); it is not Ducky.",
                        "fix": "Save, then reopen UEFN (ducky_restart_uefn) to get the memory back."})
    used = float(pc.get("memory_used_percent") or 0)
    if used > 88:
        ducky_mb = float(app.get("memory_mb") or 0) + sum(float(h["memory_mb"]) for h in helpers) + sum(
            float(p.get("memory_mb") or 0) for p in snap.get("other_ducky_processes") or [])
        biggest = (pc.get("top_apps") or [{}])[0]
        out.append({"severity": "high" if used > 95 else "medium",
                    "what": f"The PC's memory is {used:.0f}% used; Ducky's share is about {round(ducky_mb / 1024, 1)} GB.",
                    "why": f"The biggest user is {biggest.get('name', '?')} ({round(float(biggest.get('memory_mb') or 0) / 1024, 1)} GB)."
                           + (" It's mostly not Ducky." if ducky_mb < 0.15 * float(pc.get("memory_total_gb") or 1) * 1024 else ""),
                    "fix": "Close what you don't need among the top apps listed."})
    if not out:
        out.append({"severity": "ok", "what": "Nothing in Ducky looks wrong right now.", "why": "CPU, memory, handles, helpers, "
                    "sandboxes and terminals are all in their normal range.", "fix": ""})
    order = {"high": 0, "medium": 1, "low": 2, "ok": 3}
    return sorted(out, key=lambda f: order.get(f["severity"], 9))


ACTIONS = ("stop_sandbox", "close_idle_terminals", "stop_orphan_bridges", "trim_caches", "prune_sandboxes", "collect_garbage")


def self_care(action: str, target: str = "") -> dict[str, Any]:
    import psutil

    before = psutil.Process(os.getpid()).memory_info().rss
    act = (action or "").strip()
    done: dict[str, Any] = {"ok": True, "action": act}
    if act == "stop_sandbox":
        from backend.sandbox import service

        if target and target != "all":
            done.update(service.stop(target))
        else:
            done["stopped"] = service.stop_everything()
    elif act == "close_idle_terminals":
        from frontend.ui_web.terminal import get_terminal_manager

        mgr = get_terminal_manager()
        # "now" pushed past the quiet time: every agent terminal that is done closes now;
        # running ones, ones waiting on a card and the person's own stay.
        done["closed"] = mgr.close_idle_agent_terminals(now=time.time() + 24 * 3600)
    elif act == "stop_orphan_bridges":
        from backend.util import proc_table

        procs = proc_table.table()
        stopped = []
        for proc in procs.values():
            if proc.pid == os.getpid() or not _ducky_named(proc) or not proc_table.orphan(procs, proc.pid):
                continue
            role = _role(_cmdline(proc.pid)[0])
            if role in ("app", "shared tool server"):
                continue
            if _terminate(proc.pid, proc.name):
                stopped.append({"pid": proc.pid, "role": role})
        done["stopped"] = stopped
    elif act == "trim_caches":
        from backend.tools.core.web_video import cache_dir

        freed = 0
        for f in cache_dir().glob("*"):
            try:
                if f.is_file():
                    freed += f.stat().st_size
                    f.unlink()
            except OSError:
                pass
        done["freed_mb"] = _mb(freed)
    elif act == "prune_sandboxes":
        from backend.sandbox import service
        from backend.tools.panel.permission_prompt import _chat_exists

        done["removed"] = service.prune(chat_exists=_chat_exists)
    elif act == "collect_garbage":
        done["objects_freed"] = gc.collect()
    else:
        return {"ok": False, "error": f"action must be one of: {', '.join(ACTIONS)}."}
    after = psutil.Process(os.getpid()).memory_info().rss
    done["app_memory_mb"] = {"before": _mb(before), "after": _mb(after)}
    return done
