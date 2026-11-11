"""Setup over an old install, with and without Ducky-Launcher.exe.

Saved paths keep naming UEFN-Ducky.exe, the Run key keeps its name and args (its EXE
follows the Repoint rule), a build without the launcher
installs and starts as before, and the update sweep only ever stops a replaced
version's launcher, after it had its chance to close. Processes and the registry are
mocked: nothing is started or killed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import frontend.updater as updater
from frontend import app_versions as av
from frontend import autostart

CURRENT = updater.__version__
OLD = "0.0.1"
NEWER = "999.0.0"


def _install(tmp_path: Path, *, with_launcher: bool) -> Path:
    root = tmp_path / "Programs" / "UEFN Ducky"
    root.mkdir(parents=True)
    (root / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (root / "UEFN-Ducky-Bridge.exe").write_bytes(b"MZ")
    if with_launcher:
        (root / "Ducky-Launcher.exe").write_bytes(b"MZ")
    return root


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    """Just enough of winreg for autostart: one HKCU Run key in a dict."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.writes: list[tuple[str, str]] = []
        self.deletes: list[str] = []

    def OpenKey(self, *_a, **_k):  # noqa: N802 (winreg API)
        return _FakeKey()

    def CreateKeyEx(self, *_a, **_k):  # noqa: N802
        return _FakeKey()

    def QueryValueEx(self, _key, name):  # noqa: N802
        if name not in self.values:
            raise OSError("missing")
        return self.values[name], self.REG_SZ

    def SetValueEx(self, _key, name, _r, _t, value):  # noqa: N802
        self.writes.append((name, value))
        self.values[name] = value

    def DeleteValue(self, _key, name):  # noqa: N802
        self.deletes.append(name)
        self.values.pop(name, None)


@pytest.fixture
def packaged(monkeypatch):
    import frontend.bundle_root as bundle_root

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(bundle_root, "is_packaged_runtime", lambda: True)


# --- 1. Shortcuts and the Run key are kept ---------------------------------------------
# Repoint: the Run value keeps its name and --background, and names Ducky-Launcher.exe
# beside UEFN-Ducky.exe when it is there and native_launcher isn't off, else UEFN-Ducky.exe.


def _setting(monkeypatch, mode: str) -> None:
    from types import SimpleNamespace

    from frontend import settings

    monkeypatch.setattr(settings.PanelSettings, "load", classmethod(lambda cls: SimpleNamespace(native_launcher=mode)))


_RUN_CASES = [(False, "auto"), (False, "on"), (True, "off"), (True, "auto"), (True, "on")]


def _run_target(root: Path, with_launcher: bool, mode: str) -> Path:
    # Always the stub: sign-in repairs a broken root launcher before anyone clicks it.
    return (root / "UEFN-Ducky.exe").resolve()


@pytest.mark.parametrize("with_launcher, mode", _RUN_CASES)
def test_setup_over_an_old_install_keeps_the_run_value(packaged, monkeypatch, tmp_path, with_launcher, mode) -> None:
    root = _install(tmp_path, with_launcher=with_launcher)
    exe = (root / "UEFN-Ducky.exe").resolve()
    old_command = autostart.command_for(exe)  # what every old install wrote
    reg = _FakeWinreg({autostart.VALUE_NAME: old_command})
    monkeypatch.setattr(autostart, "_winreg", lambda: reg)
    _setting(monkeypatch, mode)
    # Started through the launcher, or straight: the managed EXE is the same stub.
    started = root / ("Ducky-Launcher.exe" if with_launcher else "UEFN-Ducky.exe")
    monkeypatch.setenv(av.LAUNCHER_ENV, str(started))

    assert autostart.managed_exe() == exe
    assert autostart.apply(True) is True
    want = autostart.command_for(_run_target(root, with_launcher, mode))
    assert want.endswith(f'" {autostart.BACKGROUND_FLAG}')
    # Same value name, never removed; rewritten only when the target changed.
    assert reg.deletes == []
    assert reg.values[autostart.VALUE_NAME] == want
    assert reg.writes == ([] if want == old_command else [(autostart.VALUE_NAME, want)])
    # A second start finds it matching: nothing more written.
    assert autostart.apply(True) is True
    assert len(reg.writes) <= 1


@pytest.mark.parametrize("with_launcher, mode", _RUN_CASES)
def test_a_missing_run_key_is_written_for_the_start_exe(packaged, monkeypatch, tmp_path, with_launcher, mode) -> None:
    root = _install(tmp_path, with_launcher=with_launcher)
    reg = _FakeWinreg({})
    monkeypatch.setattr(autostart, "_winreg", lambda: reg)
    _setting(monkeypatch, mode)
    monkeypatch.setenv(av.LAUNCHER_ENV, str(root / "Ducky-Launcher.exe"))

    assert autostart.apply(True) is True
    assert reg.writes == [(autostart.VALUE_NAME, autostart.command_for(_run_target(root, with_launcher, mode)))]


@pytest.mark.parametrize("started", ["UEFN-Ducky.exe", "Ducky-Launcher.exe"])
def test_shortcut_and_open_with_targets_stay_on_uefn_ducky_exe(monkeypatch, tmp_path, started) -> None:
    root = _install(tmp_path, with_launcher=True)
    monkeypatch.setenv(av.LAUNCHER_ENV, str(root / started))

    assert av.stable_exe() == root / "UEFN-Ducky.exe"
    assert av.stable_exe("UEFN-Ducky-Bridge.exe") == root / "UEFN-Ducky-Bridge.exe"
    assert av.hand_off_env()[av.LAUNCHER_ENV] == str(root / "UEFN-Ducky.exe")


# --- 2. A build without the launcher installs fine -------------------------------------


def test_a_version_without_the_launcher_is_ready_and_usable() -> None:
    folder = av.version_dir(NEWER)
    folder.mkdir(parents=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / av.READY_MARKER).write_text("{}", encoding="utf-8")

    assert not (folder / "Ducky-Launcher.exe").exists()
    assert av.is_ready(folder)
    assert av.newest_usable(CURRENT) == (NEWER, folder)


def test_the_startup_sweep_runs_with_no_launcher_anywhere(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(updater, "stop_old_bridges_after_update", lambda: [])
    monkeypatch.setattr(updater, "installer_cache_dir", lambda: tmp_path / "no-cache")
    monkeypatch.setattr(updater, "_launcher_processes", lambda: [])
    killed: list[int] = []
    monkeypatch.setattr(updater, "_terminate_pid", killed.append)

    assert updater.sweep_installer_cache() == 0
    assert updater.stop_replaced_launchers() == []
    assert killed == []


def test_the_startup_sweep_also_stops_replaced_launchers(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(updater, "stop_old_bridges_after_update", lambda: calls.append("bridges") or [])
    monkeypatch.setattr(updater, "stop_replaced_launchers", lambda: calls.append("launchers") or [])
    monkeypatch.setattr(updater, "installer_cache_dir", lambda: tmp_path / "no-cache")

    updater.sweep_installer_cache()
    assert calls == ["bridges", "launchers"]
    # The old app's own sweep after Setup (it is about to exit) stops neither.
    updater.sweep_installer_cache(keep_newer_than=CURRENT)
    assert calls == ["bridges", "launchers"]


# --- 3. The sweep only stops the replaced version's launcher, after a graceful close ---


def test_ducky_launcher_is_outside_the_uefn_ducky_sweep() -> None:
    from frontend.frozen_process import is_uefn_ducky_executable

    assert not is_uefn_ducky_executable(Path("C:/Programs/UEFN Ducky/Ducky-Launcher.exe"))
    assert is_uefn_ducky_executable(Path("C:/Programs/UEFN Ducky/UEFN-Ducky.exe"))


def test_only_an_older_versions_launcher_is_a_target(tmp_path) -> None:
    versions = av.versions_dir()
    install = tmp_path / "Programs" / "UEFN Ducky"
    processes = [
        (11, str(versions / f"app-{OLD}" / "Ducky-Launcher.exe")),  # replaced version: target
        (12, str(install / "Ducky-Launcher.exe")),  # installed copy (Setup replaced it in place)
        (13, str(versions / f"app-{CURRENT}" / "Ducky-Launcher.exe")),  # this version
        (14, str(versions / f"app-{NEWER}" / "Ducky-Launcher.exe")),  # a newer one
        (15, str(versions / f"app-{OLD}" / "UEFN-Ducky.exe")),  # not a launcher
        (16, str(tmp_path / "elsewhere" / f"app-{OLD}" / "Ducky-Launcher.exe")),  # not our versions
        (17, str(versions / f"app-{OLD}" / "Ducky-Launcher.exe")),  # hosts this process
        (18, str(versions / "app-junk" / "Ducky-Launcher.exe")),
    ]
    got = updater.replaced_launcher_pids(
        processes, current_version=CURRENT, versions_root=versions, keep_pids={17}
    )
    assert got == [11]


def _old_launcher(pid: int) -> tuple[int, str]:
    return pid, str(av.versions_dir() / f"app-{OLD}" / "Ducky-Launcher.exe")


def test_a_launcher_that_closes_on_its_own_is_never_stopped(monkeypatch) -> None:
    monkeypatch.setattr(updater, "_own_launcher_pids", lambda: set())
    polls = {"n": 0}

    def alive(_pid):
        polls["n"] += 1
        return polls["n"] < 3  # closes gracefully after its backend went away

    killed: list[int] = []
    got = updater.stop_replaced_launchers(
        list_processes=lambda: [_old_launcher(41)],
        is_alive=alive,
        terminate=killed.append,
        grace_s=60.0,
        sleep=lambda _s: None,
    )
    assert got == [] and killed == []


def test_a_stuck_old_launcher_is_stopped_only_after_the_grace(monkeypatch) -> None:
    monkeypatch.setattr(updater, "_own_launcher_pids", lambda: set())
    events: list[str] = []
    clock = {"t": 0.0}
    monkeypatch.setattr(updater.time, "monotonic", lambda: clock["t"])

    def sleep(s):
        clock["t"] += s
        events.append("wait")

    def kill(pid):
        events.append(f"kill {pid}")

    got = updater.stop_replaced_launchers(
        list_processes=lambda: [_old_launcher(42), (43, "C:/Programs/UEFN Ducky/Ducky-Launcher.exe")],
        is_alive=lambda _pid: True,
        terminate=kill,
        grace_s=1.0,
        sleep=sleep,
    )
    assert got == [42]
    assert events[-1] == "kill 42" and events.count("kill 42") == 1
    assert events.index("kill 42") > 0 and all(e == "wait" for e in events[:-1])
    assert "kill 43" not in events


def test_the_launcher_hosting_this_backend_is_never_a_target(monkeypatch) -> None:
    monkeypatch.setattr(updater.os, "getppid", lambda: 44)
    killed: list[int] = []
    got = updater.stop_replaced_launchers(
        list_processes=lambda: [_old_launcher(44)],
        is_alive=lambda _pid: True,
        terminate=killed.append,
        grace_s=0.0,
        sleep=lambda _s: None,
    )
    assert got == [] and killed == []


def test_a_broken_process_list_never_breaks_the_update() -> None:
    def boom():
        raise OSError("tasklist gone")

    assert updater.stop_replaced_launchers(list_processes=boom) == []
