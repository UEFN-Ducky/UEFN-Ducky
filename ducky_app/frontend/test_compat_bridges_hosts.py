"""IDE bridges and agent hosts start the same way in a window start and a launcher's ``--backend``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import frontend.app_versions as app_versions
import frontend.mcp_block as mcp_block
from frontend.agent_host import supervisor
from frontend.frozen_process import is_backend_cmdline, is_launcher_name
from frontend.settings import PanelSettings

WINDOW_ARGV = ["UEFN-Ducky.exe"]
BACKEND_ARGV = ["UEFN-Ducky.exe", "--backend"]


@pytest.fixture
def install(monkeypatch, tmp_path):
    """A one-dir install: the app, its bridge and the launcher side by side, run frozen."""
    root = tmp_path / "UEFN-Ducky"
    root.mkdir()
    app = root / "UEFN-Ducky.exe"
    for name in ("UEFN-Ducky.exe", "UEFN-Ducky-Bridge.exe", "Ducky-Launcher.exe"):
        (root / name).write_bytes(b"MZ")
    monkeypatch.setattr(sys, "executable", str(app))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(mcp_block, "is_packaged_runtime", lambda: True)
    monkeypatch.setattr(mcp_block, "shared_mcp_enabled", lambda settings=None: False)
    monkeypatch.setattr(app_versions, "stable_exe", lambda: app)
    return root


def _in_mode(monkeypatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", list(argv))


def _bridge_and_host(install: Path) -> tuple:
    settings = PanelSettings()
    return (
        mcp_block.resolve_bridge_command(),
        mcp_block.resolve_bridge_command(own_run=True),
        mcp_block.resolve_bridge_args(settings),
        supervisor.host_argv(0, r"\\.\pipe\ducky-host-0"),
    )


def test_bridge_name_args_and_host_argv_are_identical_in_both_modes(install, monkeypatch) -> None:
    _in_mode(monkeypatch, WINDOW_ARGV)
    window = _bridge_and_host(install)
    _in_mode(monkeypatch, BACKEND_ARGV)
    backend = _bridge_and_host(install)

    assert window == backend
    command, own, args, host = backend
    assert Path(command).name == Path(own).name == "UEFN-Ducky-Bridge.exe"
    assert args[0] == "bridge" and "--backend" not in args
    assert host == [str(install / "UEFN-Ducky.exe"), "agent-host", "--slot", "0", "--pipe", r"\\.\pipe\ducky-host-0"]


def test_children_never_run_the_launcher_or_carry_backend(install, monkeypatch) -> None:
    _in_mode(monkeypatch, BACKEND_ARGV)
    command, own, args, host = _bridge_and_host(install)
    for exe in (command, own, host[0]):
        assert not is_launcher_name(exe)
    assert not is_backend_cmdline([command, *args])
    assert not is_backend_cmdline(host)


def test_backend_flag_is_taken_before_anything_reads_argv() -> None:
    """``--backend`` leaves ``sys.argv`` before the app starts, so nothing downstream sees it."""
    from frontend.native_host import headless

    argv = ["UEFN-Ducky.exe", "--backend", "uefn-ducky://open"]
    assert headless.take_backend_flag(argv) is True
    assert argv == ["UEFN-Ducky.exe", "uefn-ducky://open"]


def test_agent_hosts_start_through_the_one_supervisor_entry() -> None:
    """Both modes reach agent hosts by ``turns._supervisor`` -> ``supervisor.start``; the backend
    never builds a supervisor or host argv of its own."""
    here = Path(__file__).resolve().parent
    turns = (here / "agent_host" / "turns.py").read_text(encoding="utf-8")
    assert "supervisor.get() or supervisor.start(" in turns
    for path in (here / "native_host").glob("*.py"):
        if path.name.startswith("test_"):
            continue
        text = path.read_text(encoding="utf-8")
        assert "Supervisor(" not in text, path.name
        assert "agent-host" not in text, path.name
        assert "host_argv" not in text, path.name
