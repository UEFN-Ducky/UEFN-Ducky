"""The Verse editor's calls: its switch, the Problems list's cache, a file's history and
the shadow ``.vproject``. The same answer, the same rows, the same events and the same files from
both backends on a fixture island. No verse-lsp
runs here, and nothing reads the person's own islands."""

from __future__ import annotations

import json
import os
import shutil
import time
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

    Editor.island, Editor.folder, Editor.python, Editor.rust = island, folder, python, rust_store
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


def _anon(value: Any, ids: list[str]) -> Any:
    """An answer without what differs by the clock: a version's id becomes its place in
    the list, and its time is only checked to be now."""
    if isinstance(value, dict):
        if "saved_at" in value:
            assert abs(value["saved_at"] - time.time()) < 600, value
        return {key: 0 if key == "saved_at" else _anon(item, ids) for key, item in value.items()}
    if isinstance(value, list):
        return [_anon(item, ids) for item in value]
    if isinstance(value, str):
        for place, entry in enumerate(ids):
            value = value.replace(entry, f"#{place}")
    return value


def test_a_files_history_keeps_each_text_once_and_lists_the_newest_first(editor):
    first, long = "\n\n  first line  \nsecond\n", "é" * 200
    path = "Content/a.verse"

    def story(backend: Any) -> tuple:
        def call(method: str, *args: Any) -> Any:
            return plain(backend.call(method, *args))

        _sql("DELETE FROM file_versions")
        _sql("DELETE FROM blobs")
        editor.open(editor.island)
        told = [call("list_file_history", path), call("snapshot_file_history", " Content\\a.verse ", first)]
        told.append(call("snapshot_file_history", path, first))  # the same text again: no new version
        told.append(call("snapshot_file_history", path, long))
        told.append(call("record_external_file_change", path, "old text\n", "new text\n"))
        told.append(call("record_external_file_change", path, "same", "same"))
        told.append(call("record_external_file_change", "", "same", "same"))
        listed = call("list_file_history", path)
        ids = [row["id"] for row in listed[1]["result"]]
        assert len(set(ids)) == 3 and ids == sorted(ids, key=int, reverse=True), ids
        told += [listed, *(call("read_file_history_entry", path, entry) for entry in ids)]
        # What is asked for wrongly gets the same refusal.
        told.append(call("read_file_history_entry", path, "12"))
        told.append(call("read_file_history_entry", path, " "))
        told.append(call("read_file_history_entry", "", ids[0]))
        told += [call("list_file_history", "Content/other.verse"), call("list_file_history", " ")]
        told += [call("snapshot_file_history", "", "x"), call("record_external_file_change", "", "a", "b")]
        editor.open("")
        told += [call("list_file_history", path), call("snapshot_file_history", path, "x"), call("read_file_history_entry", path, ids[0])]
        told += [call("record_external_file_change", path, "a", "b"), call("record_external_file_change", path, "a", "a")]
        rows = _sql("SELECT path, bytes, preview, content_hash, schema_version, source, tool FROM file_versions ORDER BY entry_id")
        blobs = _sql("SELECT hash, text, size FROM blobs ORDER BY hash")
        return _anon(told, ids), plain(rows), plain(blobs)

    theirs, ours = story(editor.python), story(editor.rust)
    for name, mine, yours in zip(("answers", "versions", "texts"), ours, theirs):
        assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
    told, rows, blobs = theirs
    listed = told[7][1]["result"]
    assert [(row["id"], row["bytes"], row["preview"]) for row in listed] == [("#0", 9, "old text"), ("#1", 400, "é" * 120), ("#2", len(first), "first line")]
    assert told[1][1] == {"ok": True, "result": {"id": "#2", "path": path}} and told[2] == told[1]
    assert told[4][1]["result"] == {"ok": True, "snapshotted": True} and told[6][1]["result"] == {"ok": True, "snapshotted": False}
    assert [reply[1]["result"]["content"] for reply in told[8:11]] == ["old text\n", long, first]
    refused = [reply[1].get("error") for reply in told[11:14] + told[15:22]]
    assert all(refused) and refused[0] == "History entry not found: 12" and refused[-1] == "No project selected.", refused
    assert told[14][1] == {"ok": True, "result": []} and told[22][1]["result"]["snapshotted"] is False
    assert len(rows) == 3 and len(blobs) == 3


_BUILT_IN = ("Verse", "UnrealEngine", "Fortnite")


def test_an_island_without_its_digests_gets_the_same_shadow_vproject(tmp_path, rust_store, python):
    """verse-lsp reads its packages from the ``.vproject``: while UEFN has not written the
    island's digests, both backends hand it the same copy pointing at FortniteGame's."""
    from frontend.settings import default_app_data_dir

    island = tmp_path / "work" / "Isle"
    saved = tmp_path / "UnrealEditorFortnite" / "Saved" / "VerseProject"
    own = saved / "Isle" / "Digests" / "BuiltIn"
    digests = {f"{name}/{name}.digest.verse": "#\n" for name in _BUILT_IN}
    _put(island, {"Isle.uefnproject": "{}", "Content/a.verse": "a := 1\n"})
    _put(saved / "FortniteGame" / "Digests" / "BuiltIn", digests)
    _put(own, {"Fortnite/Fortnite.digest.verse": "#\n"})
    packages: list[Any] = [
        {"desc": {"name": name, "dirPath": (own / name).as_posix(), "settings": {"role": "External"}}, "readOnly": True}
        for name in _BUILT_IN
    ]
    mine = {"versePath": "/é@fortnite.com/Isle", "rate": 1.5, "tags": []}
    packages += [{"desc": {"name": "Isle", "dirPath": (island / "Content").as_posix(), "settings": mine}}, "junk", {"desc": None}]
    real = saved / "Isle" / "vproject" / "Isle.vproject"

    def uefn_writes(extra: dict) -> None:
        _put(real.parent, {real.name: json.dumps({"packages": packages, "extra": extra}, indent=2)})

    projects.touch(str(island), name="Isle", when=1.0, kind="uefn")
    settings.replace_all({"uefn_project_root": str(island)}, {})
    kept = default_app_data_dir() / "verse-lsp" / "vproject-shadow" / "Isle"
    shadow = kept / "vproject" / "Isle.vproject"
    assert tmp_path in kept.parents  # never the person's own data folder

    def roots(backend: Any) -> Any:
        _forget()
        assert rust_store.request("/__panel_event", {"type": "project_changed"})[0] == 204
        status, reply = plain(backend.call("list_workspace_roots"))
        assert status == 200 and reply["ok"] is True, reply
        return reply["result"]

    seen = []
    for backend in (python, rust_store):
        shutil.rmtree(kept, ignore_errors=True)
        uefn_writes({})
        first = (roots(backend), shadow.read_bytes())
        # The copy follows the island's file.
        uefn_writes({"edited": True})
        second = (roots(backend), shadow.read_bytes())
        # UEFN wrote the island's own digests: its file is used again and the copy goes.
        _put(own, digests)
        third = (roots(backend), kept.exists())
        seen.append((first, second, third))
        for name in ("Verse", "UnrealEngine"):
            shutil.rmtree(own / name)
    assert seen[1] == seen[0]
    (found, text), (_, edited), (restored, still_there) = seen[0]
    assert text.isascii() and edited != text and restored != found and still_there is False
    copy = json.loads(text)
    paths = {package["desc"]["name"]: package["desc"]["dirPath"] for package in copy["packages"][:4]}
    assert "FortniteGame" in paths["Verse"] and "FortniteGame" in paths["UnrealEngine"]
    assert paths["Fortnite"] == (own / "Fortnite").as_posix() and copy["packages"][3]["desc"]["settings"] == mine
    assert "FortniteGame" not in real.read_text(encoding="utf-8")  # UEFN's own file is never touched
