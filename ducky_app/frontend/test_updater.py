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
            assert "kill:True" in calls
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


if __name__ == "__main__":
    test_get_update_progress_snapshot()
    test_download_updates_byte_progress()
    test_download_respects_cancel()
    test_silent_install_args_force_close()
    test_shutdown_after_delay_always_exits()
    test_apply_update_dev_sets_check_error()
    test_sweep_installer_cache_keeps_pending_newer()
    test_cached_installer_usable_requires_sha()
    test_remove_installer_file()
    test_setup_still_running_after_wait_sees_child()
    test_setup_still_running_after_wait_gone_is_false()
    print("ok")
