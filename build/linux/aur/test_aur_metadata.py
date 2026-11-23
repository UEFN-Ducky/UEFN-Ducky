"""The AUR package: PKGBUILD and .SRCINFO agree, runtime and voice depends, /opt install."""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _pkgbuild_array(name: str) -> list[str]:
    text = (HERE / "PKGBUILD").read_text(encoding="utf-8")
    body = re.search(rf"^{name}=\((.*?)\)", text, re.M | re.S).group(1)
    return re.findall(r"'([^']*)'", body)


def _srcinfo(key: str) -> list[str]:
    lines = (HERE / ".SRCINFO").read_text(encoding="utf-8").splitlines()
    return [line.split(" = ", 1)[1] for line in lines if line.strip().startswith(f"{key} = ")]


def test_depends_include_runtime_and_voice():
    depends = _pkgbuild_array("depends")
    assert {"webkit2gtk-4.1", "libayatana-appindicator", "libsecret", "espeak-ng", "portaudio"} <= set(depends)
    assert _srcinfo("depends") == depends


def test_srcinfo_matches_pkgbuild():
    text = (HERE / "PKGBUILD").read_text(encoding="utf-8")
    pkgver = re.search(r"^pkgver=(.+)$", text, re.M).group(1)
    assert _srcinfo("pkgbase") == ["uefn-ducky-bin"] and _srcinfo("pkgname") == ["uefn-ducky-bin"]
    assert _srcinfo("pkgver") == [pkgver]
    assert _srcinfo("arch") == _pkgbuild_array("arch") == ["x86_64", "aarch64"]
    assert _srcinfo("optdepends") == _pkgbuild_array("optdepends")
    for arch in ("x86_64", "aarch64"):
        [source] = _srcinfo(f"source_{arch}")
        assert source.startswith(f"UEFN-Ducky-{pkgver}-{arch}.AppImage::https://")


def test_installs_the_appimage_contents_into_opt():
    text = (HERE / "PKGBUILD").read_text(encoding="utf-8")
    assert "--appimage-extract" in text
    assert 'cp -a usr/bin "$pkgdir/opt/uefn-ducky"' in text
    assert "usr/share/applications/uefn-ducky.desktop" in text and "usr/share/icons" in text
