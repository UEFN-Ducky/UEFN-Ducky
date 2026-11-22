"""Linux install kind, its health check and its uninstall command, one fake install per kind."""

from __future__ import annotations

import sys
from pathlib import Path, PurePosixPath

import pytest

from frontend import install_health, install_info, uninstaller_link

SYSTEM_EXE = PurePosixPath("/usr/lib/uefn-ducky/UEFN-Ducky")


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")


def _owner(kind: str, verify_out: str = ""):
    """A fake package manager: *kind*'s owner query and verify succeed, the others fail."""
    owner_tool = {"deb": "dpkg", "rpm": "rpm", "aur": "pacman"}.get(kind)
    calls: list[list[str]] = []

    def run(cmd: list[str]) -> tuple[int, str]:
        calls.append(cmd)
        if cmd[0] != owner_tool:
            return 127, ""
        if cmd[1] in ("-V", "-Qkk"):
            return (1 if verify_out and cmd[0] == "pacman" else 0), verify_out
        return 0, f"{install_info.LINUX_PACKAGE}: {cmd[-1]}"

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _appimage(tmp_path: Path, head: bytes = b"\x7fELF....") -> dict[str, str]:
    image = tmp_path / "UEFN-Ducky-x86_64.AppImage"
    image.write_bytes(head)
    image.chmod(0o755)
    return {"APPIMAGE": str(image)}


def test_flatpak(tmp_path: Path) -> None:
    (tmp_path / ".flatpak-info").write_text("[Application]\n", encoding="utf-8")
    env = {"FLATPAK_ID": "org.uefnducky.Ducky", "APPIMAGE": ""}
    assert install_info.install_kind(env=env, root=str(tmp_path)) == "flatpak"
    assert install_health.linux_health(env=env, root=str(tmp_path))["ok"] is False  # no /app
    (tmp_path / "app").mkdir()
    assert install_health.linux_health(env=env, root=str(tmp_path)) == {"kind": "flatpak", "ok": True, "problem": ""}
    assert uninstaller_link.linux_uninstall_command("flatpak", env) == "flatpak uninstall org.uefnducky.Ducky"


def test_appimage(tmp_path: Path) -> None:
    env = _appimage(tmp_path)
    assert install_info.install_kind(env=env, root=str(tmp_path), exe=SYSTEM_EXE, run=_owner("deb")) == "appimage"
    assert install_health.linux_health(env=env, root=str(tmp_path))["ok"] is True
    assert uninstaller_link.linux_uninstall_command("appimage", env).startswith("rm ")
    env = _appimage(tmp_path, head=b"\0\0\0\0")  # zero-filled download
    assert install_health.linux_health(env=env, root=str(tmp_path))["problem"].startswith("The AppImage file")


@pytest.mark.parametrize(("kind", "remove"), [("deb", "sudo apt remove"), ("rpm", "sudo dnf remove"),
                                              ("aur", "sudo pacman -R")])
def test_packages(tmp_path: Path, kind: str, remove: str) -> None:
    run = _owner(kind)
    assert install_info.install_kind(env={}, root=str(tmp_path), exe=SYSTEM_EXE, run=run) == kind
    assert install_health.linux_health(kind, env={}, root=str(tmp_path), run=run)["ok"] is True
    broken = _owner(kind, verify_out="missing   /usr/lib/uefn-ducky/UEFN-Ducky")
    health = install_health.linux_health(kind, env={}, root=str(tmp_path), run=broken)
    assert health["ok"] is False and health["problem"]
    assert uninstaller_link.linux_uninstall_command(kind, {}) == f"{remove} {install_info.LINUX_PACKAGE}"


def test_source_never_asks_the_package_managers(tmp_path: Path) -> None:
    run = _owner("deb")
    exe = PurePosixPath("/home/me/code/ducky_app/frontend/install_info.py")
    assert install_info.install_kind(env={}, root=str(tmp_path), exe=exe, run=run) == "source"
    assert run.calls == []
    assert install_health.linux_health("source", env={}, root=str(tmp_path))["ok"] is True
    assert uninstaller_link.linux_uninstall_command("source", {}) == ""


def test_label_names_the_kind_and_the_machine(monkeypatch) -> None:
    import platform

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert install_info.install_label("appimage") == "AppImage · x86_64"


def test_install_info_carries_the_kind_on_linux(monkeypatch) -> None:
    monkeypatch.setattr(install_info, "install_kind", lambda **_: "deb")
    info = install_info.get_install_info()
    assert info["install_kind"] == "deb" and info["install_label"].startswith("Debian package · ")
    assert info["installed"] is False  # the Windows fields keep their meaning


def test_windows_kinds(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(install_info, "is_installed", lambda: True)
    assert install_info.install_kind() == "installer"
    monkeypatch.setattr(install_info, "is_installed", lambda: False)
    assert install_info.install_kind() == "portable"
