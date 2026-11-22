"""Linux desktop glue in open_files: xdg-open, FileManager1.ShowItems, the menu entry."""
from __future__ import annotations

import sys

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


def test_gv_str_and_exec_quote():
    assert open_files.gv_str("it's \\ ok") == "'it\\'s \\\\ ok'"
    assert open_files.desktop_exec_quote("/opt/ducky/uefn-ducky") == "/opt/ducky/uefn-ducky"
    assert open_files.desktop_exec_quote('/home/a b/$x"y') == '"/home/a b/\\$x\\"y"'
