"""Run the hung-thread plugin suites in a child interpreter (see conftest.py here)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
_CHILD_S = 300  # a whole suite; each of its tests stops itself at 100 s and prints every stack
_RESULTS = ("PASSED", "FAILED", "SKIPPED", "ERROR", "XFAIL", "XPASS")


def _unfinished(out: str) -> list[str]:
    """The tests -v started and never gave a result for: the one a timeout stopped."""
    return [line.split(" ")[0] for line in out.splitlines() if "::" in line and not any(r in line for r in _RESULTS)]


def _text(out: object) -> str:
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out or "")


# The run's per-test limit (120 s on the Mac jobs) would kill this worker mid-suite and name no test.
@pytest.mark.timeout(_CHILD_S + 60)
@pytest.mark.parametrize("name", ["test_store_sync.py", "test_uefn_plugins.py"])
def test_suite_in_child_interpreter(name: str, tmp_path: Path) -> None:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path / "appdata")
    env["APPDATA"] = str(tmp_path / "appdata" / "Roaming")
    env["DUCKY_TESTS_REAL_APPDATA"] = "1"  # the child must not re-isolate over ours
    try:
        proc = subprocess.run(
            # --show-capture=no: the captured logs after a traceback pushed the traceback itself out
            # of the tail below, so a CI failure never said which line failed.
            # -v names each test as it starts, so a stopped one is named; pytest-timeout prints
            # every thread's stack, oldest frame first, so the tail shows the stuck line (a
            # faulthandler dump after it, most recent first, pushed that line out of the message).
            [sys.executable, "-m", "pytest", str(HERE / name), "-v", "-p", "no:cacheprovider", "--no-header",
             "--tb=short", "--show-capture=no", "--timeout=100", "-o", "faulthandler_timeout=0"],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_CHILD_S,
        )
    except subprocess.TimeoutExpired as exc:  # every test passed and the interpreter never exited, or worse
        out = _text(exc.stdout)
        pytest.fail(f"{name} still running after {_CHILD_S} s in {_unfinished(out)}:\n{out[-4000:]}\n"
                    f"{_text(exc.stderr)[-4000:]}")
    out = proc.stdout
    failures = out[out.find("= FAILURES ="):] if "= FAILURES =" in out else out
    stuck = _unfinished(out)
    assert proc.returncode == 0, (f"stopped in {stuck}\n{out[-6000:]}" if stuck else failures[:6000]) + proc.stderr[-2000:]
