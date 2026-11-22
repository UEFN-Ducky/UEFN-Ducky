"""Linux desktop glue in open_files: xdg-open, FileManager1.ShowItems, the menu entry."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from frontend import open_files


@pytest.fixture()
def linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    started: list[list[str]] = []
    bus: list[tuple] = []
    state = {"bus_reply": "()"}

    class FakePopen:
        def __init__(self, cmd, **kw):
            assert kw.get("start_new_session") is True  # never tied to Ducky's own session
            started.append(list(cmd))

    monkeypatch.setattr(open_files.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(open_files.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(open_files, "gdbus_call",
                        lambda *a, **_k: bus.append(a) or state["bus_reply"])
    return {"started": started, "bus": bus, "state": state}


def test_open_path_and_url_use_xdg_open(linux, tmp_path):
    f = tmp_path / "a b.txt"
    f.write_text("x")
    assert open_files.open_path(f) is True
    assert open_files.open_url("https://uefnducky.org") is True
    assert linux["started"] == [["/usr/bin/xdg-open", str(f)], ["/usr/bin/xdg-open", "https://uefnducky.org"]]


def test_no_xdg_open_is_false(linux, monkeypatch):
    monkeypatch.setattr(open_files.shutil, "which", lambda name: None)
    assert open_files.open_url("https://x") is False


def test_show_in_folder_asks_the_file_manager(linux, tmp_path):
    f = tmp_path / "it's.verse"
    f.write_text("x")
    assert open_files.show_in_folder(f) is True
    dest, path, method, uris, startup = linux["bus"][0]
    assert (dest, path, method) == ("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                    "org.freedesktop.FileManager1.ShowItems")
    assert uris == "[" + open_files.gv_str(f.resolve().as_uri()) + "]" and startup == "''"
    assert linux["started"] == []


def test_show_in_folder_falls_back_to_the_folder(linux, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    linux["state"]["bus_reply"] = None  # no FileManager1 on this desktop
    assert open_files.show_in_folder(f) is True
    assert linux["started"] == [["/usr/bin/xdg-open", str(tmp_path)]]


PACKAGING = Path(__file__).resolve().parents[2] / "packaging" / "linux"


def test_packaged_desktop_file_is_the_template():
    text = (PACKAGING / "uefn-ducky.desktop").read_text(encoding="utf-8")
    assert text == open_files.DESKTOP_ENTRY.format(exec="uefn-ducky")
    assert "MimeType=x-scheme-handler/uefn-ducky;" in text and "Categories=Development;" in text


@pytest.mark.skipif(shutil.which("desktop-file-validate") is None, reason="desktop-file-utils not installed")
def test_desktop_file_validates():
    out = subprocess.run(["desktop-file-validate", str(PACKAGING / "uefn-ducky.desktop")],
                         capture_output=True, text=True)
    assert out.returncode == 0 and not out.stdout.strip(), out.stdout + out.stderr


def test_hicolor_icons_are_square_pngs():
    from PIL.PngImagePlugin import PngImageFile

    for size in (16, 32, 48, 64, 128, 256):
        icon = PACKAGING / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "uefn-ducky.png"
        assert PngImageFile(str(icon)).size == (size, size)


def test_scheme_handler_args_parse():
    # Exec=uefn-ducky %U: the browser's sign-in link arrives as argv[1].
    link = "uefn-ducky://signin?code=abc&state=x%20y"
    assert open_files.cli_deep_links(["/opt/ducky/uefn-ducky", link]) == [link]
    assert open_files.cli_deep_links(["/opt/ducky/uefn-ducky", "--background", link, "/tmp/x"]) == [link]
    assert open_files.cli_deep_links(["uefn-ducky", "bridge", link]) == []


def test_menu_entry_and_handler_registered_without_a_package(linux, monkeypatch, tmp_path):
    ran: list[list[str]] = []
    monkeypatch.setattr(open_files, "PACKAGED_DESKTOP_FILES", (str(tmp_path / "none.desktop"),))
    monkeypatch.setattr(open_files.subprocess, "run", lambda cmd, **_k: ran.append(list(cmd)))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    app = tmp_path / "My Apps" / "UEFN-Ducky.AppImage"
    assert open_files.register_linux_url_handler(app) is True
    entry = tmp_path / "data" / "applications" / "uefn-ducky.desktop"
    assert entry.read_text(encoding="utf-8") == open_files.DESKTOP_ENTRY.format(exec=open_files.desktop_exec_quote(str(app)))
    assert 'Exec="' in entry.read_text(encoding="utf-8")  # the space in "My Apps" is quoted
    assert ran == [["/usr/bin/xdg-mime", "default", "uefn-ducky.desktop", "x-scheme-handler/uefn-ducky"]]


def test_a_package_owns_the_entry(linux, monkeypatch, tmp_path):
    packaged = tmp_path / "uefn-ducky.desktop"
    packaged.write_text("x")
    monkeypatch.setattr(open_files, "PACKAGED_DESKTOP_FILES", (str(packaged),))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert open_files.register_linux_url_handler("/x/ducky") is True
    assert not (tmp_path / "data").exists()


def test_gv_str_and_exec_quote():
    assert open_files.gv_str("it's \\ ok") == "'it\\'s \\\\ ok'"
    assert open_files.desktop_exec_quote("/opt/ducky/uefn-ducky") == "/opt/ducky/uefn-ducky"
    assert open_files.desktop_exec_quote('/home/a b/$x"y') == '"/home/a b/\\$x\\"y"'
