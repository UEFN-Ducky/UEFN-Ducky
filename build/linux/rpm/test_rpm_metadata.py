"""The .rpm spec and dnf repo: runtime and voice Requires, the file name, signed metadata."""

import configparser
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _spec(tag: str) -> list[str]:
    text = (HERE / "uefn-ducky.spec").read_text(encoding="utf-8")
    return [m.group(1).strip() for m in re.finditer(rf"^{tag}:\s*(.+)$", text, re.M)]


def test_spec_requires_the_runtime_and_voice_libraries():
    requires = {name.strip() for line in _spec("Requires") for name in line.split(",")}
    assert {"webkit2gtk4.1", "libayatana-appindicator-gtk3", "libsecret", "espeak-ng", "portaudio"} <= requires


def test_spec_names_the_file_publish_app_expects():
    # uefn-ducky-<version>-1.<arch>.rpm
    assert _spec("Name") == ["uefn-ducky"] and _spec("Release") == ["1"]
    assert _spec("Version") == ["%{ducky_version}"]
    assert _spec("AutoReqProv") == ["no"]  # the PyInstaller bundle's private libs are not system deps


def test_repo_metadata_is_signed_and_checked():
    script = (HERE / "build_dnf_repo.sh").read_text(encoding="utf-8")
    assert "createrepo_c" in script and "release/sign_linux.py}" in script
    assert '--repo-meta "$repo/repodata/repomd.xml"' in script
    assert 'test -s "$repo/repodata/repomd.xml.asc"' in script
    repo = configparser.ConfigParser()
    repo.read(HERE / "uefn-ducky.repo", encoding="utf-8")
    assert repo["uefn-ducky"]["repo_gpgcheck"] == "1"
