"""The release runs fewer test workers while UEFN is open, and never waits for it to close."""

from __future__ import annotations

import importlib.util
from pathlib import Path

RELEASE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("publish_app_workers_test", RELEASE / "publish_app.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_failed_tests_rerun_from_pytests_cache_never_on_the_command_line() -> None:
    """2251 failed ids on one command line: [WinError 206] (Nov 10 2026)."""
    import pytest

    pa = _load()
    argv = pa.rerun_failed_argv([f"ducky_app/t.py::test_{n}" for n in range(pa.FLAKY_RERUN_MAX)])
    assert "--lf" in argv and not any("::" in a for a in argv)
    assert sum(len(a) + 3 for a in argv) < 2000
    with pytest.raises(SystemExit, match="too many to be flaky"):
        pa.rerun_failed_argv(["x.py::t"] * (pa.FLAKY_RERUN_MAX + 1))


def test_uefn_in_the_process_list_means_fewer_workers() -> None:
    pa = _load()
    with_uefn = ["System", "explorer.exe", "UnrealEditorFortnite-Win64-Shipping.exe", "python.exe"]
    assert pa.uefn_open(with_uefn)
    assert pa.pytest_workers(pa.uefn_open(with_uefn), cores=16) == "4"
    assert pa.pytest_workers(True, cores=4) == "2"  # never below 2


def test_without_uefn_half_the_cores() -> None:
    pa = _load()
    assert not pa.uefn_open(["System", "explorer.exe", "UEFN-Ducky.exe"])
    assert pa.pytest_workers(pa.uefn_open([]), cores=16) == "8"
    assert pa.pytest_workers(False, cores=2) == "2"  # never below 2


def test_an_upload_prints_percent_speed_and_time_left() -> None:
    pa = _load()
    now = [0.0]
    lines: list[str] = []
    progress = pa.UploadProgress("Setup.exe", 100_000_000, clock=lambda: now[0],
                                 out=lambda text, flush=False: lines.append(text))
    for _ in range(4):  # 10 MB every 10 s: 1 MB/s
        now[0] += 10.0
        progress.add(10_000_000)
    assert len(lines) == 4
    assert "40.0%" in lines[-1] and "40.0/100.0 MB" in lines[-1] and "977 KB/s" in lines[-1]
    assert "1m00s left" in lines[-1]
    progress.add(60_000_000)
    progress.done()
    assert "100.0%" in lines[-1] and "sent" in lines[-1] and "waiting for the server" in lines[-1]


def test_the_suite_and_build_use_the_scaled_settings(monkeypatch) -> None:
    pa = _load()
    calls: list[tuple[list[str], dict]] = []

    class _Done:
        returncode = 0

    def fake_run(cmd, **kw):
        calls.append(([str(c) for c in cmd], kw))
        return _Done()

    monkeypatch.setattr(pa, "uefn_open", lambda names=None: True)
    monkeypatch.setattr(pa.shutil, "which", lambda name: "npm")
    monkeypatch.setattr(pa.subprocess, "run", fake_run)
    monkeypatch.setattr(pa.os, "cpu_count", lambda: 16)
    monkeypatch.syspath_prepend(str(RELEASE))  # publish_app imports retry_scope by name
    import retry_scope

    # A real dist/release-test-failed.json from an earlier run must not turn this into a retry.
    monkeypatch.setattr(retry_scope, "retry_targets", lambda root: None)
    monkeypatch.setattr(retry_scope, "clear", lambda root: None)
    pa.run_regression_tests()
    pytest_cmd = next(cmd for cmd, _kw in calls if "pytest" in cmd and "-n" in cmd)
    assert pytest_cmd[pytest_cmd.index("-n") + 1] == "4"
    assert any(cmd[:2] == ["npm", "test"] and "--maxWorkers=2" in cmd for cmd, _kw in calls)


def test_pytest_gets_the_ffmpeg_bundle_when_it_exists(tmp_path, monkeypatch) -> None:
    pa = _load()
    monkeypatch.delenv("DUCKY_FFMPEG_DIR", raising=False)
    assert "DUCKY_FFMPEG_DIR" not in pa.pytest_env(tmp_path)
    bundle = tmp_path / "build" / "ffmpeg-bundle"
    bundle.mkdir(parents=True)
    assert pa.pytest_env(tmp_path)["DUCKY_FFMPEG_DIR"] == str(bundle)
    monkeypatch.setenv("DUCKY_FFMPEG_DIR", "C:/mine")
    assert pa.pytest_env(tmp_path)["DUCKY_FFMPEG_DIR"] == "C:/mine"  # never overridden


def _flaky_upload(pa, monkeypatch, failures: list) -> list:
    """Each call raises the next failure; returns ok when they run out. Gives the waits made."""
    waits: list = []
    monkeypatch.setattr(pa.time, "sleep", waits.append)

    def once(_base, _key, _path):
        if failures:
            raise failures.pop(0)
        return {"ok": True}

    monkeypatch.setattr(pa, "_upload_app_release_once", once)
    return waits


def test_a_dropped_connection_waits_and_tries_again(monkeypatch) -> None:
    pa = _load()
    failures = [pa.UploadDropped("reset"), pa.UploadDropped("reset")]
    waits = _flaky_upload(pa, monkeypatch, failures)
    assert pa.upload_app_release("https://x", "k", Path("Setup.exe")) == {"ok": True}
    assert waits == [pa.UPLOAD_DROP_WAIT_S, pa.UPLOAD_DROP_WAIT_S * 2]


def test_a_connection_that_keeps_dropping_stops_with_a_clear_message(monkeypatch) -> None:
    pa = _load()
    failures = [pa.UploadDropped("reset") for _ in range(pa.UPLOAD_DROP_TRIES)]
    _flaky_upload(pa, monkeypatch, failures)
    try:
        pa.upload_app_release("https://x", "k", Path("Setup.exe"))
    except SystemExit as exc:
        assert "dropped 5 times" in str(exc) and "finish after a failed upload" in str(exc)
    else:
        raise AssertionError("expected it to give up")
    assert failures == []


def test_an_expired_ticket_still_gets_three_tries_and_other_errors_none(monkeypatch) -> None:
    pa = _load()
    failures = [SystemExit("ticket expired") for _ in range(pa.UPLOAD_TRIES + 1)]
    _flaky_upload(pa, monkeypatch, failures)
    try:
        pa.upload_app_release("https://x", "k", Path("Setup.exe"))
    except SystemExit:
        pass
    assert len(failures) == 1
    failures[:] = [SystemExit("Presigned PUT HTTP 403: no"), SystemExit("never reached")]
    try:
        pa.upload_app_release("https://x", "k", Path("Setup.exe"))
    except SystemExit as exc:
        assert "403" in str(exc)
    assert len(failures) == 1


def test_a_reset_during_the_send_is_a_drop_and_never_completes(tmp_path, monkeypatch) -> None:
    pa = _load()
    setup = tmp_path / "Setup.exe"
    setup.write_bytes(b"x")
    calls: list = []

    def api(_base, _key, method, path, body=None, timeout=120):
        calls.append(path)
        return {"uploadUrl": "https://store/put", "ticket": "t"}

    def reset(*_args):
        raise ConnectionResetError(10054, "closed by the remote host")

    monkeypatch.setattr(pa, "_api_json", api)
    monkeypatch.setattr(pa, "_presigned_put_file", reset)
    try:
        pa._upload_app_release_once("https://x", "k", setup)
    except pa.UploadDropped as exc:
        assert "closed by the remote host" in str(exc)
    else:
        raise AssertionError("expected a drop")
    assert calls == ["/api/v1/files/app-release/ticket"]
