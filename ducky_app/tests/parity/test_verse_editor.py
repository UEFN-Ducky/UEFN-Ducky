"""The Verse editor's calls: its switch, the Problems list's cache, a file's history, the
shadow ``.vproject``, its verse-lsp sessions, the project scan, UEFN's workflow server
and revision control. The same answer, the same rows, the same events and the same files
from both backends on a fixture island. The verse-lsp, ``lore.exe`` and ``urc.exe`` are
stand-ins built with the backend (never Epic's, never ones found on this PC), the
workflow server is a stand-in in this file (never a running UEFN), and nothing reads the
person's own islands."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from backend.store.repos import projects, settings

from .parity_harness import backend_exe, plain
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
    running = [python]

    def rust_events(peek: bool = False) -> list[dict]:
        # A marker ends the page, so a story that pushed nothing does not wait out a poll.
        assert rust_store.call("push_appearance_live", _MARK) == (200, {"ok": True, "result": None})
        _, page = rust_store.request(f"/__panel_events?since={cursor[0]}")
        if not peek:
            cursor[0] = page["cursor"]
        return [event for event in page["events"] if event != page["events"][-1]]

    def seen() -> list[dict]:
        """The events the running story's backend pushed so far."""
        return list(pushed) if running[0] is python else rust_events(peek=True)

    class Editor:
        pass

    def open_(root: Path | str) -> None:
        settings.replace_all({"uefn_project_root": str(root)}, {})
        _forget()

    def each(story: Any) -> tuple[Any, list, list]:
        seen = []
        for backend in (python, rust_store):
            running[0] = backend
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

            call.raw = lambda method, *args, backend=backend: plain(backend.call(method, *args))  # a refusal too

            told = story(call)
            rows = plain(_sql("SELECT path, mtime_ns, size, errors, warnings, items FROM verse_diagnostics ORDER BY path"))
            seen.append((plain(told), rows, Editor.order(list(pushed) if backend is python else rust_events())))
        theirs, ours = seen
        for name, mine, yours in zip(("answers", "rows", "events"), ours, theirs):
            assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
        return theirs

    Editor.island, Editor.folder, Editor.python, Editor.rust = island, folder, python, rust_store
    Editor.each, Editor.open, Editor.seen = staticmethod(each), staticmethod(open_), staticmethod(seen)
    Editor.order = staticmethod(lambda events: events)  # a story whose events race says how they compare
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


_KEY, _ACCEPT = "dGhlIHNhbXBsZSBub25jZQ==", b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo="  # the WebSocket standard's own example
_PORT_FILE = "fake-verse-lsp.port"  # the stand-in holds this loopback port open while it runs


def _until(check: Any, seconds: float = 20.0) -> Any:
    """What ``check`` answers once it answers something."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if found := check():
            return found
        time.sleep(0.05)
    raise AssertionError("waited too long")


def _started(file: Path, last: int = 0) -> int:
    """The port of the stand-in started after the one on ``last``."""

    def port() -> int:
        text = file.read_text() if file.is_file() else ""
        return int(text) if text and int(text) != last else 0

    return _until(port)


def _runs(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 0.3).close()
    except OSError:
        return False
    return True


def _page(url: str) -> socket.socket:
    """An editor page, connected to a session's WebSocket."""
    page = socket.create_connection(("127.0.0.1", int(url.rsplit(":", 1)[1])), 10)
    page.sendall(f"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nsec-websocket-key: {_KEY}\r\n\r\n".encode())
    head = b""
    while not head.endswith(b"\r\n\r\n"):
        head += page.recv(1)
    assert head.startswith(b"HTTP/1.1 101") and _ACCEPT in head, head
    return page


def _say(page: socket.socket, text: str) -> None:
    """One masked text frame, as a browser sends it."""
    data, mask = text.encode(), b"\x01\x02\x03\x04"
    size = bytes([0x80 | len(data)]) if len(data) < 126 else b"\xfe" + struct.pack("!H", len(data))
    page.sendall(b"\x81" + size + mask + bytes(byte ^ mask[at % 4] for at, byte in enumerate(data)))


def _ask(page: socket.socket, text: str) -> str:
    """verse-lsp's answer to ``text``."""
    from frontend.ui_web.terminal.ws_util import parse_ws_frame_ex

    _say(page, text)
    opcode, answer = parse_ws_frame_ex(page)
    assert opcode == 1
    return answer.decode()


def _shown(status: dict) -> dict:
    """A status without what differs by run: its port, and stderr lines still on their way."""
    return {**status, "ws_url": status["ws_url"] and "ws://127.0.0.1:#", "stderr_tail": None}


@pytest.fixture
def idle(monkeypatch):
    """A verse-lsp no page is connected to is stopped after 0.3 s, not five minutes. Ask
    for it before ``lsp``: the Rust backend reads it when it starts."""
    from frontend.ui_web.verse_editor.lsp import bridge

    monkeypatch.setenv("DUCKY_VERSE_LSP_IDLE_MS", "300")
    monkeypatch.setattr(bridge, "_IDLE_STOP_S", 0.3)


@pytest.fixture
def lsp(editor, monkeypatch):
    """``editor``, with the stand-in verse-lsp as the copy both backends find first (so
    neither looks for one on this PC). Every session is stopped at the end."""
    from frontend.settings import default_app_data_dir
    from frontend.ui_web.verse_editor.lsp import detect

    exe = backend_exe()
    fake = exe.parent / "examples" / f"fake_verse_lsp{exe.suffix}"
    assert fake.is_file(), "the stand-in verse-lsp is not built: run `cargo build -p backend --examples` in native_launcher/"
    cache = default_app_data_dir() / "verse-lsp"
    cache.mkdir(parents=True, exist_ok=True)
    shutil.copy(fake, cache / "verse-lsp.exe")
    monkeypatch.setattr(detect, "_CACHE_DIR", cache)
    yield editor
    editor.python.api._verse_editor.stop_lsp()
    if editor.rust.proc.poll() is None:
        editor.rust.call("stop_verse_lsp")


def test_a_windows_verse_lsp_serves_its_page_keeps_running_over_a_reload_and_says_how_it_ended(lsp):
    island, port_file = lsp.island, lsp.island / _PORT_FILE

    def story(call):
        port_file.unlink(missing_ok=True)

        def status() -> dict:
            return call("get_verse_lsp_status", "w1")

        def start(root: Any = "") -> dict:
            return call("start_verse_lsp", str(root), "w1")

        told: list[Any] = [status()]
        first = start()
        process = _started(port_file)
        page = _page(first["ws_url"])
        told += [_shown(first), _ask(page, '{"id":1,"method":"initialize"}')]
        told.append(_ask(page, json.dumps({"id": 2, "text": "é" * 200}, ensure_ascii=False)))
        told.append(_until(lambda: status()["stderr_tail"]))
        # A second start finds it running.
        again = start(island)
        told += [_shown(again), again["ws_url"] == first["ws_url"]]
        # The page reloads: the same process, on a new listener.
        page.close()
        told.append(_until(lambda: (now := status()) and not now["bridge_up"] and now))
        back = start()
        page = _page(back["ws_url"])
        told += [_shown(back), back["ws_url"] != first["ws_url"], _ask(page, '{"id":3}'), int(port_file.read_text()) == process]
        # It ends by itself: the status says how, and the next start runs a new one.
        _say(page, '{"method":"exit"}')
        told.append(_until(lambda: (now := status()) and now["last_exit_code"] == 3 and not now["bridge_up"] and now))
        page.close()
        told += [start(lsp.folder), _shown(start())]
        fresh = _started(port_file, process)
        told += [_until(lambda: status()["stderr_tail"]), _runs(fresh), call("stop_verse_lsp", "w1")]
        told += [_until(lambda: not _runs(fresh)), status()]
        lsp.open("")
        told.append(start())
        return told

    told = lsp.each(story)[0]
    off, started, hello, long, stderr = told[:5]
    assert off["available"] and off["enabled"] and not off["running"] and off["project_root"] == ""
    assert started["running"] and started["source"] == "appdata_cache" and started["project_root"] == str(island)
    assert hello == '{"id":1,"method":"initialize"}' and "é" * 200 in long and stderr == ["fake verse-lsp up"]
    assert told[6] is True and told[7]["proc_alive"] and told[9:12] == [True, '{"id":3}', True]
    assert told[12]["error"] == "verse-lsp exited (code 3) — reopen a .verse file to reconnect"
    assert told[13]["folder_project"] is True and told[14]["running"] and told[14]["last_exit_code"] is None
    assert told[16:19] == [True, None, True] and not told[19]["proc_alive"] and told[20]["error"] == "No project root set"


def test_a_verse_lsp_no_page_is_connected_to_is_stopped_and_three_windows_keep_theirs(idle, lsp):
    port_file = lsp.island / _PORT_FILE

    def story(call):
        port_file.unlink(missing_ok=True)
        ports: list[int] = []

        def start(window: str) -> dict:
            answer = call("start_verse_lsp", "", window)
            ports.append(_started(port_file, ports[-1] if ports else 0))
            return answer

        page = _page(start("w1")["ws_url"])
        _ask(page, "{}")
        page.close()
        told: list[Any] = [_until(lambda: not _runs(ports[0])), _shown(call("get_verse_lsp_status", "w1"))]
        # A fourth window's start stops the window used longest ago.
        for window in ("w1", "w2", "w3"):
            start(window)
        call("get_verse_lsp_status", "w1")
        start("w4")
        told += [_until(lambda: not _runs(ports[2])), [_runs(port) for port in ports[1:]], call("stop_verse_lsp")]
        told.append(_until(lambda: not any(_runs(port) for port in ports)))
        return told

    told = lsp.each(story)[0]
    assert told[0] is True and not told[1]["proc_alive"] and told[1]["last_exit_code"] is None
    assert told[2:] == [True, [True, False, True, True], None, True]


def test_no_verse_lsp_outlives_the_rust_backend(lsp):
    """The stand-in stays 30 s after its input closes, so only being stopped ends it here."""
    _write(lsp.island, _VERSE)
    lsp.open(lsp.island)
    status, reply = lsp.rust.call("start_verse_lsp", "", "w1")
    assert status == 200 and reply["result"]["running"], reply
    port = _started(lsp.island / _PORT_FILE)
    assert _runs(port)
    lsp.rust.close()
    assert _until(lambda: not _runs(port), 10)


# What the stand-in says of a file is written in the file: see its first lines.
_SCAN = {
    "Content/err.verse": "ok := 1\nERR Unknown identifier `é`\nWARN unused\nINFO a note\n",
    "Content/twice.verse": "ERR2 said twice\n",
    "Content/flake.verse": "ONCE not real\n",
    "Content/syntax.verse": "x := 1\nSYNTAXONCE\n",
    "Content/await.verse": "AWAIT\n",
    "Content/crlf.verse": "a := 1\r\nERR after a line end of Windows\r\n",
    "Content/pull.verse": "PULL from the pull\n",
    **{f"Content/Many/f{number:02}.verse": f"f{number} := {number}\n" for number in range(8)},  # more than one batch of 16
}
_PENDING = {"ok": True, "pending": True, "files": [], "scanned": 0}


def _error(line: int, column: int, message: str) -> dict:
    return {"line": line, "column": column, "message": message, "severity": "error"}


def _newer(path: Path, seconds: int) -> None:
    os.utime(path, ns=(_WHEN + seconds * 10**9, _WHEN + seconds * 10**9))


@pytest.fixture
def scans(lsp, monkeypatch):
    """``lsp``, with Python's scan ids starting at 1 as a new Rust backend's do.
    ``scans.ended(n)`` waits until the windows were told the whole list ``n`` times."""
    from frontend.ui_web.verse_editor.lsp import diagnostics_scan

    monkeypatch.setattr(diagnostics_scan, "_scan_seq", 0)

    def ended(times: int) -> None:
        _until(lambda: sum(event.get("replace") is True for event in lsp.seen()) >= times, 40)

    lsp.ended = staticmethod(ended)
    yield lsp
    diagnostics_scan.abort_active_scan()


def _phases(events: list[dict]) -> list[str]:
    return [event.get("phase", "sync") for event in events]


def _by_scan(events: list[dict]) -> list[dict]:
    """Each scan's events together: a scan's last word (the whole list) and the first of
    the scan that waited for it come in either order."""
    ended, keyed = 0, []
    for event in events:
        ended += event.get("phase") == "done"
        keyed.append((event.get("scan_id", ended), event))
    return [event for _, event in sorted(keyed, key=lambda pair: pair[0])]


def test_a_scan_checks_the_stale_files_and_a_second_one_waits_for_it(scans):
    island, scans.order = scans.island, staticmethod(_by_scan)

    def story(call):
        _write(island, _SCAN)
        _newer(island / "Content/Sub/B.verse", 5)
        told = [call("scan_verse_diagnostics")]
        # Asked for again while it runs: it waits its turn, then checks what still needs it.
        _until(lambda: "opening" in _phases(scans.seen()))
        told.append(call("scan_verse_diagnostics", str(island)))
        scans.ended(2)
        told.append(call("load_verse_diagnostics_cache"))
        return told

    told, rows, events = scans.each(story)
    assert told[:2] == [_PENDING, _PENDING]
    files = {row["path"]: row for row in told[2]["files"]}
    assert len(files) == 17 and told[2]["stale_count"] == 5 and len(rows) == 17
    assert files["content/err.verse"]["items"] == [_error(2, 3, "Unknown identifier `é`"), {"line": 3, "column": 1, "message": "unused", "severity": "warning"}]
    assert files["content/twice.verse"]["items"] == [_error(1, 5, "said twice")]  # once, at its leftmost place
    assert files["content/syntax.verse"]["items"] == [_error(2, 4, "vErr:S77: Expected expression")]  # said once, and stands
    assert files["content/crlf.verse"]["items"] == [_error(2, 3, "after a line end of Windows")]
    assert files["content/pull.verse"]["items"] == [_error(1, 3, "from the pull"), _error(7, 1, "bare")]
    clean = [path for path, row in files.items() if not row["items"]]
    assert len(clean) == 12 and {"content/flake.verse", "content/await.verse"} < set(clean)
    # The first scan, then the second: each says what it checks, each file, and its end.
    first, second = [at for at, event in enumerate(events) if event.get("phase") == "started"]
    one, two = events[first:second], events[second:]
    assert one[0]["total"] == 17 and one[0]["scan_id"] == 1 and sorted(one[0]["paths"]) == sorted(files)
    assert [event["path"] for event in one if event.get("phase") == "opening"][:2] == ["content/sub/b.verse", "content/a.verse"]  # the newest first
    assert _phases(one).count("analyzing") == 2 and _phases(one).count("file") == 17
    assert one[-2:] == [{**one[-2], "phase": "done", "index": 17, "total": 17}, {**one[-1], "replace": True, "files": one[-1]["files"]}]
    assert two[0]["total"] == 5 and two[0]["scan_id"] == 2 and _phases(two).count("file") == 5
    assert two[-2]["phase"] == "done" and two[-2]["total"] == 17 and two[-1]["files"] == told[2]["files"]
    assert all(Path(event["project_root"]) == island for event in events)


def test_a_scan_with_nothing_to_check_answers_at_once_and_a_full_one_checks_every_file(scans):
    island = scans.island

    def story(call):
        told = [call("scan_verse_diagnostics")]
        scans.ended(1)
        told.append(call("scan_verse_diagnostics"))
        told.append(call("scan_verse_diagnostics", "", True))
        scans.ended(3)
        told.append(call("stop_verse_diagnostics_scan", str(island)))  # none runs: the windows are still told
        shutil.rmtree(island / "Content")
        told.append(call("scan_verse_diagnostics", "", True))
        scans.ended(5)
        told.append(call("scan_verse_diagnostics", str(scans.folder)))
        scans.open("")
        told += [call("scan_verse_diagnostics"), call("stop_verse_diagnostics_scan")]
        return told

    told, rows, events = scans.each(story)
    checked = [{"path": path, "errors": 0, "warnings": 0, "items": []} for path in ("content/a.verse", "content/sub/b.verse")]
    assert told[0] == _PENDING and told[1] == {"ok": True, "pending": False, "files": checked, "scanned": 0, "with_errors": 0, "with_warnings": 0}
    assert told[2] == _PENDING and told[3] == {"ok": True} and told[4] == _PENDING
    assert told[5] == {"ok": True, "pending": False, "files": [], "scanned": 0, "folder_project": True}
    assert told[6] == {"error": "No project root set", "files": [], "scanned": 0} and told[7] == {"ok": True}
    assert [row[0] for row in rows] == ["content/a.verse", "content/sub/b.verse"]  # a scan of no files writes nothing
    full = [event for event in events if event.get("scan_id") == 2]
    # A full scan checks the file the list does not cover too, and never keeps it.
    assert sorted(full[0]["paths"]) == ["content/__externalactors__/x.verse", "content/a.verse", "content/sub/b.verse"]
    assert _phases(full).count("file") == 3 and full[-1]["phase"] == "done" and full[-1]["total"] == 3
    stopped = events.index({"type": "verse_diagnostics_progress", "project_root": events[0]["project_root"], "phase": "error", "message": "Scan stopped"})
    assert events[stopped + 1]["files"] == checked
    assert [(event["phase"], event["total"]) for event in events if event.get("scan_id") == 3] == [("started", 0), ("done", 0)]
    assert events[-1]["files"] == [] and _phases(events).count("sync") == 5


def test_a_stopped_scan_keeps_what_it_checked_and_a_file_that_gets_no_answer_keeps_its_result(scans):
    island = scans.island

    def story(call):
        _write(island, {"Content/hang.verse": "HANG\n"})
        _newer(island / "Content/a.verse", 5)
        _newer(island / "Content/hang.verse", 1)
        told = [call("save_verse_file_cache", "Content/hang.verse", 1, 0, [_ITEM])]
        told.append(call("scan_verse_diagnostics"))
        _until(lambda: any(event.get("phase") == "checking" and event["path"] == "content/hang.verse" for event in scans.seen()))
        time.sleep(0.6)  # it now waits for an answer that never comes
        told.append(call("stop_verse_diagnostics_scan"))
        scans.ended(2)
        told.append(call("load_verse_diagnostics_cache"))
        told.append(call("scan_verse_diagnostics"))
        scans.ended(3)
        return told

    told, rows, events = scans.each(story)
    known = {"path": "content/hang.verse", "errors": 1, "warnings": 0, "items": [_ITEM]}
    assert told[1:3] == [_PENDING, {"ok": True}] and told[4] == _PENDING
    assert told[3]["files"] == [{"path": "content/a.verse", "errors": 0, "warnings": 0, "items": []}, known] and told[3]["stale_count"] == 2
    assert [row[0] for row in rows] == ["content/a.verse", "content/hang.verse", "content/sub/b.verse"] and rows[1][3] == 1
    phases = _phases(events)
    assert phases[:10] == ["sync", "started", "opening", "opening", "opening", "analyzing", "checking", "file", "checking", "error"]
    assert events[9]["message"] == "Scan stopped" and phases[10:14] == ["sync", "file", "done", "sync"]
    # Never a false "0 errors": the file that got no answer is told with what was known.
    assert events[11]["file"] == known and events[12]["total"] == 2
    again = [event for event in events if event.get("scan_id") == 2]
    assert [event["path"] for event in again if event["phase"] == "file"] == ["content/hang.verse", "content/sub/b.verse"]
    assert again[5]["phase"] == "file" and again[5]["file"] == known and again[-1]["total"] == 3


class _WorkflowServer:
    """A stand-in for UEFN's Verse workflow server on a loopback port. A build says how it
    goes and then answers; a push answers with what it was asked, or refuses ``False``."""

    def __init__(self) -> None:
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.asked: list[tuple[str, Any]] = []
        self.links: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        with contextlib.suppress(OSError):
            while True:
                link = self.listener.accept()[0]
                self.links.append(link)
                threading.Thread(target=self._serve, args=(link,), daemon=True).start()

    def _serve(self, link: socket.socket) -> None:
        reader = link.makefile("rb")
        with contextlib.suppress(OSError, ValueError, IndexError):
            while head := reader.readline():
                reader.readline()
                ask = json.loads(reader.read(int(head.split(b":")[1])))
                assert ask["type"] == 1, ask
                self.asked.append((ask["command"], ask["params"]))
                answer: dict[str, Any] = {"seq": ask["seq"], "type": 2}
                said: list[dict] = []
                if ask["command"] == "compileProject":
                    notes = [("logMessage", {"message": "Building é"}), ("updateBuildState", 3), ("updateBuildState", 0), ("canPushVerseChanges", True)]
                    said = [{"type": 0, "command": command, "params": params} for command, params in notes]
                    answer["result"] = {"success": True, "errors": []}
                elif ask["params"] is False:
                    answer["error"] = {"code": 5, "message": "nope"}
                else:
                    answer["result"] = f"pushed {ask['params']}"
                bodies = [json.dumps(message).encode() for message in [*said, answer]]
                # All at once, with a header the client has no use for: it finds each message.
                link.sendall(b"X-Server: stand-in\r\n\r\n" + b"".join(b"Content-Length: %d\r\n\r\n%b" % (len(body), body) for body in bodies))

    def drop(self) -> None:
        """Every connection ends, as when UEFN closes."""
        while self.links:
            with contextlib.suppress(OSError):
                self.links.pop().shutdown(socket.SHUT_RDWR)

    def close(self) -> None:
        self.listener.close()
        self.drop()


@pytest.fixture
def workflow(monkeypatch):
    """The stand-in workflow server, as the one both backends connect to. Ask for it
    before ``editor``: the Rust backend reads the port when it starts. Python's client
    starts new, and never reaches for the UEFN listener or UEFN's windows."""
    from frontend.ui_web.verse_editor.workflow import client

    server = _WorkflowServer()
    monkeypatch.setenv("DUCKY_VERSE_WORKFLOW_PORT", str(server.port))
    monkeypatch.setattr(client.VerseWorkflowClient.start, "__defaults__", (server.port, "127.0.0.1", 5.0))
    monkeypatch.setattr(client, "_workflow", None)
    monkeypatch.setattr(client, "_presave_dirty_packages", lambda: None)
    monkeypatch.setattr(client, "save_modal_watchdog", contextlib.nullcontext)
    yield server
    if client._workflow is not None:
        client._workflow.set_state_listener(None)
        client._workflow.stop()
    server.close()


def test_a_build_goes_through_the_workflow_server_restarts_verse_lsp_and_starts_a_scan(workflow, scans, monkeypatch):
    from frontend.ui_web.verse_editor import api

    monkeypatch.setattr(api, "_VERSE_EDITOR", scans.python.api._verse_editor)  # the one a build restarts
    port_file = scans.island / _PORT_FILE

    def story(call):
        port_file.unlink(missing_ok=True)
        told = [call("get_verse_workflow_status"), call.raw("compile_verse_project"), call.raw("push_verse_changes")]
        told += [call("connect_verse_workflow"), call("connect_verse_workflow")]
        call("start_verse_lsp", "", "w1")
        editors = _started(port_file)
        told.append(call("compile_verse_project"))
        scans.ended(1)
        # The editor's verse-lsp was stopped: its next start takes the build's digests in.
        told += [_until(lambda: not _runs(editors)), call("get_verse_workflow_status")]
        told += [call("push_verse_changes"), call.raw("push_verse_changes", False)]
        # UEFN closes: the status says so, and the next connect makes a new connection.
        workflow.drop()
        told.append(_until(lambda: (now := call("get_verse_workflow_status")) and not now["connected"] and now))
        told += [call.raw("compile_verse_project"), call("connect_verse_workflow"), call("push_verse_changes", "é")]
        told += [call("disconnect_verse_workflow"), call("get_verse_workflow_status")]
        return told

    told, rows, events = scans.each(story)
    idle = {"connected": False, "build_state": 4, "can_push_verse_changes": False, "last_log": ""}
    built = {"connected": True, "build_state": 0, "can_push_verse_changes": True, "last_log": "Building é"}
    assert told[0] == idle and "Not connected to Verse Workflow Server — open project in UEFN" in str(told[1])
    assert told[2][1]["ok"] is False and "Not connected to Verse Workflow Server" in str(told[2])
    assert told[3] == told[4] == {**idle, "connected": True}
    assert told[5] == {"success": True, "errors": [], "diagnostics_scan": _PENDING} and told[6] is True and told[7] == built
    assert told[8] == {"message": "pushed True", "status": built} and "{'code': 5, 'message': 'nope'}" in str(told[9])
    assert told[10] == {**built, "connected": False} and told[11][1]["ok"] is False and told[12] == built
    assert told[13]["message"] == "pushed é" and told[14] == told[15] == {**built, "connected": False}
    states = [event for event in events if event["type"] == "verse_workflow_state"]
    assert [(state["connected"], state["build_state"], state["can_push_verse_changes"]) for state in states] == [
        (True, 4, False), (True, 3, False), (True, 0, False), (True, 0, True), (False, 0, True), (True, 0, True),
    ]  # fmt: skip
    assert events[:4] == states[:4] and _phases(events[4:])[0] == "started" and len(rows) == 2
    asked = [("compileProject", {}), ("pushChanges", True), ("pushChanges", False), ("pushChanges", "é")]
    assert workflow.asked == asked * 2


def test_no_workflow_server_is_an_error_in_the_status(workflow, editor):
    workflow.close()
    answers = [plain(backend.call("connect_verse_workflow")) for backend in (editor.python, editor.rust)]
    for status, reply in answers:
        assert status == 200 and reply["ok"] is True and reply["result"].pop("error"), reply  # in the OS's own words
    assert answers[0] == answers[1] and answers[0][1]["result"]["connected"] is False


_NO_WINE = pytest.mark.skipif(
    sys.platform.startswith("linux"),
    reason="on Linux lore.exe runs inside UEFN's Wine prefix, which the Rust backend does not look up yet",
)
_PAD = chr(0x3164)  # the invisible filler Epic pads display names with
_LORE = {
    "status": (
        "Repository r1\nOn branch main-p1 revision 12 -> a12\nRemote revision 12 -> a12\nLocal branch in sync with remote\n"
        "Changes staged for commit:\nM Content/a.verse\nD Content/Gone.uasset\n"
        "Untracked files:\nA Content/New/\nA Content/Developers/PC/\nA Content/a.verse~mine\n"
    ),
    "history|200|--revision|a12": (
        f"Revision  : 12\nSignature : a12\nCreator   : Ann{_PAD}\n    [Autosave] 1 change\n\n"
        "Revision  : 11\nSignature : a11\nMerge     : f46f\nCreator   : Lee\n    Sync 4\n"
    ),
    "--remote|history|200|--branch|main": (
        f"Revision  : 15\nSignature : a15\nMerge     : 22f7\nBranch    : e726\nCreator   : Lee{_PAD}{_PAD}Roy\n    Sphere moved\n    more\n\n"
        "Revision  : 11\nSignature : a11\n"
    ),
    "--remote|history|1|--branch|main": "Revision  : 15\nSignature : a15\n",
    "revision|diff|a11|--target|a12": "M Content/b.verse\nA Content/New\n",
    "revision|diff|a11|--target|a15": (
        "M Content/a.verse\nA Content/Their\nA Content/Their/c.uasset\nD Content/Gone.uasset\nM Content/b.verse\n"
        "M content/new/N.uasset\nA Content/Developers/PC/Collections\n"
    ),
    "lock|query|--branch|main": f"Locks found:\nContent/a.verse by Lee{_PAD}{_PAD}Roy on branch e726\nContent\\Mine.uasset by Ann{_PAD} on branch e726\n",
    "auth|info": f"ID: 0197\nName: Ann{_PAD}\n",
}


@pytest.fixture
def revisions(tmp_path, monkeypatch, request):
    """Stand-ins for ``lore.exe`` and ``urc.exe`` as the ones both backends find first (so
    neither looks for UEFN's on this PC), and an island with a ``.lore`` folder.
    ``revisions.says(table)`` scripts their output by arguments; ``revisions.both(call)``
    is one lore call on each backend: the same answer after the same lore calls. The Rust
    backend starts here, once the environment names the stand-ins."""
    from frontend.ui_web.verse_editor.api import VerseEditorApi
    from frontend.ui_web.verse_editor.urc import lore_cli

    exe = backend_exe()
    if exe is None:
        pytest.skip("Ducky-Backend is not built: run `cargo build -p backend` in native_launcher/")
    fake = exe.parent / "examples" / f"fake_cli{exe.suffix}"
    assert fake.is_file(), "the stand-in lore.exe is not built: run `cargo build -p backend --examples` in native_launcher/"
    script = tmp_path / "tools" / "cli.json"
    script.parent.mkdir()
    for name, key in (("lore.exe", "LORE_CLI_LOCATION"), ("urc.exe", "URC_CLI_LOCATION")):
        shutil.copy(fake, script.parent / name)
        monkeypatch.setenv(key, str(script.parent / name))
    monkeypatch.setenv("FAKE_CLI_SCRIPT", str(script))
    monkeypatch.setattr(lore_cli, "_me_cache", {})

    class Revisions:
        rust, python = request.getfixturevalue("rust_store"), request.getfixturevalue("python")
        island, tools = tmp_path / "work" / "Isle", script.parent
        last: list[str] = []  # the lore calls of the newest ``both``

    def says(table: dict[str, Any]) -> None:
        rows = {key: {"out": row} if isinstance(row, str) else row for key, row in table.items()}
        script.write_text(json.dumps(rows), encoding="utf-8")

    def asked() -> list[str]:
        log = Path(f"{script}.calls")
        lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
        log.unlink(missing_ok=True)
        return lines

    def both(what: str, root: Path | None = None, **more: Any) -> Any:
        at = str(root or Revisions.island)
        theirs, calls = plain(getattr(lore_cli, what)(at, **more)), asked()
        status, ours = Revisions.rust.request(f"/__rs/lore/{what}", {"root": at, **more})
        assert (status, ours, asked()) == (200, theirs, calls), f"{what}:\n rust   {ours}\n python {theirs}"
        Revisions.last = calls
        return theirs

    Revisions.python.api._verse_editor = VerseEditorApi()
    Revisions.says, Revisions.asked, Revisions.both = staticmethod(says), staticmethod(asked), staticmethod(both)
    _put(Revisions.island, {"Isle.uefnproject": "{}"})
    (Revisions.island / ".lore").mkdir()  # empty: only its being there is read
    says(_LORE)
    return Revisions


@_NO_WINE
def test_lore_tells_an_islands_state_against_remote_main(revisions):
    island, both = revisions.island, revisions.both
    files = ("Content/a.verse", "Content/b.verse", "Content/New/n.uasset", "Content/New/sub/m.uasset", "Content/Developers/PC/x.collection", "Content/a.verse~mine")
    _put(island, dict.fromkeys(files, "x"))
    at = island.as_posix()

    assert both("remote_head") == {"ok": True, "revision": 15, "signature": "a15", "error": ""} and len(revisions.last) == 1
    head = both("local_head")
    assert (head["revision"], head["signature"], head["changes"][1]) == (12, "a12", ["D", "Content/Gone.uasset"])
    state = both("repo_state")
    # An autosave main lacks, and a check-in from another PC: ahead and behind.
    assert (state["ok"], state["branch"], state["merge_base"], state["behind"], state["ahead"], state["in_sync"]) == (True, "main-p1", "a11", True, True, False)
    assert (state["local_revision"], state["remote_revision"], state["remote_signature"]) == (12, 15, "a15") and len(revisions.last) == 3
    assert state["incoming"] == [{"revision": 15, "signature": "a15", "creator": "Lee Roy", "message": "Sphere moved", "own": False}]
    assert state["staged"] == ["Content/a.verse", "Content/Gone.uasset"] and state["untracked"][0] == "Content/New/"

    # What main lacks: the files on disk, an untracked folder's files, this PC's commits.
    pending = both("pending_paths")
    assert pending == {"ok": True, "error": "", "paths": [f"{at}/Content/a.verse", f"{at}/Content/New/n.uasset", f"{at}/Content/New/sub/m.uasset", f"{at}/Content/b.verse"]}
    assert revisions.last[-1] == "revision|diff|a11|--target|a12"

    coming = both("incoming_paths", state=state)
    assert revisions.last == ["revision|diff|a11|--target|a15"] and both("incoming_paths") == coming
    assert [(row["code"], row["rel"], row["deleted"]) for row in coming["files"]] == [
        ("M", "Content/a.verse", False), ("A", "Content/Their/c.uasset", False), ("D", "Content/Gone.uasset", True),
        ("M", "Content/b.verse", False), ("M", "content/new/N.uasset", False),
    ]  # fmt: skip
    assert coming["files"][0]["path"] == f"{at}/Content/a.verse"

    # Changed here and there: the working tree, a commit, a file under an untracked folder.
    clash = both("overlap", state=state)
    assert revisions.last == ["revision|diff|a11|--target|a15", "revision|diff|a11|--target|a12"] and both("overlap") == clash
    assert [(row["rel"], row["local"], row["remote"]) for row in clash["files"]] == [
        ("Content/a.verse", "modified", "M"), ("Content/b.verse", "modified", "M"), ("content/new/N.uasset", "added", "M"),
    ]  # fmt: skip
    assert both("overlap", state={**state, "behind": False}) == {"ok": True, "files": [], "error": ""} and revisions.last == []

    locks = both("locks")
    assert locks == {"ok": True, "me": "Ann", "error": "", "locks": [
        {"path": f"{at}/Content/a.verse", "rel": "Content/a.verse", "owner": "Lee Roy", "owner_raw": f"Lee{_PAD}{_PAD}Roy", "mine": False},
        {"path": f"{at}/Content/Mine.uasset", "rel": "Content/Mine.uasset", "owner": "Ann", "owner_raw": f"Ann{_PAD}", "mine": True},
    ]}  # fmt: skip
    assert revisions.last == ["lock|query|--branch|main", "auth|info"]
    # Who is signed in is asked once.
    assert both("locks") == locks and revisions.last == ["lock|query|--branch|main"]
    assert both("whoami") == {"id": "0197", "name": f"Ann{_PAD}"} and revisions.last == []


@_NO_WINE
def test_what_lore_refuses_is_an_answer_in_its_own_words(revisions, tmp_path):
    from frontend.ui_web.verse_editor.urc import lore_cli

    both, plain_folder = revisions.both, tmp_path / "work" / "Plain"
    plain_folder.mkdir()
    no_repo = "not a Lore repository (no .lore folder)"
    assert both("repo_state", plain_folder) == {"ok": False, "available": True, "error": no_repo}
    assert both("locks", plain_folder) == {"ok": False, "locks": [], "me": "", "error": no_repo}
    assert both("pending_paths", plain_folder)["paths"] == [] and both("remote_head", plain_folder)["error"] == no_repo
    assert both("incoming_paths", plain_folder)["error"] == no_repo and both("overlap", plain_folder) == {"ok": False, "files": [], "error": no_repo}
    assert revisions.last == []

    def error(status: Any, call: str = "repo_state", **more: Any) -> str:
        revisions.says({**_LORE, **status})
        return both(call, **more)["error"]

    revisions.says({})
    assert both("local_head")["error"] == "unexpected call status"
    assert error({"status": "\n[Error] Repository not found\nmore\n"}) == "[Error] Repository not found"
    assert error({"status": {"code": 3}}) == "lore status exited 3"
    assert error({"status": {"code": 1, "out": "said\n", "err": "  refused é\nwhy\n"}}) == "refused é"
    assert error({"status": {"sleep": 5}}, timeout=0.5) == "lore status timed out after 0.5s"
    assert error({"status": "Repository r1\n"}) == "lore status shows no current revision"
    assert error({"--remote|history|200|--branch|main": "none\n"}) == "remote branch main has no revisions"
    assert error({"--remote|history|200|--branch|main": "Revision : 3\nSignature : ffff\n"}) == "no revision shared with remote main in the last 200"
    assert error({"revision|diff|a11|--target|a12": {"code": 2, "err": "no diff"}}, "pending_paths") == "no diff"
    assert error({"revision|diff|a11|--target|a15": {"code": 2, "err": "no diff"}}, "overlap") == "no diff"
    # Nobody signed in: the locks are listed, none as this PC's, and the answer is not ok.
    revisions.says({**_LORE, "auth|info": {"code": 1, "err": "signed out"}})
    locks = both("locks")
    assert (locks["ok"], [row["mine"] for row in locks["locks"]]) == (False, [False, False]) and "auth info" in locks["error"]
    assert both("whoami") == {"id": "", "name": ""} and revisions.last == ["auth|info"]  # a failure is not remembered

    merged = "using { /Verse.org/Simulation }\n<<<<<<< ours\n# one\n=======\n# other\n>>>>>>> theirs\n"
    texts = {"merged.VERSE": merged, "clean.verse": "# <<<<<<< not at a line start\n>>>>>>> x\n", "half.json": "<<<<<<< ours\n{}\n", "A.uasset": merged}
    _put(plain_folder, texts)
    paths = [(plain_folder / name).as_posix() for name in (*texts, "gone.verse")]
    marked = lore_cli.conflict_marked(paths)
    assert revisions.rust.request("/__rs/lore/conflict_marked", {"paths": paths}) == (200, marked) and marked == paths[:1]


def test_urc_lists_the_changes_and_asks_for_a_check_in(revisions):
    from frontend.ui_web.verse_editor.urc import detect_urc

    island, changes = str(revisions.island), {"stagedChanges": ["a.verse", {"relativePath": "b.verse"}, {"name": ""}], "unstaged": [{"file": "c.verse"}]}
    found = {"urc": detect_urc.detect_urc_executable(), "lore": detect_urc.detect_lore_executable()}
    assert revisions.rust.request("/__rs/lore/detect", {}) == (200, found)
    assert found["urc"] == {"available": True, "path": str(revisions.tools / "urc.exe"), "source": "urc_cli_location", "error": ""}

    def both(method: str, *args: Any) -> Any:
        theirs, calls = plain(revisions.python.call(method, *args)), revisions.asked()
        ours = plain(revisions.rust.call(method, *args))
        assert (ours, revisions.asked()) == (theirs, calls), f"{method}{args}:\n rust   {ours}\n python {theirs}"
        revisions.last = calls
        return theirs[1].get("result", theirs[1])

    def status(out: Any) -> Any:
        revisions.says({"project|status|--json": out, "trigger|--command|CheckIn|--json": "{}\n"})
        return both("get_urc_status", island)

    told = status(json.dumps({"data": changes}) + "\n")
    assert told == {
        "available": True, "path": found["urc"]["path"], "project_root": island, "error": "", "staged_count": 2,
        "unstaged_count": 1, "staged": ["a.verse", "b.verse"], "unstaged": ["c.verse"], "raw": changes,
    }  # fmt: skip
    assert revisions.last == ["project|status|--json"]
    # The open island when none is named; an answer without ``data`` is the data.
    settings.replace_all({"uefn_project_root": f"  {island} "}, {})
    _forget()
    revisions.says({"project|status|--json": json.dumps(changes), "trigger|--command|CheckIn|--json": "{}\n"})
    assert both("get_urc_status") == told and status("[1]")["raw"] == {"data": [1]}
    assert both("urc_commit", "moved the cube") == {"ok": True, "message": "moved the cube"} and revisions.last == ["trigger|--command|CheckIn|--json"]
    assert both("urc_commit", "", island) == {"ok": True, "message": "CheckIn triggered"}
    assert both("urc_push") == {"ok": False, "error": "Push not available via URC CLI v1 in this project"} and revisions.last == []
    # What urc refuses, and what it prints that is no JSON, fail the call in the same words.
    assert status({"code": 4, "out": "said", "err": " not signed in \n"})["error"] == "not signed in"
    assert status({"code": 4})["error"] == "urc exited 4" and status("nope")["error"] == "Invalid URC JSON: nope"
    revisions.says({})
    assert both("urc_commit")["error"] == "unexpected call trigger|--command|CheckIn|--json"
    # No island at all, and one that is not on the disk (in the OS's own words).
    settings.replace_all({"uefn_project_root": ""}, {})
    _forget()
    assert both("get_urc_status")["error"] == "No project root" and both("urc_commit")["error"] == "No project root"
    gone = [plain(backend.call("get_urc_status", island + "-gone"))[1]["result"] for backend in (revisions.python, revisions.rust)]
    assert all(reply.pop("error") for reply in gone) and gone[0] == gone[1]
    assert gone[0] == {"available": True, "path": found["urc"]["path"], "project_root": island + "-gone", "staged_count": 0, "unstaged_count": 0}


def test_a_lore_changelist_drop_takes_pending_adds_only(revisions, monkeypatch):
    from frontend.ui_web.verse_editor.urc import lore_host

    island, plain_folder = revisions.island, revisions.island.parent / "Plain"
    plain_folder.mkdir()
    _put(island, {"Content/Kept.uasset": "x"})
    listed = {"ok": True, "untracked": ["Content/New/A.uasset", "\\Content\\New\\A.uasset", "Content/Newer/B.uasset", "Content/Kept.uasset", "", 7]}

    class Changelist:  # what Lore's library answers, made up
        status = staticmethod(lambda root: listed)
        drop = staticmethod(lambda root, paths: {"ok": True, "dropped": list(paths)})
        revert_local = staticmethod(lambda root: {"ok": True, "reverted": True})

    monkeypatch.setattr(lore_host, "_backend", Changelist)
    calls = {
        "content_rel": lambda root, asset_path: lore_host.content_rel_from_asset_path(asset_path),
        "drop_paths": lore_host.drop_paths, "status": lore_host.lore_status, "pending_adds": lore_host.pending_adds,
        "drop_pending_adds": lore_host.drop_pending_adds, "drop_missing_orphans": lore_host.drop_missing_orphans,
        "revert_local_commit": lore_host.revert_local_commit, "after_asset_gone": lore_host.after_asset_gone,
    }  # fmt: skip

    def both(what: str, root: Path = island, **more: Any) -> Any:
        theirs = plain(calls[what](str(root), **more))
        answer = revisions.rust.request(f"/__rs/lore/host_{what}", {"root": str(root), "status": listed, **more})
        assert answer == (200, theirs), f"{what}{more}:\n rust   {answer[1]}\n python {theirs}"
        return theirs

    # No ``.lore`` folder: Lore is never asked.
    assert both("drop_pending_adds", plain_folder, prefixes=[]) == {"ok": True, "skipped": "no_lore", "dropped": []}
    assert both("status", plain_folder)["untracked"] == [] and both("revert_local_commit", plain_folder) == {"ok": True, "skipped": "no_lore"}
    assert both("drop_paths", plain_folder, paths=["a"])["skipped"] == "no_lore" and both("after_asset_gone", plain_folder, asset_path="/Isle/X")["note"] == "no_pending_add"
    assert both("status") == listed and both("revert_local_commit") == {"ok": True, "reverted": True}
    assert both("pending_adds", prefixes=[]) == ["Content/New/A.uasset", "Content/Newer/B.uasset", "Content/Kept.uasset", "7"]
    assert both("pending_adds", prefixes=["\\Content\\New\\"]) == ["Content/New/A.uasset"]
    assert both("drop_pending_adds", prefixes=["Content/Newer", "Content/Nowhere"]) == {"ok": True, "dropped": ["Content/Newer/B.uasset"]}
    assert both("drop_pending_adds", prefixes=["Content/Nowhere"]) == {"ok": True, "dropped": []}
    # A file that is still there keeps its place in the changelist.
    assert both("drop_missing_orphans", prefixes=["Content"])["dropped"] == ["Content/New/A.uasset", "Content/Newer/B.uasset"]
    assert both("drop_paths", paths=[" ", "\\Content\\X.uasset", "Content/Y.uasset"])["dropped"] == ["Content/X.uasset", "Content/Y.uasset"]
    assert both("drop_paths", paths=["", "  "]) == {"ok": True, "dropped": []}
    assert both("after_asset_gone", asset_path="/Isle/Newer/B") == {"ok": True, "dropped": ["Content/Newer/B.uasset"]}
    assert both("after_asset_gone", asset_path="/Isle/Other/C") == {"ok": True, "dropped": [], "note": "no_pending_add"}
    rels = [both("content_rel", asset_path=path) for path in ("/Isle/Characters/Foo", " Content\\Foo.uasset ", "content/Foo", "Foo.UASSET", "/Foo", "")]
    assert rels == ["Content/Characters/Foo.uasset", "Content/Foo.uasset", "content/Foo.uasset", "Content/Foo.UASSET", "Content//Foo.uasset", "Content/.uasset"]
