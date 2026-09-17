import ctypes
import os
import sys
import threading
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import proc_exec


def test_large_output_is_delivered_but_only_bounded_diagnostics_are_retained(tmp_path):
    script = tmp_path / "large_output.py"
    script.write_text(
        "import sys\n"
        "print('x' * 1000000 + 'END')\n"
        "sys.stderr.write('y' * 1000000 + 'ERROR_END')\n",
        encoding="utf-8",
    )
    lengths = []
    result = proc_exec.run_streaming_process(
        argv=[sys.executable, str(script)], cwd=str(tmp_path), env_extra={},
        conv_id="test-large-output", on_line=lambda line: lengths.append(len(line)), timeout_s=10,
    )
    assert result.returncode == 0
    assert lengths == [1000003]
    assert len(result.raw_tail) == 4000
    assert result.raw_tail.endswith("END")
    assert len(result.stderr_tail) == 8000
    assert result.stderr_tail.endswith("ERROR_END")
    assert not hasattr(result, "_raw_ring")


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree cleanup")
def test_tree_kill_targets_only_the_owned_pid_and_falls_back(monkeypatch):
    calls = []
    killed = []
    proc = SimpleNamespace(pid=123, poll=lambda: None, kill=lambda: killed.append(True))
    monkeypatch.setattr(proc_exec.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    proc_exec._terminate_process_tree(proc)
    assert calls[0][0][1:] == ["/F", "/T", "/PID", "123"]
    assert calls[0][1]["timeout"] == 5
    assert killed == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree integration")
def test_cancelling_cli_also_exits_its_mcp_child(tmp_path):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    script = tmp_path / "cli_parent.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    cancel = threading.Event()
    handles = []

    def child_started(line):
        handle = kernel.OpenProcess(0x00100000 | 0x0001, False, int(line))
        assert handle, ctypes.get_last_error()
        handles.append(handle)
        cancel.set()

    try:
        result = proc_exec.run_streaming_process(
            argv=[sys.executable, str(script)], cwd=str(tmp_path), env_extra={},
            conv_id="test-owned-tree", on_line=child_started, timeout_s=10, cancel=cancel,
        )
        assert result.cancelled
        assert len(handles) == 1
        assert kernel.WaitForSingleObject(handles[0], 2000) == 0
    finally:
        for handle in handles:
            # The handle was opened only for this fixture's synthetic child.
            if kernel.WaitForSingleObject(handle, 0) != 0:
                kernel.TerminateProcess(handle, 1)
            kernel.CloseHandle(handle)
