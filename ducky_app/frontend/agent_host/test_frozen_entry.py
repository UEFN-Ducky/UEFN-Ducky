"""The installed EXE starts at frontend.launcher.main: ``agent-host`` runs the host there, never a second app."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from frontend import launcher
from frontend.agent_host import main as host_main


def test_agent_host_argv_runs_the_host_not_the_app(monkeypatch):
    got = []
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", "agent-host", "--slot", "0", "--pipe", "p"])
    monkeypatch.setattr(host_main, "main", lambda args: got.append(args) or 0)
    monkeypatch.setattr(launcher, "scrub_pyinstaller_boot_env", lambda: None)
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=lambda: None))

    def _app(*_a, **_k):
        raise AssertionError("agent-host booted the app (splash, hand-off) instead of the host")

    for name in ("_sweep_stale_extracts", "_wait_for_previous_version", "_hand_off_to_newest", "run_bridge"):
        monkeypatch.setattr(launcher, name, _app)
    with pytest.raises(SystemExit) as ended:
        launcher.main()
    assert ended.value.code == 0
    assert got == [["--slot", "0", "--pipe", "p"]]


@pytest.mark.parametrize("args", [["-m", "pytest", "-q"], ["-I", "-c", "print(1)"], ["-c", "x"]])
def test_python_style_args_never_boot_a_second_ducky(monkeypatch, args):
    """The release check ran 'UEFN-Ducky.exe -m pytest': a second Ducky with its logo splash
    popped up mid-chat, handed off to the open one and exited 0, read as tests passed."""
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", *args])

    def _app(*_a, **_k):
        raise AssertionError("a python-style call started Ducky")

    for name in ("scrub_pyinstaller_boot_env", "_sweep_stale_extracts", "_hand_off_to_newest", "run_bridge"):
        monkeypatch.setattr(launcher, name, _app)
    with pytest.raises(SystemExit) as ended:
        launcher.main()
    assert ended.value.code == 2


def test_ducky_options_and_files_are_not_python_style():
    for argv in (["x"], ["x", "--backend"], ["x", "--background"], ["x", "C:/a/b.verse"],
                 ["x", "uefn-ducky://store"], ["x", "agent-host"], ["x", "-"]):
        assert not launcher.python_style_call(argv)


def test_a_host_an_older_app_handed_off_clears_its_start_count(monkeypatch, tmp_path):
    """An older app's hand-off counts each host start; two uncleared ones mark the version bad."""
    from frontend import __version__, app_versions

    monkeypatch.setattr(app_versions, "versions_dir", lambda: tmp_path)
    version = str(__version__)
    assert app_versions.record_start(version) and app_versions.record_start(version)  # two hops
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", "agent-host", "--slot", "0", "--pipe", "p"])
    monkeypatch.setattr(host_main, "main", lambda args: 0)
    monkeypatch.setattr(launcher, "scrub_pyinstaller_boot_env", lambda: None)
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(inject_into_ssl=lambda: None))
    with pytest.raises(SystemExit):
        launcher.main()
    assert app_versions.record_start(version)  # a third hop is still allowed
    assert not app_versions.is_bad(version)
