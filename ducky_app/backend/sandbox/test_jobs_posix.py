"""POSIX child control: own session, group kill, death signal, memory cap (no Job Objects)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from backend.sandbox import jobs
from backend.util import proc_table

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")

# Top starts a middle process that starts a sleeper; each writes its pid to the folder.
_TREE = textwrap.dedent("""
    import os, subprocess, sys, time
    out, depth = sys.argv[1], int(sys.argv[2])
    open(os.path.join(out, str(os.getpid())), "w").close()
    if depth < 3:
        subprocess.Popen([sys.executable, __file__, out, str(depth + 1)])
    time.sleep(60)
""")


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            stat = fh.read()
    except OSError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    return stat[stat.rfind(b")") + 2:][:1] != b"Z"


def _wait_for(pred, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _tree_script(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "tree.py"
    script.write_text(_TREE)
    out = tmp_path / "pids"
    out.mkdir()
    return script, out


def _pids(out: Path) -> list[int]:
    return [int(p.name) for p in out.iterdir()]


def test_launched_stop_kills_three_level_tree(tmp_path):
    script, out = _tree_script(tmp_path)
    launched = jobs.Launched([sys.executable, str(script), str(out), "1"], cwd=str(tmp_path),
                             env=dict(os.environ), log=tmp_path / "log.txt", memory_mb=0)
    assert _wait_for(lambda: len(_pids(out)) == 3)
    pids = _pids(out)
    assert os.getpgid(launched.pid) == launched.pid  # own session
    if sys.platform.startswith("linux"):
        assert sorted(launched.tree()) == sorted(pids)
    launched.stop(timeout_s=5)
    assert _wait_for(lambda: not any(_alive(p) for p in pids)), [p for p in pids if _alive(p)]


def test_proc_exec_stop_kills_three_level_tree(tmp_path):
    from backend.agent.coding_agents import proc_exec

    script, out = _tree_script(tmp_path)
    cancel = threading.Event()
    result: dict = {}

    def run() -> None:
        result["r"] = proc_exec.run_streaming_process(
            argv=[sys.executable, str(script), str(out), "1"], cwd=str(tmp_path), env_extra={},
            conv_id="posix-tree", on_line=lambda s: None, timeout_s=0, cancel=cancel)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert _wait_for(lambda: len(_pids(out)) == 3)
    pids = _pids(out)
    cancel.set()
    t.join(timeout=20)
    assert result["r"].cancelled
    assert _wait_for(lambda: not any(_alive(p) for p in pids)), [p for p in pids if _alive(p)]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux")
def test_child_dies_with_parent_not_with_spawning_thread(tmp_path):
    # The parent starts the child from a thread that exits at once, then waits: the child must
    # survive the thread, then die (SIGKILL) when the parent process itself dies.
    ducky_app = Path(jobs.__file__).resolve().parents[2]
    parent = textwrap.dedent(f"""
        import sys, threading, time
        sys.path.insert(0, {str(ducky_app)!r})
        from backend.sandbox import jobs
        procs = []
        t = threading.Thread(target=lambda: procs.append(jobs.posix_popen([sys.executable, "-c", "import time; time.sleep(60)"])))
        t.start(); t.join()
        print(procs[0].pid, flush=True)
        time.sleep(1.0)
        print("alive" if procs[0].poll() is None else "dead", flush=True)
        sys.stdin.read()
    """)
    proc = subprocess.Popen([sys.executable, "-c", parent], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child = int(proc.stdout.readline())
    assert proc.stdout.readline().strip() == "alive"
    proc.kill()
    proc.wait(timeout=5)
    assert _wait_for(lambda: not _alive(child)), "child outlived Ducky"


def test_memory_cap_sets_rlimit_as():
    proc = jobs.posix_popen([sys.executable, "-c", "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"],
                            memory_mb=2048, stdout=subprocess.PIPE, text=True)
    out, _ = proc.communicate(timeout=10)
    assert int(out) == 2048 * 1024 * 1024


def test_group_reads_pgrp_from_proc_stat(tmp_path):
    for pid, stat in {
        "10": "10 (sh) S 1 10 10 0",
        "11": "11 (we (ird) name) S 10 10 10 0",  # comm with spaces and parens
        "12": "12 (other) S 1 12 12 0",
        "13": "13 (dead) Z 10 10 10 0",  # zombies are gone already
    }.items():
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "stat").write_text(stat)
    (tmp_path / "self").mkdir()
    assert sorted(proc_table.group(10, proc_root=str(tmp_path))) == [10, 11]
