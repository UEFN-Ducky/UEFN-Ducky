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
    pa.run_regression_tests()
    pytest_cmd = next(cmd for cmd, _kw in calls if "pytest" in cmd and "-n" in cmd)
    assert pytest_cmd[pytest_cmd.index("-n") + 1] == "4"
    assert any(cmd[:2] == ["npm", "test"] and "--maxWorkers=2" in cmd for cmd, _kw in calls)
