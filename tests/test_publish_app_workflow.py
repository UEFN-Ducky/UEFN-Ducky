"""The release's Linux build job installs every apt package build_exes needs for PyGObject."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_linux_build_installs_build_exes_apt_packages():
    src = (ROOT / "build" / "build_exes.py").read_text(encoding="utf-8")
    apt = re.search(r"^LINUX_BUILD_APT = \((.*?)\)", src, re.M)
    assert apt, "LINUX_BUILD_APT not found in build_exes.py"
    wanted = re.findall(r'"([^"]+)"', apt.group(1))
    assert wanted

    wf = (ROOT / ".github" / "workflows" / "store-publish.yml").read_text(encoding="utf-8")
    job = wf.split("\n  linux-build:", 1)[1].split("\n  linux-repo:", 1)[0]
    install = re.search(r"apt-get install -y ((?:.*\\\n)*.*)", job)
    assert install, "linux-build has no apt-get install step"
    installed = set(install.group(1).replace("\\\n", " ").split())
    assert set(wanted) <= installed, sorted(set(wanted) - installed)
