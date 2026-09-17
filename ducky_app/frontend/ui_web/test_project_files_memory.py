"""File sniffing and changing directory versions must use bounded memory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from frontend.ui_web import project_files as pf


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("binary", [False, True])
def test_editability_reads_only_file_header(tmp_path, monkeypatch, external, binary):
    target = tmp_path / "NOTES"
    target.write_bytes((b"\x00" if binary else b"hello") + b"x" * 8192)
    monkeypatch.setattr(pf, "is_locked_project_file", lambda path: False)
    monkeypatch.setattr(pf, "_is_workspace_locked_path", lambda path: False)
    monkeypatch.setattr(pf, "_resolve_relative", lambda path: target)
    original_open = Path.open
    reads = []

    class HeaderReader:
        def __enter__(self):
            self.handle = original_open(target, "rb")
            return self

        def __exit__(self, *args):
            self.handle.close()

        def read(self, size=-1):
            assert 0 < size <= 4096, "classification must not read a whole file"
            reads.append(size)
            return self.handle.read(size)

    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: HeaderReader())
    path = f"{pf.EXT_PATH_PREFIX}{target}" if external else "Content/NOTES"
    assert pf.is_editable_text_file(path) is not binary
    assert reads == [4096]


def test_external_save_does_not_read_entire_original(tmp_path, monkeypatch):
    target = tmp_path / "notes.txt"
    target.write_text("old contents", encoding="utf-8")

    def reject_whole_read(*args):
        pytest.fail("save validation must read a header, not the entire original")

    monkeypatch.setattr(Path, "read_bytes", reject_whole_read)
    pf.write_external_file(f"{pf.EXT_PATH_PREFIX}{target}", "saved contents")
    assert target.read_text(encoding="utf-8") == "saved contents"


def test_file_list_replaces_obsolete_directory_versions(tmp_path, monkeypatch):
    content = tmp_path / "Content"
    content.mkdir()
    monkeypatch.setattr(pf, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(pf, "_content_dir", lambda: content)
    monkeypatch.setattr(pf, "_workspace_folders", lambda: [])
    monkeypatch.setattr(pf, "_show_hidden_project_files", lambda: False)
    monkeypatch.setattr(pf, "_file_paths_cache", {})
    for index in range(12):
        (content / f"file{index}.verse").write_text("", encoding="utf-8")
        stamp = 1_700_000_000 + index
        os.utime(content, (stamp, stamp))
        rows = pf.list_project_file_paths()
        assert len(rows) == index + 1
        assert pf.list_project_file_paths() is rows
    assert len(pf._file_paths_cache) == 1
