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
    assert "ripgrep" in installed  # the panel's prebuild check-shipped-ui.mjs runs rg


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


def test_both_builds_unpack_the_verse_grammar_before_building():
    for name, build_step in (("linux-build", "Freeze (PyInstaller) + runtime smoke"), ("publish", "Test")):
        job = _jobs()[name]
        names = [s.get("name") for s in job["steps"]]
        assert names.index("Verse language assets") < names.index(build_step), name
        assert job["env"]["VERSE_LANG_ASSETS"] == "${{ secrets.VERSE_LANG_ASSETS }}", name
        assert "build/ci_verse_lang.sh" in job["steps"][names.index("Verse language assets")]["run"], name


def test_verse_grammar_unpacks_and_a_real_release_needs_the_secret(tmp_path):
    import base64
    import io
    import os
    import shutil
    import subprocess
    import tarfile

    import pytest

    bash = shutil.which("bash")
    if not bash:
        pytest.skip("no bash")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b'{"scopeName": "source.verse"}'
        info = tarfile.TarInfo("./verse.tmGrammar.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    script = str(ROOT / "build" / "ci_verse_lang.sh")
    lang = tmp_path / "ducky_app/frontend/ui_web/web/src/verse-editor/lang"

    def run(dry, secret):
        env = {**os.environ, "VERSE_LANG_ASSETS": secret}
        return subprocess.run([bash, script, dry], cwd=tmp_path, env=env, capture_output=True, text=True)

    assert run("false", "").returncode == 1
    assert run("true", "").returncode == 0 and (lang / "verse-dark.tmTheme.json").is_file()
    shutil.rmtree(lang)
    r = run("false", base64.b64encode(buf.getvalue()).decode())
    assert r.returncode == 0, r.stderr
    assert (lang / "verse.tmGrammar.json").read_bytes() == data


def test_flatpak_builds_on_24_04_and_the_release_waits_for_it():
    jobs = _jobs()
    flatpak = jobs["linux-flatpak"]
    assert flatpak["runs-on"] == "ubuntu-24.04"  # 22.04's flatpak-builder needs appstream-compose
    assert all("ci_test.sh" not in s.get("run", "") for s in jobs["linux-build"]["steps"])
    assert any("build/linux/flatpak/ci_test.sh" in s.get("run", "") for s in flatpak["steps"])
    uploads = [s["with"]["name"] for s in flatpak["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    assert uploads == ["linux-dist-flatpak"]  # publish and the bumps merge linux-dist-*
    assert "linux-flatpak" in jobs["publish"]["needs"]


def test_linux_repo_writes_only_an_armored_uefn_ducky_gpg_at_the_root():
    run = "\n".join(s.get("run", "") for s in _jobs()["linux-repo"]["steps"])
    assert "uefn-ducky.asc" not in run  # the Store's uds_linux_repo refuses any other root file
    assert 'gpg --batch --armor --export > "$repo/uefn-ducky.gpg"' in run
    assert 'cp "$GNUPGHOME/repo.asc" "$repo/uefn-ducky.gpg"' in run
