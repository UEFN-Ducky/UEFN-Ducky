"""Linux memory readers: /proc/self/status, /proc/meminfo, /proc/pressure/memory, malloc_trim."""

from __future__ import annotations

import sys

import pytest

from frontend import heap_trim, low_memory, memory_report

_LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="glibc / Linux /proc")


def test_proc_status_reads_rss_from_a_fake_file(tmp_path):
    status = tmp_path / "status"
    status.write_text("Name:\tpython3\nVmHWM:\t  512000 kB\nVmRSS:\t  419840 kB\nRssAnon:\t  307200 kB\n"
                      "Threads:\t42\n")
    got = memory_report.proc_status(str(status))
    assert got["VmRSS"] == 419840 * 1024  # 410 MB
    assert memory_report._mb(got["VmRSS"]) == 410.0
    assert got["RssAnon"] == 307200 * 1024 and got["VmHWM"] == 512000 * 1024
    assert "Threads" not in got  # a count, not a size
    assert memory_report.proc_status(str(tmp_path / "missing")) == {}


def test_meminfo_reads_mem_available_from_a_fake_file(tmp_path):
    info = tmp_path / "meminfo"
    info.write_text("MemTotal:       16303052 kB\nMemFree:         1200000 kB\n"
                    "MemAvailable:    6501171 kB\nHugePages_Total:       0\n")
    got = low_memory.meminfo(str(info))
    assert round(got["MemAvailable"] / 1024**3, 1) == 6.2
    assert got["HugePages_Total"] == 0
    assert low_memory.meminfo(str(tmp_path / "missing")) == {}


def test_pressure_reads_avg10_and_warns_when_stalled(tmp_path, monkeypatch):
    psi = tmp_path / "memory"
    psi.write_text("some avg10=31.50 avg60=12.00 avg300=3.00 total=123\n"
                   "full avg10=12.25 avg60=5.00 avg300=1.00 total=45\n")
    assert low_memory.pressure(str(psi)) == {"some": 31.5, "full": 12.25}
    assert low_memory.pressure(str(tmp_path / "missing")) is None
    monkeypatch.setattr(low_memory.sys, "platform", "linux")
    monkeypatch.setattr(low_memory, "pressure", lambda: {"some": 31.5, "full": 12.25})
    assert low_memory._stalled()
    monkeypatch.setattr(low_memory, "pressure", lambda: {"some": 31.5, "full": 2.0})
    assert not low_memory._stalled()
    monkeypatch.setattr(low_memory, "pressure", lambda: None)
    assert not low_memory._stalled()


def test_free_memory_gb_uses_mem_available_on_linux(monkeypatch):
    from frontend.ui_web import admission

    monkeypatch.setattr(admission.sys, "platform", "linux")
    monkeypatch.setattr(low_memory, "meminfo", lambda: {"MemAvailable": int(6.2 * 1024**3)})
    assert round(admission.free_memory_gb(), 1) == 6.2


def test_heap_trim_on_linux_measures_rss_anon(monkeypatch):
    sizes = iter([500 * heap_trim.MB, 380 * heap_trim.MB])
    monkeypatch.setattr(heap_trim, "_private_bytes", lambda: next(sizes))
    calls = []
    got = heap_trim._trim_with(lambda: calls.append("malloc_trim"))
    assert calls == ["malloc_trim"] and got["freed_mb"] == 120.0


@_LINUX
def test_real_linux_trim_and_report():
    assert heap_trim.heap_free_mb() >= 0.0
    got = heap_trim.trim()
    assert "error" not in got and got["freed_mb"] >= 0.0
    proc = memory_report._process()
    assert proc["rss_mb"] > 0 and proc["private_mb"] > 0 and "error" not in proc
    assert "heap_free_mb" in memory_report._native()
