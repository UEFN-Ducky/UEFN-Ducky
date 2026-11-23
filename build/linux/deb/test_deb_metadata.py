"""The .deb control file: the runtime libraries, voice dependencies and placeholders."""

from pathlib import Path

HERE = Path(__file__).resolve().parent


def _fields() -> dict[str, str]:
    text = (HERE / "control").read_text(encoding="utf-8")
    return dict(line.split(": ", 1) for line in text.splitlines() if line and not line.startswith(" "))


def _names(value: str) -> set[str]:
    return {alt.strip().split(" ")[0] for dep in value.split(",") for alt in dep.split("|")}


def test_control_depends_on_the_runtime_and_voice_libraries():
    depends = _names(_fields()["Depends"])
    assert {"libwebkit2gtk-4.1-0", "libayatana-appindicator3-1", "libsecret-1-0", "espeak-ng", "libportaudio2"} <= depends


def test_control_is_filled_in_by_build_deb():
    fields = _fields()
    assert fields["Package"] == "uefn-ducky"
    assert (fields["Version"], fields["Architecture"], fields["Installed-Size"]) == ("@VERSION@", "@ARCH@", "@SIZE@")
    script = (HERE / "build_deb.sh").read_text(encoding="utf-8")
    assert all(f"s/@{key}@/" in script for key in ("VERSION", "ARCH", "SIZE"))
    assert 'uefn-ducky_${version}_${arch}.deb' in script  # the name publish_app.py picks up


def test_repo_release_is_signed_through_sign_linux():
    script = (HERE / "build_apt_repo.sh").read_text(encoding="utf-8")
    assert "release/sign_linux.py}" in script
    assert '"${signer[@]}" --repo-meta dists/stable/Release' in script
    assert "test -s dists/stable/InRelease && test -s dists/stable/Release.gpg" in script
