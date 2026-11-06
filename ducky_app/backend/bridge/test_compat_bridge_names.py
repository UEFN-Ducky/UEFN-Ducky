"""The bridge sweep never stops the native launcher or the backend it starts."""

from __future__ import annotations

import os

import pytest

import backend.bridge.bridge_version as bv
from frontend.app_paths import exe_name_matches

NEW = "1.2.471"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    bv.reset_for_tests()
    monkeypatch.setenv(bv.DIR_ENV, str(tmp_path / "bridges"))
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    table = {os.getpid(): 500.0}
    monkeypatch.setattr(bv, "_create_time", lambda pid: table.get(int(pid), 0.0))
    yield
    bv.reset_for_tests()


def _install(tmp_path):
    exe = tmp_path / "install" / "UEFN-Ducky.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    return str(exe), bv._installed_at(str(exe))


def test_the_launcher_name_never_matches_the_ducky_sweeps() -> None:
    assert not exe_name_matches("Ducky-Launcher.exe")
    assert not exe_name_matches("ducky-launcher.exe")
    assert exe_name_matches("UEFN-Ducky.exe")


def test_launcher_and_backend_are_not_bridges() -> None:
    old = "C:/old/UEFN-Ducky-1.2.467.exe"
    assert not bv._is_bridge({"name": "Ducky-Launcher.exe", "exe": "C:/x/Ducky-Launcher.exe", "cmdline": []})
    assert not bv._is_bridge({"name": "", "exe": "C:/x/Ducky-Launcher.exe", "cmdline": ["Ducky-Launcher.exe", "bridge"]})
    assert not bv._is_bridge({"name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "--backend"]})
    assert not bv._is_bridge({"name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "--backend", "bridge"]})
    assert not bv._is_bridge({"name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "C:/p/a.verse", "--BACKEND"]})
    # Real bridges still are.
    assert bv._is_bridge({"name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "bridge"]})
    assert bv._is_bridge({"name": "UEFN-Ducky-Bridge.exe", "exe": "C:/old/UEFN-Ducky-Bridge.exe", "cmdline": []})


def test_stop_old_bridges_skips_launcher_side_processes(tmp_path) -> None:
    root = tmp_path / "bridges"
    bv.note_running_app(NEW, root=root)
    exe, installed = _install(tmp_path)
    old = str(tmp_path / "old" / "UEFN-Ducky-1.2.467.exe")
    launcher = str(tmp_path / "launcher" / "Ducky-Launcher.exe")
    procs = [
        # The launcher and its backend, from an older install and started before it: kept.
        {"pid": 501, "name": "Ducky-Launcher.exe", "exe": launcher, "cmdline": [launcher], "create_time": installed - 3600},
        {"pid": 502, "name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "--backend"], "create_time": installed - 3600},
        {"pid": 503, "name": "UEFN-Ducky.exe", "exe": exe, "cmdline": [exe, "--backend", "bridge"], "create_time": installed - 60},
        # An old bridge next to them: stopped.
        {"pid": 504, "name": "UEFN-Ducky.exe", "exe": old, "cmdline": [old, "bridge"], "create_time": installed + 60},
    ]
    killed: list[int] = []

    stopped = bv.stop_old_bridges(
        version=NEW, root=root, processes=lambda: procs, kill=lambda pid: killed.append(pid) or True, exe=exe
    )

    assert stopped == killed == [504]


def test_a_launcher_backend_as_the_running_app_is_kept(tmp_path) -> None:
    """The backend records itself in app.json like a window: never its own sweep's target."""
    root = tmp_path / "bridges"
    bv.note_running_app(NEW, root=root)  # this process plays the backend
    exe, installed = _install(tmp_path)
    procs = [{"pid": os.getpid(), "name": "UEFN-Ducky.exe", "exe": str(tmp_path / "x" / "UEFN-Ducky.exe"),
              "cmdline": ["UEFN-Ducky.exe", "bridge"], "create_time": 0.0}]

    assert bv.stop_old_bridges(
        version=NEW, root=root, processes=lambda: procs, kill=lambda pid: pytest.fail(f"stopped {pid}"), exe=exe
    ) == []
