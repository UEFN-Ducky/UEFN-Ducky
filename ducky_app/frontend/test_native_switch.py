"""The native launcher switch: the setting, the decision, the fallbacks, the rollout numbers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend.app_paths import resolve_app_data_dir
from frontend.settings import PanelSettings, normalize_native_launcher


# --- d2: the native_launcher setting -------------------------------------------------


@pytest.fixture
def files_backend(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", "files")
    path = resolve_app_data_dir() / "panel_settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_default_is_auto() -> None:
    assert PanelSettings().native_launcher == "auto"


def test_old_file_without_the_key_means_auto(files_backend: Path) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "terminals_enabled": False}), encoding="utf-8")
    assert PanelSettings.load().native_launcher == "auto"


def test_saved_off_stays_off(files_backend: Path) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "native_launcher": "off"}), encoding="utf-8")
    s = PanelSettings.load()
    assert s.native_launcher == "off"
    s.save()
    assert PanelSettings.load().native_launcher == "off"


def test_decide_always_starts_the_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher's window is up in ~0.1 s, the Python window in 1-2 s: no setting turns it off."""
    monkeypatch.setattr(ns, "_no_native", False)
    monkeypatch.delenv(ns.NO_NATIVE_ENV, raising=False)
    for setting in ("auto", PanelSettings().native_launcher, "on", "off", "garbage"):
        assert ns.decide(setting, True, True, False) == "native", setting


@pytest.mark.parametrize("raw", ["garbage", "", None, 1, True, ["on"], {"on": 1}, "ON ", "Auto"])
def test_bad_value_counts_as_off_or_is_normalized(files_backend: Path, raw: object) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "native_launcher": raw}), encoding="utf-8")
    got = PanelSettings.load().native_launcher
    # null reads like a missing key (the loader drops nulls): the default, auto.
    expected = "auto" if raw is None else {"ON ": "on", "Auto": "auto"}.get(raw if isinstance(raw, str) else "", "off")
    assert got == expected


def test_normalize_native_launcher() -> None:
    assert [normalize_native_launcher(v) for v in ("off", "on", "auto", "nope", None, 3)] == [
        "off", "on", "auto", "off", "off", "off",
    ]


def test_save_keeps_every_other_key_and_its_order(files_backend: Path) -> None:
    files_backend.write_text(json.dumps({"web_access": "on", "terminals_enabled": False}), encoding="utf-8")
    s = PanelSettings.load()
    before = PanelSettings.load().to_json_dict()
    s.native_launcher = "auto"
    s.save()
    after = json.loads(files_backend.read_text(encoding="utf-8"))
    assert after["native_launcher"] == "auto"
    assert after["web_access"] == "on" and after["terminals_enabled"] is False
    keys = [k for k in after if k != "native_launcher"]
    assert keys == [k for k in before if k != "native_launcher"]
    assert {k: v for k, v in after.items() if k != "native_launcher"} == {
        k: v for k, v in before.items() if k != "native_launcher"
    }
    assert PanelSettings.load().native_launcher == "auto"


def test_save_turns_garbage_into_off(files_backend: Path) -> None:
    s = PanelSettings(web_access="on")
    s.native_launcher = "warp-speed"
    s.save()
    assert json.loads(files_backend.read_text(encoding="utf-8"))["native_launcher"] == "off"


def test_database_round_trip() -> None:
    s = PanelSettings.load()
    s.native_launcher = "on"
    s.save()
    assert PanelSettings.load().native_launcher == "on"


def test_schema_describes_the_setting() -> None:
    from frontend.settings_schema import settings_schema

    entry = settings_schema()["fields"]["native_launcher"]
    assert entry["default"] == "auto"
    assert entry["enum"] == ["off", "on", "auto"]
    assert entry["settable"] is False


# --- d3: decide / run_native / the launch hook ----------------------------------------

from frontend import native_switch as ns  # noqa: E402


@pytest.mark.parametrize(
    "setting, exe, wv2, loop, expected",
    [
        ("off", True, True, False, "native"),
        ("off", True, True, True, "native"),
        ("garbage", True, True, False, "native"),
        ("on", True, True, False, "native"),
        ("on", True, True, True, "native"),
        ("on", False, True, False, "python"),
        ("on", True, False, False, "python"),
        ("auto", True, True, False, "native"),
        ("auto", True, True, True, "native"),
        ("auto", False, True, False, "python"),
        ("auto", True, False, False, "python"),
    ],
)
def test_decision_table(setting: str, exe: bool, wv2: bool, loop: bool, expected: str) -> None:
    assert ns.decide(setting, exe, wv2, loop) == expected


def test_python_reason() -> None:
    assert ns.python_reason("off", False, False, True) == "no_exe"
    assert ns.python_reason("on", False, True, False) == "no_exe"
    assert ns.python_reason("on", True, False, False) == "webview2"
    assert ns.python_reason("auto", True, False, False) == "webview2"
    assert ns.python_reason("on", True, True, True) == ""


class FakeProc:
    def __init__(self, pid: int, *, exit_after: float | None = None, code: int = 0,
                 ready_after: float | None = None, ready: dict | None = None) -> None:
        self.pid = pid
        self.started = time_now()
        self.exit_after = exit_after
        self.code = code
        self.ready_after = ready_after
        self.ready = ready if ready is not None else {"painted_ms": 640, "window_ms": 95}
        self.killed = False

    def poll(self) -> int | None:
        elapsed = time_now() - self.started
        if self.ready_after is not None and elapsed >= self.ready_after:
            path = ns.ready_path(self.pid)
            if not path.exists() and not getattr(self, "_wrote", False):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(self.ready), encoding="utf-8")
                self._wrote = True
        if self.killed:
            return 1
        if self.exit_after is not None and elapsed >= self.exit_after:
            return self.code
        return None

    def kill(self) -> None:
        self.killed = True


def time_now() -> float:
    import time

    return time.monotonic()


def _spawner(proc: FakeProc, calls: list):
    def spawn(cmd: list[str], env: dict[str, str]) -> FakeProc:
        calls.append((cmd, env))
        return proc

    return spawn


def test_run_native_ready_returns_none(tmp_path: Path) -> None:
    calls: list = []
    proc = FakeProc(4242, ready_after=0.0)
    out = ns.run_native(["UEFN-Ducky.exe", "uefn-ducky://x", "--flag"], 5, spawn=_spawner(proc, calls),
                        exe=tmp_path / "Ducky-Launcher.exe")
    assert out is None
    assert ns.last_ready == {"painted_ms": 640, "window_ms": 95}
    cmd, env = calls[0]
    assert cmd == [str(tmp_path / "Ducky-Launcher.exe"), "uefn-ducky://x", "--flag"]
    assert env[ns.NATIVE_CHILD_ENV] == "1"
    assert not ns.ready_path(4242).exists()


@pytest.mark.parametrize("code", [20, 21, 22, 23, 24, 7])
def test_run_native_exit_code_means_fallback(tmp_path: Path, code: int) -> None:
    proc = FakeProc(5000 + code, exit_after=0.0, code=code)
    assert ns.run_native(["x"], 5, spawn=_spawner(proc, []), exe=tmp_path / "L.exe") == code


def test_run_native_exit_zero_is_ready(tmp_path: Path) -> None:
    proc = FakeProc(6000, exit_after=0.0, code=0)
    assert ns.run_native(["x"], 5, spawn=_spawner(proc, []), exe=tmp_path / "L.exe") is None


def test_run_native_timeout_kills_and_returns_23(tmp_path: Path) -> None:
    proc = FakeProc(6001)
    assert ns.run_native(["x"], 0.05, spawn=_spawner(proc, []), exe=tmp_path / "L.exe") == 23
    assert proc.killed


def test_run_native_spawn_error_returns_24(tmp_path: Path) -> None:
    def boom(cmd, env):
        raise OSError("nope")

    assert ns.run_native(["x"], 5, spawn=boom, exe=tmp_path / "L.exe") == 24


def test_real_spawn_of_a_missing_exe_returns_24(tmp_path: Path) -> None:
    assert ns.run_native(["x"], 1, exe=tmp_path / "missing" / "Ducky-Launcher.exe") == 24


def test_crash_loop_three_failures_in_ten_minutes_hold_auto_for_a_day() -> None:
    t = 1_000_000.0
    assert not ns.note_failure(t)
    assert not ns.note_failure(t + 60)
    assert not ns.crash_loop_active(t + 61)
    assert ns.note_failure(t + 120)
    assert ns.crash_loop_active(t + 121)
    assert ns.crash_loop_active(t + 120 + 24 * 3600 - 1)
    assert not ns.crash_loop_active(t + 120 + 24 * 3600 + 1)
    # The hold is the launcher supervisor's own; this side still starts the launcher.
    assert ns.decide("auto", True, True, ns.crash_loop_active(t + 121)) == "native"


def test_old_failures_drop_out_of_the_window() -> None:
    t = 2_000_000.0
    ns.note_failure(t)
    ns.note_failure(t + 1)
    assert not ns.note_failure(t + 11 * 60)
    assert not ns.crash_loop_active(t + 11 * 60)


def test_crash_loop_file_written_by_the_launcher_is_read() -> None:
    import time

    path = ns.crash_loop_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"failures": [], "until": time.time() + 3600, "extra": 1}), encoding="utf-8")
    assert ns.crash_loop_active()
    path.write_text("not json", encoding="utf-8")
    assert not ns.crash_loop_active()


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> list:
    spawned: list = []

    def refuse(*a, **k):
        spawned.append(a)
        raise AssertionError("off must never spawn")

    monkeypatch.setattr(ns.subprocess, "Popen", refuse)
    monkeypatch.setattr(ns, "_spawn", refuse)
    monkeypatch.setattr(ns, "run_native", refuse)
    return spawned


def test_off_still_starts_the_launcher(launcher_present: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sync_records(monkeypatch)
    started: list = []
    monkeypatch.setattr(ns, "run_native", lambda argv, timeout_s, exe=None: started.append(argv) or None)
    for setting in ("off", "junk", "auto", None):
        assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting=setting) is True
    assert len(started) == 4 and all(r[0] == "native" for r in rows)


def test_hidden_start_backend_and_child_never_spawn(no_spawn: list, monkeypatch: pytest.MonkeyPatch) -> None:
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on", start_hidden=True) is False
    assert ns.maybe_start_native(["UEFN-Ducky.exe", "--backend"], setting="on") is False
    monkeypatch.setenv(ns.NATIVE_CHILD_ENV, "1")
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert no_spawn == []


@pytest.fixture
def launcher_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    exe = tmp_path / "Ducky-Launcher.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(ns.LAUNCHER_EXE_ENV, str(exe))
    monkeypatch.setattr(ns, "webview2_ok", lambda: True)
    monkeypatch.delenv(ns.NATIVE_CHILD_ENV, raising=False)
    return exe


def _sync_records(monkeypatch: pytest.MonkeyPatch) -> list:
    rows: list = []
    monkeypatch.setattr(ns, "record_start_soon", lambda *a: rows.append(a))
    monkeypatch.setattr(ns, "record_start", lambda *a: rows.append(a))
    return rows


def test_hook_native_ready(launcher_present: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sync_records(monkeypatch)
    proc = FakeProc(7001, ready_after=0.0)
    monkeypatch.setattr(ns, "_spawn", _spawner(proc, []))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is True
    assert rows == [("native", 95, 640, "")]


@pytest.mark.parametrize("code, reason", [(20, "webview2"), (21, "protocol"), (22, "crash_loop.other"),
                                          (23, "no_hello"), (24, "start_error"), (9, "exit_other")])
def test_hook_each_exit_code_falls_back(launcher_present: Path, monkeypatch: pytest.MonkeyPatch,
                                        code: int, reason: str) -> None:
    rows = _sync_records(monkeypatch)
    monkeypatch.setattr(ns, "_spawn", _spawner(FakeProc(7100 + code, exit_after=0.0, code=code), []))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert rows == [("python", None, None, reason)]
    marker = ns.crash_loop_path()
    failures = json.loads(marker.read_text(encoding="utf-8"))["failures"] if marker.is_file() else []
    # webview2 never tried the launcher; a 22 is already counted by the launcher's supervisor.
    assert len(failures) == (0 if reason in ("webview2", "crash_loop.other") else 1)


def test_crash_loop_fallback_names_the_launchers_slug(launcher_present: Path, monkeypatch: pytest.MonkeyPatch,
                                                      tracked: list) -> None:
    # What the launcher's supervisor leaves after 3 backends died on a 10013 bind.
    ns._write_json(ns.crash_loop_path(), {"failures": [1.0], "until": 0, "reason": "port_blocked"})
    real_record = ns.record_start
    rows = _sync_records(monkeypatch)
    monkeypatch.setattr(ns, "_spawn", _spawner(FakeProc(7400, exit_after=0.0, code=22), []))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert rows == [("python", None, None, "crash_loop.port_blocked")]
    row = real_record("python", None, None, "crash_loop.port_blocked")
    assert row["fallback"] == "crash_loop.port_blocked"
    # A slug off the fixed list never goes out as free text.
    ns._write_json(ns.crash_loop_path(), {"reason": "C:/Users/x"})
    assert ns.fallback_reason(22) == "crash_loop.other"
    from frontend import usage_events as ev

    assert ev.validate("launcher.start", {"mode": "python", "fallback": "crash_loop.port_blocked"})["fallback"] \
        == "crash_loop.port_blocked"


def test_hook_timeout_falls_back(launcher_present: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sync_records(monkeypatch)
    proc = FakeProc(7200)
    monkeypatch.setattr(ns, "_spawn", _spawner(proc, []))
    real = ns.run_native
    monkeypatch.setattr(ns, "run_native", lambda argv, timeout_s=15, **k: real(argv, 0.05, **k))
    monkeypatch.setattr(ns.time, "perf_counter", _fake_clock([0.0, 23.0, 23.0]))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert proc.killed
    assert rows == [("python", None, None, "timeout")]


def test_hook_waits_for_the_launchers_own_no_hello(launcher_present: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The launcher's 15 s hello wait starts after the launcher is up: its exit 23 at ~16 s
    must arrive before the hook gives up, and read as no_hello, not timeout."""
    rows = _sync_records(monkeypatch)
    assert ns.NATIVE_WAIT_S > 15 + 5
    calls: list[float] = []
    real = ns.run_native

    def run(argv, timeout_s=15, **k):
        calls.append(timeout_s)
        return real(argv, 0.05, **k)

    monkeypatch.setattr(ns, "run_native", run)
    monkeypatch.setattr(ns, "_spawn", _spawner(FakeProc(7300, exit_after=0.0, code=23), []))
    monkeypatch.setattr(ns.time, "perf_counter", _fake_clock([0.0, 16.0, 16.0]))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert calls == [ns.NATIVE_WAIT_S]
    assert rows == [("python", None, None, "no_hello")]


def _fake_clock(values: list[float]):
    it = iter(values)
    last = [values[-1]]

    def clock() -> float:
        try:
            last[0] = next(it)
        except StopIteration:
            pass
        return last[0]

    return clock


def test_hook_spawn_error_falls_back(launcher_present: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = _sync_records(monkeypatch)

    def boom(cmd, env):
        raise OSError("blocked by antivirus")

    monkeypatch.setattr(ns, "_spawn", boom)
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert rows == [("python", None, None, "start_error")]


def test_hook_missing_exe_or_webview2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_spawn: list) -> None:
    rows = _sync_records(monkeypatch)
    monkeypatch.setenv(ns.LAUNCHER_EXE_ENV, str(tmp_path / "nope.exe"))
    monkeypatch.delenv(ns.NATIVE_CHILD_ENV, raising=False)
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    exe = tmp_path / "Ducky-Launcher.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setenv(ns.LAUNCHER_EXE_ENV, str(exe))
    monkeypatch.setattr(ns, "webview2_ok", lambda: False)
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert rows == [("python", None, None, "no_exe"), ("python", None, None, "webview2")]
    assert no_spawn == []





# --- d4: rollout numbers ---------------------------------------------------------------


@pytest.fixture
def tracked(monkeypatch: pytest.MonkeyPatch) -> list:
    from frontend import usage_stats

    events: list = []
    monkeypatch.setattr(usage_stats, "track", lambda name, **props: events.append((name, props)) or True)
    return events


def test_one_event_per_native_start(tracked: list) -> None:
    row = ns.record_start("native", 95.4, 640, "")
    assert row["mode"] == "native" and row["window_ms"] == 95 and row["painted_ms"] == 640
    assert tracked == [("launcher.start", {"mode": "native", "fallback": "none", "painted_ms": 640, "window_ms": 95})]


def test_fallback_start_sends_its_reason(tracked: list) -> None:
    ns.record_start("python", None, None, "no_hello")
    names = [n for n, _ in tracked]
    assert names.count("launcher.start") == 1
    assert tracked[0] == ("launcher.start", {"mode": "python", "fallback": "no_hello"})
    code = [p for n, p in tracked if n == "error.code"]
    assert len(code) == 1 and code[0]["code"] == "native_launcher_fallback" and code[0]["reason"] == "no_hello"


@pytest.mark.parametrize("reason", sorted(ns._NOT_A_FAILURE))
def test_expected_fallback_is_not_an_error(tracked: list, reason: str) -> None:
    ns.record_start("python", None, None, reason)
    assert [n for n, _ in tracked] == ["launcher.start"]
    assert tracked[0][1]["fallback"] == reason


def test_timeout_fallback_still_sends_its_error(tracked: list) -> None:
    ns.record_start("python", None, None, "timeout")
    code = [p for n, p in tracked if n == "error.code"]
    assert len(code) == 1 and code[0]["reason"] == "timeout"


def test_fallback_event_passes_the_existing_allowlist() -> None:
    from frontend import usage_events as ev

    cleaned = ev.validate("error.code", {"code": "native_launcher_fallback", "where": "ui", "reason": "no_hello"})
    assert cleaned == {"code": "native_launcher_fallback", "where": "ui", "reason": "no_hello"}


def test_free_text_reason_is_never_sent(tracked: list) -> None:
    row = ns.record_start("python", "x", -5, "C:/Users/someone/secret path")
    assert row["fallback"] == "other" and row["window_ms"] is None and row["painted_ms"] is None
    assert all("secret" not in json.dumps(p) for _, p in tracked)


def test_hook_records_exactly_one_start(launcher_present: Path, monkeypatch: pytest.MonkeyPatch,
                                        tracked: list) -> None:
    monkeypatch.setattr(ns, "record_start_soon", ns.record_start)
    monkeypatch.setattr(ns, "_spawn", _spawner(FakeProc(8001, ready_after=0.0), []))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is True
    monkeypatch.setattr(ns, "_spawn", _spawner(FakeProc(8002, exit_after=0.0, code=21), []))
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="on") is False
    assert [n for n, _ in tracked].count("launcher.start") == 2
    assert ns.start_summary()["starts"] == 2


def test_summary_counts_fallbacks(tracked: list) -> None:
    assert ns.start_summary()["starts"] == 0
    for painted in (600, 800, 1000):
        ns.record_start("native", 90, painted, "")
    ns.record_start("python", None, None, "no_hello")
    ns.record_start("python", None, None, "no_hello")
    ns.record_start("python", None, None, "webview2")
    s = ns.start_summary()
    assert s["starts"] == 6 and s["native"] == 3 and s["python"] == 3
    assert s["fallbacks"] == {"no_hello": 2, "webview2": 1}
    # webview2 never tried the launcher: 2 failures out of 5 tries.
    assert s["fallback_rate"] == 0.4
    assert s["painted_ms_median"] == 800 and s["painted_ms_max"] == 1000
    assert s["crash_loop_active"] is False


def test_starts_file_keeps_the_last_200(tracked: list) -> None:
    for _ in range(ns.STARTS_KEPT + 5):
        ns.record_start("native", 1, 2, "")
    assert ns.start_summary()["starts"] == ns.STARTS_KEPT


def test_launcher_main_exits_when_native_painted(monkeypatch: pytest.MonkeyPatch) -> None:
    src = (Path(ns.__file__).parent / "launcher.py").read_text(encoding="utf-8")
    hook = src.index("maybe_start_native(sys.argv, start_hidden=start_hidden)")
    assert src.index("_hand_off_to_newest(bridge=False)") < hook < src.index("from frontend.ui_web.run import run")


# --- UEFN_DUCKY_NO_NATIVE: the launcher's own fallback never loops --------------------


@pytest.fixture
def no_native(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "_no_native", False)
    monkeypatch.setenv(ns.NO_NATIVE_ENV, "1")


@pytest.mark.parametrize("setting", ["on"])
def test_no_native_decides_python(no_native: None, setting: str) -> None:
    assert ns.decide(setting, True, True, False) == "python"
    assert ns.python_reason(setting, True, True, False) == "launcher_fallback"


@pytest.mark.parametrize("setting", ["on"])
def test_no_native_hook_runs_python_and_drops_the_env(no_native: None, launcher_present: Path,
                                                    no_spawn: list, monkeypatch: pytest.MonkeyPatch,
                                                    setting: str) -> None:
    import os

    rows = _sync_records(monkeypatch)
    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting=setting) is False
    assert ns.NO_NATIVE_ENV not in os.environ  # agent hosts, bridges: nothing to inherit
    assert no_spawn == [] and rows == [("python", None, None, "launcher_fallback")]
    # Still python after the env is gone, and not a crash-loop failure.
    assert ns.decide(setting, True, True, False) == "python"
    assert not ns.crash_loop_path().exists()


def test_no_native_dropped_even_when_off(no_native: None, no_spawn: list) -> None:
    import os

    assert ns.maybe_start_native(["UEFN-Ducky.exe"], setting="off") is False
    assert ns.NO_NATIVE_ENV not in os.environ


def test_child_env_has_no_no_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ns, "_no_native", False)
    monkeypatch.setenv(ns.NO_NATIVE_ENV, "1")
    ns.take_no_native()
    seen: list[dict] = []

    def spawn(cmd, env):
        seen.append(env)
        raise OSError("no")

    assert ns.run_native(["UEFN-Ducky.exe"], 0, spawn=spawn, exe=tmp_path / "x.exe") == ns.EXIT_START_ERROR
    assert ns.NO_NATIVE_ENV not in seen[0]


def test_without_no_native_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ns, "_no_native", False)
    monkeypatch.delenv(ns.NO_NATIVE_ENV, raising=False)
    assert ns.decide("on", True, True, False) == "native"
    assert ns.decide("auto", True, True, False) == "native"
    monkeypatch.setenv(ns.NO_NATIVE_ENV, "0")
    assert ns.decide("on", True, True, False) == "native"
