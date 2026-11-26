"""The release checks every Mac file's version, and no Mac CI run counts as a real Mac user."""
import subprocess
import shutil
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _parity_step() -> dict:
    steps = _load("store-publish.yml")["jobs"]["publish"]["steps"]
    return next(s for s in steps if s.get("name") == "Windows and Linux are the same version")


def test_parity_step_checks_both_mac_globs():
    run = _parity_step()["run"]
    assert "'*-macos-arm64.*'" in run and "'*-macos-x64.*'" in run
    steps = _load("store-publish.yml")["jobs"]["publish"]["steps"]
    mac_dl = next(i for i, s in enumerate(steps) if s.get("with", {}).get("pattern") == "macos-dist-*")
    assert mac_dl < steps.index(_parity_step())  # the files are there when it looks


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("names, ok", [
    (["UEFN-Ducky-2.31.0-macos-arm64.dmg", "UEFN-Ducky-2.31.0-macos-x64.dmg"], True),
    (["UEFN-Ducky-2.31.0-macos-arm64.dmg", "UEFN-Ducky-2.30.9-macos-x64.dmg"], False),
    (["UEFN-Ducky-2.31.0-macos-arm64.dmg"], False),
])
def test_parity_step_runs(tmp_path, names, ok):
    (tmp_path / "dist" / "macos").mkdir(parents=True)
    for n in names:
        (tmp_path / "dist" / "macos" / n).write_bytes(b"x")
    run = _parity_step()["run"]
    # The Windows build half needs the real venv: stand in the built version.
    run = run.split('VERSION="$built"', 1)[1]
    script = 'set -eo pipefail\nVERSION=2.31.0\n' + run
    # Script as bytes on stdin: Windows argv quoting eats single quotes, text mode adds \r.
    done = subprocess.run(["bash", "-s"], input=script.encode(), cwd=tmp_path, capture_output=True)
    assert (done.returncode == 0) is ok, done.stdout + done.stderr


def test_install_check_jobs_on_both_chips():
    doc = _load("macos-install-check.yml")
    assert set(doc["jobs"]) == {"install", "update", "rollback"}
    for job in doc["jobs"].values():
        rows = job["strategy"]["matrix"]["include"]
        assert {(r["runner"], r["arch"]) for r in rows} == {("macos-26", "arm64"), ("macos-15-intel", "x64")}
    inputs = doc.get("on", doc.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["job"]["options"] == ["install", "update", "rollback", "all"]
    assert inputs["channel"]["options"] == ["stable", "beta"]


def test_look_modes_and_only_telemetry_sends():
    doc = _load("macos-look.yml")
    inputs = doc.get("on", doc.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == ["look", "remote-uefn", "tcc", "telemetry"]
    run = next(s for s in doc["jobs"]["look"]["steps"] if s.get("name") == "Start, wait and screenshot")["run"]
    branches = run.split('case "$MODE" in', 1)[1].split("\n            esac", 1)[0]
    unsets = [b.split(")", 1)[0].strip() for b in branches.split(";;") if "unset DUCKY_SANDBOX" in b]
    assert unsets == ["telemetry"]
    assert run.count("unset DUCKY_SANDBOX") == 1 and "kTCCServiceMicrophone" in run and "DUCKY_TEST_ACCOUNT_TOKEN" in run
    upload = next(s for s in doc["jobs"]["look"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["if"] == "${{ always() }}" and upload["with"]["name"] == "macos-look-${{ inputs.arch }}"


@pytest.mark.parametrize("name", ["macos-look.yml", "macos-app.yml", "macos-install-check.yml", "macos-tests.yml",
                                  "macos-plugins-smoke.yml"])
def test_mac_ci_is_a_test_copy(name):
    assert _load(name)["env"]["DUCKY_SANDBOX"] == "1"
