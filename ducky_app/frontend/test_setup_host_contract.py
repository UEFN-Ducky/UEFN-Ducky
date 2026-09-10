"""Installer host / engine names must survive the panel process sweep."""

from __future__ import annotations

from pathlib import Path

from frontend.app_paths import (
    exe_name_matches,
    kill_process_ps_filter,
    process_name_matches,
)


def test_panel_exe_still_matches() -> None:
    assert exe_name_matches("UEFN-Ducky.exe")
    assert exe_name_matches("UEFN-Ducky-1.1.92.exe")
    assert process_name_matches("UEFN-Ducky")
    assert process_name_matches("UEFN-Ducky-1.1.92")
    assert process_name_matches("UEFN-Ducky_bridge")


def test_setup_host_and_engine_survive_sweep() -> None:
    assert not exe_name_matches("UEFN-Ducky-Setup.exe")
    assert not exe_name_matches("UEFN-Ducky-Setup-1.1.92.exe")
    assert not exe_name_matches("Setup-engine.exe")
    assert not process_name_matches("UEFN-Ducky-Setup")
    assert not process_name_matches("UEFN-Ducky-Setup-1.1.92")
    assert not process_name_matches("Setup-engine")
    filt = kill_process_ps_filter()
    assert "UEFN-Ducky*" in filt
    assert "UEFN-Ducky-Setup*" in filt
    assert "-notlike" in filt


def test_setup_host_source_forwards_silent_flags() -> None:
    root = Path(__file__).resolve().parents[2]
    engine = (root / "release" / "installer" / "host" / "Engine.cs").read_text(encoding="utf-8")
    iss = (root / "release" / "installer" / "UEFN-Ducky.iss").read_text(encoding="utf-8")
    assert 'ExeName = "Setup-engine.exe"' in engine
    for flag in (
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/CURRENTUSER",
        "/ALLUSERS",
        "/NOLAUNCH",
    ):
        assert flag in engine
    assert "OutputBaseFilename=Setup-engine" in iss
    assert "CmdLineSwitch('/NOLAUNCH')" in iss
    assert "setup-progress.txt" in iss
    assert "UEFN-Ducky-Setup-" not in iss.split("OutputBaseFilename", 1)[1][:80]
