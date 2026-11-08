"""A native crash in the app process leaves a dump the next crash report can name."""

from __future__ import annotations

import importlib.machinery
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from frontend import minidump, native_crash

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows crash filter")
_APP_ROOT = Path(__file__).resolve().parents[1]

# The child loads pythonnet like the app does, installs the filter, then crashes on a
# native thread (ctypes calls catch access violations themselves, the app's .NET and
# WebView2 threads don't).
_CHILD = textwrap.dedent(
    """
    import ctypes, sys, threading, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    folder = Path(sys.argv[2])
    try:
        import clr  # noqa: F401  pythonnet, as the app loads it
    except Exception:
        pass
    ctypes.windll.kernel32.SetErrorMode(0x0002)  # no Windows error box
    from frontend import native_crash
    assert native_crash.install(folder)
    if sys.argv[3] == "busy":
        def spin():
            n = 0
            while True:
                n += 1
        for _ in range(2):
            threading.Thread(target=spin, daemon=True).start()
        time.sleep(0.5)
    (folder / "fault.txt").write_text(str(time.time()))
    k = ctypes.windll.kernel32
    k.CreateThread.restype = ctypes.c_void_p
    k.CreateThread.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p)
    k.CreateThread(None, 0, None, None, 0, None)  # a native thread that runs address 0
    time.sleep(60)
    """
)


def _crash_child(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess, Path]:
    folder = tmp_path / "crash"
    folder.mkdir()
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(_APP_ROOT), str(folder), mode],
        capture_output=True, text=True, timeout=90,
    )
    return proc, folder


@pytest.mark.parametrize("mode", ["quiet", "busy"])
def test_a_native_crash_writes_a_dump_that_names_the_exception(tmp_path: Path, mode: str) -> None:
    """busy = D24: other Python threads hold the GIL while the fault happens; the filter
    needs the GIL, so it must still get it and finish within 10 s."""
    # PathFinder, not find_spec: pythonnet's clr, once imported here, has no __spec__ and find_spec raises.
    if importlib.machinery.PathFinder.find_spec("clr") is None:
        pytest.skip("pythonnet not installed")
    proc, folder = _crash_child(tmp_path, mode)
    assert proc.returncode != 0, proc.stderr
    dumps = list(folder.glob("ducky-*.dmp"))
    assert len(dumps) == 1, (proc.returncode, proc.stderr[-2000:])
    faulted = float((folder / "fault.txt").read_text())
    assert dumps[0].stat().st_mtime - faulted < 10.0
    info = minidump.read(dumps[0])
    assert info.get("code_name") == "ACCESS_VIOLATION", info
    assert info["main_module"].lower().startswith("python")
    assert minidump.cause(info, app=True).startswith("Ducky's app process crashed: ACCESS_VIOLATION")


def test_the_filter_is_set_again_after_another_one_replaced_it(monkeypatch) -> None:
    monkeypatch.setattr(native_crash, "_installed", dict(native_crash._installed, callback=None, address=None, previous=None, watch=None))
    assert native_crash.install()
    try:
        assert native_crash.ensure_installed() is False  # still ours
        other = native_crash._FILTER(lambda _ptrs: 0)
        native_crash._kernel32().SetUnhandledExceptionFilter(ctypes_address(other))
        assert native_crash.ensure_installed() is True  # replaced: ours again
        assert native_crash.ensure_installed() is False
    finally:
        native_crash.uninstall()


def test_the_watch_reinstalls_every_interval(monkeypatch) -> None:
    calls: list[float] = []
    monkeypatch.setattr(native_crash, "_installed", dict(native_crash._installed, address=1, watch=None))
    monkeypatch.setattr(native_crash, "ensure_installed", lambda: calls.append(time.monotonic()) or len(calls) > 2)
    native_crash.start_watch(every_s=0.01)
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(calls) >= 3


def test_write_dump_writes_a_readable_dump_of_this_process(tmp_path: Path) -> None:
    path = tmp_path / "ducky-test.dmp"
    assert native_crash.write_dump(path)
    info = minidump.read(path)
    assert not info.get("unreadable") and info["code"] is None
    assert os.path.getsize(path) > 0


def ctypes_address(callback: object) -> int:
    import ctypes

    return ctypes.cast(callback, ctypes.c_void_p).value
