"""ducky_health / ducky_self_care: both backends say the same about the same report,
process table, command lines and files. The process tables here are made up, and nothing
is ever stopped: the only live look is each backend's own report of this PC, held to its
shape and to the parts that come from the fixture data folder."""

from __future__ import annotations

import copy
import json
import os
from typing import Any

import pytest

from .parity_harness import plain

NOW = 1_795_000_000.0
MB = 1 << 20


def _proc(pid: int, name: str, ppid: int, size_mb: int = 1, started: float = 100.0):
    from backend.util import proc_table

    return proc_table.Proc(pid, ppid, name, size_mb * MB, size_mb * MB, 1, 1, 0.0, started)


def _table(*rows: Any) -> dict[int, Any]:
    """Lowest pid first, as the Rust table is kept."""
    return {p.pid: p for p in sorted(rows, key=lambda p: p.pid)}


@pytest.fixture
def health(rust_store, monkeypatch):
    """``health.rust(what, **body)`` asks the Rust rules; ``health.cmdlines(cmds)`` gives the
    Python ones the same made-up command lines; ``health.sent(procs)`` is a table as JSON;
    ``health.findings(snap)`` is the Python findings at ``NOW``."""
    from frontend import self_health

    class Health:
        py = self_health

        @staticmethod
        def findings(snap: dict) -> Any:
            with monkeypatch.context() as at:
                at.setattr(self_health.time, "time", lambda: NOW)
                return plain(self_health.findings(copy.deepcopy(snap)))

        @staticmethod
        def rust(what: str, **body: Any) -> Any:
            status, reply = rust_store.request(f"/__rs/health/{what}", body)
            assert status == 200, reply
            return reply

        @staticmethod
        def cmdlines(cmds: dict[int, list[str]]) -> dict[str, list[str]]:
            monkeypatch.setattr(self_health, "_cmdline", lambda pid: (cmds.get(pid, []), ""))
            return {str(pid): cmd for pid, cmd in cmds.items()}

        @staticmethod
        def sent(procs: dict[int, Any]) -> list[dict[str, Any]]:
            return [p._asdict() for p in procs.values()]

    return Health


def _base() -> dict:
    return {"app": {"cpu_percent": 1, "memory_mb": 300, "private_mb": 280, "handles": 900},
            "pc": {"cpus": 8, "memory_used_percent": 40, "memory_total_gb": 64, "top_apps": []},
            "helpers": [], "other_ducky_processes": [], "uefn": [], "sandboxes": {}, "terminals": {}, "storage": {}}


def _with(**parts: Any) -> dict:
    snap = _base()
    for key, value in parts.items():
        if isinstance(value, dict) and isinstance(snap.get(key), dict):
            snap[key].update(value)
        else:
            snap[key] = value
    return snap


_HOT = {"name": "ca-stdout-test", "where": "agent.py:_read", "stack": ["_read (agent.py:10)", "run (threading.py:1)"]}
_TOTAL = {"total_mb": 8700.5, "idle": True, "conhosts": 20,
          "parts": {"app": 1350.0, "agent_hosts": 1120.0, "tool_servers": 1654.0, "plugin_workers": 2900.0,
                    "webview2": 1309.0, "terminals": 0.4, "conhosts": 120.0, "agents": 247.1},
          "shared_tool_servers": [{"pid": 2, "exe": "C:/D/1.2.470/B.exe", "version": "1.2.470", "mb": 932.0},
                                  {"pid": 3, "exe": "C:/D/1.2.470/B.exe", "version": "1.2.470", "mb": 722.0},
                                  {"pid": 4, "exe": "C:/D/old/B.exe", "version": "", "mb": 10.0},
                                  {"pid": 5, "exe": "", "version": "", "mb": 11.0}, {"pid": 6, "mb": 12.5}],
          "plugin_workers": [{"pid": 7, "plugin": "forge", "mb": 725.0, "idle_min": 40},
                             {"pid": 8, "plugin": "forge", "mb": 725.5, "idle_min": 12},
                             {"pid": 9, "plugin": "atlas", "mb": 30.0, "idle_min": 9},
                             {"pid": 10, "plugin": "city", "mb": 30.0, "idle_min": None},
                             {"pid": 11, "plugin": "blender", "mb": 1500.0, "idle_min": 10}],
          "big_idle_processes": [{"pid": 1, "name": "UEFN-Ducky.exe", "part": "app", "private_mb": 1350.0},
                                 {"pid": 12, "name": "python.exe", "part": "plugin_workers", "private_mb": 725.0}]}

SNAPS = {
    "healthy": _base(),
    "nothing at all": {},
    "one fallback": _with(tool_fallbacks={"count": 91, "last_hour": 1, "reason": "it did not answer in 10 s (pid 7)"}),
    "many fallbacks": _with(tool_fallbacks={"count": 4, "last_hour": 3, "reason": ""}),
    "healed": _with(auto_heal=[{"at": NOW - 7200, "fixed": "old"}, {"at": NOW - 50, "fixed": "a"}, {"at": NOW - 40},
                               {"at": NOW - 30, "fixed": "c"}, {"at": NOW - 20, "fixed": "d"}]),
    "healed once": _with(auto_heal=[{"at": NOW - 1, "fixed": "the queue"}]),
    "core wait high": _with(app={"core_wait_ms": {"p50": 1, "p95": 60.5, "max": 90, "samples": 6000}}),
    "core wait medium": _with(app={"core_wait_ms": {"p50": 1, "p95": 30, "max": 30, "samples": 6000}}),
    "core wait fine": _with(app={"core_wait_ms": {"p50": None, "p95": None, "max": None, "samples": 0}}),
    "threads": _with(app={"hot_threads": [dict(_HOT, share=0.8), dict(_HOT, name="warm", share=0.3), dict(_HOT, name="edge", share=0.5),
                                          {"name": "native thread 9", "share": 0.985, "where": "native code", "stack": []},
                                          {"name": "bare", "share": 1.25}]}),
    "in the window": _with(agents_in_window=["0a1b2c3d", "ffeeddcc"]),
    "piled": _with(app={"inflight": [{"source": "bridge", "method": "group_members", "count": 25, "oldest_s": 3.9},
                                     {"source": "bridge", "method": "get_version", "count": 1, "oldest_s": 0.1},
                                     {"source": "http", "method": "load_messages", "count": 2, "oldest_s": 10.5}]}),
    "frozen": _with(ui_last_hour={"ui_stalls": 1200, "ui_stall_p95_ms": 1063.2, "ui_stall_pc": " PC busy 80% ",
                                  "top_ui_calls": [["browser_pane_set_bounds", 4333], ["get_settings", 70]]}),
    "frozen a little": _with(ui_last_hour={"ui_stalls": 45, "ui_stall_p95_ms": 3016.8, "top_ui_calls": []}),
    "stalls below the bar": _with(ui_last_hour={"ui_stalls": 120, "ui_stall_p95_ms": 999.9}),
    "busy app": _with(app={"cpu_percent": 240, "private_mb": 3500, "handles": 40000}),
    "big by rss only": _with(app={"private_mb": 0, "memory_mb": 2048.5}),
    "webview": _with(helpers=[{"name": "conhost.exe", "count": 3, "memory_mb": 18.0, "cpu_percent": 0.0},
                              {"name": "msedgeWebView2.exe", "count": 7, "memory_mb": 3000.1, "cpu_percent": 2.5}]),
    "orphans": _with(other_ducky_processes=[{"pid": 9, "memory_mb": 120.5, "orphan": True}, {"pid": 10, "memory_mb": 300, "orphan": False},
                                            {"pid": 11, "memory_mb": 2, "orphan": True}]),
    "sandboxes": _with(sandboxes={"programs": [{"name": "node.exe", "memory_mb": 2048, "cpu_percent": 10},
                                               {"name": "vite", "memory_mb": None, "cpu_percent": 201.0},
                                               {"name": "small", "memory_mb": 10.5, "cpu_percent": 1.0}]}),
    "terminals": _with(terminals={"open": 12, "agent_owned": 9}),
    "storage": _with(storage={"ducky_db_wal_mb": 300.4, "cache_mb": 1024.1, "sandboxes_mb": 10.0}),
    "big sandboxes": _with(storage={"cache_mb": 5, "sandboxes_mb": 20480}),
    "uefn": _with(uefn=[{"name": "UnrealEditorFortnite.exe", "private_mb": 45 * 1024, "memory_mb": 23 * 1024},
                        {"name": "UnrealEditorFortnite.exe", "private_mb": 0, "memory_mb": 31000.7},
                        {"name": "UnrealEditor.exe", "private_mb": 100, "memory_mb": 900000}]),
    "full pc, not ducky": _with(pc={"memory_used_percent": 96, "top_apps": [{"name": "UnrealEditorFortnite.exe", "memory_mb": 46000}]}),
    "full pc, ducky": _with(app={"memory_mb": 9000}, helpers=[{"name": "x", "count": 1, "memory_mb": 2000.5}],
                            other_ducky_processes=[{"memory_mb": 900}], pc={"memory_used_percent": 88.6, "memory_total_gb": 0}),
    "ducky total": _with(ducky_total=_TOTAL, pc={"memory_total_gb": 32}),
    "small total": _with(ducky_total={"total_mb": 380.0, "parts": {"app": 380.0}, "idle": True, "conhosts": 0}),
    "medium total": _with(ducky_total={"total_mb": 3200.0, "parts": {"app": 1024.0, "agents": 2176.0}, "idle": False, "conhosts": 7}),
    "total on a small pc": _with(ducky_total={"total_mb": 1300.0, "parts": {"app": 1300.0}}, pc={"memory_total_gb": 8}),
}


@pytest.mark.parametrize("name", list(SNAPS))
def test_a_report_gets_the_same_findings(health, name):
    snap = SNAPS[name]
    ours = health.rust("findings", snap=snap, now=NOW)
    assert ours == health.findings(snap)
    assert ours and all(list(found) == ["severity", "what", "why", "fix"] for found in ours)


def test_everything_wrong_at_once_reads_the_same(health):
    snap: dict[str, Any] = {}
    for part in SNAPS.values():
        for key, value in part.items():
            if isinstance(value, dict) and isinstance(snap.get(key), dict):
                snap[key] = {**snap[key], **value}
            else:
                snap[key] = copy.deepcopy(value)
    ours = health.rust("findings", snap=snap, now=NOW)
    assert ours == health.findings(snap)
    assert len(ours) > 15 and [f["severity"] for f in ours] == sorted((f["severity"] for f in ours), key=["high", "medium", "low", "ok"].index)


_FORGE = "C:/Users/x/AppData/Local/UEFN-Ducky/uefn_plugins/forge/forge-engine/worker.py"
COMMANDS = [
    [], ["x"], ["x", "bridge", "--shared-daemon"], ["x", "Bridge", "--port", "4200"], ["x", "bridge", "--adapter"],
    ["x", "--multiprocessing-fork", "parent_pid=4"], ["x", "agent-host", "--slot", "1"], ["bridge"], ["x", "my-bridge"],
    ["x", "abridge"], ["python.exe", _FORGE], ["python.exe", _FORGE.replace("/", "\\")],
    ["python", "D:/Repos/UEFN-Plugin-City-Builder/run.py"], ["python", "D:/tools/blend_3-Engine/run.py"],
    ["python", "D:/uefn_plugins//double/x.py"], ["python", "D:/uefn_plugins/"], ["python", 'D:/uefn_plugins/a"b/c', "D:/uefn_plugins/real/w.py"],
    ["python", "D:/x/plugins/atlas/w.py"], ["python", "D:/x/Plugins//atlas/w.py"], ["python", "D:/tools/jobs/w.py", "-v"],
    ["python", "-m", "worker"], ["python", "w.py"], ["python", "D:/uefn_plugins/uefn-plugin-forge/w.py"],
    ["python", "D:/a b/uefn-plugin-/x-engine/w.PY"], ["node", "/srv/caf\u00e9_9-engine/run"],
]  # fmt: skip


@pytest.mark.parametrize("cmd", COMMANDS, ids=[" ".join(cmd) or "(none)" for cmd in COMMANDS])
def test_a_command_line_names_the_same_role_and_plugin(health, cmd):
    theirs = {"role": health.py._role(cmd), "home": health.py._plugin_home(cmd), "of": health.py._plugin_of(cmd)}
    assert health.rust("plugin", cmd=cmd) == theirs


def test_the_same_processes_are_counted_as_duckys(health):
    """The Nov 5 2026 table: an idle Ducky holding 9 GB in leftovers."""
    me = 100
    rows = [_proc(me, "UEFN-Ducky.exe", 1, 1350), _proc(1, "explorer.exe", 0, 50, 1.0),
            _proc(101, "UEFN-Ducky.exe", me, 1120, 200.0),  # agent host
            _proc(102, "UEFN-Ducky-Bridge.exe", 1, 932, 200.0), _proc(103, "UEFN-Ducky-Bridge.exe", 1, 722, 200.0),
            _proc(104, "msedgewebview2.exe", me, 978, 200.0), _proc(105, "msedgewebview2.exe", 104, 331, 200.0),
            _proc(106, "claude.exe", 101, 200, 200.0), _proc(107, "bash.exe", me, 3, 200.0),
            _proc(108, "UEFN-Ducky.exe", me, 90, 200.0),  # a plugin's own worker
            _proc(109, "python.exe", me, 40, 200.0),  # a Python child that is no worker
            _proc(120, "pythonw.exe", me, 60, 200.0),  # a worker by its command line only
            _proc(130, "UEFN-Ducky.exe", me, 77, 50.0),  # started before its "parent": that pid was reused
            _proc(300, "UnrealEditorFortnite.exe", 1, 12000, 200.0)]  # never Ducky's
    rows += [_proc(110 + i, "python.exe", me, 725, 200.0) for i in range(4)]  # Forge workers
    rows += [_proc(200 + i, "conhost.exe", me, 6, 200.0) for i in range(20)]
    procs = _table(*rows)
    forge = ["python.exe", "C:/Users/x/AppData/Local/UEFN-Ducky/plugins/uefn-plugin-forge/worker.py"]
    cmds = {**{110 + i: forge for i in range(4)}, 109: ["python.exe", "C:/tools/script.py"], 120: ["pythonw.exe", "D:/jobs/Worker.py"]}
    known = {101: ("agent host", "C:/Ducky/1.2.470/UEFN-Ducky.exe"),
             102: ("shared tool server", "C:/Ducky/1.2.470/UEFN-Ducky-Bridge.exe"),
             103: ("shared tool server", "C:/Ducky/1.2.470/UEFN-Ducky-Bridge.exe"), 108: ("worker", "")}
    sent = health.cmdlines(cmds)
    for idle, hosts, last_used, own in ((True, {101}, {"forge": NOW - 40 * 60, "Jobs": NOW - 90}, {108: "city-builder"}),
                                        (False, set(), {}, {}), (True, set(), {"uefn-plugin-forge": NOW - 29.9 * 60}, {109: "x"})):
        theirs = health.py.ducky_footprint(procs, me, known, hosts, idle=idle, now=NOW, last_used=last_used, own_workers=own)
        ours = health.rust("footprint", procs=health.sent(procs), me=me, known={str(pid): list(pair) for pid, pair in known.items()},
                           host_pids=sorted(hosts), idle=idle, now=NOW, last_used=last_used,
                           own_workers={str(pid): plugin for pid, plugin in own.items()}, cmdlines=sent)
        assert ours == plain(theirs)
        assert list(ours["parts"]) == list(theirs["parts"]) and ours["conhosts"] == 20
        snap = _with(ducky_total=theirs, pc={"memory_total_gb": 32})
        assert health.rust("findings", snap=plain(snap), now=NOW) == health.findings(snap)
    assert theirs["parts"]["plugin_workers"] == 2960 and 300 not in [b["pid"] for b in theirs["big_idle_processes"]]
    # A table without this process, and a Ducky that is alone.
    for table, root in ((procs, 999), (_table(_proc(me, "UEFN-Ducky.exe", 1, 380)), me), ({}, me)):
        theirs = health.py.ducky_footprint(table, root, idle=True, last_used={}, own_workers={})
        assert health.rust("footprint", procs=health.sent(table), me=root, idle=True, cmdlines=sent) == plain(theirs)


def test_only_tool_servers_whose_parent_is_gone_are_picked(health, monkeypatch):
    procs = _table(
        _proc(1, "System", 0, 1, 1.0),
        _proc(11, "UEFN-Ducky-Bridge.exe", 999999, 900),  # orphan
        _proc(12, "UEFN-Ducky-Bridge.exe", 999999),  # shared daemon: kept
        _proc(13, "UEFN-Ducky.exe", 999999),  # an app: kept
        _proc(14, "UEFN-Ducky-Bridge.exe", 1),  # parent alive: kept
        _proc(15, "notepad.exe", 999999),  # not Ducky: never looked at
        _proc(16, "uefn-ducky-bridge.exe", 17, 3, 50.0),  # its parent's pid now belongs to a newer process
        _proc(17, "explorer.exe", 1, 1, 90.0),
        _proc(18, "UEFN-Ducky.exe", 999999),  # an agent host that lost its app
        _proc(os.getpid(), "UEFN-Ducky.exe", 999999),  # the one asking is never stopped
    )
    sent = health.cmdlines({11: ["x", "bridge", "--port", "4200"], 12: ["x", "bridge", "--shared-daemon"], 13: ["x"],
                            14: ["x", "bridge", "--adapter"], 16: ["x", "bridge", "--adapter"], 18: ["x", "agent-host"]})
    asked: list[int] = []
    monkeypatch.setattr(health.py, "_terminate", lambda pid, name: (asked.append(pid), True)[1])
    theirs = health.py._stop_orphan_bridges(procs)
    assert asked == [11, 16, 18]
    assert health.rust("orphans", procs=health.sent(procs), me=os.getpid(), cmdlines=sent) == plain(theirs)


def test_the_same_idle_plugin_workers_are_picked(health):
    me = os.getpid()
    old = NOW - 3600
    procs = _table(
        _proc(me, "UEFN-Ducky.exe", 1, 400, 1.0),
        _proc(11, "python.exe", me, 725, old), _proc(12, "python.exe", me, 725, old),  # idle Forge workers
        _proc(13, "blender.exe", 11, 300, old),  # a worker's own child: stopped with it
        _proc(14, "python.exe", me, 725, old),  # busy: in a call
        _proc(15, "python.exe", me, 200, old),  # a child that runs no plugin
        _proc(16, "claude.exe", me, 300, old),  # an agent CLI whose arguments name the plugin folder
        _proc(17, "python.exe", 999, 725, old),  # a plugin worker that isn't Ducky's
        _proc(18, "python.exe", me, 725, NOW - 120),  # young and never called: kept
        _proc(19, "python.exe", 12, 100, old),  # a worker under a worker
        _proc(20, "UEFN-Ducky-Bridge.exe", 1, 100, old), _proc(21, "node.exe", 20, 150, old),  # a tool server's worker
        _proc(22, "python.exe", 21, 50, 0.0),  # start unknown
        _proc(23, "cmd.exe", me, 5, old), _proc(24, "msedgewebview2.exe", me, 5, old), _proc(25, "conhost.exe", me, 5, old),
    )
    sent = health.cmdlines({11: ["python.exe", _FORGE], 12: ["python.exe", _FORGE], 13: ["blender.exe", "-b"], 14: ["python.exe", _FORGE],
                            15: ["python.exe", "C:/tools/script.py"], 16: ["claude.exe", "--add-dir", _FORGE], 17: ["python.exe", _FORGE],
                            18: ["python.exe", _FORGE], 19: ["python.exe", _FORGE], 21: ["node", "D:/uefn_plugins/atlas/run.js"],
                            22: ["python", "D:/uefn_plugins/atlas/w.py"], 23: ["cmd.exe", "/c", _FORGE], 24: ["x", _FORGE], 25: ["x", _FORGE]})
    cpu = {14: 37.0, 13: 0.5}
    picked = []
    for last_used in ({}, {"forge": NOW - 120}, {"forge": NOW - 2400}, {"Forge-Tools": NOW - 601, "atlas": NOW - 599.5}):
        theirs = health.py.idle_plugin_workers(procs, cpu, now=NOW, last_used=last_used)
        ours = health.rust("idle_workers", procs=health.sent(procs), cpu={str(pid): used for pid, used in cpu.items()}, me=me,
                           now=NOW, last_used=last_used, cmdlines=sent)
        assert ours == plain(theirs)
        picked.append([(row["pid"], row["plugin"]) for row in ours])
    assert picked == [[(11, "forge"), (12, "forge"), (21, "atlas")], [(21, "atlas")], [(11, "forge"), (12, "forge"), (21, "atlas")],
                      [(11, "forge"), (12, "forge")]]
    rows = [{"plugin": "forge-3d"}, {"plugin": "atlas"}, {"plugin": "forge-3d"}, {"plugin": "city_builder"}, {"plugin": "x9y"}]
    assert health.rust("workers_text", rows=rows) == health.py._workers_text(rows)


def test_wait_percentiles_and_fallbacks_read_the_same(health, monkeypatch, tmp_path):
    for samples in ([], [3.0], [float(v) for v in range(100, 0, -1)], [0.04, 0.05, 0.15, 12.25, 7.349], [1.0] * 19 + [50.0]):
        assert health.rust("core_wait", samples=samples) == health.py.core_wait_stats(samples)
    monkeypatch.setattr(health.py, "_app_dir", lambda: tmp_path)
    rows = [{"at": NOW - 7200, "slot": "0", "reason": "old"}, {"at": NOW - 60, "reason": "one"}, "junk", {"at": str(NOW - 5), "reason": 7},
            {"at": "soon", "reason": "never"}, {"reason": "no time"}]
    files = [{"count": 91, "recent": rows}, {"count": 3, "last_at": NOW - 30, "reason": "r"}, {"count": 3.9, "last_at": NOW - 3601},
             {"count": "12", "recent": rows[:1]}, {"count": "many", "recent": rows[:2]}, {"count": [1], "recent": "text"}, {"count": True},
             {}, [1, 2], "text", None, {"recent": [{"at": NOW, "reason": ""}], "reason": "outer"}]
    for data in files:
        (tmp_path / "shared_mcp_fallbacks.json").write_text(json.dumps(data), encoding="utf-8")
        assert health.rust("tool_fallbacks", data=data, now=NOW) == health.py.tool_fallbacks(NOW), data
    (tmp_path / "shared_mcp_fallbacks.json").unlink()
    assert health.rust("tool_fallbacks", now=NOW) == health.py.tool_fallbacks(NOW) == {"count": 0, "last_hour": 0, "reason": ""}


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    return "number" if isinstance(value, (int, float)) else type(value).__name__


def _same_shape(ours: Any, theirs: Any, where: str) -> None:
    """The same keys with the same kinds of values; a part that has no value now may be
    empty on either side."""
    if ours is None or theirs is None:
        return
    assert _kind(ours) == _kind(theirs), f"{where}: {ours!r} / {theirs!r}"
    if isinstance(theirs, dict):
        assert list(ours) == list(theirs), where
        for key in theirs:
            _same_shape(ours[key], theirs[key], f"{where}.{key}")
    elif isinstance(theirs, list) and ours and theirs:
        _same_shape(ours[0], theirs[0], f"{where}[0]")


def test_a_report_of_this_pc_has_the_same_parts_and_reads_the_same_files(health, rust_store, tmp_path, monkeypatch):
    import sys
    import time

    import psutil

    base = health.py._app_dir()
    assert tmp_path in base.parents and (base / "ducky.db").is_file()
    for name, size in (("chats/p/c1/messages.json", 300_000), ("chats/p/c2/a/b/big.bin", 2_500_000), ("cache/web_video/v.mp4", 1_234_567),
                       ("logs/app.log", 52_429), ("sandboxes/chat-1/work/x.txt", 104_858), ("perf/empty.jsonl", 0)):
        (base / name).parent.mkdir(parents=True, exist_ok=True)
        (base / name).write_bytes(b"x" * size)
    for folder in ("sandboxes/chat-1/home", "sandboxes/chat-1/logs", "sandboxes/chat-2", "sandboxes/duckyos-deploy/work", "sandboxes/bad name/work"):
        (base / folder).mkdir(parents=True, exist_ok=True)
    (base / "sandboxes/chat-2/.last_used").write_text("1", encoding="utf-8")
    (base / "sandboxes/not-a-folder").write_text("1", encoding="utf-8")
    real = time.time()
    rows = [{"at": real - 7200, "reason": "old"}, {"at": real - 60, "reason": "it did not answer in 10 s (pid 7)"}]
    (base / "shared_mcp_fallbacks.json").write_text(json.dumps({"count": 91, "recent": rows}), encoding="utf-8")

    status, reply = rust_store.call("ducky_health_snapshot")
    assert status == 200 and reply["ok"] is True, reply
    ours = reply["result"]
    monkeypatch.setattr(health.py, "_storage_seen", {"at": 0.0, "folders": {}})
    theirs = plain(health.py.snapshot())

    folders = ("chats_mb", "sandboxes_mb", "cache_mb", "perf_mb", "logs_mb", "tools_mb")
    assert {key: ours["storage"][key] for key in folders} == {key: theirs["storage"][key] for key in folders}
    assert ours["storage"]["chats_mb"] == 2.7 and ours["storage"]["tools_mb"] == 0.0 and ours["storage"]["ducky_db_mb"] >= 0
    assert ours["tool_fallbacks"] == theirs["tool_fallbacks"] == {"count": 91, "last_hour": 1, "reason": rows[1]["reason"]}
    assert ours["sandboxes"]["count"] == theirs["sandboxes"]["count"] == 2
    assert any("could not join Ducky's tool server" in found["what"] for found in ours["findings"])

    assert list(ours) == list(theirs)
    for part in ("app", "pc", "storage", "ducky_total", "tool_fallbacks", "helpers", "other_ducky_processes", "uefn", "findings"):
        _same_shape(ours[part], theirs[part], part)
    for part in ("sandboxes", "terminals", "running_agents"):
        if "error" not in theirs[part]:
            _same_shape(ours[part], theirs[part], part)
    # The numbers are this PC's and this backend's own.
    assert ours["app"]["pid"] == rust_store.proc.pid and ours["app"]["memory_mb"] > 1
    assert ours["app"]["threads"] > 1 or sys.platform == "darwin"  # ps has no thread count
    assert ours["app"]["inflight_total"] == 1 and ours["app"]["inflight"][0]["method"] == "ducky_health_snapshot"
    assert 1 <= ours["pc"]["cpus"] <= psutil.cpu_count() and ours["pc"]["memory_total_gb"] == theirs["pc"]["memory_total_gb"]
    assert abs(ours["pc"]["memory_used_percent"] - theirs["pc"]["memory_used_percent"]) < 10
    assert abs(ours["pc"]["processes"] - theirs["pc"]["processes"]) < 60 and ours["pc"]["top_apps"]
    assert ours["ducky_total"]["parts"]["app"] > 1 and ours["ducky_total"]["idle"] is False
    assert all(list(found) == ["severity", "what", "why", "fix"] for found in ours["findings"])
    # A second report, with the perf part asked for: the folders are not walked again.
    (base / "chats/p/more.bin").write_bytes(b"x" * 3_000_000)
    again = rust_store.call("ducky_health_snapshot", True)[1]["result"]
    assert again["storage"]["chats_mb"] == 2.7 and "perf" in again and list(again)[-1] == "findings"


def test_self_care_frees_the_same_and_refuses_the_same(health, rust_store):
    from backend.tools.core.web_video import cache_dir

    def care(action: Any) -> Any:
        status, reply = rust_store.call("ducky_self_care_run", action)
        assert status == 200 and reply["ok"] is True, reply
        return reply["result"]

    for wrong in ("format_disk", "", None, " Trim_Caches"):
        assert care(wrong) == health.py.self_care(str(wrong or ""))
    answers = []
    for run in (lambda: health.py.self_care(" trim_caches "), lambda: care(" trim_caches ")):
        folder = cache_dir()
        (folder / "a.mp4").write_bytes(b"x" * 1_500_000)
        (folder / "b.part").write_bytes(b"x" * 700_000)
        (folder / "kept").mkdir(exist_ok=True)
        answers.append(run())
        assert [p.name for p in folder.iterdir()] == ["kept"]
    theirs, ours = answers
    assert ours["freed_mb"] == theirs["freed_mb"] == 2.1 and ours["action"] == theirs["action"] == "trim_caches"
    assert list(ours) == list(theirs) and ours["app_memory_mb"]["before"] > 1 and ours["app_memory_mb"]["after"] > 1
    assert list(care("collect_garbage")) == list(health.py.self_care("collect_garbage"))
    # What the sandbox, terminal and UEFN services do is theirs to answer: asked here, it says so and does nothing.
    for theirs_only in ("stop_sandbox", "close_idle_terminals", "prune_sandboxes", "free_uefn_memory"):
        assert care(theirs_only) == {"ok": False, "error": f"{theirs_only} is not answered by this backend yet."}
