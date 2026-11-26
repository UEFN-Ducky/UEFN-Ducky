"""macOS crash files: faulthandler into ~/Library/Logs/UEFN-Ducky, no minidumps."""

from __future__ import annotations

import faulthandler
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from frontend import app_paths, native_crash

APP_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(app_paths.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("DUCKY_TESTS_ISOLATED_APPDATA", raising=False)
    monkeypatch.setitem(native_crash._installed, "folder", None)
    monkeypatch.setattr(native_crash.ctypes, "WinDLL", lambda *a, **k: pytest.fail("Win32 call on macOS"), raising=False)
    yield tmp_path
    handle = native_crash._installed.pop("mac_file", None)
    faulthandler.disable()
    if handle is not None:
        handle.close()
    faulthandler.enable()


def test_install_writes_into_library_logs(mac):
    assert native_crash._dir() == mac / "Library" / "Logs" / "UEFN-Ducky"
    old_empty = native_crash._dir()
    old_empty.mkdir(parents=True)
    (old_empty / "crash-20000101.txt").write_text("")
    (old_empty / "crash-20000102.txt").write_text("Fatal Python error")
    assert native_crash.install() is True
    files = sorted(p.name for p in native_crash._dir().glob("crash-*.txt"))
    assert files[0] == "crash-20000102.txt" and len(files) == 2  # the empty old day is gone
    assert files[1].startswith("crash-2") and native_crash._installed["mac_file"] is not None


def test_signal_reports_land_there_too(mac):
    got = native_crash.write_signal_report("WebContent", -11)
    assert got is not None and got.parent == mac / "Library" / "Logs" / "UEFN-Ducky"


def test_a_real_segfault_leaves_the_stack(tmp_path):
    child = textwrap.dedent(f"""
        import ctypes, sys
        from pathlib import Path
        sys.platform = "darwin"
        from frontend import native_crash
        assert native_crash.install_mac(Path({str(tmp_path)!r}))
        ctypes.string_at(0)
    """)
    subprocess.run([sys.executable, "-c", child], cwd=APP_ROOT, capture_output=True, timeout=120)
    (crash,) = tmp_path.glob("crash-*.txt")
    text = crash.read_text(encoding="utf-8", errors="replace")
    assert "in string_at" in text and ("Fatal Python error" in text or "fatal exception" in text)
