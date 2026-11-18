"""Release gates: dependency pins and lint."""
import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PINNED_GIT = re.compile(r"git\+\S+@[0-9a-f]{40}(?:\s|$)")
TODAYS_RULES = {"E4", "E7", "E9", "F"}


def test_ruff_config_selects_at_least_today_s_rules():
    lint = tomllib.loads((ROOT / "ruff.toml").read_text(encoding="utf-8"))["lint"]
    missing = TODAYS_RULES - set(lint["select"])
    assert not missing, f"ruff.toml dropped rule families: {sorted(missing)}"


def test_the_release_test_phase_runs_ruff():
    source = (ROOT / "release" / "publish_app.py").read_text(encoding="utf-8")
    assert 'RUFF_PATHS = ("ducky_app", "release", "build", "scripts")' in source
    assert '"-m", "ruff", "check", *RUFF_PATHS' in source
    phase = source.split("def run_regression_tests", 1)[1]
    assert phase.index("run_ruff()") < phase.index('"-m", "pytest"'), "ruff must run before pytest"


@pytest.mark.skipif(importlib.util.find_spec("ruff") is None, reason="ruff not installed")
def test_ruff_check_is_clean():
    done = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-cache", "ducky_app", "release", "build", "scripts"],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-2000:]


def test_requirements_have_no_unpinned_git_url():
    loose = []
    for name in ("requirements.txt", "requirements-dev.txt"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0].strip()
            if "git+" in code and not PINNED_GIT.search(code):
                loose.append(f"{name}: {code}")
    assert not loose, f"git requirements need @<40-hex commit>: {loose}"
