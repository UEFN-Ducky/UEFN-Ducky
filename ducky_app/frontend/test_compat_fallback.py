"""Every launcher fallback runs today's Python window and leaves the person's data alone.

For each fallback (20 WebView2 missing or old, 21 protocol mismatch, 22 crash loop,
23 no hello) the launch hook returns False with argv untouched, so launcher.py goes on to
the Python window with the same arguments, and nothing outside ``launcher/`` in app data
changes: panel_settings.json, chats and the WebView2 profile stay byte for byte.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

ns = pytest.importorskip("frontend.native_switch")

from frontend.app_paths import resolve_app_data_dir  # noqa: E402
from frontend.settings import PanelSettings  # noqa: E402

ARGV = ["UEFN-Ducky.exe", r"C:\Islands\My Island\Content\a.verse", "uefn-ducky://chat/c1"]


# --- fixtures ---------------------------------------------------------------------------


class _Proc:
    """A launcher process that exits with ``code`` (None: never exits, never paints)."""

    def __init__(self, code: int | None) -> None:
        self.pid = 424242
        self.code = code
        self.killed = False

    def poll(self) -> int | None:
        return self.code

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Temp app data with settings, a chat and a WebView2 profile, launcher next to the EXE."""
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    monkeypatch.delenv(ns.NATIVE_CHILD_ENV, raising=False)
    root = resolve_app_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    chat = root / "chats" / "projects" / "_no_project" / "conversations" / "c1"
    chat.mkdir(parents=True, exist_ok=True)
    (chat / "messages.json").write_text(json.dumps([{"role": "user", "text": "hi"}]), encoding="utf-8")
    profile = root / "webview" / "EBWebView" / "Default"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "Preferences").write_text("{}", encoding="utf-8")
    exe = tmp_path / "Programs" / "UEFN Ducky" / ns.LAUNCHER_EXE_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(ns.LAUNCHER_EXE_ENV, str(exe))
    monkeypatch.setattr(ns, "webview2_ok", lambda: True)
    return root


@pytest.fixture
def starts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """record_start_soon, kept in a list (no thread, no usage stats database)."""
    rows: list[tuple[Any, ...]] = []
    monkeypatch.setattr(ns, "record_start_soon", lambda *a: rows.append(a))
    return rows


def _setting(mode: str) -> None:
    s = PanelSettings.load()
    s.web_access = "on"
    s.native_launcher = mode
    s.save()


def _snapshot(root: Path) -> dict[str, str]:
    """Every file in app data outside launcher/ (the switch's own folder), by content hash."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not rel.startswith("launcher/"):
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _spawn_exiting(monkeypatch: pytest.MonkeyPatch, code: int | None) -> list[list[str]]:
    cmds: list[list[str]] = []

    def spawn(cmd: list[str], env: dict[str, str]) -> _Proc:
        assert env[ns.NATIVE_CHILD_ENV] == "1"
        cmds.append(cmd)
        return _Proc(code)

    monkeypatch.setattr(ns, "_spawn", spawn)
    return cmds


def _no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object) -> None:
        raise AssertionError("the launcher was started")

    monkeypatch.setattr(ns, "_spawn", boom)


def _falls_back(root: Path, argv: list[str]) -> None:
    """The hook says 'run the Python window', argv as it was, the person's files untouched."""
    from frontend.ui_web.webview_app import webview_profile_dir

    profile = webview_profile_dir()
    before = _snapshot(root)
    assert "panel_settings.json" in before and any(k.startswith("chats/") for k in before)
    assert ns.maybe_start_native(argv) is False
    assert argv == ARGV
    assert _snapshot(root) == before
    assert webview_profile_dir() == profile == str(root / "webview")


# --- 20: WebView2 missing or old --------------------------------------------------------


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_webview2_missing_runs_python_without_starting_the_launcher(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _setting(mode)
    monkeypatch.setattr(ns, "webview2_ok", lambda: False)
    _no_spawn(monkeypatch)
    _falls_back(appdata, list(ARGV))
    assert starts == [("python", None, None, "webview2")]
    assert not ns.crash_loop_path().exists(), "no WebView2 is not a launcher failure"


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_webview2_old_exit_20_runs_python_with_the_same_argv(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _setting(mode)
    cmds = _spawn_exiting(monkeypatch, ns.EXIT_WEBVIEW2)
    _falls_back(appdata, list(ARGV))
    assert cmds == [[str(ns.launcher_exe()), *ARGV[1:]]]
    assert starts == [("python", None, None, "webview2")]
    assert not ns.crash_loop_path().exists(), "an old WebView2 is not a launcher failure"


# --- 23: no hello -----------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["on", "auto"])
def test_no_hello_exit_23_runs_python_with_the_same_argv(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    _setting(mode)
    cmds = _spawn_exiting(monkeypatch, ns.EXIT_NO_HELLO)
    _falls_back(appdata, list(ARGV))
    assert cmds == [[str(ns.launcher_exe()), *ARGV[1:]]]
    assert starts == [("python", None, None, "no_hello")]
    assert len(json.loads(ns.crash_loop_path().read_text(encoding="utf-8"))["failures"]) == 1


def test_launcher_that_never_paints_is_killed_and_python_runs(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ready file and no exit within the wait: the hook kills it and falls back (23)."""
    _setting("on")
    procs: list[_Proc] = []

    def spawn(cmd: list[str], env: dict[str, str]) -> _Proc:
        procs.append(_Proc(None))
        return procs[-1]

    clock = iter(range(0, 10_000, 20))  # every look at the clock is 20 s later
    monkeypatch.setattr(ns, "_spawn", spawn)
    monkeypatch.setattr(ns.time, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(ns.time, "sleep", lambda _s: None)
    _falls_back(appdata, list(ARGV))
    assert procs and procs[0].killed
    assert starts and starts[0][0] == "python" and starts[0][3] in ("no_hello", "timeout")


# --- 21: protocol mismatch, 22: crash loop ----------------------------------------------


@pytest.mark.parametrize("mode", ["on", "auto"])
@pytest.mark.parametrize(("code", "reason"), [(21, "protocol"), (22, "crash_loop")])
def test_exit_21_and_22_run_python_with_the_same_argv(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch, mode: str, code: int, reason: str
) -> None:
    _setting(mode)
    cmds = _spawn_exiting(monkeypatch, code)
    _falls_back(appdata, list(ARGV))
    assert cmds == [[str(ns.launcher_exe()), *ARGV[1:]]]
    assert starts == [("python", None, None, reason)]
    assert len(json.loads(ns.crash_loop_path().read_text(encoding="utf-8"))["failures"]) == 1


def _at(monkeypatch: pytest.MonkeyPatch, when: float) -> None:
    monkeypatch.setattr(ns.time, "time", lambda: when)


def test_three_failures_hold_auto_on_python_for_24_hours(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setting("auto")
    t0 = 1_800_000_000.0
    cmds = _spawn_exiting(monkeypatch, ns.EXIT_PROTOCOL)
    for i in range(3):  # 3 failures within 10 min write the marker
        _at(monkeypatch, t0 + i * 60)
        _falls_back(appdata, list(ARGV))
    assert len(cmds) == 3
    marker = json.loads(ns.crash_loop_path().read_text(encoding="utf-8"))
    assert marker["until"] == pytest.approx(t0 + 120 + 24 * 3600)
    starts.clear()
    for later in (121, 3600, 24 * 3600 + 119):  # held: no launcher at all
        _at(monkeypatch, t0 + later)
        _falls_back(appdata, list(ARGV))
    assert len(cmds) == 3
    assert [s[3] for s in starts] == ["crash_loop_active"] * 3
    assert len(json.loads(ns.crash_loop_path().read_text(encoding="utf-8")).get("failures", [])) == 0
    _at(monkeypatch, t0 + 24 * 3600 + 121)  # the hold is over: auto tries again
    _falls_back(appdata, list(ARGV))
    assert len(cmds) == 4


def test_on_still_tries_the_launcher_during_the_hold(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setting("on")
    now = 1_800_000_000.0
    _at(monkeypatch, now)
    ns._write_json(ns.crash_loop_path(), {"failures": [], "until": now + 3600})
    cmds = _spawn_exiting(monkeypatch, ns.EXIT_PROTOCOL)
    _falls_back(appdata, list(ARGV))
    assert len(cmds) == 1 and starts == [("python", None, None, "protocol")]


@pytest.mark.parametrize("marker", [None, "{not json", json.dumps({"until": 9e18, "failures": [1, 2]})])
def test_off_never_reads_the_marker(
    appdata: Path, starts: list, monkeypatch: pytest.MonkeyPatch, marker: str | None
) -> None:
    _setting("off")
    if marker is not None:
        ns.crash_loop_path().parent.mkdir(parents=True, exist_ok=True)
        ns.crash_loop_path().write_text(marker, encoding="utf-8")
    before = ns.crash_loop_path().read_bytes() if marker is not None else None

    def boom(*_a: object) -> None:
        raise AssertionError("off read launcher state")

    for name in ("crash_loop_active", "_crash_loop_state", "_read_json", "webview2_ok", "launcher_exe"):
        monkeypatch.setattr(ns, name, boom)
    _no_spawn(monkeypatch)
    _falls_back(appdata, list(ARGV))
    assert starts == []
    if before is None:
        assert not ns.crash_loop_path().exists()
    else:
        assert ns.crash_loop_path().read_bytes() == before
