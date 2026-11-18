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


@pytest.mark.skipif(sys.platform != "win32", reason="one NtQuerySystemInformation call")
def test_one_table_is_fast():
    # Best of five: a busy test machine can stall any single call; the per-process psutil
    # scan this replaces took minutes on a busy PC, the table takes well under a second.
    times = []
    for _ in range(5):
        start = time.perf_counter()
        proc_table.table()
        times.append(time.perf_counter() - start)
    assert min(times) < 1.0, times


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
