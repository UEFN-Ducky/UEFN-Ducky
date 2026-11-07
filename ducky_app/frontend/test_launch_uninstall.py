"""Settings → Uninstall opens UEFN Ducky's own uninstaller, never Inno's silent one."""

from __future__ import annotations

from frontend import updater
from frontend.uninstaller_link import EXE_NAME


def _setup(monkeypatch, tmp_path, *, own: bool):
    if own:
        (tmp_path / EXE_NAME).write_bytes(b"MZ")
    monkeypatch.setattr(updater, "get_install_info", lambda: {
        "installed": True,
        "install_location": str(tmp_path),
        "uninstall_command": '"C:\\x\\unins000.exe"',
        "quiet_uninstall_command": '"C:\\x\\unins000.exe" /SILENT',
    })
    started: list[str] = []
    exits: list[bool] = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **_k: started.append(cmd))
    monkeypatch.setattr(updater, "_shutdown_after_delay", lambda: exits.append(True))
    return started, exits


def test_opens_our_uninstaller_and_keeps_the_app_open(monkeypatch, tmp_path):
    started, exits = _setup(monkeypatch, tmp_path, own=True)
    out = updater.launch_uninstall()
    assert out["ok"] and out["stage"] == "opened"
    assert started == [f'"{tmp_path / EXE_NAME}"']
    assert exits == []  # it can still answer "Keep UEFN Ducky"


def test_without_it_uses_the_windowed_command_not_the_silent_one(monkeypatch, tmp_path):
    started, exits = _setup(monkeypatch, tmp_path, own=False)
    out = updater.launch_uninstall()
    assert out["ok"] and out["stage"] == "uninstalling"
    assert started == ['"C:\\x\\unins000.exe"']
    assert exits == [True]
