"""A release test retry runs what failed and the tests next to the fix, never the full suite."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parent
pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")


def _load():
    spec = importlib.util.spec_from_file_location("retry_scope_test", RELEASE / "retry_scope.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod  # a dataclass looks its module up while it is made
    spec.loader.exec_module(mod)
    return mod


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


def _write(root: Path, rel: str, text: str = "x = 1\n") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    for rel in ("app/core/thing.py", "app/core/test_thing.py", "app/core/test_other.py",
                "app/ui/test_ui.py", "app/far/test_far.py", "app/ui/web/src/a.ts"):
        _write(tmp_path, rel)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def _fail_then_fix(rs, repo: Path, *changed: str) -> None:
    rs.record_failure(repo, ["app/ui/test_ui.py::test_flaky", "app/far/test_far.py::test_slow"])
    for rel in changed:
        _write(repo, rel, "x = 2\n")
    _git(repo, "commit", "-q", "-am", "fix")


def test_the_retry_is_the_failed_tests_plus_the_tests_beside_the_fix(repo: Path) -> None:
    rs = _load()
    _fail_then_fix(rs, repo, "app/core/thing.py", "app/far/test_far.py")
    retry = rs.retry_targets(repo)
    assert retry is not None and not retry.web_changed
    assert retry.targets == [
        "app/core/test_other.py",  # beside the changed source file
        "app/core/test_thing.py",
        "app/far/test_far.py",  # the changed test file covers its own failed test
        "app/ui/test_ui.py::test_flaky",  # failed, untouched: just that test
    ]


def test_a_web_panel_fix_brings_vitest_back(repo: Path, monkeypatch) -> None:
    rs = _load()
    monkeypatch.setattr(rs, "WEB_DIR", "app/ui/web/")
    _fail_then_fix(rs, repo, "app/ui/web/src/a.ts")
    retry = rs.retry_targets(repo)
    assert retry is not None and retry.web_changed


def test_no_record_an_old_record_or_another_history_runs_the_full_suite(repo: Path) -> None:
    rs = _load()
    assert rs.retry_targets(repo) is None
    _fail_then_fix(rs, repo, "app/core/thing.py")
    assert rs.retry_targets(repo, now=time.time() + rs.MAX_AGE_S + 1) is None
    record = repo / "dist" / "release-test-failed.json"
    data = json.loads(record.read_text())
    record.write_text(json.dumps({**data, "sha": "0" * 40}))
    assert rs.retry_targets(repo) is None
    rs.clear(repo)
    assert not record.exists() and rs.retry_targets(repo) is None


def test_last_failed_reads_pytests_own_cache(repo: Path) -> None:
    rs = _load()
    cache = repo / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True)
    (cache / "lastfailed").write_text(json.dumps({"b.py::t": True, "a.py::t": True}))
    assert rs.last_failed(repo) == ["a.py::t", "b.py::t"]
    assert rs.last_failed(repo / "nowhere") == []
