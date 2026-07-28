"""CLI open-paths + Open-with registration (no UI)."""

from __future__ import annotations

from pathlib import Path

from frontend.open_files import (
    cli_open_paths,
    enqueue_open_paths,
    register_windows_open_with,
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
