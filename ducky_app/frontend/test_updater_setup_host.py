"""In-app updater against the signed Ducky Setup host (and the plain Inno stub).

Every install in the wild runs the downloaded Setup with the same switches; the
host must accept them and the updater must follow the host's process model.
No real installer is ever started here: subprocess and process probes are fakes.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import pytest

import frontend.updater as updater

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "release" / "installer" / "host"

# The argv every updater generation passes (identical in 1.0.626 .. 1.2.342:
# Init e070c3d, ef796b9, 1ba1908, 2bff572, 0840a9a).
LEGACY_ARGV = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS"]


@pytest.fixture(autouse=True)
def _never_exit(monkeypatch):
    armed: list[str] = []
    monkeypatch.setattr(updater, "_shutdown_after_delay", lambda: armed.append("shutdown"))
    monkeypatch.setattr(updater, "_exit_self_after_delay", lambda: armed.append("exit_self"))
    return armed


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(updater, "_LAUNCH_RETRY_S", 0.0)
    monkeypatch.setattr(updater, "_ELEVATION_HANDOFF_S", 0.0)
    monkeypatch.setattr(updater, "_HANDOFF_POLL_S", 0.0)
    monkeypatch.setattr(updater.time, "sleep", lambda _s: None)


def _reset() -> None:
    updater._cancel.clear()
    updater._set_progress(stage="idle", downloaded_bytes=0, total_bytes=0, error=None)


# --- argv -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "flag"),
    [("user", "/CURRENTUSER"), (None, "/CURRENTUSER"), ("weird", "/CURRENTUSER"), ("machine", "/ALLUSERS")],
)
def test_argv_is_unchanged_from_every_installed_generation(scope, flag) -> None:
    assert updater._silent_install_args(scope) == [*LEGACY_ARGV, flag]


def test_host_treats_updater_argv_as_silent_and_forwards_it() -> None:
    """HostArgs.IsSilent keys on /VERYSILENT; Run() passes the args through untouched."""
    host_args = (HOST / "HostArgs.cs").read_text(encoding="utf-8")
    program = (HOST / "Program.cs").read_text(encoding="utf-8")
    engine = (HOST / "Engine.cs").read_text(encoding="utf-8")
    assert '"/VERYSILENT"' in host_args and '"/SILENT"' in host_args
    assert "OrdinalIgnoreCase" in host_args
    # Silent branch is taken before any window, WebView2 or message box.
    main = program.split("static int Main", 1)[1].split("static int Silent", 1)[0]
    assert main.index("HostArgs.IsSilent(args)") < main.index("AssemblyResolve")
    silent = program.split("static int Silent", 1)[1].split("static int Go", 1)[0]
    assert "MessageBox" not in silent and "Engine.Run(args)" in silent
    run = engine.split("public static int Run(", 1)[1].split("public static Process Start(", 1)[0]
    assert "Wait(Start(path, args), path)" in run
    assert "Arguments = HostArgs.Join(args)" in engine


def test_same_appid_everywhere() -> None:
    from frontend.install_info import INNO_APP_ID, UNINSTALL_KEY

    iss = (ROOT / "release" / "installer" / "UEFN-Ducky.iss").read_text(encoding="utf-8")
    engine = (HOST / "Engine.cs").read_text(encoding="utf-8")
    guid = re.search(r'#define MyAppId "([0-9A-F-]+)"', iss).group(1)
    assert INNO_APP_ID == "{" + guid + "}"
    assert UNINSTALL_KEY.split("\\")[-1] == "{" + guid + "}_is1"
    assert "{" + guid + "}_is1" in engine
    assert guid == "EAD694ED-E221-40B0-909B-AFFD7F683C9E"  # never changes


def test_handoff_event_name_matches_host() -> None:
    engine = (HOST / "Engine.cs").read_text(encoding="utf-8")
    m = re.search(r'HandoffEventName\(int hostPid\) => @"([^"]+)" \+ hostPid', engine)
    assert m, "host must name the event as the updater expects"
    assert updater._HANDOFF_EVENT.format(pid=42) == m.group(1) + "42"


# --- which processes mean "Setup is still running" ---------------------------


class _Completed:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _tasklist(monkeypatch, *images: str) -> None:
    out = "".join(f'"{name}","{i + 10}","Console","1","1,000 K"\n' for i, name in enumerate(images))
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **k: _Completed(out))


def test_running_image_names_parses_csv(monkeypatch) -> None:
    _tasklist(monkeypatch, "explorer.exe", "Setup-engine.exe", "Setup-1.2.343.exe")
    assert updater._running_image_names() == {"explorer.exe", "setup-engine.exe", "setup-1.2.343.exe"}


@pytest.mark.parametrize(
    ("images", "expected"),
    [
        (("Setup-1.2.343.exe",), True),  # plain Inno stub, or the host itself
        (("Setup-engine.exe",), True),  # host gone, elevated engine installing
        (("SETUP-ENGINE.EXE",), True),
        (("Setup-1.2.342.exe", "UEFN-Ducky.exe"), False),  # other version / the app
        ((), False),
    ],
)
def test_installer_process_running_sees_host_and_engine(monkeypatch, images, expected) -> None:
    _tasklist(monkeypatch, *images)
    assert updater._installer_process_running(Path("C:/t/Setup-1.2.343.exe")) is expected


def test_installer_process_running_false_when_tasklist_fails(monkeypatch) -> None:
    monkeypatch.setattr(updater.sys, "platform", "win32")

    def boom(*_a, **_k):
        raise OSError("no tasklist")

    monkeypatch.setattr(updater.subprocess, "run", boom)
    assert updater._installer_process_running(Path("Setup-1.exe")) is False


# --- machine scope: host waits for the elevated engine ------------------------


class FakeHost:
    """A Setup process: exits with ``code`` after ``polls_until_exit`` polls."""

    def __init__(self, code: int = 0, polls_until_exit: int = 0, pid: int = 4242) -> None:
        self.pid = pid
        self.code = code
        self.left = polls_until_exit
        self.polls = 0

    def poll(self) -> int | None:
        self.polls += 1
        if self.left > 0:
            self.left -= 1
            return None
        return self.code

    def wait(self) -> int:
        raise AssertionError("must poll, not block, so the hand-off can be seen")


def test_machine_scope_closes_panel_once_host_signals_handoff(monkeypatch, fast) -> None:
    host = FakeHost(code=0, polls_until_exit=10_000)
    monkeypatch.setattr(updater, "_popen_setup", lambda _d, _a: host)
    seen = {"n": 0}

    def signalled(pid: int) -> bool:
        assert pid == host.pid
        seen["n"] += 1
        return seen["n"] >= 3  # UAC answered on the third look

    monkeypatch.setattr(updater, "_host_handoff_signalled", signalled)
    monkeypatch.setattr(updater, "_installer_process_running", lambda _d: pytest.fail("not needed"))
    assert updater._launch_setup_until_handoff(Path("Setup-9.exe"), ["/VERYSILENT"]) == (0, True)
    assert host.polls == 3


def test_machine_scope_plain_stub_exit_then_elevated_child(monkeypatch, fast) -> None:
    """Unsigned fallback (plain Inno stub): exits 0 after UAC Yes, child still listed."""
    monkeypatch.setattr(updater, "_popen_setup", lambda _d, _a: FakeHost(code=0))
    monkeypatch.setattr(updater, "_host_handoff_signalled", lambda _p: False)
    monkeypatch.setattr(updater, "_installer_process_running", lambda _d: True)
    assert updater._launch_setup_until_handoff(Path("Setup-9.exe"), ["/VERYSILENT"]) == (0, True)


def test_machine_scope_host_finished_install_itself(monkeypatch, fast) -> None:
    """Host waited for the whole elevated install and returned 0: done, nothing left."""
    monkeypatch.setattr(updater, "_popen_setup", lambda _d, _a: FakeHost(code=0, polls_until_exit=5))
    monkeypatch.setattr(updater, "_host_handoff_signalled", lambda _p: False)
    monkeypatch.setattr(updater, "_installer_process_running", lambda _d: False)
    assert updater._launch_setup_until_handoff(Path("Setup-9.exe"), ["/VERYSILENT"]) == (0, False)


def test_machine_scope_uac_declined_retries_once_then_reports(monkeypatch, fast) -> None:
    launches = {"n": 0}

    def popen(_d, _a):
        launches["n"] += 1
        return FakeHost(code=2)  # engine exit code passed back by the host

    monkeypatch.setattr(updater, "_popen_setup", popen)
    monkeypatch.setattr(updater, "_host_handoff_signalled", lambda _p: False)
    monkeypatch.setattr(updater, "_installer_process_running", lambda _d: False)
    assert updater._launch_setup_until_handoff(Path("Setup-9.exe"), ["/VERYSILENT"]) == (2, False)
    assert launches["n"] == 2


def test_wait_setup_exit_maps_oserror_to_failure(fast) -> None:
    class Broken:
        pid = 1

        def poll(self):
            raise OSError("handle gone")

    assert updater._wait_setup_exit(Broken()) == 1


def test_user_scope_never_waits_for_host(monkeypatch, fast) -> None:
    host = FakeHost(code=0, polls_until_exit=10_000)
    monkeypatch.setattr(updater, "_popen_setup", lambda _d, _a: host)
    monkeypatch.setattr(updater, "_host_handoff_signalled", lambda _p: pytest.fail("user scope"))
    assert updater._launch_setup_until_handoff(
        Path("Setup-9.exe"), ["/VERYSILENT"], wait_for_elevation=False
    ) == (0, True)


@pytest.mark.skipif(sys.platform != "win32", reason="named kernel events")
def test_host_handoff_signalled_reads_a_real_named_event() -> None:
    import ctypes
    import os

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.restype = ctypes.c_void_p
    kernel32.CreateEventW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    fake_host_pid = 900_000_000 + os.getpid()  # no real host has this PID
    assert updater._host_handoff_signalled(fake_host_pid) is False
    handle = kernel32.CreateEventW(None, 1, 1, updater._HANDOFF_EVENT.format(pid=fake_host_pid))
    assert handle
    try:
        assert updater._host_handoff_signalled(fake_host_pid) is True
    finally:
        kernel32.CloseHandle(handle)
    assert updater._host_handoff_signalled(fake_host_pid) is False  # gone with the host


# --- apply_update end to end (fakes for network and Setup) --------------------


def _status(scope: str, sha: str, version: str = "1.2.343") -> dict:
    return {
        "channel": "installed",
        "installed": True,
        "update_available": True,
        "installer_url": "https://uefnducky.org/x/UEFN-Ducky-Setup-1.2.343.exe",
        "installer_sha256": sha,
        "remote_version": version,
        "install_scope": scope,
    }


@pytest.fixture
def harness(monkeypatch, tmp_path, fast):
    cache = tmp_path / "cache"
    cache.mkdir()
    calls: dict = {"downloads": 0, "launch": []}
    monkeypatch.setattr(updater, "installer_cache_dir", lambda: cache)
    monkeypatch.setattr(updater, "_stop_all_agents", lambda: None)
    monkeypatch.setattr(updater, "_prepare_installer_exe", lambda _p: None)
    monkeypatch.setattr("frontend.frozen_process.kill_uefn_ducky_processes", lambda include_self=False: True)

    def launch(dest, args, *, wait_for_elevation=True):
        calls["launch"].append((dest, list(args), wait_for_elevation, dest.read_bytes()))
        return calls.get("result", (0, True))

    monkeypatch.setattr(updater, "_launch_setup_until_handoff", launch)
    _reset()
    yield cache, calls
    _reset()


def test_stale_unsigned_cache_is_replaced_by_signed_download(monkeypatch, harness, _never_exit) -> None:
    cache, calls = harness
    signed = b"MZ signed ducky setup host 1.2.343"
    (cache / "Setup-1.2.343.exe").write_bytes(b"MZ old unsigned inno stub 1.2.343")
    monkeypatch.setattr(updater, "get_app_update_status", lambda: _status("user", hashlib.sha256(signed).hexdigest()))

    def download(_url, dest, **_k):
        calls["downloads"] += 1
        dest.write_bytes(signed)
        return None

    monkeypatch.setattr(updater, "_download", download)
    result = updater.apply_update()
    assert result == {"ok": True, "error": None, "stage": "restarting"}
    assert calls["downloads"] == 1
    dest, args, waited, launched_bytes = calls["launch"][0]
    assert dest.name == "Setup-1.2.343.exe" and launched_bytes == signed
    assert args == [*LEGACY_ARGV, "/CURRENTUSER"] and waited is False
    assert _never_exit == ["shutdown"]


def test_matching_cache_is_reused_without_download(monkeypatch, harness) -> None:
    cache, calls = harness
    signed = b"MZ signed"
    (cache / "Setup-1.2.343.exe").write_bytes(signed)
    monkeypatch.setattr(updater, "get_app_update_status", lambda: _status("machine", hashlib.sha256(signed).hexdigest()))
    monkeypatch.setattr(updater, "_download", lambda *_a, **_k: pytest.fail("cache matches the feed"))
    assert updater.apply_update()["ok"] is True
    _dest, args, waited, _b = calls["launch"][0]
    assert args == [*LEGACY_ARGV, "/ALLUSERS"] and waited is True


def test_bad_digest_never_launches(monkeypatch, harness) -> None:
    cache, calls = harness
    monkeypatch.setattr(updater, "get_app_update_status", lambda: _status("user", "0" * 64))

    def download(_url, dest, **_k):
        dest.write_bytes(b"tampered")
        return None

    monkeypatch.setattr(updater, "_download", download)
    result = updater.apply_update()
    assert result["ok"] is False and result["stage"] == "verify"
    assert calls["launch"] == []
    assert not (cache / "Setup-1.2.343.exe").exists()


def test_declined_uac_keeps_verified_cache(monkeypatch, harness, _never_exit) -> None:
    cache, calls = harness
    signed = b"MZ signed"
    (cache / "Setup-1.2.343.exe").write_bytes(signed)
    monkeypatch.setattr(updater, "get_app_update_status", lambda: _status("machine", hashlib.sha256(signed).hexdigest()))
    calls["result"] = (2, False)
    result = updater.apply_update()
    assert result["ok"] is False and "Installer did not finish" in result["error"]
    assert (cache / "Setup-1.2.343.exe").read_bytes() == signed
    assert _never_exit == []


def test_host_finished_install_clears_cache_and_exits(monkeypatch, harness, _never_exit) -> None:
    cache, calls = harness
    signed = b"MZ signed"
    (cache / "Setup-1.2.343.exe").write_bytes(signed)
    (cache / "Setup-1.2.300.exe").write_bytes(b"MZ older unsigned")
    monkeypatch.setattr(updater, "get_app_update_status", lambda: _status("machine", hashlib.sha256(signed).hexdigest()))
    monkeypatch.setattr(updater, "sweep_setup_host_leftovers", lambda app_root=None: 0)
    calls["result"] = (0, False)
    assert updater.apply_update()["ok"] is True
    assert not any(cache.iterdir())
    assert _never_exit == ["exit_self"]


# --- what the host leaves in %LOCALAPPDATA%/UEFN-Ducky ------------------------


def _leftovers(root: Path) -> None:
    (root / "setup-engine" / "run-77").mkdir(parents=True)
    (root / "setup-engine" / "Setup-engine.exe").write_bytes(b"MZ" * 10)
    (root / "setup-engine" / "run-77" / "Setup-engine.exe").write_bytes(b"MZ")
    (root / "setup-ui").mkdir()
    (root / "setup-ui" / "index.html").write_text("x")
    (root / "setup-progress.txt").write_text("100\nDone")
    (root / "setup-webview-1234").mkdir()
    (root / "ducky.db").write_bytes(b"keep")


def test_sweep_setup_host_leftovers_when_idle(monkeypatch, tmp_path) -> None:
    _leftovers(tmp_path)
    monkeypatch.setattr(updater, "_running_image_names", lambda: {"explorer.exe", "uefn-ducky.exe"})
    assert updater.sweep_setup_host_leftovers(tmp_path) == 4
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith("setup-")]
    assert (tmp_path / "ducky.db").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "running",
    [{"setup-engine.exe"}, {"setup-1.2.343.exe"}, {"uefn-ducky-setup-1.2.343.exe"}, {"uefn-ducky-setup-1.2.343.tmp"}, None],
)
def test_sweep_setup_host_leftovers_waits_for_any_setup(monkeypatch, tmp_path, running) -> None:
    _leftovers(tmp_path)
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "_running_image_names", lambda: running)
    assert updater.sweep_setup_host_leftovers(tmp_path) == 0
    assert (tmp_path / "setup-engine" / "Setup-engine.exe").is_file()


def test_sweep_setup_host_leftovers_skips_process_list_when_clean(monkeypatch, tmp_path) -> None:
    (tmp_path / "ducky.db").write_bytes(b"keep")
    monkeypatch.setattr(updater, "_running_image_names", lambda: pytest.fail("nothing to remove, no tasklist"))
    assert updater.sweep_setup_host_leftovers(tmp_path) == 0
