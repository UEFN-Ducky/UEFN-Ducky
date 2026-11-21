"""Another launch or another Ducky never closes the person's running Ducky.

Oct 21 2026: opening Ducky while it was starting killed it as "wedged" (its server is up
~10 s in), and a test copy of Ducky that quit took the person's Ducky down with it; both
showed as "Ducky closed by itself" with no crash trace.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from frontend import open_files
from frontend.ui_web import webview_app


def test_a_starting_ducky_is_waited_for_not_killed(monkeypatch) -> None:
    tries: list[float] = []
    monkeypatch.setattr(open_files, "read_living_panel_pid", lambda: 4242)
    monkeypatch.setattr(open_files, "try_handoff_to_running",
                        lambda paths, links=None, **kw: tries.append(kw.get("timeout_s", 0.35)) or len(tries) >= 4)
    monkeypatch.setattr(webview_app, "_ducky_process_alive", lambda pid: True)
    assert webview_app._hand_off_to_living_panel([], []) is True
    assert len(tries) == 4  # answered on the fourth try, ~6 s in: handed off, nothing reclaimed


def test_a_dead_or_stale_panel_pid_is_not_waited_on(monkeypatch) -> None:
    tries: list[int] = []
    monkeypatch.setattr(open_files, "read_living_panel_pid", lambda: 4242)
    monkeypatch.setattr(open_files, "try_handoff_to_running", lambda paths, links=None, **kw: tries.append(1) and False)
    monkeypatch.setattr(webview_app, "_ducky_process_alive", lambda pid: False)
    assert webview_app._hand_off_to_living_panel([], []) is False
    assert len(tries) == 1  # one short try, then a normal cold start


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process handles")
def test_an_exited_ducky_is_not_alive_while_a_handle_to_it_is_open() -> None:
    """Oct 22 + 23 2026: after a frozen Ducky was force-closed, its process could still be
    opened (another process held a handle), so the next launch called it alive and spent
    45 s trying to hand off to it, then ~5 s taskkilling it."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])  # Popen keeps a handle to it
    assert proc.wait(timeout=30) == 0
    assert open_files.panel_process_alive(proc.pid) is False
    import os

    assert open_files.panel_process_alive(os.getpid()) is True


def test_reclaim_skips_a_panel_pid_that_already_exited(monkeypatch, tmp_path) -> None:
    from frontend import frozen_process

    pid_file = tmp_path / "panel.pid"
    pid_file.write_text("4242", encoding="utf-8")
    killed: list[int] = []
    monkeypatch.setattr(frozen_process.sys, "platform", "win32")
    monkeypatch.setattr(frozen_process, "panel_pid_path", lambda: pid_file)
    monkeypatch.setattr(frozen_process, "_kill_pid_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(open_files, "panel_process_alive", lambda pid: False)
    assert frozen_process.reclaim_stale_panel_process() is False
    assert killed == [] and not pid_file.exists()
    pid_file.write_text("4242", encoding="utf-8")
    monkeypatch.setattr(open_files, "panel_process_alive", lambda pid: True)
    assert frozen_process.reclaim_stale_panel_process() is True
    assert killed == [4242]


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell filter")
def test_quitting_reaps_this_duckys_bridges_never_another_duckys_or_a_window(monkeypatch) -> None:
    from frontend.app_paths import bridge_ps_filter

    rows = (
        "@([pscustomobject]@{Name='UEFN-Ducky.exe'; CommandLine='\"C:\\x\\UEFN-Ducky.exe\"'},"
        "[pscustomobject]@{Name='UEFN-Ducky-Bridge.exe'; CommandLine='\"C:\\x\\UEFN-Ducky-Bridge.exe\" bridge --port 4200'},"
        "[pscustomobject]@{Name='UEFN-Ducky.exe'; CommandLine='\"C:\\x\\UEFN-Ducky.exe\" bridge --port 4200 --adapter'},"
        "[pscustomobject]@{Name='UEFN-Ducky.exe'; CommandLine='\"C:\\t\\UEFN-Ducky.exe\" --profile testbed'},"
        # another Ducky's bridge (a test copy on its own port) is not this one's to end
        "[pscustomobject]@{Name='UEFN-Ducky-Bridge.exe'; CommandLine='\"C:\\t\\UEFN-Ducky-Bridge.exe\" bridge --port 4601 --adapter'})"
    )
    monkeypatch.setattr("frontend.settings.UEFN_LISTENER_PORT", 4200)
    script = f"{rows} | Where-Object {{ {bridge_ps_filter()} }} | ForEach-Object {{ $_.CommandLine }}"
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=60).stdout.split("\n")
    picked = [line.strip() for line in out if line.strip()]
    assert len(picked) == 2 and all(" bridge --port 4200" in line for line in picked)  # its two bridges only
