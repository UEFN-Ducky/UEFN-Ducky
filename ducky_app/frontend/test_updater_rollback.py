"""A swapped-in AppImage that never gets ready is rolled back to .previous."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import frontend.app_versions as av
import frontend.updater as updater

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX sh watchdog")


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)


def _swap(tmp_path: Path, b_body: str) -> tuple[Path, Path]:
    app = tmp_path / "UEFN-Ducky.AppImage"
    ran = tmp_path / "a-ran"
    _script(tmp_path / "UEFN-Ducky.AppImage.previous", f'echo "$@" > "{ran}"')
    _script(app, b_body)
    av.begin_appimage_swap("9.9.9", from_version="1.2.600", app=app,
                           previous=tmp_path / "UEFN-Ducky.AppImage.previous")
    return app, ran


def test_a_b_that_exits_at_once_brings_a_back(tmp_path) -> None:
    app, ran = _swap(tmp_path, "exit 3")
    proc = updater.start_watchdog(app, version="9.9.9", timeout_s=10, sh=shutil.which("sh") or "/bin/sh")
    assert proc.wait(timeout=30) == 0
    assert ran.is_file()  # A was started again
    assert not (tmp_path / "UEFN-Ducky.AppImage.previous").exists()
    assert "a-ran" in app.read_text(encoding="utf-8")  # $APPIMAGE is A's file again
    failure = av.appimage_swap_failure()
    assert failure and failure["from"] == "9.9.9" and failure["to"] == updater.__version__


def test_a_b_that_hangs_without_ready_is_rolled_back_too(tmp_path) -> None:
    app, ran = _swap(tmp_path, "sleep 30")
    proc = updater.start_watchdog(app, version="9.9.9", timeout_s=2, sh=shutil.which("sh") or "/bin/sh")
    assert proc.wait(timeout=30) == 0
    assert ran.is_file() and av.appimage_swap_failure()


def test_a_b_that_gets_ready_stays(tmp_path) -> None:
    _state, ok, _failed = av.appimage_swap_paths()
    app, ran = _swap(tmp_path, f'touch "{ok}"; sleep 3')
    proc = updater.start_watchdog(app, version="9.9.9", timeout_s=10, sh=shutil.which("sh") or "/bin/sh")
    assert proc.wait(timeout=30) == 0
    assert not ran.exists() and "touch" in app.read_text(encoding="utf-8")
    assert av.appimage_swap_failure() is None


def test_ready_marks_only_the_swapped_in_version(tmp_path) -> None:
    av.begin_appimage_swap("9.9.9", from_version="1.2.600", app=tmp_path / "a", previous=tmp_path / "b")
    assert not av.confirm_appimage_swap("1.2.600")
    assert av.confirm_appimage_swap("9.9.9")
    assert av.appimage_swap_paths()[1].is_file()
