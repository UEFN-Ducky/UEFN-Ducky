"""macOS desktop glue in open_files: `open`, `open -R` (Finder reveal), `open <url>`."""
from __future__ import annotations

import sys

import pytest

from frontend import open_files


@pytest.fixture()
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    started: list[list[str]] = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            assert kw.get("start_new_session") is True
            started.append(list(cmd))

    monkeypatch.setattr(open_files.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(open_files, "gdbus_call", lambda *a, **k: pytest.fail("no D-Bus on macOS"))
    monkeypatch.setattr(open_files.shutil, "which", lambda name: pytest.fail(f"no {name} on macOS"))
    return started


def test_open_path_and_url_use_open(mac, tmp_path):
    f = tmp_path / "a b.verse"
    f.write_text("x")
    assert open_files.open_path(f) is True
    assert open_files.open_url("https://uefnducky.org") is True
    assert mac == [["open", str(f)], ["open", "https://uefnducky.org"]]


def test_show_in_folder_reveals_in_finder(mac, tmp_path):
    f = tmp_path / "x.verse"
    f.write_text("x")
    assert open_files.show_in_folder(f) is True
    assert mac == [["open", "-R", str(f)]]


def test_show_in_folder_of_a_missing_file_opens_its_folder(mac, tmp_path):
    assert open_files.show_in_folder(tmp_path / "gone.txt") is True
    assert mac == [["open", str(tmp_path)]]


def test_no_open_binary_is_false(mac, monkeypatch):
    def boom(*a, **k):
        raise OSError("no open")

    monkeypatch.setattr(open_files.subprocess, "Popen", boom)
    assert open_files.open_url("https://x") is False
