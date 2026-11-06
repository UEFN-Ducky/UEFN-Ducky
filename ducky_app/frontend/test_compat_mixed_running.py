"""An old Python window and a new launcher start running together never kill each other."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

import frontend.frozen_process as fp
import frontend.open_files as open_files

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="panel.pid reclaim is Windows-only")

OLD_WINDOW = ("UEFN-Ducky.exe", ["C:/old/UEFN-Ducky.exe"])
BACKEND = ("UEFN-Ducky.exe", ["C:/new/UEFN-Ducky.exe", "--backend", "uefn-ducky://open"])
LAUNCHER = ("Ducky-Launcher.exe", ["C:/new/Ducky-Launcher.exe"])


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A fake process table, panel.pid in tmp, and a record of every kill."""
    table: dict[int, tuple[str, list[str]]] = {}
    killed: list[int] = []
    pid_file = tmp_path / "panel.pid"
    monkeypatch.setattr(fp, "panel_pid_path", lambda **_: pid_file)
    monkeypatch.setattr(fp, "_kill_pid_tree", lambda pid: killed.append(pid) or True)
    monkeypatch.setattr(fp, "_process_identity", lambda pid: table.get(int(pid)))
    monkeypatch.setattr(open_files, "panel_process_alive", lambda pid: int(pid) in table)
    return SimpleNamespace(table=table, killed=killed, pid_file=pid_file)


def test_names_and_command_lines() -> None:
    assert fp.is_launcher_name("Ducky-Launcher.exe") and fp.is_launcher_name("Ducky-Launcher")
    assert not fp.is_launcher_name("UEFN-Ducky.exe")
    assert fp.is_backend_cmdline(BACKEND[1])
    assert not fp.is_backend_cmdline(OLD_WINDOW[1])
    assert not fp.is_backend_cmdline(["--backend"])  # argv[0] is the program, not a flag
    assert not fp.is_uefn_ducky_executable("Ducky-Launcher.exe")


def test_a_new_start_never_reclaims_a_living_launcher_backend(world) -> None:
    world.table[700] = BACKEND
    world.pid_file.write_text("700", encoding="utf-8")

    assert fp.reclaim_stale_panel_process() is False
    assert world.killed == []
    assert world.pid_file.read_text(encoding="utf-8") == "700"  # still the hand-off target


def test_a_new_start_never_reclaims_the_launcher_itself(world) -> None:
    world.table[701] = LAUNCHER
    world.pid_file.write_text("701", encoding="utf-8")

    assert fp.reclaim_stale_panel_process() is False
    assert world.killed == []


def test_an_old_zombie_window_is_still_reclaimed(world) -> None:
    world.table[702] = OLD_WINDOW
    world.pid_file.write_text("702", encoding="utf-8")

    assert fp.reclaim_stale_panel_process() is True
    assert world.killed == [702] and not world.pid_file.exists()


def test_panel_pid_stays_a_plain_int_in_backend_mode(world, monkeypatch) -> None:
    """Older versions read ``int(panel.pid)``: a backend's claim must hand off to them and back."""
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", "--backend"])
    fp.claim_panel_process()
    text = world.pid_file.read_text(encoding="utf-8")
    assert text == str(os.getpid()) and int(text) == os.getpid()
    fp.release_panel_process()
    assert not world.pid_file.exists()


def test_old_window_to_launcher_and_back_hand_off(world) -> None:
    """Old window owns panel.pid -> a launcher's backend finds it living, and the reverse."""
    world.table[800] = OLD_WINDOW
    world.pid_file.write_text("800", encoding="utf-8")
    assert open_files.read_living_panel_pid() == 800  # the backend hands off to the old window

    world.table.pop(800)
    world.table[801] = BACKEND
    world.pid_file.write_text("801", encoding="utf-8")
    assert open_files.read_living_panel_pid() == 801  # an old window hands off to the backend
    assert fp.reclaim_stale_panel_process() is False and world.killed == []


def test_reclaim_panel_port_skips_a_silent_backend(world, monkeypatch) -> None:
    import psutil

    world.table[900] = BACKEND
    world.table[901] = OLD_WINDOW
    conns = [SimpleNamespace(pid=p, status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=5555)) for p in (900, 901)]
    monkeypatch.setattr(psutil, "net_connections", lambda kind="tcp": conns)
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(name=lambda: world.table[int(pid)][0]))
    monkeypatch.setattr(fp, "_panel_answers", lambda port, timeout_s=3.0: False)

    assert fp.reclaim_panel_port(5555) is True
    assert world.killed == [901]
