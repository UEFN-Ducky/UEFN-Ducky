"""Health probes on macOS: no /proc, no Win32 calls; psutil (or nothing) instead."""

from __future__ import annotations

import builtins
import ctypes
import os
import sys

import pytest

from frontend import busy_pc, gpu_memory, heap_trim, low_memory, memory_report


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    real_open = builtins.open

    def no_proc(path, *a, **k):
        if str(path).startswith("/proc"):
            pytest.fail(f"read {path} on macOS")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", no_proc)
    if hasattr(ctypes, "WinDLL"):
        monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: pytest.fail("Win32 call on macOS"))
    if hasattr(ctypes, "windll"):
        monkeypatch.setattr(ctypes, "windll", None)


def test_heap_trim_is_a_noop(mac, monkeypatch):
    monkeypatch.setattr(heap_trim, "_malloc_trim", lambda: pytest.fail("malloc_trim on macOS"))
    assert heap_trim.trim()["skipped"]
    assert heap_trim.maybe_trim(now=1e6) is False
    assert heap_trim.heap_free_mb() == 0.0


def test_gpu_memory_is_unknown(mac):
    assert gpu_memory.read() is None


def test_busy_pc_leaves_priorities_alone(mac):
    assert busy_pc.raise_window_priority() == 0


def test_low_memory_has_no_psi_and_no_launch_box(mac, monkeypatch):
    monkeypatch.delenv("DUCKY_SKIP_LOW_MEMORY_CHECK", raising=False)
    assert low_memory._stalled() is False
    assert low_memory.check_at_launch() is True  # no message box off Windows


def test_memory_report_uses_psutil(mac):
    psutil = pytest.importorskip("psutil")
    assert memory_report.mallinfo() is None
    assert memory_report._native() == {}
    got = memory_report._process()
    assert got["pid"] == os.getpid() and got["rss_mb"] > 0
    if hasattr(psutil.Process().memory_full_info(), "uss"):
        assert got["private_mb"] > 0 and "error" not in got
