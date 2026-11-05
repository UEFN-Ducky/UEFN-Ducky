"""What a release test retry runs: only what failed, plus the tests next to what the fix changed.

A release whose full suite failed hands the failing tests to a fixer ducky, which commits on
top in the same worktree, and the test step runs again. Nov 2026: every retry ran the whole
suite again (8+ minutes on half the cores) for a one-line fix to one test. Now a failed full
run records its failures and the commit it ran on; the next test step in that worktree, on a
commit built on top of it, runs:

* the tests that failed,
* every test file the fix changed,
* the test files in the folder of every source file the fix changed.

The panel's vitest suite runs again only when the fix changed the web panel. A retry that
fails records its own failures for the next one. The record is dropped once a test step
passes, and ignored when it is older than ``MAX_AGE_S`` or the commit is not an ancestor.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

MAX_AGE_S = 3 * 3600.0
WEB_DIR = "ducky_app/frontend/ui_web/web/"
_RECORD = Path("dist") / "release-test-failed.json"
_LASTFAILED = Path(".pytest_cache") / "v" / "cache" / "lastfailed"


@dataclass
class Retry:
    since: str
    targets: list[str] = field(default_factory=list)
    web_changed: bool = False


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True)


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def last_failed(root: Path) -> list[str]:
    """pytest's own record of the tests that failed in the last run here."""
    try:
        data = json.loads((root / _LASTFAILED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted(str(k) for k, v in data.items() if v) if isinstance(data, dict) else []


def record_failure(root: Path, failed: list[str]) -> None:
    path = root / _RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sha": _head(root), "at": time.time(), "failed": failed}, indent=2) + "\n",
                    encoding="utf-8")


def clear(root: Path) -> None:
    try:
        (root / _RECORD).unlink()
    except OSError:
        pass


def _is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def retry_targets(root: Path, now: float | None = None) -> Retry | None:
    """The retry's tests, or None when the full suite has to run (no usable record)."""
    try:
        rec = json.loads((root / _RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sha, failed = str(rec.get("sha") or ""), [str(f) for f in rec.get("failed") or []]
    now = time.time() if now is None else now
    if not sha or not failed or now - float(rec.get("at") or 0) > MAX_AGE_S:
        return None
    if _git(root, "merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
        return None  # another history: test it all
    changed = [p for p in _git(root, "diff", "--name-only", sha, "HEAD").stdout.splitlines() if p.strip()]
    targets = {f for f in failed if (root / f.split("::", 1)[0]).is_file()}
    for path in changed:
        if _is_test(path) and (root / path).is_file():
            targets.add(path)
        elif path.endswith(".py") and (root / path).parent.is_dir():
            folder = (root / path).parent
            targets.update(p.relative_to(root).as_posix() for p in folder.glob("test_*.py"))
    # A test id already covered by its whole file runs once.
    files = {t for t in targets if "::" not in t}
    kept = sorted(t for t in targets if "::" not in t or t.split("::", 1)[0] not in files)
    if not kept:
        return None  # the failing tests are gone: nothing narrower to run
    return Retry(since=sha, targets=kept, web_changed=any(p.startswith(WEB_DIR) for p in changed))
