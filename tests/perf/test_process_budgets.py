"""Memory budgets per Ducky helper process: a release fails when one grows.

Each helper role starts from source in a fresh subprocess (isolated AppData, test ports),
is brought to ready, idles, and is measured as private bytes (``PrivateUsage``), the same
number Task Manager's "Commit size" shows. A 100-call soak then checks nothing piles up per call.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(sys.platform != "win32", reason="private bytes are read with the Win32 API"),
]

MB = 1024 * 1024
# Budgets (private bytes). Measured on main 2026-11-05, from source, no plugins installed:
# shared daemon 570 MB at ready, agent host 16 MB at ready (idle, no turn run yet).
# The daemon guard sits at today's size plus headroom so growth fails the release; the
# 1.5 GB idle target wants it at 250 MB (or merged), checked by the xfail test below.
DAEMON_BUDGET_MB = 650
DAEMON_TARGET_MB = 250
AGENT_HOST_BUDGET_MB = 300
SOAK_GROWTH_MB = 50
SOAK_CALLS = 100
IDLE_S = 20.0
READY_TIMEOUT_S = 120.0

_DUCKY_APP = Path(__file__).resolve().parents[2] / "ducky_app"


class _Counters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def private_mb(pid: int) -> float:
    """PrivateUsage of a process, in MB."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    try:
        counters = _Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return counters.PrivateUsage / MB
    finally:
        kernel32.CloseHandle(handle)


def _child_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DUCKY_")}
    for key in ("UEFN_DUCKY_SHARED_MCP_SLOT", "UEFN_DUCKY_PROCESS_ROLE", "UEFN_DUCKY_MCP_BRIDGE", "PYTEST_CURRENT_TEST"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def _spawn(args: list[str], env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    out = open(log, "wb")  # noqa: SIM115 — closed when the test ends the process
    proc = subprocess.Popen(
        [sys.executable, "-m", "frontend", *args],
        cwd=str(_DUCKY_APP),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    proc._budget_log = out  # type: ignore[attr-defined]
    return proc


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    log = getattr(proc, "_budget_log", None)
    if log is not None:
        log.close()


def _tail(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-2000:]
    except OSError:
        return ""


def _report(role: str, value: float, budget: float) -> None:
    print(f"{role}: {value:.0f} MB private (budget {budget:.0f} MB)")


# ── shared daemon (the agents' tool server) ──────────────────────────────────────────────


class _DaemonClient:
    def __init__(self, sock: Any) -> None:
        from frontend import shared_mcp_adapter as adapter

        self._adapter = adapter
        self._sock = sock
        self._next = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next += 1
        rid = self._next
        self._adapter._write_frame(self._sock, {"op": "mcp", "id": rid, "method": method, "params": params or {}})
        while True:
            frame = self._adapter._read_frame(self._sock)
            if frame.get("op") == "mcp" and frame.get("id") == rid:
                return frame

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


def _start_daemon(tmp_path: Path) -> tuple[subprocess.Popen[bytes], _DaemonClient]:
    from frontend import __version__
    from frontend import shared_mcp_adapter as adapter

    env = _child_env()
    proc = _spawn(["bridge", "--shared-daemon"], env, tmp_path / "daemon.log")
    deadline = time.monotonic() + READY_TIMEOUT_S
    os.environ["UEFN_DUCKY_APP_VERSION"] = str(__version__)
    try:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"shared daemon exited ({proc.returncode}):\n{_tail(tmp_path / 'daemon.log')}")
            state = adapter._read_state()
            sock = adapter._try_hello(state) if state else None
            if sock is not None:
                client = _DaemonClient(sock)
                listed = client.request("tools/list")  # waits for the plugin gate: ready
                assert "result" in listed, listed
                return proc, client
            time.sleep(0.2)
    finally:
        os.environ.pop("UEFN_DUCKY_APP_VERSION", None)
    _stop(proc)
    pytest.fail(f"shared daemon never answered:\n{_tail(tmp_path / 'daemon.log')}")


def test_shared_daemon_idle_budget(tmp_path: Path) -> None:
    proc, client = _start_daemon(tmp_path)
    try:
        time.sleep(IDLE_S)
        ready = private_mb(proc.pid)
        _report("shared daemon at ready", ready, DAEMON_BUDGET_MB)
        assert ready <= DAEMON_BUDGET_MB, f"daemon {ready:.0f} MB > {DAEMON_BUDGET_MB} MB"

        for i in range(SOAK_CALLS):  # fake tool calls: a missing tool, then a catalog read
            client.request("tools/call", {"name": "budget_probe_missing_tool", "arguments": {"i": i}})
            if i % 10 == 0:
                client.request("tools/list")
        time.sleep(2.0)
        after = private_mb(proc.pid)
        _report(f"shared daemon after {SOAK_CALLS} calls", after, ready + SOAK_GROWTH_MB)
        assert after <= ready + SOAK_GROWTH_MB, f"daemon grew {after - ready:.0f} MB over {SOAK_CALLS} calls"
    finally:
        client.close()
        _stop(proc)


@pytest.mark.xfail(reason="shared daemon is ~570 MB at ready; the 1.5 GB target wants 250 MB", strict=False)
def test_shared_daemon_meets_the_target(tmp_path: Path) -> None:
    proc, client = _start_daemon(tmp_path)
    try:
        time.sleep(IDLE_S)
        ready = private_mb(proc.pid)
        _report("shared daemon vs target", ready, DAEMON_TARGET_MB)
        assert ready <= DAEMON_TARGET_MB, f"daemon {ready:.0f} MB > {DAEMON_TARGET_MB} MB"
    finally:
        client.close()
        _stop(proc)


# ── agent host ───────────────────────────────────────────────────────────────────────────


def _start_agent_host(tmp_path: Path) -> tuple[subprocess.Popen[bytes], Any]:
    from frontend.agent_host import protocol
    from frontend.agent_host.supervisor import APP_OPS, KEY_ENV

    key = os.urandom(32)
    tag = f"budget-{os.urandom(3).hex()}"
    listener = protocol.listen(tag, key)
    env = _child_env(**{KEY_ENV: key.hex(), "UEFN_DUCKY_SHARED_MCP_SLOT": "97"})
    proc = _spawn(["agent-host", "--slot", "97", "--pipe", protocol.pipe_name(tag)], env, tmp_path / "host.log")
    box: dict[str, Any] = {}

    def _accept() -> None:
        try:
            box["conn"] = listener.accept()
        except Exception as exc:  # noqa: BLE001 — reported below
            box["error"] = exc

    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    t.join(READY_TIMEOUT_S)
    listener.close()
    if "conn" not in box:
        _stop(proc)
        pytest.fail(f"agent host never connected ({box.get('error')}):\n{_tail(tmp_path / 'host.log')}")
    channel = protocol.Channel(
        box["conn"], send_ops=APP_OPS, recv_ops=protocol.HOST_OPS, on_frame=lambda _f: None,
        on_closed=lambda _r: None, dead_after_s=protocol.DEAD_AFTER_S, alive=lambda: proc.poll() is None,
    ).start()
    return proc, channel


def test_agent_host_idle_budget(tmp_path: Path) -> None:
    proc, channel = _start_agent_host(tmp_path)
    try:
        time.sleep(IDLE_S)
        assert proc.poll() is None, _tail(tmp_path / "host.log")
        ready = private_mb(proc.pid)
        _report("agent host at ready", ready, AGENT_HOST_BUDGET_MB)
        assert ready <= AGENT_HOST_BUDGET_MB, f"agent host {ready:.0f} MB > {AGENT_HOST_BUDGET_MB} MB"

        for i in range(SOAK_CALLS):  # fake calls: cancels for chats this host never ran
            channel.send("cancel", conv_id=f"budget-probe-{i}", run_id=f"budget-run-{i}")
        time.sleep(3.0)
        assert proc.poll() is None, _tail(tmp_path / "host.log")
        after = private_mb(proc.pid)
        _report(f"agent host after {SOAK_CALLS} calls", after, ready + SOAK_GROWTH_MB)
        assert after <= ready + SOAK_GROWTH_MB, f"agent host grew {after - ready:.0f} MB over {SOAK_CALLS} calls"
    finally:
        try:
            channel.send("shutdown")
            time.sleep(0.5)
            channel.close("test done")
        except Exception:  # noqa: BLE001
            pass
        _stop(proc)


def test_private_mb_reads_this_process() -> None:
    assert 1 < private_mb(os.getpid()) < 64 * 1024
