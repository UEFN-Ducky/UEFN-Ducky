"""Swarm soak: a sandboxed Ducky with N stub-Claude agents, measured the same way every time.

Starts the build given by --exe in its own profile (%TEMP%\\dsbx: Local, Roaming, home),
seeds it (first-run setup done, the anthropic plugin copied in, the stub Claude CLI from
stub_claude.py set as Claude Code's CLI path, a 200-file fixture repo, one group with a
lead and N-1 members on claude_code / claude-haiku-5-5, Allow everything on each), sends
every chat a task, and while they work:

  * every 10 s opens a random chat (``load_messages``) and times it: the "press a chat" check
  * answers each ask_user 60 s after it appears (``ui_rpc_respond``)
  * every 5 s samples the health snapshot (core-wait, threads, running agents), the CPU of
    the app, its WebView2 and its tool servers, and the PC's free memory

At the end it reads the sandbox ducky.db (read-only) for perf/error/stuck rows and writes
results.json + results.md to --out. The sandbox is stopped by PID. The person's own Ducky
(:4199/:4200, real AppData) is never touched.

Usage:
  .venv/Scripts/python.exe tests/e2e/soak/swarm_soak.py --exe dist/UEFN-Ducky-X/UEFN-Ducky.exe \\
      --agents 16 --minutes 10 --out %TEMP%\\soak-run1
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STUB = HERE / "stub_claude.py"
PANEL_PORT = 4400  # the panel API listens on PANEL_PORT - 1 = 4399
BRIDGE_PORT = 4401
LIVE_PORTS = (4199, 4200)
MODEL = "claude-haiku-5-5"
OPEN_TIMEOUT_S = 60.0
ANSWER_AFTER_S = 60.0
TASK = ("Swarm soak task for {name}: work through the fixture repo, read files, search, run the "
        "test command and write your notes. Keep going until the turn ends.")


# ----------------------------------------------------------------------------- verdict math


@dataclass
class Sample:
    t: float
    core_wait_p50: float | None = None
    core_wait_p95: float | None = None
    threads: int | None = None
    agents_running: int | None = None
    app_cpu: float = 0.0  # % of the whole PC
    webview_cpu: float = 0.0
    tool_cpu: float = 0.0
    free_gb: float | None = None
    tool_servers: int | None = None  # Ducky tool-server processes alive at this sample


@dataclass
class ChatOpen:
    t: float
    seconds: float
    timed_out: bool = False
    rows: int = 0


@dataclass
class MessageSend:
    id: str  # SOAKMSG-<n>, the stub logs it when it reaches the agent
    moment: str
    conv: str
    sent_at: float


@dataclass
class StopPress:
    moment: str
    conv: str
    pressed_at: float
    stopped_at: float | None = None  # when the chat left list_running_agents


@dataclass
class Run:
    started: float
    ended: float
    agents: int
    samples: list[Sample] = field(default_factory=list)
    opens: list[ChatOpen] = field(default_factory=list)
    stub_events: list[dict[str, Any]] = field(default_factory=list)
    conv_save_p95_ms: float | None = None
    questions_answered: int = 0
    messages_sent: int = 0
    sends: list[MessageSend] = field(default_factory=list)
    stops: list[StopPress] = field(default_factory=list)
    index_lock_left: bool | None = None


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (pct 0-100); None for no values."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    k = (len(vals) - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def _r(value: float | None, nd: int = 1) -> float | None:
    return None if value is None else round(value, nd)


def _avg_max(values: list[float]) -> dict[str, float | None]:
    return {"avg": _r(statistics.fmean(values), 2) if values else None, "max": _r(max(values), 2) if values else None}


def find_metric(obj: Any, name: str) -> dict[str, Any] | None:
    """First dict under ``obj`` whose key contains ``name`` and that has a p95 (perf reports)."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if name in str(key) and isinstance(value, dict) and "p95" in value:
                return value
        for value in obj.values():
            found = find_metric(value, name)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_metric(value, name)
            if found:
                return found
    return None


def summarize(run: Run) -> dict[str, Any]:
    """The numbers every phase check compares."""
    s = run.samples
    minutes = max((run.ended - run.started) / 60.0, 1e-9)
    done = [o.seconds for o in run.opens if not o.timed_out]
    tools = [e for e in run.stub_events if e.get("kind") == "tool"]
    messages = [e for e in run.stub_events if e.get("kind") == "message"]
    sessions = {e.get("session") for e in run.stub_events if e.get("kind") == "turn_start"}
    return {
        "minutes": round(minutes, 1),
        "agents": run.agents,
        "samples": len(s),
        "core_wait_ms": {"p50": _r(percentile([x.core_wait_p50 for x in s if x.core_wait_p50 is not None], 50)),
                         "p95": _r(percentile([x.core_wait_p95 for x in s if x.core_wait_p95 is not None], 95))},
        "threads_max": max((x.threads for x in s if x.threads is not None), default=None),
        "chat_open_s": {"count": len(run.opens), "p50": _r(percentile(done, 50), 2), "p95": _r(percentile(done, 95), 2),
                        "max": _r(max(done), 2) if done else None,
                        "timeouts": sum(1 for o in run.opens if o.timed_out)},
        "conv_save_p95_ms": _r(run.conv_save_p95_ms),
        "cpu_pct_of_pc": {"app": _avg_max([x.app_cpu for x in s]), "webview2": _avg_max([x.webview_cpu for x in s]),
                          "tool_servers": _avg_max([x.tool_cpu for x in s])},
        "min_free_gb": _r(min((x.free_gb for x in s if x.free_gb is not None), default=None), 2),
        "agents_running_max": max((x.agents_running for x in s if x.agents_running is not None), default=None),
        "tool_servers_max": max((x.tool_servers for x in s if x.tool_servers is not None), default=None),
        "turns_started": len(sessions),
        "tool_calls": len(tools),
        "tool_calls_per_min": round(len(tools) / minutes, 1),
        "messages_sent": run.messages_sent,
        "messages_delivered": len(messages),
        "questions_answered": run.questions_answered,
    }


DELIVER_LIMIT_S = 15.0
STOP_LIMIT_S = 15.0

# --budget: what a run may cost before it fails. Agent hosts seat 4 agents each, and each host
# shares one tool server, so more tool servers than hosts + 1 means a process per agent again.
BUDGET_OPEN_P95_S = 1.5
BUDGET_APP_THREADS = 200
BUDGET_MEMORY_DROP_GB = 4.0
AGENTS_PER_HOST = 4


def budget_failures(summary: dict[str, Any], free_before_gb: float | None) -> list[str]:
    """One line per missed budget, naming the measure, its value and the limit ([] = all met)."""
    failures: list[str] = []
    agents = int(summary.get("agents") or 0)
    open_p95 = (summary.get("chat_open_s") or {}).get("p95")
    if open_p95 is not None and open_p95 > BUDGET_OPEN_P95_S:
        failures.append(f"p95_open {open_p95} s > {BUDGET_OPEN_P95_S} s ({agents} agents)")
    threads = summary.get("threads_max")
    if threads is not None and threads > BUDGET_APP_THREADS:
        failures.append(f"app_threads {threads} > {BUDGET_APP_THREADS} ({agents} agents)")
    servers = summary.get("tool_servers_max")
    allowed = -(-agents // AGENTS_PER_HOST) + 1
    if servers is not None and servers > allowed:
        failures.append(f"tool_servers {servers} > {allowed} ({agents} agents, {AGENTS_PER_HOST} per host)")
    low = summary.get("min_free_gb")
    if free_before_gb is not None and low is not None and free_before_gb - low > BUDGET_MEMORY_DROP_GB:
        failures.append(f"memory_drop {round(free_before_gb - low, 2)} GB > {BUDGET_MEMORY_DROP_GB} GB "
                        f"({agents} agents)")
    return failures


def interrupt_table(run: Run) -> dict[str, Any]:
    """Per typed message: when it reached the agent and how; late (>15 s), lost, duplicated.
    Per Stop: how long until the chat stopped. Plus whether a git index.lock was left."""
    seen: dict[str, list[dict[str, Any]]] = {}
    for e in run.stub_events:
        if e.get("kind") == "message" and e.get("id"):
            seen.setdefault(str(e["id"]), []).append(e)
    rows = []
    for send in run.sends:
        hits = sorted(seen.get(send.id, []), key=lambda e: float(e.get("t") or 0))
        first = hits[0] if hits else None
        took = float(first["t"]) - send.sent_at if first else None
        rows.append({"id": send.id, "moment": send.moment, "conv": send.conv, "sent_at": send.sent_at,
                     "delivered_at": float(first["t"]) if first else None, "seconds": _r(took),
                     "path": " + ".join(dict.fromkeys(str(h.get("path") or "?") for h in hits)) or None,
                     "deliveries": len(hits), "late": took is not None and took > DELIVER_LIMIT_S,
                     "lost": first is None, "duplicate": len(hits) > 1})
    stops = []
    for stop in run.stops:
        took = None if stop.stopped_at is None else stop.stopped_at - stop.pressed_at
        stops.append({"moment": stop.moment, "conv": stop.conv, "pressed_at": stop.pressed_at,
                      "stopped_at": stop.stopped_at, "seconds": _r(took),
                      "late": took is None or took > STOP_LIMIT_S})
    on_time = sum(1 for r in rows if not (r["late"] or r["lost"]))
    return {"messages": rows, "stops": stops, "on_time": on_time, "sent": len(rows),
            "late": sum(r["late"] for r in rows), "lost": sum(r["lost"] for r in rows),
            "duplicated": sum(r["duplicate"] for r in rows), "stops_late": sum(s["late"] for s in stops),
            "index_lock_left": run.index_lock_left,
            "ok": bool(rows) and on_time == len(rows) and not any(r["duplicate"] for r in rows)
            and not any(s["late"] for s in stops) and run.index_lock_left is not True}


def memory_table(summary: dict[str, Any], memory_mb: int, free_before_gb: float | None) -> dict[str, Any]:
    return {"agents": summary["agents"], "memory_per_agent_mb": memory_mb,
            "agents_running_max": summary["agents_running_max"], "min_free_gb": summary["min_free_gb"],
            "free_before_gb": free_before_gb,
            "held_gb_max": _r((summary["agents_running_max"] or 0) * memory_mb / 1024, 2)}


def _clock(t: float | None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "-"


def render_interrupt_md(table: dict[str, Any]) -> str:
    lines = ["## Typed messages", "", f"{table['on_time']}/{table['sent']} within {DELIVER_LIMIT_S:g} s; "
             f"late {table['late']}, lost {table['lost']}, duplicated {table['duplicated']}", "",
             "| Moment | Sent | Delivered | Seconds | Path |", "| --- | --- | --- | --- | --- |"]
    for r in table["messages"]:
        flag = " LOST" if r["lost"] else (" LATE" if r["late"] else "") + (" DUPLICATE" if r["duplicate"] else "")
        lines.append(f"| {r['moment']} | {_clock(r['sent_at'])} | {_clock(r['delivered_at'])} | "
                     f"{r['seconds'] if r['seconds'] is not None else '-'}{flag} | {r['path'] or '-'} |")
    lines += ["", "## Stop", "", "| Moment | Pressed | Stopped | Seconds |", "| --- | --- | --- | --- |"]
    for s in table["stops"]:
        lines.append(f"| {s['moment']} | {_clock(s['pressed_at'])} | {_clock(s['stopped_at'])} | "
                     f"{s['seconds'] if s['seconds'] is not None else 'never'}{' LATE' if s['late'] else ''} |")
    lock = table["index_lock_left"]
    lines += ["", f"git index.lock left behind: {'unknown' if lock is None else ('YES' if lock else 'no')}"]
    return "\n".join(lines) + "\n"


def render_memory_md(table: dict[str, Any]) -> str:
    return "\n".join(["## Memory admission", "", "| Measure | Value |", "| --- | --- |",
                      f"| Agents / memory each | {table['agents']} / {table['memory_per_agent_mb']} MB |",
                      f"| Agents running at once (max) | {table['agents_running_max']} |",
                      f"| Held by agents (max) | {table['held_gb_max']} GB |",
                      f"| Free memory before / min | {table['free_before_gb']} GB / {table['min_free_gb']} GB |"]) + "\n"


def render_md(summary: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
    c, o, cpu = summary["core_wait_ms"], summary["chat_open_s"], summary["cpu_pct_of_pc"]
    lines = [
        f"# Swarm soak: {summary['agents']} agents, {summary['minutes']} min",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Core-wait p50 / p95 | {c['p50']} ms / {c['p95']} ms |",
        f"| Threads max | {summary['threads_max']} |",
        f"| Chat open p50 / p95 / max | {o['p50']} s / {o['p95']} s / {o['max']} s ({o['count']} opens) |",
        f"| Chat open timeouts (>{OPEN_TIMEOUT_S:g} s) | {o['timeouts']} |",
        f"| conv_save p95 | {summary['conv_save_p95_ms']} ms |",
        f"| App CPU avg / max | {cpu['app']['avg']} / {cpu['app']['max']} % of PC |",
        f"| WebView2 CPU avg / max | {cpu['webview2']['avg']} / {cpu['webview2']['max']} % of PC |",
        f"| Tool servers CPU avg / max | {cpu['tool_servers']['avg']} / {cpu['tool_servers']['max']} % of PC |",
        f"| Min free memory | {summary['min_free_gb']} GB |",
        f"| Agents running at once (max) | {summary['agents_running_max']} |",
        f"| Tool servers (max) | {summary.get('tool_servers_max')} |",
        f"| Turns started | {summary['turns_started']} |",
        f"| Tool calls / min | {summary['tool_calls_per_min']} ({summary['tool_calls']} total) |",
        f"| Messages delivered / sent | {summary['messages_delivered']} / {summary['messages_sent']} |",
        f"| Questions answered | {summary['questions_answered']} |",
    ]
    for key, value in (extra or {}).items():
        lines += ["", f"## {key}", "", "```json", json.dumps(value, indent=1, default=str)[:6000], "```"]
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- sandbox


def real_local() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def refuse_reason(exe: Path, box: Path) -> str:
    """Why this run must not start ("" = it may)."""
    if not exe.is_file():
        return f"--exe not found: {exe}"
    local = real_local()
    if _inside(exe, local) or "program files" in str(exe).lower():
        return f"--exe {exe} is an installed app (use a dist/ build)"
    if _inside(box, local / "UEFN-Ducky") or box.resolve() == local.resolve():
        return f"sandbox folder {box} is the real AppData"
    try:
        import psutil

        for proc in psutil.process_iter(["exe"]):
            if proc.info["exe"] and Path(proc.info["exe"]).resolve() == exe.resolve():
                return f"{exe} is already running (pid {proc.pid}); stop it first"
    except Exception:  # noqa: BLE001 - psutil missing is reported by main
        pass
    return ""


def sandbox_env(box: Path, extra: dict[str, str]) -> dict[str, str]:
    """os.environ with every profile folder pointed into the sandbox (as backend/sandbox/env.py)."""
    env = dict(os.environ)
    for key in ("UEFN_DUCKY_SHARED_MCP", "DUCKY_CONV_ID", "DUCKY_RUN_ID", "UEFN_DUCKY_MCP_BRIDGE",
                "UEFN_DUCKY_LAUNCHER_EXE", "UEFN_DUCKY_SWITCHED_FROM"):
        env.pop(key, None)
    env.update({
        "USERPROFILE": str(box / "home"), "HOME": str(box / "home"),
        "LOCALAPPDATA": str(box / "Local"), "APPDATA": str(box / "Roaming"),
        "TEMP": str(box / "temp"), "TMP": str(box / "temp"),
        "UEFN_DUCKY_PANEL_PORT": str(PANEL_PORT), "UEFN_DUCKY_PORT": str(BRIDGE_PORT),
        "DUCKY_SANDBOX": str(box), "DUCKY_SKIP_CLI_UPDATE": "1", "GIT_CONFIG_NOSYSTEM": "1",
        **extra,
    })
    return env


def make_fixture_repo(repo: Path, files: int = 200, slow_commit_s: int = 0) -> None:
    for i in range(files):
        sub = repo / f"pkg{i % 10}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"module_{i}.py").write_text(
            f'"""Fixture module {i} for the swarm soak (stub)."""\n\n\ndef value_{i}():\n    return {i}\n',
            encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=soak", "-c", "user.email=soak@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True)
    if slow_commit_s:  # the interrupt scenario presses Stop while a commit holds index.lock
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\nsleep {slow_commit_s}\n", encoding="utf-8", newline="\n")


def write_launcher(box: Path) -> Path:
    """claude.cmd with absolute paths (no PATH lookups), next to no claude.exe: the plugin's
    resolve_claude_bin swaps a .cmd for a sibling or ~/.local/bin claude.exe."""
    launcher = box / "stub" / "claude.cmd"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(f'@"{sys.executable}" "{STUB}" %*\r\n', encoding="utf-8")
    return launcher


def copy_plugin(box: Path, plugin_repo: str = "") -> None:
    """The owner's installed anthropic plugin, plus the plugin repo's backend/*.py when given."""
    src = real_local() / "UEFN-Ducky" / "uefn_plugins" / "anthropic"
    dst = box / "Local" / "UEFN-Ducky" / "uefn_plugins" / "anthropic"
    if not src.is_dir():
        raise SystemExit(f"installed anthropic plugin not found at {src}")
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    if plugin_repo:
        for py in (Path(plugin_repo) / "backend").glob("*.py"):
            if not py.name.startswith("test_"):
                shutil.copy2(py, dst / "backend" / py.name)


class Panel:
    """POST /__panel_api/<method> on the sandbox's panel port; never the live ports."""

    def __init__(self, port: int) -> None:
        if port in LIVE_PORTS:
            raise ValueError(f"port {port} is the person's own Ducky")
        self.base = f"http://127.0.0.1:{port}/__panel_api/"

    def call(self, method: str, timeout: float = 30.0, **args: Any) -> Any:
        req = urllib.request.Request(self.base + method, data=json.dumps({"args": args}).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        if not body.get("ok", False):
            raise RuntimeError(f"{method}: {body.get('error')}")
        return body.get("result")

    def wait_ready(self, limit_s: float = 60.0) -> None:
        deadline = time.monotonic() + limit_s
        last = ""
        while time.monotonic() < deadline:
            try:
                self.call("get_settings", timeout=5)
                return
            except Exception as exc:  # noqa: BLE001 - not up yet
                last = str(exc)
                time.sleep(1.0)
        raise SystemExit(f"sandbox Ducky did not answer get_settings in {limit_s:g} s ({last})")


# ----------------------------------------------------------------------------- measuring


def classify(proc: Any) -> str:
    name = (proc.name() or "").lower()
    if name.startswith("msedgewebview2"):
        return "webview2"
    try:
        cmd = " ".join(proc.cmdline()).lower()
    except Exception:  # noqa: BLE001
        cmd = ""
    if "stub_claude.py" in cmd:
        return "agent"
    if "bridge" in name or " bridge" in cmd:
        return "tool"
    return "app" if "ducky" in name else "other"


class CpuMeter:
    """% of the PC per role for the app's process tree, from cpu_times deltas."""

    def __init__(self, root_pid: int) -> None:
        import psutil

        self.psutil = psutil
        self.root = psutil.Process(root_pid)
        self.prev: dict[int, float] = {}
        self.prev_t = time.monotonic()

    def sample(self) -> dict[str, float]:
        now = time.monotonic()
        out = {"app": 0.0, "webview2": 0.0, "tool": 0.0, "agent": 0.0, "other": 0.0, "agents": 0, "tools": 0}
        cur: dict[int, float] = {}
        try:
            procs = [self.root, *self.root.children(recursive=True)]
        except self.psutil.Error:
            procs = []
        span = max(now - self.prev_t, 1e-6) * (self.psutil.cpu_count() or 1)
        for proc in procs:
            try:
                t = proc.cpu_times()
                cur[proc.pid] = t.user + t.system
                role = classify(proc)
            except self.psutil.Error:
                continue
            out[role] += max(cur[proc.pid] - self.prev.get(proc.pid, cur[proc.pid]), 0.0) / span * 100.0
            out["agents"] += role == "agent"
            out["tools"] += role == "tool"
        self.prev, self.prev_t = cur, now
        return out


def pc_state(exclude_root: int | None = None) -> dict[str, Any]:
    """Free memory, process count, top 5 by CPU and memory, the owner's own agent CLIs (D26)."""
    import psutil

    procs = list(psutil.process_iter(["pid", "name", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(1.0)
    rows = []
    for p in procs:
        try:
            rows.append({"pid": p.pid, "name": p.info["name"], "cpu": p.cpu_percent(None),
                         "memory_mb": round((p.info["memory_info"].rss if p.info["memory_info"] else 0) / 2**20)})
        except psutil.Error:
            continue
    mine: set[int] = set()
    if exclude_root:
        try:
            root = psutil.Process(exclude_root)
            mine = {root.pid, *(c.pid for c in root.children(recursive=True))}
        except psutil.Error:
            pass
    owner_agents = [r for r in rows if r["pid"] not in mine
                    and str(r["name"] or "").lower() in ("claude.exe", "codex.exe", "node.exe")]
    vm = psutil.virtual_memory()
    return {"free_gb": round(vm.available / 2**30, 2), "processes": len(rows),
            "top_cpu": sorted(rows, key=lambda r: -r["cpu"])[:5],
            "top_memory": sorted(rows, key=lambda r: -r["memory_mb"])[:5],
            "owner_agent_processes": len(owner_agents)}


def db_rows(db: Path) -> dict[str, Any]:
    """Row counts of the perf / error / stuck / activity tables, read-only."""
    if not db.is_file():
        return {"error": f"no database at {db}"}
    out: dict[str, Any] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            if not any(word in table.lower() for word in ("perf", "error", "stuck", "activity", "crash")):
                continue
            entry: dict[str, Any] = {"rows": con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]}
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            for col in ("kind", "category", "event", "type"):
                if col in cols:
                    entry[f"by_{col}"] = dict(con.execute(
                        f'SELECT "{col}", COUNT(*) FROM "{table}" GROUP BY 1 ORDER BY 2 DESC LIMIT 15').fetchall())
                    break
            out[table] = entry
    finally:
        con.close()
    return out


def read_stub_log(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def stop_tree(pid: int) -> None:
    import psutil

    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.Error:
        return
    for proc in procs:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=15)


# ----------------------------------------------------------------------------- the run


def seed(panel: Panel, box: Path, launcher: Path | None, agents: int) -> list[dict[str, str]]:
    """First-run done, stub CLI set, one group (lead + members). Returns [{id, name}], lead first."""
    panel.call("finish_first_run_setup")
    patch: dict[str, Any] = {"default_coding_agent": "claude_code"}
    if launcher is not None:
        patch["coding_agents"] = {"claude_code": {"enabled": True, "cli_path": str(launcher)}}
    panel.call("save_agent_settings", patch=patch)
    group = panel.call("group_create", name="Swarm soak", open_tab=False)
    chats = []
    for i in range(agents):
        name = "Soak lead" if i == 0 else f"Soak member {i}"
        conv = panel.call("create_conversation", folder_id=group["folder_id"], config={"title": name})
        panel.call("group_add_member", group_id=group["id"], conv_id=conv["id"], as_leader=i == 0)
        panel.call("set_agent_allow_everything", conv_id=conv["id"], on=True)
        chats.append({"id": conv["id"], "name": name})
    return chats


def interrupt_director(panel: Panel, chats: list[dict[str, str]], stub_log: Path, run: Run,
                       stop: threading.Event) -> None:
    """Interrupt scenario: 10 typed messages at set moments for the first two agents, and two
    Stops for the third (during its git commit, then during streaming of its next turn)."""
    moments = {"streaming": "during streaming text", "command": "during a 3-minute command",
               "ask": "during an ask_user wait", "approval": "during an approval-card wait",
               "after_turn": "right after a turn ends"}
    talkers = {c["id"] for c in chats[:2]}
    stopper = chats[2]["id"] if len(chats) > 2 else ""
    done: set[tuple[str, str]] = set()
    due: list[tuple[float, str, str]] = []  # (at, conv, what)
    offset = 0
    counter = [0]

    def send(conv: str, what: str) -> None:
        counter[0] += 1
        msg_id = f"SOAKMSG-{counter[0]}"
        try:
            panel.call("send_message", conv_id=conv, text=f"{msg_id}: soak message ({moments[what]}).",
                       mode="agent", model=MODEL, coding_agent="claude_code")
            run.sends.append(MessageSend(id=msg_id, moment=moments[what], conv=conv, sent_at=time.time()))
            run.messages_sent += 1
        except Exception as exc:  # noqa: BLE001
            print(f"send {msg_id} failed: {exc}", flush=True)

    def press_stop(conv: str, moment: str) -> None:
        press = StopPress(moment=moment, conv=conv, pressed_at=time.time())
        run.stops.append(press)
        try:
            panel.call("cancel_agent", conv_id=conv)
        except Exception as exc:  # noqa: BLE001
            print(f"stop failed: {exc}", flush=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not stop.is_set():
            try:
                if conv not in (panel.call("list_running_agents", timeout=10) or []):
                    press.stopped_at = time.time()
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)

    while not stop.wait(1.0):
        events = read_stub_log(stub_log)
        for e in events[offset:]:
            conv, kind = str(e.get("conv") or ""), e.get("kind")
            what = "after_turn" if kind == "turn_end" and not e.get("interrupted") else (
                e.get("phase") if kind == "phase" else None)
            if not what:
                continue
            if conv in talkers and what in moments and (conv, what) not in done:
                done.add((conv, what))
                due.append((time.monotonic() + (2.0 if what == "after_turn" else 3.0), conv, what))
            elif conv == stopper:
                if what == "git" and (conv, "stop_git") not in done:
                    done.add((conv, "stop_git"))
                    due.append((time.monotonic() + 3.0, conv, "stop_git"))
                elif what == "streaming" and (conv, "stop_git") in done and (conv, "stop_stream") not in done:
                    done.add((conv, "stop_stream"))
                    due.append((time.monotonic() + 3.0, conv, "stop_stream"))
        offset = len(events)
        now = time.monotonic()
        for item in [d for d in due if d[0] <= now]:
            due.remove(item)
            _, conv, what = item
            if what == "stop_git":
                press_stop(conv, "during a git commit")
                time.sleep(2.0)
                try:  # a plain message (no id) starts the turn the second Stop lands in
                    panel.call("send_message", conv_id=conv, text="Go again.", mode="agent", model=MODEL,
                               coding_agent="claude_code")
                except Exception:  # noqa: BLE001
                    pass
            elif what == "stop_stream":
                press_stop(conv, "during streaming text")
            else:
                send(conv, what)


def run_soak(panel: Panel, app_pid: int, chats: list[dict[str, str]], minutes: float, stub_log: Path,
             director: Any = None) -> Run:
    run = Run(started=time.time(), ended=0.0, agents=len(chats))
    stop = threading.Event()
    for chat in chats:
        try:
            panel.call("send_message", conv_id=chat["id"], text=TASK.format(name=chat["name"]), mode="agent",
                       model=MODEL, coding_agent="claude_code")
            run.messages_sent += 1
        except Exception as exc:  # noqa: BLE001 - counted as not sent
            print(f"send to {chat['name']} failed: {exc}", flush=True)

    def sampler() -> None:
        meter = CpuMeter(app_pid)
        while not stop.wait(5.0):
            cpu = meter.sample()
            s = Sample(t=time.time(), app_cpu=cpu["app"], webview_cpu=cpu["webview2"], tool_cpu=cpu["tool"],
                       agents_running=cpu["agents"], tool_servers=int(cpu["tools"]))
            try:
                snap = panel.call("ducky_health_snapshot", timeout=30) or {}
                app = snap.get("app") or {}
                wait = app.get("core_wait_ms") or {}
                s.core_wait_p50, s.core_wait_p95 = wait.get("p50"), wait.get("p95")
                s.threads = app.get("threads")
                s.free_gb = (snap.get("pc") or {}).get("memory_free_gb")
                running = (snap.get("running_agents") or {}).get("count")
                if running is not None:
                    s.agents_running = max(int(running), s.agents_running or 0)
            except Exception as exc:  # noqa: BLE001 - a slow snapshot is itself a finding
                print(f"health snapshot failed: {exc}", flush=True)
            if s.free_gb is None:
                import psutil

                s.free_gb = round(psutil.virtual_memory().available / 2**30, 2)
            run.samples.append(s)

    def opener() -> None:
        while not stop.wait(10.0):
            chat = random.choice(chats)
            started = time.monotonic()
            try:
                rows = panel.call("load_messages", timeout=OPEN_TIMEOUT_S, conv_id=chat["id"]) or []
                run.opens.append(ChatOpen(t=time.time(), seconds=time.monotonic() - started, rows=len(rows)))
            except Exception:  # noqa: BLE001 - a timeout or error is a failed press
                run.opens.append(ChatOpen(t=time.time(), seconds=time.monotonic() - started, timed_out=True))

    def answerer() -> None:
        seen: dict[str, float] = {}
        while not stop.wait(2.0):
            try:
                pending = panel.call("ui_rpc_pending_questions", timeout=20) or []
            except Exception:  # noqa: BLE001
                continue
            now = time.monotonic()
            for q in pending:
                rid = str(q.get("request_id") or "")
                first = seen.setdefault(rid, now)
                if now - first < ANSWER_AFTER_S:
                    continue
                answers = {str(item.get("id") or "q"): {"selected": [], "text": "Go on.", "skipped": False}
                           for item in (q.get("params") or {}).get("questions") or []}
                try:
                    if panel.call("ui_rpc_respond", request_id=rid,
                                  payload={"ok": True, "answers": answers, "skipped_all": False}):
                        run.questions_answered += 1
                except Exception:  # noqa: BLE001
                    pass

    workers = [threading.Thread(target=f, name=f.__name__, daemon=True) for f in (sampler, opener, answerer)]
    if director is not None:
        workers.append(threading.Thread(target=director, args=(panel, chats, stub_log, run, stop),
                                        name="director", daemon=True))
    for w in workers:
        w.start()
    end = time.monotonic() + minutes * 60
    while time.monotonic() < end:
        time.sleep(15)
        last = run.samples[-1] if run.samples else None
        print(f"{time.strftime('%H:%M:%S')}  samples {len(run.samples)}  opens {len(run.opens)} "
              f"(timeouts {sum(o.timed_out for o in run.opens)})  agents {last.agents_running if last else '?'}  "
              f"core-wait p95 {last.core_wait_p95 if last else '?'} ms", flush=True)
    stop.set()
    for w in workers:
        w.join(70)
    run.ended = time.time()
    try:
        snap = panel.call("ducky_health_snapshot", timeout=60, detail=True) or {}
        metric = find_metric(snap.get("perf"), "conv_save")
        run.conv_save_p95_ms = float(metric["p95"]) if metric and metric.get("p95") is not None else None
    except Exception as exc:  # noqa: BLE001
        print(f"final perf snapshot failed: {exc}", flush=True)
    run.stub_events = read_stub_log(stub_log)
    return run


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exe", required=True, help="dist/UEFN-Ducky-X/UEFN-Ducky.exe (never the installed app)")
    ap.add_argument("--agents", type=int, default=16)
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--real", action="store_true", help="real Claude Code instead of the stub (p8-real only)")
    ap.add_argument("--out", default=str(Path(os.environ.get("TEMP", ".")) / "swarm-soak"))
    ap.add_argument("--box", default=str(Path(os.environ.get("TEMP", ".")) / "dsbx"), help="sandbox folder")
    ap.add_argument("--plugin-repo", default="", help="overlay this anthropic plugin repo's backend/*.py")
    ap.add_argument("--scenario", default="swarm", choices=("swarm", "interrupt", "memory"),
                    help="interrupt: 3 agents, 10 timed messages and 2 Stops; memory: agents hold --memory-mb")
    ap.add_argument("--memory-mb", type=int, default=0, help="STUB_MEMORY_MB per agent (memory scenario: 300)")
    ap.add_argument("--budget", action="store_true",
                    help="exit 1 when chat-open p95, app threads, tool servers or memory miss their budget")
    args = ap.parse_args()
    try:
        import psutil  # noqa: F401
    except ImportError:
        print("pip install psutil", file=sys.stderr)
        return 2

    exe, box, out = Path(args.exe).resolve(), Path(args.box), Path(args.out)
    if args.scenario == "interrupt":
        args.agents = 3
    if args.scenario == "memory" and not args.memory_mb:
        args.memory_mb = 300
    why = refuse_reason(exe, box)
    if why:
        print("REFUSED:", why, file=sys.stderr)
        return 2
    repo_root = HERE.parents[2]
    if _inside(out, repo_root) or _inside(box, repo_root):
        print("REFUSED: --out and --box must be outside the repo", file=sys.stderr)
        return 2
    if box.exists():
        shutil.rmtree(box)
    for sub in ("Local", "Roaming", "home", "temp"):
        (box / sub).mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    copy_plugin(box, args.plugin_repo)
    launcher = None if args.real else write_launcher(box)
    repo = box / "repo"
    make_fixture_repo(repo, slow_commit_s=20 if args.scenario == "interrupt" else 0)
    stub_log = box / "stub_log.jsonl"
    extra = {"STUB_SANDBOX": "1", "STUB_FIXTURE_REPO": str(repo), "STUB_LOG": str(stub_log),
             "STUB_SCENARIO": "interrupt" if args.scenario == "interrupt" else "swarm"}
    if args.memory_mb:
        extra["STUB_MEMORY_MB"] = str(args.memory_mb)
    env = sandbox_env(box, extra)

    before = pc_state()
    proc = subprocess.Popen([str(exe)], cwd=str(box), env=env)
    print(f"sandbox Ducky pid {proc.pid}, panel :{PANEL_PORT - 1}, box {box}", flush=True)
    results: dict[str, Any] = {}
    try:
        panel = Panel(PANEL_PORT - 1)
        panel.wait_ready(60.0)
        chats = seed(panel, box, launcher, args.agents)
        run = run_soak(panel, proc.pid, chats, args.minutes, stub_log,
                       director=interrupt_director if args.scenario == "interrupt" else None)
        run.index_lock_left = (repo / ".git" / "index.lock").exists()
        after = pc_state(exclude_root=proc.pid)
        summary = summarize(run)
        results = {"summary": summary, "pc_before": before, "pc_after": after,
                   "db": db_rows(box / "Local" / "UEFN-Ducky" / "ducky.db"),
                   "samples": [asdict(s) for s in run.samples], "opens": [asdict(o) for o in run.opens],
                   "exe": str(exe), "real": bool(args.real), "scenario": args.scenario}
        if args.scenario == "interrupt":
            results["interrupt"] = interrupt_table(run)
        if args.scenario == "memory":
            results["memory"] = memory_table(summary, args.memory_mb, before.get("free_gb"))
    finally:
        stop_tree(proc.pid)
        for crash in (box / "Local" / "CrashDumps", *(box / "Local" / "UEFN-Ducky").glob("crash*")):
            shutil.rmtree(crash, ignore_errors=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    if not results:
        print("the run failed before it finished; nothing measured", file=sys.stderr)
        return 1
    md = render_md(results["summary"])
    if "interrupt" in results:
        md += "\n" + render_interrupt_md(results["interrupt"])
    if "memory" in results:
        md += "\n" + render_memory_md(results["memory"])
    failures = budget_failures(results["summary"], before.get("free_gb")) if args.budget else []
    if args.budget:
        md += "\n## Budgets\n\n" + ("\n".join(f"- FAIL {f}" for f in failures) or "- PASS: every budget met") + "\n"
    for key, label in (("pc_before", "PC before"), ("pc_after", "PC after"), ("db", "Database rows")):
        md += f"\n## {label}\n\n```json\n{json.dumps(results[key], indent=1, default=str)[:6000]}\n```\n"
    (out / "results.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"wrote {out / 'results.md'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
