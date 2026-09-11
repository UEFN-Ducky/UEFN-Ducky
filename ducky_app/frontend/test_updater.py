"""Tests for in-app updater progress reporting."""

from __future__ import annotations

import http.server
import tempfile
import threading
from pathlib import Path

import frontend.updater as updater


def _reset_progress() -> None:
    updater._cancel.clear()
    updater._set_progress(stage="idle", downloaded_bytes=0, total_bytes=0, error=None)


def test_get_update_progress_snapshot() -> None:
    _reset_progress()
    snap = updater.get_update_progress()
    assert snap["stage"] == "idle"
    assert snap["downloaded_bytes"] == 0
    assert snap["total_bytes"] == 0
    assert snap["error"] is None
    # Mutating the returned dict must not corrupt module state.
    snap["stage"] = "hacked"
    assert updater.get_update_progress()["stage"] == "idle"


def test_download_updates_byte_progress() -> None:
    _reset_progress()
    payload = b"x" * (1024 * 300)  # > one 256 KiB chunk so progress ticks mid-download

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "setup-test.bin"
            port = server.server_address[1]
            err = updater._download(f"http://127.0.0.1:{port}/setup.bin", dest)
            assert err is None, err
            prog = updater.get_update_progress()
            assert prog["stage"] == "download"
            assert prog["total_bytes"] == len(payload)
            assert prog["downloaded_bytes"] == len(payload)
            assert dest.read_bytes() == payload
    finally:
        server.shutdown()
        _reset_progress()


def test_download_respects_cancel() -> None:
    _reset_progress()
    import time

    payload = b"x" * (1024 * 256 * 4)
    started = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            # First chunk, then stall so cancel_update wins mid-download.
            self.wfile.write(payload[: 1024 * 64])
            self.wfile.flush()
            started.set()
            time.sleep(2.0)
            self.wfile.write(payload[1024 * 64 :])

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "setup-cancel.bin"
            port = server.server_address[1]
            result: list[str | None] = [None]

            def _run() -> None:
                result[0] = updater._download(f"http://127.0.0.1:{port}/setup.bin", dest)

            worker = threading.Thread(target=_run, daemon=True)
            worker.start()
            assert started.wait(2.0), "download never started"
            updater.cancel_update()
            worker.join(timeout=3.0)
            assert result[0] == updater._CANCELLED, result[0]
    finally:
        server.shutdown()
        _reset_progress()


def test_silent_install_args_force_close() -> None:
    """In-app Setup must force-close so Restart Manager does not stall the upgrade."""
    args = updater._silent_install_args("user")
    assert "/VERYSILENT" in args
    assert "/FORCECLOSEAPPLICATIONS" in args
    assert "/CLOSEAPPLICATIONS" in args
    assert "/NOCLOSEAPPLICATIONS" not in args
    assert "/CURRENTUSER" in args
    assert "/NOLAUNCH" not in args
    assert updater._silent_install_args("machine")[-1] == "/ALLUSERS"


def test_shutdown_after_delay_always_exits() -> None:
    """Stuck update lock: shutdown must os._exit even if process kill fails."""
    import os

    calls: list[str] = []

    def fake_release() -> None:
        calls.append("release")

    def fake_kill(*, include_self: bool = True) -> bool:
        calls.append(f"kill:{include_self}")
        return False

    def fake_exit(code: int) -> None:
        calls.append(f"exit:{code}")
        raise SystemExit(code)

    original_timer = updater.threading.Timer

    class ImmediateTimer:
        def __init__(self, _delay: float, fn: object) -> None:
            self._fn = fn  # type: ignore[assignment]

        def start(self) -> None:
            self._fn()  # type: ignore[operator]

    updater.threading.Timer = ImmediateTimer  # type: ignore[assignment]
    try:
        import frontend.frozen_process as fp

        orig_release = fp.release_panel_process
        orig_kill = fp.kill_uefn_ducky_processes
        orig_exit = os._exit
        fp.release_panel_process = fake_release  # type: ignore[assignment]
        fp.kill_uefn_ducky_processes = fake_kill  # type: ignore[assignment]
        os._exit = fake_exit  # type: ignore[assignment]
        try:
            try:
                updater._shutdown_after_delay()
            except SystemExit as exc:
                assert exc.code == 0
            assert "release" in calls
            assert "kill:False" in calls
            assert "exit:0" in calls
        finally:
            fp.release_panel_process = orig_release
            fp.kill_uefn_ducky_processes = orig_kill
            os._exit = orig_exit  # type: ignore[assignment]
    finally:
        updater.threading.Timer = original_timer  # type: ignore[assignment]


def test_apply_update_dev_sets_check_error() -> None:
    """Dev channel fails at check and surfaces stage/error via get_update_progress."""
    _reset_progress()

    def fake_status() -> dict:
        return {
            "channel": "dev",
            "installed": False,
            "update_available": False,
            "installer_url": None,
            "installer_sha256": None,
            "remote_version": None,
            "install_scope": None,
        }

    original = updater.get_app_update_status
    updater.get_app_update_status = fake_status  # type: ignore[assignment]
    try:
        result = updater.apply_update()
        assert result["ok"] is False
        assert result["stage"] == "check"
        prog = updater.get_update_progress()
        assert prog["stage"] == "check"
        assert prog["error"]
        assert "Dev builds" in str(prog["error"])
    finally:
        updater.get_app_update_status = original
        _reset_progress()


def test_apply_update_requires_sha256() -> None:
    _reset_progress()

    def fake_status() -> dict:
        return {
            "channel": "installed",
            "installed": True,
            "update_available": True,
            "installer_url": "https://example.test/Setup.exe",
            "installer_sha256": None,
            "remote_version": "9.9.9",
            "install_scope": "user",
        }

    original = updater.get_app_update_status
    updater.get_app_update_status = fake_status  # type: ignore[assignment]
    try:
        result = updater.apply_update()
        assert result["ok"] is False
        assert result["stage"] == "check"
        assert "sha256" in str(result["error"]).lower()
    finally:
        updater.get_app_update_status = original
        _reset_progress()


def test_sweep_installer_cache_keeps_pending_newer() -> None:
    """After a successful install, drop Setup <= current; keep a newer pending cache."""
    with tempfile.TemporaryDirectory() as tmp:
        cache = Path(tmp) / "UEFN-Ducky"
        cache.mkdir()
        old = cache / "Setup-1.0.100.exe"
        current = cache / "Setup-1.0.200.exe"
        pending = cache / "Setup-1.0.300.exe"
        for path in (old, current, pending):
            path.write_bytes(b"x")

        original = updater.installer_cache_dir
        updater.installer_cache_dir = lambda: cache  # type: ignore[assignment]
        try:
            removed = updater.sweep_installer_cache(keep_newer_than="1.0.200")
            assert removed == 2
            assert not old.exists()
            assert not current.exists()
            assert pending.exists()
        finally:
            updater.installer_cache_dir = original  # type: ignore[assignment]


def test_cached_installer_usable_requires_sha() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "Setup-1.0.1.exe"
        payload = b"hello-setup"
        dest.write_bytes(payload)
        digest = __import__("hashlib").sha256(payload).hexdigest()
        assert updater._cached_installer_usable(dest, digest) is True
        assert updater._cached_installer_usable(dest, "0" * 64) is False
        assert updater._cached_installer_usable(dest, None) is False
        assert updater._cached_installer_usable(dest.parent / "missing.exe", digest) is False


def test_remove_installer_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "Setup-1.0.1.exe"
        dest.write_bytes(b"x")
        assert updater.remove_installer_file(dest) is True
        assert not dest.exists()
        assert updater.remove_installer_file(dest) is True


def test_setup_still_running_after_wait_sees_child() -> None:
    """Elevation handoff: stub exited but Setup image still listed → install underway."""
    calls = {"n": 0}

    def fake_running(_dest: Path) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    original = updater._installer_process_running
    original_handoff = updater._ELEVATION_HANDOFF_S
    updater._installer_process_running = fake_running  # type: ignore[assignment]
    updater._ELEVATION_HANDOFF_S = 1.0
    try:
        assert updater._setup_still_running_after_wait(Path("Setup-1.0.610.exe")) is True
        assert calls["n"] >= 2
    finally:
        updater._installer_process_running = original  # type: ignore[assignment]
        updater._ELEVATION_HANDOFF_S = original_handoff


def test_setup_still_running_after_wait_gone_is_false() -> None:
    original = updater._installer_process_running
    original_handoff = updater._ELEVATION_HANDOFF_S
    updater._installer_process_running = lambda _d: False  # type: ignore[assignment]
    updater._ELEVATION_HANDOFF_S = 0.35
    try:
        assert updater._setup_still_running_after_wait(Path("Setup-1.0.610.exe")) is False
    finally:
        updater._installer_process_running = original  # type: ignore[assignment]
        updater._ELEVATION_HANDOFF_S = original_handoff


def test_prepare_installer_exe_tolerates_missing_motw() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "Setup-1.2.12.exe"
        dest.write_bytes(b"MZ")
        updater._prepare_installer_exe(dest)


def test_launch_setup_retries_once_when_first_stub_dies() -> None:
    """First Check-for-updates after a fresh download: stub dies, retry succeeds."""
    launches = {"n": 0}

    class FakeProc:
        def wait(self) -> int:
            return 1 if launches["n"] == 1 else 0

    def fake_popen(_dest: Path, _args: list[str]) -> FakeProc:
        launches["n"] += 1
        return FakeProc()

    original_popen = updater._popen_setup
    original_running = updater._installer_process_running
    original_retry = updater._LAUNCH_RETRY_S
    original_handoff = updater._ELEVATION_HANDOFF_S
    updater._popen_setup = fake_popen  # type: ignore[assignment]
    updater._installer_process_running = lambda _d: False  # type: ignore[assignment]
    updater._LAUNCH_RETRY_S = 0.0
    updater._ELEVATION_HANDOFF_S = 0.0
    try:
        code, running = updater._launch_setup_until_handoff(Path("Setup-1.exe"), ["/VERYSILENT"])
        assert code == 0
        assert running is False
        assert launches["n"] == 2
    finally:
        updater._popen_setup = original_popen  # type: ignore[assignment]
        updater._installer_process_running = original_running  # type: ignore[assignment]
        updater._LAUNCH_RETRY_S = original_retry
        updater._ELEVATION_HANDOFF_S = original_handoff


def test_launch_setup_retries_once_then_keeps_decline() -> None:
    launches = {"n": 0}

    class FakeProc:
        def wait(self) -> int:
            return 1

    def fake_popen(_dest: Path, _args: list[str]) -> FakeProc:
        launches["n"] += 1
        return FakeProc()

    original_popen = updater._popen_setup
    original_running = updater._installer_process_running
    original_retry = updater._LAUNCH_RETRY_S
    original_handoff = updater._ELEVATION_HANDOFF_S
    updater._popen_setup = fake_popen  # type: ignore[assignment]
    updater._installer_process_running = lambda _d: False  # type: ignore[assignment]
    updater._LAUNCH_RETRY_S = 0.0
    updater._ELEVATION_HANDOFF_S = 0.0
    try:
        code, running = updater._launch_setup_until_handoff(Path("Setup-1.exe"), ["/VERYSILENT"])
        assert code == 1
        assert running is False
        assert launches["n"] == 2
    finally:
        updater._popen_setup = original_popen  # type: ignore[assignment]
        updater._installer_process_running = original_running  # type: ignore[assignment]
        updater._LAUNCH_RETRY_S = original_retry
        updater._ELEVATION_HANDOFF_S = original_handoff


def test_launch_setup_user_scope_does_not_wait_for_finish() -> None:
    class FakeProc:
        def poll(self) -> int | None:
            return None

        def wait(self) -> int:
            raise AssertionError("per-user must not wait for Setup to finish")

    original_popen = updater._popen_setup
    original_running = updater._installer_process_running
    updater._popen_setup = lambda _d, _a: FakeProc()  # type: ignore[assignment]
    updater._installer_process_running = lambda _d: True  # type: ignore[assignment]
    try:
        code, running = updater._launch_setup_until_handoff(
            Path("Setup-1.exe"), ["/VERYSILENT"], wait_for_elevation=False
        )
        assert code == 0
        assert running is True
    finally:
        updater._popen_setup = original_popen  # type: ignore[assignment]
        updater._installer_process_running = original_running  # type: ignore[assignment]


def test_popen_setup_breakaway_flags_in_source() -> None:
    src = Path(updater.__file__).read_text(encoding="utf-8")
    assert "CREATE_BREAKAWAY_FROM_JOB" in src
    assert "include_self=False" in src


if __name__ == "__main__":
    test_get_update_progress_snapshot()
    test_download_updates_byte_progress()
    test_download_respects_cancel()
    test_silent_install_args_force_close()
    test_shutdown_after_delay_always_exits()
    test_apply_update_dev_sets_check_error()
    test_apply_update_requires_sha256()
    test_sweep_installer_cache_keeps_pending_newer()
    test_cached_installer_usable_requires_sha()
    test_remove_installer_file()
    test_setup_still_running_after_wait_sees_child()
    test_setup_still_running_after_wait_gone_is_false()
    test_prepare_installer_exe_tolerates_missing_motw()
    test_launch_setup_retries_once_when_first_stub_dies()
    test_launch_setup_retries_once_then_keeps_decline()
    test_launch_setup_user_scope_does_not_wait_for_finish()
    test_popen_setup_breakaway_flags_in_source()
    print("ok")


def test_installer_url_allowed_https_anywhere_http_only_loopback() -> None:
    assert updater.installer_url_allowed("https://uefnducky.org/x/Setup.exe")
    assert updater.installer_url_allowed("http://127.0.0.1:8765/UEFN-Ducky-Setup-1.2.1.exe")
    assert updater.installer_url_allowed("http://localhost:1/x.exe")
    assert not updater.installer_url_allowed("http://uefnducky.org/x/Setup.exe")
    assert not updater.installer_url_allowed("http://10.0.0.5/x.exe")
    assert not updater.installer_url_allowed("ftp://127.0.0.1/x.exe")
    assert not updater.installer_url_allowed("")


def test_local_feed_rehearsal_downloads_verifies_and_launches(tmp_path, monkeypatch) -> None:
    """The exact path build/upgrade_proof/serve_update_feed.py exercises: feed
    override → update_available → download from loopback → sha256 → Setup
    launched. The "Setup" here is where.exe, which rejects the Inno switches and
    exits non-zero, so the run ends as a declined install with the cache kept."""
    import hashlib
    import json
    import shutil
    import sys
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from frontend import version_check

    fake_setup = tmp_path / "where.exe"
    shutil.copy(r"C:\Windows\System32\where.exe", fake_setup)
    digest = hashlib.sha256(fake_setup.read_bytes()).hexdigest()
    payload = {"currentVersion": "9.9.9", "installerUrl": "/Setup-9.9.9.exe", "installerSha256": digest}
    body = json.dumps({"handled": True, "payload": payload}).encode()
    data = fake_setup.read_bytes()

    class H(BaseHTTPRequestHandler):
        def _send(self, content: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):  # noqa: N802
            self._send(body, "application/json")

        def do_GET(self):  # noqa: N802
            self._send(data, "application/octet-stream")

        def log_message(self, *a):  # noqa: D401
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        monkeypatch.setenv(version_check.UPDATE_BASE_URL_ENV, base)
        monkeypatch.setattr(version_check, "is_packaged_runtime", lambda: True)
        monkeypatch.setattr("frontend.ui_web.web_dev.is_frozen_dev_exe", lambda: False)
        monkeypatch.setattr(
            "frontend.install_info.get_install_info",
            lambda: {"installed": True, "install_location": str(tmp_path), "install_scope": "user"},
        )
        status = version_check.get_app_update_status()
        assert status["update_available"] and status["remote_version"] == "9.9.9"
        assert status["installer_url"] == f"{base}/Setup-9.9.9.exe"
        assert status["installer_sha256"] == digest

        cache = tmp_path / "cache"
        monkeypatch.setattr(updater, "installer_cache_dir", lambda: cache)
        monkeypatch.setattr(updater, "get_app_update_status", lambda: status)
        monkeypatch.setattr(updater, "_stop_all_agents", lambda: None)
        monkeypatch.setattr(updater, "_LAUNCH_RETRY_S", 0.0)
        monkeypatch.setattr(updater, "_ELEVATION_HANDOFF_S", 0.0)
        monkeypatch.setattr("frontend.frozen_process.kill_uefn_ducky_processes", lambda include_self=False: None)
        assert sys.platform == "win32"
        _reset_progress()
        result = updater.apply_update()
        # where.exe rejected /VERYSILENT… and exited non-zero: same as a declined UAC.
        assert result["ok"] is False and result["stage"] == "installing"
        assert "Installer did not finish" in str(result["error"])
        dest = cache / "Setup-9.9.9.exe"
        assert dest.is_file() and dest.read_bytes() == data  # verified download kept for retry
        assert updater._cached_installer_usable(dest, digest)
    finally:
        srv.shutdown()
        _reset_progress()
