"""How the person runs, tests and builds Ducky on Windows today, read from the tree.

``snapshot()`` is what running this file saved as ``dev_workflow.json``;
``test_windows_golden.py`` compares today's tree with it. Linux work adds next to these,
it never changes them: a new required build option, a renamed output, a launcher mode
gone, a dependency Windows now installs, or fewer tests collected on Windows.
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]

# Lines of build_exes.main() that name what a Windows build writes (dist/UEFN-Ducky-<v>/
# with UEFN-Ducky.exe and UEFN-Ducky-Bridge.exe; -Dev for --dev).
BUILD_OUTPUT_LINES = (
    'exe_stem = "UEFN-Ducky-Dev" if dev_build else "UEFN-Ducky"',
    'bridge_stem = f"{exe_stem}-Bridge"',
    'out = out_dir / f"{exe_stem}-{app_version}"',
)
WINDOWS_MARKERS = {"os_name": "nt", "sys_platform": "win32", "platform_system": "Windows"}


def _load_build_exes():
    spec = importlib.util.spec_from_file_location("golden_build_exes", REPO / "build" / "build_exes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_exes() -> dict:
    """``py build/build_exes.py`` with no arguments: its options, defaults and output names."""
    mod = _load_build_exes()
    seen: list[argparse.ArgumentParser] = []
    real = argparse.ArgumentParser.parse_args

    def grab(self, args=None, namespace=None):
        seen.append(self)
        return real(self, [], namespace)  # no arguments, as the person runs it

    argparse.ArgumentParser.parse_args = grab
    try:
        defaults = vars(mod._parse_args())
    finally:
        argparse.ArgumentParser.parse_args = real
    source = (REPO / "build" / "build_exes.py").read_text(encoding="utf-8")
    return {
        "defaults": defaults,
        "options": sorted(o for a in seen[0]._actions for o in a.option_strings),
        "required": sorted(a.dest for a in seen[0]._actions if a.required),
        "stale_patterns": list(mod._stale_artifact_patterns(dev_build=False)),
        "stale_patterns_dev": list(mod._stale_artifact_patterns(dev_build=True)),
        "launcher_exe": mod.LAUNCHER_EXE,
        "output_lines": [line for line in BUILD_OUTPUT_LINES if line in source],
    }


def launcher() -> dict:
    """Running from source (``python -m frontend``, the EXE's own ``launcher.py``): its modes."""
    text = (REPO / "ducky_app" / "frontend" / "launcher.py").read_text(encoding="utf-8")
    modes = set(re.findall(r'sys\.argv\[1\] == "([^"]+)"', text))
    for group in re.findall(r"sys\.argv\[1\] in \(([^)]*)\)", text):
        modes.update(re.findall(r'"([^"]+)"', group))
    modes.update(m for m in ("--after-update", "--adapter", "--port", "--shared-daemon") if f'"{m}"' in text)
    return {
        "from_source": (REPO / "ducky_app" / "frontend" / "__main__.py").is_file(),
        "modes": sorted(modes),
    }


def windows_requirements(name: str) -> list[str]:
    """The packages ``pip install -r <name>`` installs on Windows (names only: versions move)."""
    from packaging.requirements import Requirement

    out = set()
    for raw in (REPO / name).read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith(("#", "-")):
            continue
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate(WINDOWS_MARKERS):
            out.add(req.name.lower().replace("_", "-"))
    return sorted(out)


def pytest_setup() -> dict:
    ini = configparser.ConfigParser()
    ini.read(REPO / "pytest.ini", encoding="utf-8")
    return {"testpaths": ini["pytest"]["testpaths"].split()}


def collected_tests() -> int:
    """``python -m pytest --collect-only`` from the repo root, as the person runs it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_", "DUCKY_TESTS_"))}
    # Run inside a test, LOCALAPPDATA is that test's AppData: the child's real-AppData guard
    # would watch it and fail with rc 3 when the parent's leftover threads write there.
    # The child gets an empty AppData of its own, which nothing else writes into.
    with tempfile.TemporaryDirectory(prefix="ducky-golden-collect-", ignore_cleanup_errors=True) as home:
        env.update(LOCALAPPDATA=home, APPDATA=os.path.join(home, "Roaming"), XDG_DATA_HOME=home,
                   XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_CACHE_HOME=os.path.join(home, ".cache"))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
            cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
        )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    m = re.search(r"(\d+) tests? collected", tail)
    if proc.returncode != 0 or not m or "error" in tail:
        out = "\n".join(line for line in proc.stdout.splitlines() if not line.startswith(("ducky_app/", "tests/")))
        raise AssertionError(
            f"pytest --collect-only failed (rc {proc.returncode}): {tail}\n"
            f"--- stdout (test ids left out) ---\n{out[-4000:]}\n--- stderr ---\n{proc.stderr[-4000:]}"
        )
    return int(m.group(1))


def snapshot(*, with_count: bool = True) -> dict:
    snap = {
        "build_exes": build_exes(),
        "launcher": launcher(),
        "requirements": {n: windows_requirements(n) for n in ("requirements.txt", "requirements-dev.txt")},
        "pytest": pytest_setup(),
    }
    if with_count:
        snap["pytest"]["collected"] = collected_tests()
    return snap


if __name__ == "__main__":
    # python ducky_app/tests/fixtures/windows_golden/dev_workflow.py  (on Windows, on purpose)
    import json

    out = Path(__file__).with_name("dev_workflow.json")
    out.write_text(json.dumps(snapshot(), indent=2) + "\n", encoding="utf-8")
    print(f"{out.name}: {out.stat().st_size} bytes")
