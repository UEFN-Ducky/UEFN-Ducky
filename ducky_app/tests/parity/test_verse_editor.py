"""The Verse editor's calls: its switch, the Problems list's cache, a file's history, the
shadow ``.vproject``, its verse-lsp sessions, the project scan and UEFN's workflow server. The same answer, the same rows, the same
events and the same files from both backends on a fixture island. The verse-lsp is a
stand-in built with the backend (never Epic's, never one found on this PC), the workflow
server is a stand-in in this file (never a running UEFN), and nothing reads the person's
own islands."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import struct
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
