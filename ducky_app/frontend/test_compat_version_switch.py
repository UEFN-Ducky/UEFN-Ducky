"""Old stub -> new version, and new -> old after a rollback, with and without Ducky-Launcher.exe.

The version switch works the same whether a version folder carries the native launcher
or not: the old argv and handover still parse, a new handover still parses under the
old rules, and a rollback never starts the launcher. No EXE runs: starts are recorded.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from frontend import app_versions as av

OLD = "1.2.470"
NEW = "1.2.471"
STUB = "1.2.469"  # the installed EXE that hands off


def _tree(version: str, *, with_launcher: bool) -> Path:
    folder = av.version_dir(version)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "UEFN-Ducky.exe").write_bytes(b"MZ")
    (folder / "UEFN-Ducky-Bridge.exe").write_bytes(b"MZ")
    if with_launcher:
        (folder / av.NATIVE_LAUNCHER_EXE).write_bytes(b"MZ")
    (folder / av.READY_MARKER).write_text("{}", encoding="utf-8")
    return folder


@pytest.fixture(params=[False, True], ids=["no-launcher", "launcher"])
def trees(request, monkeypatch, tmp_path) -> dict[str, Any]:
    with_launcher = request.param
    install = tmp_path / "Programs" / "UEFN Ducky"
    install.mkdir(parents=True)
    (install / "UEFN-Ducky.exe").write_bytes(b"MZ")
    if with_launcher:
        (install / av.NATIVE_LAUNCHER_EXE).write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv(av.NO_HOP_ENV, raising=False)
    monkeypatch.delenv(av.LAUNCHER_ENV, raising=False)
    monkeypatch.setattr(sys, "executable", str(install / "UEFN-Ducky.exe"))
    return {
        "launcher": with_launcher,
        "install": install,
        OLD: _tree(OLD, with_launcher=with_launcher),
        NEW: _tree(NEW, with_launcher=with_launcher),
    }


class _Left(Exception):
    pass


# A rollback restart waits for the failed process to end (--after-update <pid>): started at
# once, the old version saw that pid alive in boot.json and turned its crash record off.
_WAIT_FOR_ME = ["--after-update", str(__import__("os").getpid())]


@pytest.fixture
def starts(monkeypatch) -> list[tuple[Path, list[str]]]:
    """Record detached starts and make os._exit raise instead of leaving."""
    got: list[tuple[Path, list[str]]] = []
    monkeypatch.setattr(av, "start_detached", lambda exe, args, **k: got.append((exe, list(args))) or True)

    def _exit(code):
        raise _Left(code)

    monkeypatch.setattr(av.os, "_exit", _exit)
    return got


def _hand_off(argv: list[str], current: str = STUB, *, bridge: bool = False) -> None:
    sys.argv = ["UEFN-Ducky.exe", *argv]
    try:
        av.hand_off(current, bridge=bridge)
    except _Left:
        pass


# --- the same with and without the launcher --------------------------------------------


def test_newest_usable_and_mark_ready(trees) -> None:
    assert av.newest_usable(STUB) == (NEW, trees[NEW])
    assert av.newest_usable(OLD) == (NEW, trees[NEW])
    assert av.newest_usable(NEW) is None
    assert av.record_start(NEW) is True
    assert av.read_boot()[NEW]["starts"] == 1
    av.mark_ready(NEW)
    assert av.read_boot()[NEW]["starts"] == 0


@pytest.mark.parametrize(
    "argv",
    [[], ["--background"], ["uefn-ducky://store/x"], ["--after-update", "4242"], ["C:/proj/My Island.uefnproject"]],
)
def test_old_stub_hands_old_argv_to_the_new_version_unchanged(trees, starts, monkeypatch, argv) -> None:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    _hand_off(argv)
    assert starts == [(trees[NEW] / "UEFN-Ducky.exe", argv)]
    assert av.read_boot()[NEW]["starts"] == 1


def test_the_hand_off_never_starts_the_launcher(trees, starts, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    # Even a process whose LAUNCHER_ENV names Ducky-Launcher.exe hands off to UEFN-Ducky.exe.
    monkeypatch.setenv(av.LAUNCHER_ENV, str(trees["install"] / av.NATIVE_LAUNCHER_EXE))
    _hand_off([])
    assert [exe.name for exe, _ in starts] == ["UEFN-Ducky.exe"]
    assert av.hand_off_env()[av.LAUNCHER_ENV] == str(trees["install"] / "UEFN-Ducky.exe")


def test_a_backend_hands_off_attached_so_the_launcher_keeps_its_pipes(trees, starts, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    ran: list[tuple[Path, list[str]]] = []
    monkeypatch.setattr(av, "run_attached", lambda exe, args: ran.append((exe, list(args))) or 0)
    with pytest.raises(SystemExit) as exc:
        _hand_off([av.BACKEND_ARG, "--background"])
    assert exc.value.code == 0
    assert ran == [(trees[NEW] / "UEFN-Ducky.exe", [av.BACKEND_ARG, "--background"])]
    assert starts == []
    assert av.read_boot()[NEW]["starts"] == 1  # a backend start counts like a panel start


def test_bridges_hand_off_as_before(trees, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky-Bridge.exe", "bridge", "--port", "4200"])
    monkeypatch.setattr(sys, "executable", str(trees["install"] / "UEFN-Ducky-Bridge.exe"))
    ran: list[tuple[Path, list[str]]] = []
    monkeypatch.setattr(av, "run_attached", lambda exe, args: ran.append((exe, list(args))) or 3)
    with pytest.raises(SystemExit):
        av.hand_off(STUB, bridge=True)
    assert ran == [(trees[NEW] / "UEFN-Ducky-Bridge.exe", ["bridge", "--port", "4200"])]


# --- new -> old after a rollback -------------------------------------------------------


def test_a_failed_new_start_rolls_back_to_the_old_version(trees, starts, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    install_exe = trees["install"] / "UEFN-Ducky.exe"
    monkeypatch.setenv(av.LAUNCHER_ENV, str(install_exe))
    monkeypatch.setattr(sys, "executable", str(trees[NEW] / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe"])

    assert av.fall_back_after_failed_start(NEW, "crashed at start") is True
    assert starts == [(install_exe, _WAIT_FOR_ME)]
    assert av.is_bad(NEW)

    # The installed stub starts again and now lands on the old version.
    starts.clear()
    monkeypatch.setattr(sys, "executable", str(install_exe))
    _hand_off([])
    assert starts == [(trees[OLD] / "UEFN-Ducky.exe", [])]


def test_a_rollback_ignores_the_launcher(trees, starts, monkeypatch) -> None:
    # Started through Ducky-Launcher.exe: the fallback still starts UEFN-Ducky.exe.
    monkeypatch.setenv(av.LAUNCHER_ENV, str(trees["install"] / av.NATIVE_LAUNCHER_EXE))
    monkeypatch.setattr(sys, "executable", str(trees[NEW] / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe"])

    assert av.fall_back_after_failed_start(NEW, "crashed") is True
    assert starts == [(trees["install"] / "UEFN-Ducky.exe", _WAIT_FOR_ME)]


def test_a_failed_backend_only_marks_bad_so_there_is_one_window(trees, starts, monkeypatch) -> None:
    monkeypatch.setattr(sys, "executable", str(trees[NEW] / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe", av.BACKEND_ARG])

    assert av.fall_back_after_failed_start(NEW, "crashed") is False
    assert starts == []  # the launcher exits; the stub that started it falls back
    assert av.is_bad(NEW)
    assert av.newest_usable(STUB) == (OLD, trees[OLD])


def test_the_installed_copy_never_rolls_back(trees, starts, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe"])
    assert av.fall_back_after_failed_start(STUB, "crashed") is False
    assert starts == []


@pytest.mark.parametrize("argv", [[], ["--background"], ["uefn-ducky://store/x"]])
def test_a_hidden_start_stays_hidden_through_the_hand_off(trees, starts, monkeypatch, argv) -> None:
    """launcher.py takes --background out of argv and sets START_HIDDEN_ENV before the
    hand-off: the newer version must get --background back (once), or a sign-in start
    opens a window."""
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setenv(av.START_HIDDEN_ENV, "1")
    _hand_off(argv)
    want = argv if av.BACKGROUND_ARG in argv else [*argv, av.BACKGROUND_ARG]
    assert starts == [(trees[NEW] / "UEFN-Ducky.exe", want)]
    starts.clear()
    monkeypatch.delenv(av.START_HIDDEN_ENV)
    monkeypatch.setattr(sys, "executable", str(trees[NEW] / "UEFN-Ducky.exe"))
    monkeypatch.setenv(av.LAUNCHER_ENV, str(trees["install"] / "UEFN-Ducky.exe"))
    monkeypatch.setenv(av.START_HIDDEN_ENV, "1")
    assert av.fall_back_after_failed_start(NEW, "crashed") is True  # the rollback restart too
    assert starts == [(trees["install"] / "UEFN-Ducky.exe", [*_WAIT_FOR_ME, av.BACKGROUND_ARG])]


def test_failed_while_starting_follows_record_start_and_mark_ready(trees) -> None:
    assert av.failed_while_starting(NEW) is False
    av.record_start(NEW)
    assert av.failed_while_starting(NEW) is True
    av.mark_ready(NEW)
    assert av.failed_while_starting(NEW) is False


def test_a_run_panel_failure_rolls_back_before_the_error_dialog(trees, starts, monkeypatch) -> None:
    """webview_app.run catches _run_panel errors itself: a version that fails there before
    its panel is up must roll back at once (the update drill saw the dialog instead, and
    the version marked bad only on the 3rd start)."""
    from frontend.ui_web import webview_app

    import frontend

    monkeypatch.setattr(frontend, "__version__", NEW)
    monkeypatch.setenv(av.LAUNCHER_ENV, str(trees["install"] / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "executable", str(trees[NEW] / "UEFN-Ducky.exe"))
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky.exe"])
    monkeypatch.delenv(av.START_HIDDEN_ENV, raising=False)
    av.mark_ready(NEW)  # panel came up: a later error is not a failed start
    assert webview_app._roll_back_failed_start(ModuleNotFoundError("x")) is False
    assert starts == [] and not av.is_bad(NEW)
    av.record_start(NEW)  # started by the stub's hand-off, panel not up yet
    assert webview_app._roll_back_failed_start(ModuleNotFoundError("No module named '_tkinter'")) is True
    assert av.is_bad(NEW) and "_tkinter" in av.read_boot()[NEW]["reason"]
    assert starts == [(trees["install"] / "UEFN-Ducky.exe", _WAIT_FOR_ME)]


# --- handover JSON both ways -----------------------------------------------------------


def _old_take_handover(path: Path, max_age_s: float = 120.0) -> dict[str, Any]:
    """The 1.2.470 parse rules, frozen: a dict with a fresh ``at``; other fields free."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or time.time() - float(data.get("at") or 0) > max_age_s:
        return {}
    return data


def test_an_old_handover_is_accepted_by_the_new_code(trees) -> None:
    old = {
        "hidden": False,
        "maximized": True,
        "from": OLD,
        "chats": [{"id": "c1", "title": "Running chat"}],
        "at": time.time(),
    }
    (av.versions_dir() / av.HANDOVER_FILE).write_text(json.dumps(old), encoding="utf-8")
    assert av.take_handover() == old
    assert av.take_handover() == {}


def test_a_new_handover_is_accepted_by_the_old_rules(trees) -> None:
    av.write_handover(
        {"hidden": True, "maximized": False, "from": NEW, "chats": [], "native_launcher": "on"}
    )
    got = _old_take_handover(av.versions_dir() / av.HANDOVER_FILE)
    assert got["hidden"] is True and got["from"] == NEW and got["chats"] == []
    assert isinstance(got["at"], float)


def test_a_stale_or_broken_handover_is_ignored(trees) -> None:
    path = av.versions_dir() / av.HANDOVER_FILE
    path.write_text(json.dumps({"hidden": True, "at": time.time() - 3600}), encoding="utf-8")
    assert av.take_handover() == {}
    path.write_text("not json", encoding="utf-8")
    assert av.take_handover() == {}
    path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert av.take_handover() == {}
