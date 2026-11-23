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


def _jobs():
    import yaml

    return yaml.safe_load((ROOT / ".github" / "workflows" / "store-publish.yml").read_text(encoding="utf-8"))["jobs"]


def test_bumps_skip_with_a_warning_when_their_secret_is_missing():
    steps = {s.get("name"): s for s in _jobs()["linux-bumps"]["steps"]}
    for step, secret in (("AUR (uefn-ducky-bin)", "AUR_SSH_PRIVATE_KEY"), ("Flathub (online.uefnducky.Ducky)", "FLATHUB_TOKEN")):
        first = steps[step]["run"].strip().splitlines()[0]
        assert f'-z "${secret}"' in first and "::warning::" in first and secret in first.split("::warning::")[1], first
        assert first.rstrip().endswith("exit 0; fi"), first


def test_no_other_linux_job_is_skipped_or_soft_failed():
    for name in ("linux-version", "linux-build", "linux-repo"):
        job = _jobs()[name]
        assert not job.get("continue-on-error"), name
        for step in job["steps"]:
            assert not step.get("continue-on-error"), (name, step.get("name"))
            run = step.get("run", "")
            assert "::warning::" not in run, (name, step.get("name"))
            assert "AUR_SSH_PRIVATE_KEY" not in run and "FLATHUB_TOKEN" not in run, (name, step.get("name"))
