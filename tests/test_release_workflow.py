"""store-publish.yml ships macOS: macos-build on both chips, before publish, green without Apple secrets."""
import re
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "store-publish.yml"
APPLE = ("MACOS_CERT_P12_B64", "MACOS_CERT_PASSWORD", "APPLE_TEAM_ID", "ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_KEY_P8_B64")


def _doc():
    return yaml.safe_load(WF.read_text(encoding="utf-8"))


def _on(doc):
    return doc.get("on", doc.get(True))  # pyyaml reads a bare `on:` key as True


def test_macos_build_runs_before_publish():
    jobs = _doc()["jobs"]
    assert "macos-build" in jobs["publish"]["needs"]
    assert jobs["macos-build"]["needs"] == "linux-version"
    assert jobs["macos-build"]["if"] == "inputs.mode != 'linux-bumps'"


def test_both_mac_runners():
    rows = _doc()["jobs"]["macos-build"]["strategy"]["matrix"]["include"]
    assert {(r["runner"], r["arch"]) for r in rows} == {("macos-26", "arm64"), ("macos-15-intel", "x64")}
    assert not any("large" in r["runner"] for r in rows)  # paid runners


def test_green_without_apple_secrets():
    job = _doc()["jobs"]["macos-build"]
    assert set(APPLE) <= set(job["env"])
    text = "\n".join(str(s.get("run", "")) for s in job["steps"])
    # Nothing ever refuses to go on because a secret is missing, and none is echoed.
    assert not re.search(r'-z "\$\{?(' + "|".join(APPLE) + ")", text)
    assert not re.search(r"echo[^\n]*\$\{?(" + "|".join(APPLE) + ")", text)
    sign = next(s for s in job["steps"] if s.get("name", "").startswith("Sign"))
    assert "codesign --force --deep -s -" in sign["run"]
    assert "::notice::unsigned macOS build (no Apple secrets)" in sign["run"]
    notarize = next(s for s in job["steps"] if s.get("name") == "Notarize")
    assert notarize["if"] == "env.MACOS_SIGNED == '1'"
    cleanup = next(s for s in job["steps"] if "keychain" in s.get("name", "").lower() and "Delete" in s["name"])
    assert "always()" in cleanup["if"]


def test_macos_bumps_tap_and_skips_without_token():
    jobs = _doc()["jobs"]
    job = jobs["macos-bumps"]
    assert job["if"] == jobs["linux-bumps"]["if"] and job["needs"] == ["publish"]
    assert job["runs-on"] == "macos-26"
    token, *rest = job["steps"]
    assert '-z "$HOMEBREW_TAP_TOKEN"' in token["run"] and "::warning::" in token["run"] and "SKIP=1" in token["run"]
    assert "exit 1" not in token["run"]
    assert all(s.get("if") == "env.SKIP != '1'" for s in rest)
    run = rest[-1]["run"]
    assert "build/macos/cask_bump.py" in run and "brew audit --cask --online" in run
    assert "UEFN-Ducky/homebrew-tap" in run and 'commit -m "uefn-ducky $VERSION"' in run


def test_upload_download_and_modes():
    doc = _doc()
    job = doc["jobs"]["macos-build"]
    up = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up["with"]["name"] == "macos-dist-${{ matrix.arch }}" and up["with"]["retention-days"] == 14
    downs = [s["with"] for s in doc["jobs"]["publish"]["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact")]
    assert {"pattern": "macos-dist-*", "path": "dist/macos", "merge-multiple": True} in downs
    assert "macos-packages" in _on(doc)["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert doc["env"]["DUCKY_SANDBOX"] == "1"


def test_iced_x86_pin_per_platform():
    """1.22.0's macOS wheel is arm64-only, so an Intel Mac stays on 1.21.0; nothing else changes."""
    from packaging.requirements import Requirement

    reqs = WF.parents[2] / "requirements.txt"
    pins = [Requirement(line.split("#")[0]) for line in reqs.read_text(encoding="utf-8").splitlines()
            if line.lower().startswith("iced-x86")]

    def pin(sys_platform, machine):
        env = {"sys_platform": sys_platform, "platform_machine": machine}
        return [str(r.specifier) for r in pins if r.marker.evaluate(env)]

    assert pin("darwin", "x86_64") == ["==1.21.0"]
    assert pin("darwin", "arm64") == ["==1.22.0"]
    assert pin("win32", "AMD64") == ["==1.22.0"]
    assert pin("linux", "x86_64") == pin("linux", "aarch64") == ["==1.22.0"]
    assert pin("win32", "ARM64") == []  # no win_arm64 wheel, as before
    for wf in ("plugin-macos.yml", "plugin-linux-arm64.yml"):
        assert "-r \"$RUNNER_TEMP/iced.txt\"" in (WF.parent / wf).read_text(encoding="utf-8")
