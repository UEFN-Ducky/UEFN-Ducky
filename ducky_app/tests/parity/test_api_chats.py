"""The sidebar's folder and chat lists: the same answer and the same folder rows from both
backends. Tests taking ``account`` run signed out and signed in."""

from __future__ import annotations

import json
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


def test_rust_leaves_the_one_list_merge_to_python(seed, rust_store) -> None:
    """Rows an older version stored per island move with their attachment folders, which
    is still Python's: Rust says so instead of listing without them."""
    seed(_FOLDERS, [_doc("x")])
    chats.conv_save("an-island", _doc("elsewhere"), messages=None)
    for method in ("list_all_conversations", "list_folders"):
        assert rust_store.call(method) == (200, {"ok": False, "error": "chats are not merged into one list yet"})
    chats.conv_delete("elsewhere")
    assert [row["id"] for row in rust_store.call("list_all_conversations")[1]["result"]] == ["x"]


# --------------------------------------------------------------------------- saves


class _Both:
    """One call on each backend from the same folders and chats: the answer, the events
    and what is stored after must match. ``updated`` is the save's time and a new
    folder's id is random: both are compared as given names."""

    def __init__(self, seed: Any, python: Any, rust: Any, monkeypatch: Any) -> None:
        from frontend.ui_web.agent_turns import push

        self.seed, self.python, self.rust = seed, python, rust
        self.pushed: list[dict] = []
        monkeypatch.setattr(push, "_panel_push", self.pushed.append)  # never the live app's panel
        self.cursor = 0

    def _rust_events(self) -> list[dict]:
        # A marker ends the page, so a call that pushed nothing does not wait out a poll.
        self.rust.call("push_appearance_live", {"marker": 1})
        _, page = self.rust.request(f"/__panel_events?since={self.cursor}")
        self.cursor = page["cursor"]
        return page["events"][:-1]

    def _seen(self, answer: Any, events: list, started: float) -> list:
        from frontend.ui_web.chats import saves

        saves.flush_chat_saves()
        folders = plain(chats.folders_get(_LIST))
        docs = plain(chats.conv_list(_LIST, with_messages=False))
        for doc in docs:
            doc["updated"] = "now" if doc["updated"] >= started else doc["updated"]
        text = json.dumps((answer, events, folders, docs))
        for folder in folders:
            if len(folder["id"]) == 36:  # a new folder's random id
                text = text.replace(folder["id"], "folder of " + folder["group_hub_id"])
        return json.loads(text)

    def call(self, folders: list, docs: list, method: str, *args: Any) -> tuple[Any, list, list, list]:
        import time

        from frontend.ui_web.chats import conversations, saves

        seen = []
        for backend in (self.python, self.rust):
            saves.flush_chat_saves()
            saves._forget_saved()  # what Python remembers it stored is replaced below
            conversations._sort_migrated_slugs.clear()
            self.seed(folders, docs)
            self.pushed.clear()
            started = time.time()
            answer = backend.call(method, *args)
            events = list(self.pushed) if backend is self.python else self._rust_events()
            seen.append(self._seen(answer, events, started))
        theirs, ours = seen
        for name, mine, yours in zip(("answer", "events", "folders", "chats"), ours, theirs):
            assert mine == yours, f"{method}{args} {name}: rust {mine} != python {yours}"
        return tuple(theirs)


@pytest.fixture
def both(seed, python, rust_store, monkeypatch) -> _Both:
    return _Both(seed, python, rust_store, monkeypatch)


_ODD = _doc(
    "odd", title="Old name", folder_id="f1", created="3", provider=None, coding_agent=" ", thinking_effort=" High ",
    enabled_skills=["a", 2], enabled_subskills={"p": ["x", 1]}, token_usage={"calls": []}, agent_allow_rules=["Bash:ls", " "],
    ducky_name=" Dev ", extra={"dropped": True}, settings_edited=5,
)


@pytest.mark.parametrize(
    ("title", "stored"),
    [("  New name é ", "New name é"), ("", "Old name"), ("   ", "Old name"), ("New ducky 3", "Old name"), ("Group", "Old name")],
)
def test_a_chat_is_renamed_the_same(both, title: str, stored: str) -> None:
    answer, events, _, docs = both.call(_FOLDERS, [_ODD, _doc("other")], "rename_conversation", "odd", title)
    assert answer == [200, {"ok": True, "result": None}]
    assert events == [{"type": "chats_changed", "conv_id": "odd", "title": stored, "folder_id": "f1", "open": False}]
    by_id = {doc["id"]: doc for doc in docs}
    assert (by_id["odd"]["title"], by_id["odd"]["updated"], by_id["other"]["updated"]) == (stored, "now", 20.0)
    assert by_id["odd"]["coding_agent"] == "ducky" and "extra" not in by_id["odd"]


def test_a_placeholder_title_is_renamed_to_another_and_an_unknown_chat_is_left(both) -> None:
    _, events, _, docs = both.call([], [_doc("c", title="New ducky")], "rename_conversation", "c", "Ducky 2")
    assert events[0]["title"] == docs[0]["title"] == "Ducky 2"
    answer, events, _, docs = both.call([], [_doc("c")], "rename_conversation", "nope", "X")
    assert (answer, events, docs[0]["updated"]) == ([200, {"ok": True, "result": None}], [], 20.0)


@pytest.mark.parametrize(("folder_id", "name", "title"), [("f2", " Fun ", "Hub"), ("g1", " Crew ", "Crew"), ("g1", "  ", "Team"), ("nope", "X", "Hub")])
def test_a_folder_is_renamed_the_same_and_a_groups_chat_with_it(both, folder_id: str, name: str, title: str) -> None:
    hub = _doc("hub", title="Hub", folder_id="g1", is_group=True)
    answer, events, folders, docs = both.call(_FOLDERS, [hub, _doc("member", folder_id="g1")], "rename_folder", folder_id, name)
    assert (answer, events) == ([200, {"ok": True, "result": None}], [])
    assert {doc["id"]: doc["title"] for doc in docs} == {"hub": title, "member": "Chat member"}
    assert name.strip() in {f["name"] for f in folders} or folder_id == "nope" or not name.strip()


def test_archive_is_never_renamed(both) -> None:
    answer, _, folders, _ = both.call(_FOLDERS, [], "rename_folder", " archive ", "Bin")
    assert answer == [200, {"ok": False, "error": "Cannot rename the Archive folder"}]
    assert "Bin" not in {f["name"] for f in folders}


def test_a_group_from_before_group_folders_gets_its_folder(both) -> None:
    old = [_doc("old", title="Raiders", folder_id="f1", is_group=True), _doc("unnamed", title="", is_group=True, sort_order=3.0)]
    _, _, folders, docs = both.call(_FOLDERS, [*old, _doc("gone", folder_id="archive", is_group=True)], "list_folders")
    made = {f["id"]: (f["name"], f["parent_id"], f["sort_order"]) for f in folders if f["group_hub_id"] in ("old", "unnamed")}
    # Each is last among its siblings: after g1 (numbered 1 by then) and after Archive.
    assert made == {"folder of old": ("Raiders", "f1", 2.0), "folder of unnamed": ("Group", "", 1_000_001.0)}
    assert {doc["id"]: (doc["folder_id"], doc["updated"]) for doc in docs} == {
        "old": ("folder of old", "now"), "unnamed": ("folder of unnamed", "now"), "gone": ("archive", 20.0),
    }


def test_chats_that_were_never_ordered_are_numbered_newest_first(seed, python, rust_store) -> None:
    from frontend.ui_web.chats import conversations, saves

    docs = [
        _doc("a", sort_order=0, updated=1.0), _doc("b", sort_order=0, updated=9.0), _doc("c", sort_order=0, updated=5.0),
        _doc("kept", folder_id="f1", sort_order=0), _doc("d", folder_id="f2", sort_order=0), _doc("e", folder_id="f2", sort_order=4.0),
    ]
    listed = []
    for backend in (python, rust_store):
        saves._forget_saved()
        conversations._sort_migrated_slugs.clear()
        seed(_FOLDERS, docs)
        status, reply = backend.call("list_conversations", None)
        assert (status, reply["ok"]) == (200, True), reply
        saves.flush_chat_saves()
        stored = {doc["id"]: (doc["sort_order"], doc["updated"]) for doc in chats.conv_list(_LIST, with_messages=False)}
        listed.append((plain(reply["result"]), stored))
    assert listed[1] == listed[0]
    assert listed[0][1] == {"b": (0.0, 9.0), "c": (1.0, 5.0), "a": (2.0, 1.0), "kept": (0.0, 20.0), "d": (0.0, 20.0), "e": (4.0, 20.0)}
