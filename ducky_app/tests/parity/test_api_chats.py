"""The sidebar's folder and chat lists: the same answer and the same folder rows from both
backends. Tests taking ``account`` run signed out and signed in."""

from __future__ import annotations

from typing import Any

import pytest

from backend.store.repos import chats

from .parity_harness import plain

_LIST = "_no_project"  # every chat and folder is in the one list
_FOLDERS = [
    {"id": "f1", "name": "Work é", "parent_id": "", "sort_order": 0, "group_hub_id": ""},
    {"id": "f2", "name": "Play", "parent_id": "", "sort_order": 0, "group_hub_id": ""},
    {"id": "f3", "name": "Deep", "parent_id": "f1", "sort_order": 0, "group_hub_id": ""},
    {"id": "g1", "name": "Team", "parent_id": "f1", "sort_order": "", "group_hub_id": " hub "},
]


def _doc(conv_id: str, **fields: Any) -> dict:
    return {"id": conv_id, "title": "Chat " + conv_id, "folder_id": "", "created": 10.0, "updated": 20.0, "sort_order": 1.0, **fields}


_CHATS = [
    _doc("a", folder_id="f1", sort_order=2.0, file_path=" C:\\work\\a.verse ", ducky_style="custom:Mine", thinking_effort=" HIGH "),
    _doc("b", folder_id="f1", sort_order=2.0, updated=30.0, ducky_style="custom:gone", provider="openai", model="m", coding_agent="codex"),
    _doc("c", sort_order=2.0, updated=30.0, ducky_style="no-such-ducky", file_path="C:/work/a.verse", tts_voice=" v ", tts_speed="1.5"),
    _doc("hub", folder_id="g1", is_group=True, leader_conv_id=" a ", group_members=[{"id": "a", "role": "lead"}, "junk"], muted=1),
    _doc("gone", folder_id="archive", is_group=True, sort_order=0, title="An archived group with no folder"),
    _doc(
        "tokens", sort_order=0.5, profile_id=" dev ", parent_conv_id=" hub ", terminal_session_id="t1", ducky_name="Dev",
        ducky_personality="calm", tool_call_count=3, file_count=2,
        coding_agent_stats={"context_tokens": 900, "num_turns": 3},
        token_usage={"calls": [{"input_tokens": 1}, {"input_tokens": 100, "cache_read_tokens": 10, "cache_write_tokens": 10}]},
    ),
]


@pytest.fixture
def seed(rust_store, python):
    """``seed(folders, chats)`` stores them as the app does; the chats are imported (a
    new install has nothing to import) before Rust is asked for them."""
    from frontend import ducky_assets
    from frontend.ui_web.chats import store

    store._repo()
    (ducky_assets.custom_duckies_dir(for_write=True) / "mine.png").write_bytes(b"png")

    def put(folders: list, docs: list) -> None:
        chats.folders_replace(_LIST, folders)
        for doc in docs:
            chats.conv_save(_LIST, dict(doc), messages=None)

    return put


def _same(python: Any, rust: Any, method: str, *args: Any) -> Any:
    theirs, ours = python.call(method, *args), rust.call(method, *args)
    assert plain(ours) == plain(theirs), f"{method}{args}: rust {ours} != python {theirs}"
    assert theirs[0] == 200 and theirs[1]["ok"] is True, theirs
    return plain(theirs[1]["result"])


def test_the_lists_are_the_same(seed, python, rust_store, account) -> None:
    seed(_FOLDERS, _CHATS)
    # Rust first: it adds Archive, then numbers the two folders inside f1.
    status, reply = rust_store.call("list_folders")
    assert (status, reply["ok"]) == (200, True), reply
    saved = plain(chats.folders_get(_LIST))
    folders = _same(python, rust_store, "list_folders")
    assert reply["result"] == folders and plain(chats.folders_get(_LIST)) == saved
    assert [(f["id"], f["sort_order"]) for f in folders[:3]] == [("f1", 0.0), ("f2", 0.0), ("archive", 1_000_000.0)]
    assert sorted((f["sort_order"], f["parent_id"]) for f in folders[3:]) == [(0.0, "f1"), (1.0, "f1")]
    assert {f["id"]: f["group_hub_id"] for f in folders}["g1"] == "hub"

    rows = _same(python, rust_store, "list_all_conversations")
    assert [r["id"] for r in rows] == ["gone", "tokens", "hub", "b", "c", "a"]
    by_id = {r["id"]: r for r in rows}
    assert (by_id["a"]["ducky_style"], by_id["a"]["file_path"], by_id["a"]["thinking_effort"]) == ("custom:Mine", "C:/work/a.verse", "high")
    assert by_id["b"]["ducky_style"] == by_id["c"]["ducky_style"] != "custom:gone"
    assert by_id["tokens"]["context_tokens"] == 40 and by_id["hub"]["group_members"] == [{"id": "a", "role": "lead"}]

    assert [r["id"] for r in _same(python, rust_store, "list_conversations", "f1")] == ["b", "a"]
    assert [r["id"] for r in _same(python, rust_store, "list_conversations", "")] == ["tokens", "c"]
    assert _same(python, rust_store, "list_conversations", "nope") == []
    assert len(_same(python, rust_store, "list_conversations", None)) == len(_CHATS)
    assert [r["id"] for r in _same(python, rust_store, "list_conversations_for_file", "C:\\work\\a.verse ")] == ["c", "a"]
    for nothing in ("", "  ", "C:/other"):
        assert _same(python, rust_store, "list_conversations_for_file", nothing) == []


def test_python_fixes_the_folders_as_rust_does(seed, python, rust_store) -> None:
    seed(_FOLDERS, [])
    theirs = plain((python.call("list_folders"), chats.folders_get(_LIST)))
    chats.folders_replace(_LIST, _FOLDERS)
    ours = plain((rust_store.call("list_folders"), chats.folders_get(_LIST)))
    assert ours[0] == theirs[0]
    assert ours[1] == theirs[1]
    assert sorted(f["sort_order"] for f in theirs[1] if f["parent_id"] == "f1") == [0.0, 1.0]


@pytest.mark.parametrize("args", [("New",), ("  Spaced  ", "f1"), ("", ""), ("In a group", "g1"), ("Lost", "no-such-parent")])
def test_a_folder_is_created_the_same(seed, python, rust_store, args: tuple) -> None:
    left = []
    for backend in (python, rust_store):
        seed(_FOLDERS, [])
        status, reply = backend.call("create_folder", *args)
        assert (status, reply["ok"]) == (200, True), reply
        new_id = reply["result"]["id"]
        assert len(new_id) == 36 and new_id.count("-") == 4
        rows = [{**row, "id": "new"} if row["id"] == new_id else row for row in plain(chats.folders_get(_LIST))]
        left.append(({**reply["result"], "id": "new"}, rows))
    assert left[1] == left[0]
    made = next(row for row in left[0][1] if row["id"] == "new")
    assert made["name"] == (args[0].strip() or "Folder") and made["parent_id"] == (args[1] if len(args) > 1 else "")


def test_no_folder_is_created_inside_archive(seed, python, rust_store) -> None:
    seed(_FOLDERS, [])
    before = plain(chats.folders_get(_LIST))
    for parent in ("archive", " archive "):
        theirs, ours = python.call("create_folder", "X", parent), rust_store.call("create_folder", "X", parent)
        assert plain(ours) == plain(theirs) == [200, {"ok": False, "error": "Cannot create folders inside Archive"}]
    assert plain(chats.folders_get(_LIST)) == before


def test_rust_leaves_the_one_time_moves_to_python(seed, rust_store) -> None:
    """Three old layouts are put right by a chat save, which is still Python's: Rust says
    so instead of listing them unfixed."""

    def refused(method: str, error: str) -> None:
        assert rust_store.call(method) == (200, {"ok": False, "error": error})

    seed(_FOLDERS, [_doc("old-group", is_group=True)])
    refused("list_folders", "a group has no folder yet")
    chats.conv_delete("old-group")

    seed(_FOLDERS, [_doc("x", sort_order=0), _doc("y", sort_order=0)])
    refused("list_all_conversations", "chats are not numbered yet")
    chats.conv_save(_LIST, _doc("y", sort_order=1.0), messages=None)

    chats.conv_save("an-island", _doc("elsewhere"), messages=None)
    refused("list_all_conversations", "chats are not merged into one list yet")
    refused("list_folders", "chats are not merged into one list yet")
    chats.conv_delete("elsewhere")
    assert [row["id"] for row in rust_store.call("list_all_conversations")[1]["result"]] == ["x", "y"]
