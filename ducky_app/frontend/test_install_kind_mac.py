"""macOS install kind: the .app bundle (/Applications or ~/Applications) or a source checkout."""

from __future__ import annotations

import sys
from pathlib import PurePosixPath

import pytest

from frontend import install_info


@pytest.fixture(autouse=True)
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.mark.parametrize(("exe", "kind"), [
    ("/Applications/UEFN Ducky.app/Contents/MacOS/UEFN-Ducky", "app"),
    ("/Users/a/Applications/UEFN Ducky.app/Contents/MacOS/UEFN-Ducky", "app"),
    ("/Applications/UEFN Ducky.app/Contents/MacOS/_internal/frontend/install_info.py", "app"),
    ("/Users/a/code/UEFN-Ducky/ducky_app/frontend/install_info.py", "source"),
    ("/Users/a/Downloads/MacOS/UEFN-Ducky", "source"),
])
def test_kind(exe, kind):
    assert install_info.install_kind(exe=PurePosixPath(exe)) == kind


def test_label_and_info(monkeypatch):
    import platform

    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert install_info.install_label("app") == "App · arm64"
    monkeypatch.setattr(install_info, "install_kind", lambda **_: "app")
    info = install_info.get_install_info()
    assert info["install_kind"] == "app" and info["install_label"] == "App · arm64"
    assert info["installed"] is False  # the Windows registry fields keep their meaning
