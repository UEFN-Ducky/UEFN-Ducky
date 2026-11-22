"""Crash dumps are read down to one line: what crashed, where, and in which process."""

from __future__ import annotations

import os
import struct
import sys
import threading
import time
from pathlib import Path

from frontend import boot_report, minidump


def build_dump(
    modules: list[tuple[int, int, str]],
    code: int | None = None,
    address: int = 0,
    params: tuple[int, ...] = (),
    ptype: str = "",
) -> bytes:
    """A small but real minidump: header, directory, module list, exception, Crashpad info."""
    blob = bytearray(32)
    count = 1 + (code is not None) + bool(ptype)
    dir_rva = len(blob)
    blob.extend(bytes(12 * count))
    entries: list[tuple[int, int, int]] = []

    def utf8(text: str) -> int:
        rva = len(blob)
        raw = text.encode("utf-8")
        blob.extend(struct.pack("<I", len(raw)) + raw)
        return rva

    name_rvas = []
    for _base, _size, name in modules:
        raw = name.encode("utf-16-le")
        name_rvas.append(len(blob))
        blob.extend(struct.pack("<I", len(raw)) + raw)
    rva = len(blob)
    blob.extend(struct.pack("<I", len(modules)))
    for (base, size, _name), name_rva in zip(modules, name_rvas):
        blob.extend(struct.pack("<QIIII", base, size, 0, 0, name_rva) + bytes(108 - 24))
    entries.append((4, len(blob) - rva, rva))
    if code is not None:
        rva = len(blob)
        blob.extend(struct.pack("<IIIIQQII", 1234, 0, code, 0, 0, address, len(params), 0))
        blob.extend(b"".join(struct.pack("<Q", p) for p in params) + bytes(8 * (15 - len(params))) + bytes(8))
        entries.append((6, len(blob) - rva, rva))
    if ptype:
        key, value = utf8("ptype"), utf8(ptype)
        table = len(blob)
        blob.extend(struct.pack("<III", 1, key, value))
        rva = len(blob)
        blob.extend(struct.pack("<I", 1) + bytes(32) + struct.pack("<IIII", 12, table, 0, 0))
        entries.append((0x43500001, len(blob) - rva, rva))
    struct.pack_into("<4sIIIIIQ", blob, 0, b"MDMP", 0xA793, count, dir_rva, 0, 0, 0)
    for i, entry in enumerate(entries):
        struct.pack_into("<III", blob, dir_rva + 12 * i, *entry)
    return bytes(blob)


_MODULES = [
    (0x7FF600000000, 0x200000, r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application\msedgewebview2.exe"),
    (0x7FFA10000000, 0x400000, r"C:\Users\someone\AppData\Local\Temp\KERNELBASE.dll"),
]


def _oom_dump() -> bytes:
    return build_dump(_MODULES, 0xE0000008, 0x7FFA10000000 + 0xC483A, (2 * 1024**2,), ptype="browser")


def test_a_dump_names_the_exception_the_module_and_the_process() -> None:
    info = minidump.parse(_oom_dump())
    assert info["code_name"] == "OUT_OF_MEMORY"
    assert info["module"] == "KERNELBASE.dll" and info["offset"] == 0xC483A
    assert info["main_module"] == "msedgewebview2.exe" and info["process_type"] == "browser"
    text = minidump.summary(info)
    assert text == "WebView2 browser process: OUT_OF_MEMORY at KERNELBASE.dll+0xc483a (2 MB allocation failed)"
    assert "someone" not in text and "Users" not in text
    assert minidump.cause(info) == "The chat window ran out of memory (WebView2 browser process)."


def test_an_access_violation_says_what_it_touched() -> None:
    info = minidump.parse(build_dump(_MODULES[:1], 0xC0000005, 0x7FF600000000 + 0x1A2B, (0, 0)))
    assert minidump.summary(info) == "WebView2 process: ACCESS_VIOLATION at msedgewebview2.exe+0x1a2b (reading 0x0)"
    assert minidump.cause(info, app=True) == "Ducky's app process crashed: ACCESS_VIOLATION in msedgewebview2.exe+0x1a2b."


def test_a_truncated_or_foreign_file_is_unreadable_not_a_crash(tmp_path: Path) -> None:
    whole = _oom_dump()
    for data in (whole[:60], whole[:200], b"", b"PK\x03\x04" + bytes(64)):
        info = minidump.parse(data)
        assert info.get("unreadable") is True
        assert minidump.summary(info).startswith("unreadable dump")
        assert minidump.cause(info) == ""
    assert minidump.read(tmp_path / "missing.dmp")["unreadable"] is True


def test_only_the_three_newest_dumps_are_kept(tmp_path: Path) -> None:
    tmp_path = tmp_path / "reports"
    tmp_path.mkdir()
    now = time.time()
    for i in range(5):
        path = tmp_path / f"d{i}.dmp"
        path.write_bytes(b"x")
        os.utime(path, (now - 100 + i, now - 100 + i))
    (tmp_path / "keep.txt").write_text("not a dump")
    minidump.prune(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d2.dmp", "d3.dmp", "d4.dmp", "keep.txt"]
    assert [p.name for p in minidump.dumps_since(tmp_path, now - 97.5)] == ["d3.dmp", "d4.dmp"]


def test_the_crash_report_names_the_webview_dump_written_since_the_last_start(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")  # WebView2's dump folders
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    started = time.time() - 600
    folder = boot_report._webview_dumps_dir()
    folder.mkdir(parents=True)
    old = folder / "old.dmp"
    old.write_bytes(_oom_dump())
    os.utime(old, (started - 60, started - 60))  # an earlier launch's crash: not this one
    (folder / "new.dmp").write_bytes(_oom_dump())
    dumps = boot_report.crash_dumps(started)
    assert [d["name"] for d in dumps] == ["new.dmp"]
    previous = {"state": "ok", "started": started, "version": "1.2.428", "stages": [["window_shown", 4.0]]}
    body = boot_report.build_report(previous, "", dumps=dumps, ended=dumps[-1]["at"])
    assert body is not None
    log = body["error_log"]
    assert "Crash dump new.dmp: WebView2 browser process: OUT_OF_MEMORY at KERNELBASE.dll+0xc483a" in log
    assert boot_report.NO_TRACE not in log
    assert "The chat window ran out of memory (WebView2 browser process)." in boot_report.why_summary(body)
    assert old.read_bytes()[:4] == b"MDMP"  # reading never changes a dump


def test_the_app_dump_folder_is_read_too(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")  # the app's .dmp folder
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    boot_report._dir().mkdir(parents=True)
    (boot_report._dir() / "ducky-20261025-101233.dmp").write_bytes(
        build_dump(_MODULES[:1], 0xC0000005, 0x7FF600000010, (1, 8))
    )
    (dump,) = boot_report.crash_dumps(0.0)
    assert dump["name"] == "ducky-20261025-101233.dmp"
    assert dump["cause"].startswith("Ducky's app process crashed: ACCESS_VIOLATION")


# ── Memory heartbeat ─────────────────────────────────────────────────────────


def test_a_heartbeat_at_low_memory_is_the_crash_reports_cause() -> None:
    ended = 1_000_000.0
    previous = {
        "state": "ok",
        "started": ended - 3600,
        "stages": [["window_shown", 4.0]],
        "heartbeat": {"at": ended - 20, "free_gb": 0.2, "total_gb": 63.0, "commit_gb": 70.0,
                      "commit_limit_gb": 72.0, "app_gb": 3.4, "webview_gb": 2.1, "agents": 16, "threads": 539},
    }
    body = boot_report.build_report(previous, "", ended=ended)
    assert body is not None
    assert ("Last heartbeat 20 s before the end: 0.2 GB free of 63 GB, commit 70 of 72 GB, app 3.4 GB, "
            "WebView2 2.1 GB, 16 agents, 539 threads.") in body["error_log"]
    assert "The PC had 0.2 GB free 20 s before Ducky closed." in boot_report.why_summary(body)


def test_a_heartbeat_with_plenty_of_memory_is_no_cause_and_no_end_time_is_bounded() -> None:
    previous = {"state": "ok", "started": 1.0, "heartbeat": {"at": 100.0, "free_gb": 12.0, "total_gb": 63.0}}
    body = boot_report.build_report(previous, "")
    assert body is not None
    assert "Last heartbeat at most 30 s before the end: 12.0 GB free of 63 GB." in body["error_log"]
    assert "The PC had" not in boot_report.why_summary(body)


def test_heartbeats_write_boot_json_and_never_more_than_every_30_s(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))  # the same folder on Linux
    boot_report._dir().mkdir(parents=True)
    monkeypatch.setattr(boot_report, "_state", {"state": "ok", "started": time.time()})
    monkeypatch.setattr(boot_report, "_heartbeat", {"thread": None})
    sleeps: list[float] = []
    real_sleep = time.sleep

    def fake_sleep(seconds: float) -> None:
        if threading.current_thread().name != "boot-heartbeat":  # another test's thread, still polling
            return real_sleep(seconds)
        sleeps.append(seconds)
        boot_report._state["state"] = "closed"  # one beat, then a clean exit ends the loop

    monkeypatch.setattr(boot_report.time, "sleep", fake_sleep)
    boot_report.start_heartbeat(lambda: 3, every_s=1.0)
    boot_report._heartbeat["thread"].join(5)
    beat = boot_report._read_json(boot_report._boot_path())["heartbeat"]
    assert beat["agents"] == 3 and beat["at"] > 0
    if os.name == "nt":
        assert beat["free_gb"] > 0 and beat["total_gb"] >= beat["free_gb"]
    assert sleeps == [boot_report.HEARTBEAT_S]


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
