"""Linux crash reports: the app's own crash (faulthandler) and a child a signal killed
(native_crash.watch_child) both reach the next launch's crash report."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from frontend import minidump, native_crash

_APP_ROOT = Path(__file__).resolve().parents[1]

# Launch 1: starts recording, a "WebKitWebProcess" child dies on SIGSEGV under the watchdog,
# then the app itself dies on SIGSEGV.
_CRASHING_LAUNCH = textwrap.dedent(
    """
    import os, signal, subprocess, sys
    sys.path.insert(0, sys.argv[1])
    from frontend import boot_report, native_crash
    boot_report.begin("9.9.9", ask=False)
    child = subprocess.Popen([sys.executable, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"])
    native_crash.watch_child(child, "WebKitWebProcess").join(30)
    os.kill(os.getpid(), signal.SIGSEGV)
    """
)
# Launch 2: what the crash pop-up would ask to send.
_NEXT_LAUNCH = textwrap.dedent(
    """
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from frontend import boot_report
    boot_report.begin("9.9.9", ask=False)
    print(json.dumps(boot_report._pending.get("body") or {}))
    """
)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux signals")
def test_a_crash_and_a_crashed_child_are_in_the_next_report(tmp_path: Path) -> None:
    # Both: a real install reads XDG_DATA_HOME, the test conftest's app_paths hook LOCALAPPDATA.
    env = dict(os.environ, XDG_DATA_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    env.pop("DUCKY_SANDBOX", None)
    first = subprocess.run([sys.executable, "-c", _CRASHING_LAUNCH, str(_APP_ROOT)],
                           env=env, capture_output=True, text=True, timeout=60)
    assert first.returncode == -signal.SIGSEGV, first.stderr
    crash = tmp_path / "UEFN-Ducky" / "crash"
    assert len(list(crash.glob("ducky-*-WebKitWebProcess.sigcrash"))) == 1
    second = subprocess.run([sys.executable, "-c", _NEXT_LAUNCH, str(_APP_ROOT)],
                            env=env, capture_output=True, text=True, timeout=60)
    body = json.loads(second.stdout.strip().splitlines()[-1])
    log = body["error_log"]
    assert body["source"] == "crash" and "Ducky crashed" in body["message"]
    assert "Fatal Python error: Segmentation fault" in log
    assert "SIGSEGV in libwebkit2gtk" in log


def test_only_crash_signals_write_a_report(tmp_path: Path) -> None:
    folder = tmp_path / "crash"
    assert native_crash.write_signal_report("helper", 0, folder) is None
    assert native_crash.write_signal_report("helper", 1, folder) is None
    assert native_crash.write_signal_report("helper", -int(signal.SIGTERM), folder) is None
    assert not folder.exists()


def test_a_signal_report_reads_like_a_dump(tmp_path: Path) -> None:
    path = native_crash.write_signal_report("WebKitWebProcess", -int(signal.SIGSEGV), tmp_path)
    assert path is not None and path.suffix == ".sigcrash"
    info = minidump.read(path)
    assert info["code_name"] == "SIGSEGV" and info["module"] == "libwebkit2gtk"
    assert minidump.summary(info) == "WebKitWebProcess process: SIGSEGV at libwebkit2gtk"
    assert minidump.cause(info) == "The chat window crashed (WebKitWebProcess process): SIGSEGV in libwebkit2gtk."


def test_a_broken_signal_report_is_unreadable(tmp_path: Path) -> None:
    bad = tmp_path / "ducky-x.sigcrash"
    bad.write_text("{", encoding="utf-8")
    assert minidump.read(bad)["unreadable"] is True
