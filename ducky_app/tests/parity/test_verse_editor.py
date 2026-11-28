"""The Verse editor's calls: its switch and the Problems list's cache. The same answer,
the same rows and the same events from both backends on a fixture island. No verse-lsp
runs here, and nothing reads the person's own islands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from backend.store.repos import projects, settings

from .parity_harness import plain
from .test_api_project_files import _forget, _put
from .test_store_chats import _sql

_MARK = {"marker": 1}
_WHEN = 1_700_000_000_123_456_700  # every fixture file's modified time, so both runs keep the same rows
_VERSE = {
    "Isle.uefnproject": "{}",
    "Content/a.verse": "a := 1\n",
    "Content/Sub/B.verse": "b := 2\n",
    "Content/__ExternalActors__/x.verse": "x := 3\n",
    "Content/notes.txt": "not verse",
    "root.verse": "outside Content\n",
}
_ITEM = {"line": 3, "column": 1, "severity": "error", "message": "Unknown identifier `é`"}


def _write(root: Path, files: dict[str, str]) -> None:
    _put(root, files)
    for rel in files:
        os.utime(root / rel, ns=(_WHEN, _WHEN))


@pytest.fixture
def editor(tmp_path, rust_store, python, monkeypatch):
    """``editor.each(story)`` runs ``story(call)`` on each backend from the same files and
    an empty cache, and gives what it returned, the cache rows it left and the events it
    pushed; all three must match. ``call(method, *args)`` is one panel call's result."""
    from frontend.ui_web.agent_turns import push
    from frontend.ui_web.verse_editor.api import VerseEditorApi
    from frontend.ui_web.verse_editor.lsp import diagnostics_cache

    pushed: list[dict] = []
    monkeypatch.setattr(push, "_panel_push", pushed.append)  # never the live app's panel
    python.api._verse_editor = VerseEditorApi()
    island, folder = tmp_path / "work" / "Isle", tmp_path / "work" / "Plain Folder"
    _put(folder, {"README.md": "# folder\n", "Content/c.verse": "c := 1\n"})
    projects.touch(str(island), name="Isle", when=2.0, kind="uefn")
    projects.touch(str(folder), name="Plain Folder", when=1.0, kind="folder")
    cursor = [0]

    def rust_events() -> list[dict]:
        # A marker ends the page, so a story that pushed nothing does not wait out a poll.
        assert rust_store.call("push_appearance_live", _MARK) == (200, {"ok": True, "result": None})
        _, page = rust_store.request(f"/__panel_events?since={cursor[0]}")
        cursor[0] = page["cursor"]
        return page["events"][:-1]

    class Editor:
        pass

    def open_(root: Path | str) -> None:
        settings.replace_all({"uefn_project_root": str(root)}, {})
        _forget()

    def each(story: Any) -> tuple[Any, list, list]:
        seen = []
        for backend in (python, rust_store):
            _write(island, _VERSE)
            _sql("DELETE FROM verse_diagnostics")
            diagnostics_cache._MEM.clear()
            diagnostics_cache._MEM_DISK_FP.clear()
            open_(island)
            pushed.clear()

            def call(method: str, *args: Any, backend: Any = backend) -> Any:
                status, reply = plain(backend.call(method, *args))
                assert status == 200 and reply["ok"] is True, f"{method}{args}: {reply}"
                return reply["result"]

            told = story(call)
            rows = plain(_sql("SELECT path, mtime_ns, size, errors, warnings, items FROM verse_diagnostics ORDER BY path"))
            seen.append((plain(told), rows, list(pushed) if backend is python else rust_events()))
        theirs, ours = seen
        for name, mine, yours in zip(("answers", "rows", "events"), ours, theirs):
            assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
        return theirs

    Editor.island, Editor.folder = island, folder
    Editor.each, Editor.open = staticmethod(each), staticmethod(open_)
    _forget()
    yield Editor
    _forget()


def test_the_editor_switch_is_its_setting(editor):
    def story(call):
        on = call("is_verse_editor_enabled")
        settings.replace_all({"verse_editor_enabled": False}, {})
        return [on, call("is_verse_editor_enabled")]

    assert editor.each(story)[0] == [True, False]


def test_the_problems_cache_keeps_each_checked_file(editor):
    island = editor.island

    def story(call):
        told = [call("load_verse_diagnostics_cache")]
        told.append(call("save_verse_file_cache", "Content/a.verse", 2, 1, [_ITEM, {"line": 9}]))
        told.append(call("save_verse_file_cache", " \\Content\\Sub\\B.verse ", 0, 0))
        told.append(call("save_verse_file_cache", "Content/missing.verse", 1, 0, []))
        # Checked, but not a file the Problems list covers: never kept.
        told.append(call("save_verse_file_cache", "root.verse", 4, 0, []))
        told.append(call("save_verse_file_cache", "Content/__ExternalActors__/x.verse", 5, 0, []))
        told.append(call("load_verse_diagnostics_cache"))
        # An edited file is checked again; a deleted one leaves the list.
        (island / "Content/Sub/B.verse").write_text("b := 2222\n", encoding="utf-8")
        os.utime(island / "Content/Sub/B.verse", ns=(_WHEN + 1, _WHEN + 1))
        told.append(call("load_verse_diagnostics_cache", str(island)))
        (island / "Content/a.verse").unlink()
        told.append(call("load_verse_diagnostics_cache", str(island / "Content")))
        told.append(call("save_verse_file_cache", "Content/Sub/B.verse", 0, 3, [], str(island)))
        return told

    told, rows, events = editor.each(story)
    empty, a, b, missing, outside, external, both, edited, deleted, again = told
    assert empty == {"files": [], "stale_count": 2, "from_cache": True}
    assert a == {"ok": True, "path": "content/a.verse"} and b == {"ok": True, "path": "content/sub/b.verse"}
    assert missing == {"ok": False, "error": "File not found"} and outside["ok"] is True and external["ok"] is True
    assert both["files"] == [
        {"path": "content/a.verse", "errors": 2, "warnings": 1, "items": [_ITEM, {"line": 9}]},
        {"path": "content/sub/b.verse", "errors": 0, "warnings": 0, "items": []},
    ]
    # A file that had problems is always checked again.
    assert (both["stale_count"], edited["stale_count"], deleted["stale_count"]) == (1, 2, 1)
    assert [row["path"] for row in deleted["files"]] == ["content/sub/b.verse"] and again["ok"] is True
    assert [row[0] for row in rows] == ["content/sub/b.verse"] and rows[0][4] == 3
    assert len(events) == 5 and all(event["type"] == "verse_diagnostics_sync" and event["live"] is True for event in events)
    assert events[0]["files"] == [{"path": "content/a.verse", "errors": 2, "warnings": 1, "items": [_ITEM, {"line": 9}]}]
    assert events[0]["replace"] is False and Path(events[0]["project_root"]) == island


def test_the_problems_cache_is_empty_without_an_island(editor):
    def story(call):
        told = [call("load_verse_diagnostics_cache", str(editor.folder))]
        editor.open(editor.folder)
        told.append(call("load_verse_diagnostics_cache"))
        editor.open("")
        told += [call("load_verse_diagnostics_cache"), call("save_verse_file_cache", "Content/a.verse", 1, 0, [])]
        return told

    told, rows, events = editor.each(story)
    off = {"files": [], "stale_count": 0, "from_cache": False}
    assert told == [off, off, off, {"ok": False, "error": "No project root set"}] and rows == [] and events == []
