"""The release commit still reaches main when someone pushed to main while the release built."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parent
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def _load():
    spec = importlib.util.spec_from_file_location("commit_before_store_test", RELEASE / "commit_before_store.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _setup(tmp_path: Path) -> tuple[Path, Path, Path]:
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    dev = tmp_path / "dev"
    _git(tmp_path, "clone", "-q", str(origin), str(dev))
    (dev / "app.txt").write_text("v1\n")
    _git(dev, "add", "app.txt")
    _git(dev, "commit", "-q", "-m", "first")
    _git(dev, "push", "-q", "origin", "HEAD:main")
    rel = tmp_path / "rel"
    _git(tmp_path, "clone", "-q", str(origin), str(rel))
    _git(rel, "checkout", "-q", "--detach")
    _git(rel, "config", "user.name", "t")
    _git(rel, "config", "user.email", "t@example.invalid")
    return origin, dev, rel


def test_a_push_to_main_during_the_build_is_rebased_onto(tmp_path: Path) -> None:
    origin, dev, rel = _setup(tmp_path)
    (rel / "version.txt").write_text("1.2.470\n")  # the build's version bump
    (dev / "fix.txt").write_text("fix\n")  # a fix pushed while the release built
    _git(dev, "add", "fix.txt")
    _git(dev, "commit", "-q", "-m", "fix during build")
    _git(dev, "push", "-q", "origin", "HEAD:main")

    _load().commit_and_push_before_publish(rel, "Release desktop 1.2.470.")

    log = _git(origin, "log", "--format=%s", "main")
    assert log.splitlines() == ["Release desktop 1.2.470.", "fix during build", "first"]


def test_a_conflicting_change_on_main_still_stops_the_release(tmp_path: Path) -> None:
    origin, dev, rel = _setup(tmp_path)
    (rel / "app.txt").write_text("release\n")
    (dev / "app.txt").write_text("other\n")
    _git(dev, "commit", "-q", "-am", "conflict")
    _git(dev, "push", "-q", "origin", "HEAD:main")

    with pytest.raises(SystemExit, match="does not rebase"):
        _load().commit_and_push_before_publish(rel, "Release desktop 1.2.470.")
    assert _git(origin, "log", "-1", "--format=%s", "main") == "conflict"
    assert not (rel / ".git" / "rebase-merge").exists()
