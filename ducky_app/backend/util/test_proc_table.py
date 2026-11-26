"""proc_table: the whole PC's processes in one call, matching what psutil reports."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil
import pytest

from backend.util import proc_table


def test_the_table_matches_psutil_for_this_process():
    table = proc_table.table()
    me = table[os.getpid()]
    p = psutil.Process()
    assert me.name.lower() == p.name().lower()
    assert me.ppid == p.ppid()
    assert me.threads >= 1 and me.rss > 0 and me.private > 0
    assert abs(me.started - p.create_time()) < 2
    assert len(table) > 10


def test_mac_table_reads_one_ps_listing(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: pytest.fail("psutil per-process scan"))
    text = ("    1     0  12000   1:02.50 Thu Nov 26 09:01:02 2026     /sbin/launchd\n"
            "  501     1   2048 1-02:00:01 Wed Nov  4 10:00:00 2026     /Applications/UEFN Ducky.app/Contents/MacOS/UEFN Ducky\n"
            "garbage\n")
    table = proc_table._mac_table(text)
    assert set(table) == {1, 501}
    app = table[501]
    assert (app.ppid, app.name, app.rss, app.private) == (1, "UEFN Ducky", 2048 * 1024, 2048 * 1024)
    assert app.cpu_s == 86400 + 7201 and table[1].cpu_s == 62.5
    assert app.started == time.mktime((2026, 11, 4, 10, 0, 0, 0, 0, -1))


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach thread ports")
def test_mac_thread_times_are_keyed_by_the_native_thread_id():
    import threading

    assert threading.get_native_id() in proc_table.mac_thread_times()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin") and not sys.platform.startswith("linux"),
                    reason="one NtQuerySystemInformation call / one /proc read / one ps")
def test_one_table_is_fast():
    # Best of five: a busy test machine can stall any single call; the per-process psutil
    # scan this replaces took minutes on a busy PC, the table takes well under a second.
    times = []
    for _ in range(5):
        start = time.perf_counter()
        proc_table.table()
        times.append(time.perf_counter() - start)
    assert min(times) < 1.0, times


@pytest.mark.skipif(os.name == "nt", reason="Linux /proc reader")
def test_linux_table_reads_proc_without_psutil(tmp_path, monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: pytest.fail("psutil per-process scan"))
    (tmp_path / "stat").write_text("cpu 1 2 3\nbtime 1000\n")
    tick, page = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")

    def proc(pid, comm, ppid, argv0=""):
        d = tmp_path / str(pid)
        d.mkdir()
        rest = ["S", str(ppid)] + ["0"] * 9 + [str(3 * tick), str(tick)] + ["0"] * 4 + ["7", "0", str(50 * tick)]
        (d / "stat").write_text(f"{pid} ({comm}) {' '.join(rest)} 0 0\n")
        (d / "statm").write_text("1000 300 100 1 0 50 0\n")
        (d / "cmdline").write_bytes(argv0.encode() + b"\0--flag\0")

    proc(1, "systemd", 0)
    proc(42, "UEFN-Ducky-Brid", 1, "/opt/ducky/UEFN-Ducky-Bridge")  # comm is cut at 15 chars
    proc(43, "odd) name", 42)
    (tmp_path / "self").mkdir()  # not a pid
    table = proc_table._linux_table(str(tmp_path))
    assert sorted(table) == [1, 42, 43]
    bridge = table[42]
    assert bridge.name == "UEFN-Ducky-Bridge" and bridge.ppid == 1 and bridge.threads == 7
    assert bridge.rss == 300 * page and bridge.private == 200 * page
    assert bridge.cpu_s == 4.0 and bridge.started == 1050.0
    assert table[43].name == "odd) name" and table[43].ppid == 42
    assert proc_table.tree(table, 42) == [42, 43]


def test_mac_shaped_memory_info_gives_rss_as_private_never_vms(monkeypatch):
    from types import SimpleNamespace

    mem = SimpleNamespace(rss=412 * 2**20, vms=398 * 2**30, pfaults=1, pageins=0)  # macOS: no private/shared
    times = SimpleNamespace(user=1.0, system=0.5)
    proc = SimpleNamespace(pid=7, info={"ppid": 1, "name": "UEFN-Ducky", "memory_info": mem, "num_threads": 3,
                                        "cpu_times": times, "create_time": 10.0})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: [proc])
    row = proc_table._psutil_table()[7]
    assert row.rss == row.private == 412 * 2**20


def test_ancestor_finds_the_launcher_above_a_hand_off_stub():
    def p(pid, ppid, name, started):
        return proc_table.Proc(pid, ppid, name, 0, 0, 1, 1, 0.0, started)

    procs = {1: p(1, 0, "explorer.exe", 1.0), 10: p(10, 1, "Ducky-Launcher.exe", 2.0),
             20: p(20, 10, "UEFN-Ducky.exe", 3.0), 30: p(30, 20, "UEFN-Ducky.exe", 4.0)}
    assert proc_table.ancestor(procs, 30, "ducky-launcher.exe").pid == 10  # stub in between
    assert proc_table.ancestor(procs, 20, "Ducky-Launcher.exe").pid == 10  # direct parent
    procs[10] = p(10, 1, "Ducky-Launcher.exe", 9.0)  # pid reused by a newer process
    assert proc_table.ancestor(procs, 30, "Ducky-Launcher.exe") is None


def test_cpu_and_tree_see_a_busy_child_and_its_child():
    code = ("import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
            "end = time.time() + 30\nwhile time.time() < end: pass")
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        deadline = time.time() + 15
        while time.time() < deadline and len(proc_table.tree(proc_table.table(), child.pid)) < 2:
            time.sleep(0.2)
        procs, cpu = proc_table.sample(0.5)
        family = proc_table.tree(procs, child.pid)
        # A venv's python.exe is a launcher that starts the real interpreter as its child,
        # so the family is 2 or 4 processes.
        assert family[0] == child.pid and len(family) >= 2
        assert sum(cpu.get(pid, 0.0) for pid in family) > 5.0
        assert not proc_table.orphan(procs, child.pid)
    finally:
        for pid in proc_table.tree(proc_table.table(), child.pid)[::-1]:
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass
        child.wait(10)
