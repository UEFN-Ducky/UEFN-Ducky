"""CLI open-paths + Open-with registration (no UI)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from frontend.open_files import (
    cli_open_paths,
    enqueue_open_paths,
    register_windows_open_with,
    register_windows_url_protocol,
    take_pending_open_paths,
)


def test_cli_open_paths_picks_existing_files(tmp_path: Path) -> None:
    f = tmp_path / "notes.verse"
    f.write_text("x", encoding="utf-8")
    missing = tmp_path / "gone.txt"
    folder = tmp_path / "dir"
    folder.mkdir()
    argv = ["UEFN-Ducky.exe", str(f), str(missing), str(folder), "--port", "9", "-x"]
    assert cli_open_paths(argv) == [str(f.resolve())]


def test_cli_open_paths_ignores_bridge() -> None:
    assert cli_open_paths(["exe", "bridge", "C:\\somewhere\\file.txt"]) == []


def test_pending_queue_dedupes(tmp_path: Path) -> None:
    a = str(tmp_path / "a.txt")
    take_pending_open_paths()  # clear
    enqueue_open_paths([a, a, ""])
    assert take_pending_open_paths() == [a]
    assert take_pending_open_paths() == []


class _Key:
    def __init__(self, path: str) -> None:
        self.path = path

    def __enter__(self) -> "_Key":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self) -> None:
        super().__init__("winreg")
        self.values: dict[tuple[str, str], str] = {}

    def CreateKeyEx(self, root, path, *args):  # noqa: N802 - winreg's names
        return _Key(path)

    def SetValueEx(self, key, name, _reserved, _kind, value):  # noqa: N802
        self.values[(key.path, name)] = value


APP_KEY = r"Software\Classes\Applications\UEFN-Ducky.exe"
PROTO_KEY = r"Software\Classes\uefn-ducky"


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reg = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", reg)
    monkeypatch.setattr(sys, "platform", "win32")
    stub = tmp_path / "UEFN-Ducky.exe"
    stub.write_bytes(b"MZ")
    return reg, stub.resolve(), stub.resolve().parent / "Ducky-Launcher.exe"


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from frontend.settings import PanelSettings

    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls, **_: PanelSettings(native_launcher=mode)))


def _register(reg: _FakeWinreg, stub: Path) -> tuple[str, str]:
    reg.values.clear()
    register_windows_open_with(stub)
    register_windows_url_protocol(stub)
    # Names never change: the ProgID is UEFN-Ducky.exe's, the protocol key is uefn-ducky.
    assert reg.values[(APP_KEY, "FriendlyAppName")] == "UEFN Ducky"
    assert (r"Software\Classes\*\OpenWithList\UEFN-Ducky.exe", "") in reg.values
    assert reg.values[(PROTO_KEY, "URL Protocol")] == ""
    assert reg.values[(rf"{APP_KEY}\DefaultIcon", "")] == str(stub)
    assert not any("Ducky-Launcher" in path for path, _ in reg.values)
    return reg.values[(rf"{APP_KEY}\shell\open\command", "")], reg.values[(rf"{PROTO_KEY}\shell\open\command", "")]


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_commands_start_the_launcher_when_present(install, monkeypatch, mode) -> None:
    reg, stub, launcher = install
    launcher.write_bytes(b"MZ")
    _mode(monkeypatch, mode)
    assert _register(reg, stub) == (f'"{launcher}" "%1"', f'"{launcher}" "%1"')


@pytest.mark.parametrize(("mode", "present"), [("off", True), ("on", False), ("auto", False)])
def test_commands_stay_on_the_stub_when_off_or_absent(install, monkeypatch, mode, present) -> None:
    reg, stub, launcher = install
    if present:
        launcher.write_bytes(b"MZ")
    _mode(monkeypatch, mode)
    assert _register(reg, stub) == (f'"{stub}" "%1"', f'"{stub}" "%1"')


def test_self_heal_rewrites_both_commands_both_ways(install, monkeypatch) -> None:
    reg, stub, launcher = install
    _mode(monkeypatch, "auto")
    assert _register(reg, stub)[0] == f'"{stub}" "%1"'  # old install: no launcher yet
    launcher.write_bytes(b"MZ")  # an update ships it
    assert _register(reg, stub) == (f'"{launcher}" "%1"',) * 2
    _mode(monkeypatch, "off")
    assert _register(reg, stub) == (f'"{stub}" "%1"',) * 2
    _mode(monkeypatch, "on")
    launcher.unlink()
    assert _register(reg, stub) == (f'"{stub}" "%1"',) * 2


def test_register_windows_open_with_writes_applications_key(tmp_path: Path, monkeypatch) -> None:
    import sys

    if sys.platform != "win32":
        return
    import winreg

    # Unique name so we never clobber the real UEFN-Ducky.exe Open-with entry.
    exe = tmp_path / "UEFN-Ducky-pytest-open.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(
        "frontend.frozen_process.is_uefn_ducky_executable",
        lambda _p: True,
    )
    register_windows_open_with(exe)
    app = rf"Software\Classes\Applications\{exe.name}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{app}\shell\open\command") as key:
            cmd, _ = winreg.QueryValueEx(key, "")
        assert str(exe) in cmd and "%1" in cmd
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{app}\SupportedTypes") as key:
            winreg.QueryValueEx(key, ".*")
    finally:
        for sub in (
            rf"{app}\shell\open\command",
            rf"{app}\shell\open",
            rf"{app}\shell",
            rf"{app}\SupportedTypes",
            rf"{app}\DefaultIcon",
            app,
            rf"Software\Classes\*\OpenWithList\{exe.name}",
        ):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
            except OSError:
                pass
