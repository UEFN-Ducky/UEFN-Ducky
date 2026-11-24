"""Run the hung-thread plugin suites in a child interpreter (see conftest.py here)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


@pytest.mark.parametrize("name", ["test_store_sync.py", "test_uefn_plugins.py"])
def test_suite_in_child_interpreter(name: str, tmp_path: Path) -> None:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path / "appdata")
    env["APPDATA"] = str(tmp_path / "appdata" / "Roaming")
    env["DUCKY_TESTS_REAL_APPDATA"] = "1"  # the child must not re-isolate over ours
    proc = subprocess.run(
        # --show-capture=no: the captured logs after a traceback pushed the traceback itself out
        # of the tail below, so a CI failure never said which line failed.
        [sys.executable, "-m", "pytest", str(HERE / name), "-q", "-p", "no:cacheprovider", "--no-header",
         "--tb=short", "--show-capture=no"],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    failures = proc.stdout[proc.stdout.find("= FAILURES ="):] if "= FAILURES =" in proc.stdout else proc.stdout
    assert proc.returncode == 0, (failures[:6000] + proc.stderr[-2000:])
