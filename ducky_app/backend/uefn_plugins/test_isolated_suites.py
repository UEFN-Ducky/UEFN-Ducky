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
            [sys.executable, "-m", "pytest", str(HERE / name), "-q", "-p", "no:cacheprovider", "--no-header",
             "--tb=short", "--show-capture=no", "--timeout=100", "-o", "faulthandler_timeout=90"],
            cwd=str(REPO),
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_CHILD_S,
        )
    except subprocess.TimeoutExpired as exc:  # every test passed and the interpreter never exited, or worse
        pytest.fail(f"{name} still running after {_CHILD_S} s:\n{_text(exc.stdout)[-4000:]}\n{_text(exc.stderr)[-4000:]}")
    failures = proc.stdout[proc.stdout.find("= FAILURES ="):] if "= FAILURES =" in proc.stdout else proc.stdout
    assert proc.returncode == 0, (failures[:6000] + proc.stderr[-2000:])
