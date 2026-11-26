"""Start at login on macOS: a LaunchAgent plist, written only by the bundled app."""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

import pytest

from frontend import autostart

APP_EXE = "/Applications/UEFN Ducky.app/Contents/MacOS/UEFN-Ducky"


@pytest.fixture
def mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    monkeypatch.setattr(sys, "executable", APP_EXE)
    return tmp_path


def test_enable_writes_the_launch_agent(mac):
    assert autostart.apply(True) is True
    agent = mac / "Library" / "LaunchAgents" / "online.uefnducky.Ducky.plist"
    data = plistlib.loads(agent.read_bytes())
    assert data["Label"] == "online.uefnducky.Ducky" and data["RunAtLoad"] is True
    assert data["ProgramArguments"] == [str(Path(APP_EXE)), "--background"]
    assert autostart.registered_command() == autostart.command_for(Path(APP_EXE))
    assert autostart.apply(False) is True and not agent.exists()
    assert autostart.registered_command() is None


@pytest.mark.parametrize("exe", ["/Users/a/src/UEFN-Ducky/.venv/bin/python3", "/usr/local/bin/python3"])
def test_a_source_run_never_writes_it(mac, monkeypatch, exe):
    monkeypatch.setattr(sys, "executable", exe)
    assert autostart.apply(True) is False
    assert not (mac / "Library" / "LaunchAgents").exists()


def test_a_test_copy_never_writes_it(mac, monkeypatch):
    monkeypatch.setenv("DUCKY_SANDBOX", "1")
    assert autostart.apply(True) is False


def test_home_applications_bundle_counts(mac):
    exe = "/Users/a/Applications/UEFN Ducky.app/Contents/MacOS/UEFN-Ducky"
    assert autostart.mac_app_exe(exe) == Path(exe)
