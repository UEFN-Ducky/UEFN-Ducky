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


def test_upload_download_and_modes():
    doc = _doc()
    job = doc["jobs"]["macos-build"]
    up = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert up["with"]["name"] == "macos-dist-${{ matrix.arch }}" and up["with"]["retention-days"] == 14
    downs = [s["with"] for s in doc["jobs"]["publish"]["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact")]
    assert {"pattern": "macos-dist-*", "path": "dist/macos", "merge-multiple": True} in downs
    assert "macos-packages" in _on(doc)["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert doc["env"]["DUCKY_SANDBOX"] == "1"
