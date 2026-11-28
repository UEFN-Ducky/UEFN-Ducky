"""The sidebar's folder and chat lists: the same answer and the same folder rows from both
backends. Tests taking ``account`` run signed out and signed in."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.store import db
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
        conn = db.connect()
        with db.write_txn(conn):  # what an earlier call deleted can be stored again
            conn.execute("DELETE FROM workspace_state WHERE key LIKE 'deleted_%'")
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


def _differences(mine: Any, yours: Any, where: str = "") -> list:
    """Only what differs, as ``(where, rust, python)``: a whole chat list is unreadable."""
    if isinstance(mine, dict) and isinstance(yours, dict):
        return [d for key in sorted({*mine, *yours}) for d in _differences(mine.get(key, "<absent>"), yours.get(key, "<absent>"), f"{where}.{key}")]
    if isinstance(mine, list) and isinstance(yours, list) and len(mine) == len(yours):
        named = lambda row, index: row["id"] if isinstance(row, dict) and "id" in row else index  # noqa: E731
        return [d for index, (a, b) in enumerate(zip(mine, yours)) for d in _differences(a, b, f"{where}[{named(b, index)}]")]
    return [] if mine == yours else [(where, repr(mine)[:160], repr(yours)[:160])]


class _Both:
    """One call on each backend from the same folders and chats: the answer, the events
    and what is stored after must match. ``updated`` is the save's time and a new
    folder's id is random: both are compared as given names."""

    def __init__(self, seed: Any, python: Any, rust: Any, monkeypatch: Any) -> None:
        from frontend.ui_web.agent_turns import push

        self.seed, self.python, self.rust = seed, python, rust
        self.pushed: list[dict] = []
        monkeypatch.setattr(push, "_panel_push", self.pushed.append)  # never the live app's panel
        # No agent runs here; Python's stop of an archived chat would start its terminal manager.
        monkeypatch.setattr("frontend.ui_web.chats.archive_stop.stop_archived", lambda *args, **kwargs: None)
        # Nor is any agent told its team changed: that goes to the app's agent inboxes.
        monkeypatch.setattr("frontend.ui_web.chats.nesting_notice._deliver", lambda *args, **kwargs: None)
        self.cursor = 0
        self.before = lambda: None  # runs after each seed
        self.after = lambda: None  # its answer after each call must match too
        self.deleted: list = []  # the ids the last call marked deleted

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
        # By id: the stored order is by save time, and each backend saves in its own order.
        docs = sorted(plain(chats.conv_list(_LIST, with_messages=False)), key=lambda doc: doc["id"])
        for doc in docs:
            doc["updated"] = "now" if doc["updated"] >= started else doc["updated"]
        marks = [row[0] for row in db.connect().execute("SELECT key FROM workspace_state WHERE key LIKE 'deleted_%' ORDER BY key")]
        text = json.dumps((answer, events, folders, docs, marks, self.after()))
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
            self.before()
            self.pushed.clear()
            started = time.time()
            answer = backend.call(method, *args)
            events = list(self.pushed) if backend is self.python else self._rust_events()
            seen.append(self._seen(answer, events, started))
        theirs, ours = seen
        for name, mine, yours in zip(("answer", "events", "folders", "chats", "deleted", "after"), ours, theirs):
            assert mine == yours, f"{method}{args} {name}: rust != python: {_differences(mine, yours)}"
        self.deleted = theirs[4]
        return tuple(theirs[:4])


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


@pytest.mark.parametrize(("conv_id", "muted"), [(" odd ", True), ("odd", 0), ("hub", "yes"), ("nope", True)])
def test_a_chat_is_muted_the_same(both, conv_id: str, muted: Any) -> None:
    hub = _doc("hub", folder_id="g1", is_group=True, muted=True)
    answer, events, _, docs = both.call(_FOLDERS, [_ODD, hub], "set_muted", conv_id, muted)
    if conv_id == "nope":
        assert (answer[1]["result"], events) == ({"ok": False, "error": "Conversation not found"}, [])
        return
    assert answer[1]["result"] == {"ok": True, "id": conv_id.strip(), "muted": bool(muted), "is_group": conv_id == "hub"}
    assert [event["conv_id"] for event in events] == [conv_id.strip()]
    assert {doc["id"]: doc.get("muted") for doc in docs}[conv_id.strip()] is bool(muted)  # the other chat is as seeded


def test_chats_made_signed_out_are_listed_the_same(seed, python, rust_store, login) -> None:
    from backend.uefn_plugins import data_crypto

    from .conftest import ADK, LOGIN

    assert _same(python, rust_store, "list_local_chats") == {"ok": True, "chats": []}
    seed([], [_doc("mine", title="Made signed out"), _doc("old", folder_id="archive")])
    assert _same(python, rust_store, "list_local_chats") == {"ok": True, "chats": []}
    assert data_crypto.adk(login(LOGIN)) == ADK
    waiting = _same(python, rust_store, "list_local_chats")
    assert [(chat["id"], chat["title"]) for chat in waiting["chats"]] == [("mine", "Made signed out")]


def test_chats_made_signed_out_are_added_or_deleted_the_same(both, seed, python, rust_store, login) -> None:
    """Every chat exists twice: Rust acts on the ``r-`` set, Python on the ``p-`` set."""
    from backend.store import sealed_files
    from backend.uefn_plugins import data_crypto
    from frontend.ui_web.chats import store

    from .conftest import ADK, LOGIN

    docs = []
    for x in "rp":
        docs += [
            _doc(f"{x}-hub", folder_id=f"{x}-team", is_group=True), _doc(f"{x}-seat", folder_id=f"{x}-team", parent_conv_id=f"{x}-hub"),
            _doc(f"{x}-alone", updated=30.0), _doc(f"{x}-child", parent_conv_id=f"{x}-alone"), _doc(f"{x}-bin", updated=25.0),
        ]
    seed([_folder(f"{x}-team", hub=f"{x}-hub") for x in "rp"], docs)
    files = store._conversations_dir(None)
    sealed_files.ensure_keypair("_local")
    notes = {name: files / name / "attachments" / "note.txt" for name in ("r-alone", "p-alone", "r-bin", "p-bin")}
    for note in notes.values():  # what a signed-out chat keeps on disk is sealed for this PC
        note.parent.mkdir(parents=True)
        note.write_bytes(b"hello")
        assert sealed_files.seal_file(note, "_local") and sealed_files.is_sealed_file(note)

    def each(method: str, *names: str) -> tuple[Any, Any]:
        bare = lambda seen: json.loads(json.dumps(plain(seen)).replace('"p-', '"').replace('"r-', '"'))  # noqa: E731
        both.pushed.clear()
        theirs = python.call(method, [f"p-{name}" for name in names])
        pushed = list(both.pushed)
        ours, events = rust_store.call(method, [f"r-{name}" for name in names]), both._rust_events()
        assert (bare(ours), bare(events)) == (bare(theirs), bare(pushed)), (ours, events, theirs, pushed)
        return bare(theirs)[1]["result"], bare(pushed)

    assert each("adopt_local_chats", "child") == ({"ok": True, "moved": [], "failed": []}, [])  # signed out: nothing waits
    assert each("delete_local_chats", "bin") == ({"ok": True, "deleted": []}, [])
    assert data_crypto.adk(login(LOGIN)) == ADK

    answer, events = each("adopt_local_chats", "child", "", "nope")  # a chat brings the chat that started it
    assert (answer["ok"], sorted(answer["moved"]), answer["failed"]) == (True, ["alone", "child"], []) and events == [{"type": "chats_changed", "open": False}]
    assert [notes[name].read_bytes() for name in ("r-alone", "p-alone")] == [b"hello", b"hello"]  # opened: the account's now
    assert sealed_files.is_sealed_file(notes["r-bin"]) and sealed_files.is_sealed_file(notes["p-bin"])

    answer, events = each("delete_local_chats", "bin", "seat")  # a seat takes its whole group
    assert sorted(answer["deleted"]) == ["bin", "hub", "seat"] and sorted(events[0]["removed_conv_ids"]) == ["bin", "hub", "seat"]
    assert sorted(path.name for path in files.iterdir()) == ["p-alone", "r-alone"]
    assert each("delete_local_chats") == ({"ok": True, "deleted": []}, [])

    assert sorted(doc["id"] for doc in chats.conv_list(_LIST, with_messages=False)) == ["p-alone", "p-child", "r-alone", "r-child"]
    assert _same(python, rust_store, "list_local_chats") == {"ok": True, "chats": []}


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


# --------------------------------------------------------------------------- deletes


def _folder(folder_id: str, parent_id: str = "", hub: str = "", sort_order: float = 0) -> dict:
    return {"id": folder_id, "name": folder_id.upper(), "parent_id": parent_id, "sort_order": sort_order, "group_hub_id": hub}


# top > [ g1 (group hub) > [ g2 (group hub2), in1 ] ], side > [ g3 (group hub3) ], plain > [ deep ]
_TREE = [
    _folder("top", sort_order=1), _folder("g1", "top", "hub"), _folder("g2", "g1", "hub2", 1), _folder("in1", "g1", sort_order=2),
    _folder("side", sort_order=2), _folder("g3", "side", "hub3"), _folder("plain", sort_order=3), _folder("deep", "plain"),
]
_TEAM = [
    _doc("hub", folder_id="g1", is_group=True, leader_conv_id="lead", group_members=[{"member_conv_id": "lead", "name": "Lead: build"}]),
    _doc("hub2", folder_id="g2", is_group=True, leader_conv_id="m2"),
    _doc("hub3", folder_id="g3", is_group=True),
    _doc("lead", folder_id="g1", parent_conv_id=" hub ", leader_conv_id="hub"),  # the spaces: not found as one it started
    _doc("m1", folder_id="g1"),
    _doc("m2", folder_id="g2", parent_conv_id="hub2"),
    _doc("sub", parent_conv_id="m1"),
    _doc("subsub", parent_conv_id="sub", folder_id="plain"),
    _doc("far", parent_conv_id="hub"),  # the group's, but filed at the top
    _doc("loose", folder_id="in1"),
    _doc("other", folder_id="plain"),
    _doc("deepest", folder_id="deep"),
    _doc("free"),
]


def _placed(docs: list) -> dict:
    return {doc["id"]: (doc["folder_id"], (doc.get("parent_conv_id") or "").strip()) for doc in docs}


@pytest.mark.parametrize(
    ("folder_id", "archive_members"),
    [("plain", False), ("plain", True), ("g1", False), ("g1", True), ("g2", 1), ("g3", False), ("top", True), ("in1", ""), ("nope", False), ("nope", True), ("", False)],
)
def test_a_folder_is_deleted_the_same(both, folder_id: str, archive_members: Any) -> None:
    answer, events, folders, docs = both.call(_TREE, _TEAM, "delete_folder", folder_id, archive_members)
    assert answer[1]["ok"] is True, answer
    assert folder_id not in {folder["id"] for folder in folders}
    assert len(docs) == len(_TEAM), "a deleted folder never deletes a chat"
    assert [event["type"] for event in events] == ["chats_changed"]


def test_a_deleted_group_goes_to_archive_and_its_duckies_stay_or_go_with_it(both) -> None:
    answer, events, folders, docs = both.call(_TREE, _TEAM, "delete_folder", "g1", False)
    assert answer[1]["result"] == ["hub"] and events == [{"type": "chats_changed", "open": False, "removed_conv_ids": ["hub", "hub"], "removed_folder_ids": ["g1"]}]
    placed = _placed(docs)
    # Its duckies move up to where the group was, on their own; the nested group stays a group.
    assert (placed["hub"], placed["lead"], placed["m1"], placed["far"]) == (("archive", ""), ("top", ""), ("top", ""), ("", ""))
    assert {folder["id"]: folder["parent_id"] for folder in folders if folder["id"] in ("g2", "in1")} == {"g2": "top", "in1": "top"}
    assert both.deleted == ["deleted_folder:g1"]

    answer, events, folders, docs = both.call(_TREE, _TEAM, "delete_folder", "g1", True)
    assert answer[1]["result"] == ["hub", "hub2"]
    assert events[0]["removed_folder_ids"] == ["g1", "g2", "in1"] and set(events[0]["removed_conv_ids"]) == {"hub", "hub2", "far", "m2"}
    placed = _placed(docs)
    in_archive = {conv_id: parent for conv_id, (folder, parent) in placed.items() if folder == "archive"}
    # Archive keeps the team's shape: each ducky under its group, the nested group under the outer one.
    assert in_archive == {"hub": "", "hub2": "hub", "lead": "hub", "m1": "hub", "m2": "hub2", "sub": "m1", "subsub": "sub", "far": "hub", "loose": "hub"}
    # "top" held only the group: it goes too.
    assert {folder["id"] for folder in folders} == {"side", "g3", "plain", "deep", "archive"}
    assert both.deleted == ["deleted_folder:g1", "deleted_folder:g2", "deleted_folder:in1", "deleted_folder:top"]


def test_archive_is_never_deleted(both) -> None:
    answer, events, folders, _ = both.call(_TREE, _TEAM, "delete_folder", " archive ")
    assert answer == [200, {"ok": False, "error": "Cannot delete the Archive folder"}] and events == []
    assert "archive" in {folder["id"] for folder in folders}


@pytest.mark.parametrize(
    ("conv_id", "keep_inside", "gone"),
    [
        ("m1", False, {"m1", "sub", "subsub"}), ("hub", False, {"hub", "far"}), ("hub", True, {"hub"}),
        ("free", 1, {"free"}), ("nope", False, set()), ("", False, set()),
    ],
)
def test_a_chat_is_deleted_the_same_with_the_chats_it_started(both, conv_id: str, keep_inside: Any, gone: set) -> None:
    from frontend.ui_web.chats import store

    files = store._conversations_dir(None)

    def attach() -> None:
        for name in ("m1", "sub", "free", "lead"):
            (files / name / "attachments").mkdir(parents=True, exist_ok=True)
            (files / name / "attachments" / "shot.png").write_bytes(b"png")

    both.before = attach
    both.after = lambda: sorted(path.name for path in files.iterdir())
    answer, events, _, docs = both.call(_TREE, _TEAM, "delete_conversation", conv_id, keep_inside)
    assert answer == [200, {"ok": True, "result": None}]
    assert {doc["id"] for doc in _TEAM} - {doc["id"] for doc in docs} == gone
    assert set(events[0]["removed_conv_ids"]) == (gone or {conv_id}) - {""}
    assert both.deleted == sorted("deleted_chat:" + name for name in gone)
    assert set(both.after()) == {"m1", "sub", "free", "lead"} - gone
    if keep_inside and conv_id == "hub":
        assert _placed(docs)["far"] == ("", "")


# --------------------------------------------------------------------------- moves

_ROSTER = [
    {"member_conv_id": "lead", "name": "Lead: build", "write_allowed": ["src\\", "a.py"], "lane_set_at": 5, "lane_set_by": "hub"},
    {"member_conv_id": "m1", "name": "Worker", "color": "#123456", "tts_speed": "1.5"},
    "junk",
]
_MOVES = [dict(doc, group_members=_ROSTER) if doc["id"] == "hub" else doc for doc in _TEAM]


def _teams(docs: list) -> dict:
    rows = lambda doc: [row["member_conv_id"] for row in doc.get("group_members") or [] if isinstance(row, dict)]  # noqa: E731
    return {doc["id"]: (rows(doc), doc["leader_conv_id"]) for doc in docs if doc.get("is_group")}


@pytest.mark.parametrize(
    ("conv_id", "folder_id"),
    [
        ("free", "plain"), ("free", "g1"), ("free", "in1"), ("lead", ""), ("lead", "g2"), ("m1", "g2"), ("m1", None), ("m2", "plain"), ("m2", "g1"),
        ("sub", "g3"), ("far", "g1"), ("hub2", "plain"), ("free", "archive"), ("m1", " archive "), ("lead", "archive"), ("hub", "archive"),
        ("hub2", "archive"), ("hub3", "archive"),
    ],
)
def test_a_chat_is_moved_the_same_and_its_team_follows(both, conv_id: str, folder_id: Any) -> None:
    answer, events, _, docs = both.call(_TREE, _MOVES, "move_conversation", conv_id, folder_id)
    assert answer == [200, {"ok": True, "result": None}]
    assert len(docs) == len(_MOVES) and [event["type"] for event in events] == ["chats_changed"]
    archiving = (folder_id or "").strip() == "archive"
    assert ("removed_conv_ids" in events[0]) == archiving
    if not archiving:
        assert _placed(docs)[conv_id][0] == (folder_id or "")


def test_a_ducky_joins_the_group_it_is_moved_into_and_a_group_its_leader_left_gets_another(both) -> None:
    _, _, _, docs = both.call(_TREE, _MOVES, "move_conversation", "free", "g1")
    assert _placed(docs)["free"] == ("g1", "hub") and _teams(docs)["hub"] == (["lead", "m1", "free"], "lead")
    rows = {row["member_conv_id"]: row for row in next(doc for doc in docs if doc["id"] == "hub")["group_members"]}
    # A stored member keeps its lane as it was stored; the new one has none.
    assert (rows["lead"]["write_allowed"], rows["lead"]["lane_set_at"], rows["free"]["write_allowed"], rows["free"]["name"]) == (["src\\", "a.py"], 5, None, "Chat free")

    _, _, _, docs = both.call(_TREE, _MOVES, "move_conversation", "lead", "")
    assert _placed(docs)["lead"] == ("", "") and _teams(docs)["hub"] == (["m1"], "m1")


def test_a_move_names_what_it_cannot_do(both) -> None:
    answer, events, _, _ = both.call(_TREE, _MOVES, "move_conversation", "no'pe", "plain")
    assert answer == [200, {"ok": False, "error": 'Conversation not found: "no\'pe"'}] and events == []
    answer, _, _, docs = both.call(_TREE, _MOVES, "move_conversation", "free", "nope")
    assert answer == [200, {"ok": False, "error": "Unknown folder: 'nope'"}] and _placed(docs)["free"] == ("", "")


_ARCHIVED = [
    _doc("hub", folder_id="archive", is_group=True, title="Raiders", leader_conv_id="lead", group_members=_ROSTER),
    _doc("hub2", folder_id="archive", is_group=True, title="", parent_conv_id="hub"),
    _doc("lead", folder_id="archive", parent_conv_id="hub"),
    _doc("m2", folder_id="archive", parent_conv_id="hub2"),
    _doc("sub", folder_id="archive", parent_conv_id="lead"),
    _doc("solo", folder_id="archive"),
    _doc("left", folder_id="plain", parent_conv_id="hub"),  # never archived: it stays where it is
]
_AROUND = [_folder("top", sort_order=1), _folder("plain", sort_order=2)]


@pytest.mark.parametrize(
    ("conv_id", "folder_id"),
    [("hub", ""), ("hub", "plain"), ("hub", "nope"), ("hub2", "top"), ("lead", ""), ("lead", "plain"), ("m2", "top"), ("solo", "plain"), ("sub", "")],
)
def test_a_chat_returns_from_archive_the_same(both, conv_id: str, folder_id: str) -> None:
    answer, _, _, docs = both.call(_AROUND, _ARCHIVED, "move_conversation", conv_id, folder_id)
    assert answer[1]["ok"] is (folder_id != "nope"), answer
    assert (_placed(docs)[conv_id][0] == "archive") is (folder_id == "nope")


def test_a_group_returns_from_archive_whole(both) -> None:
    _, _, folders, docs = both.call(_AROUND, _ARCHIVED, "move_conversation", "hub", "plain")
    made = {folder["id"]: (folder["name"], folder["parent_id"]) for folder in folders if folder["group_hub_id"]}
    assert made == {"folder of hub": ("Raiders", "plain"), "folder of hub2": ("Group", "folder of hub")}
    assert _placed(docs) == {
        "hub": ("folder of hub", ""), "hub2": ("folder of hub2", ""), "lead": ("folder of hub", "hub"), "m2": ("folder of hub2", "hub2"),
        "sub": ("folder of hub", "lead"), "solo": ("archive", ""), "left": ("plain", "hub"),
    }


# --------------------------------------------------------------------------- drag and drop


def _chat(conv_id: str, folder_id: str, sort_order: Any = 1) -> dict:
    return {"id": conv_id, "folder_id": folder_id, "sort_order": sort_order}


_STALE = {  # rows a window that is behind can send: none of them may change or create anything
    "folders": [
        {"id": "nope", "parent_id": "", "sort_order": 1}, {"id": "archive", "parent_id": "top", "sort_order": 1}, {"id": "top", "parent_id": "g2"},
        {"id": "plain", "parent_id": "gone"}, {"id": "deep", "parent_id": "archive"}, {"id": "side", "parent_id": "side"}, {"id": " "}, "junk",
    ],
    "chats": [_chat("nope", "plain"), _chat("free", "archive"), _chat("m1", "gone"), {"id": ""}, "junk", {"id": "other", "sort_order": "x"}],
}


@pytest.mark.parametrize(
    "layout",
    [
        {"folders": [], "chats": []},
        {"folders": [], "chats": [_chat("free", "g1", 5)]},
        {"folders": [], "chats": [_chat("lead", ""), _chat("m1", "g2", "0.5"), _chat("m2", "plain", True)]},
        {"folders": [], "chats": [_chat("free", "plain"), _chat("free", "g3", 2), {"id": "other", "sort_order": 9}]},
        {"folders": [{"id": "plain", "parent_id": "top", "sort_order": 0.5}, {"id": "deep", "sort_order": "7"}], "chats": []},
        {"folders": [{"id": "g2", "parent_id": "", "sort_order": 4, "group_hub_id": "hub2"}], "chats": [_chat("lead", "g2")]},
        {"folders": [{"id": "g3", "parent_id": "g1", "sort_order": 3, "group_hub_id": "hub3"}, {"id": "g1", "parent_id": "g3"}], "chats": [_chat("sub", "g3")]},
        {"folders": [{"id": "in1", "parent_id": None, "sort_order": None}, {"id": "in1", "parent_id": "plain", "sort_order": 2}], "chats": [_chat("loose", "in1", 3)]},
        _STALE,
        {"folders": [], "chats": [], "project_slug": " _no_project "},
    ],
)
def test_a_dragged_layout_is_saved_the_same_and_the_teams_follow(both, layout: dict) -> None:
    answer, events, _, docs = both.call(_TREE, _MOVES, "apply_sidebar_layout", layout)
    assert answer == [200, {"ok": True, "result": None}]
    assert events == [{"type": "chats_changed", "open": False}] and len(docs) == len(_MOVES)


def test_a_stale_layout_changes_no_place_and_a_drag_rebuilds_the_rosters(both) -> None:
    _, _, folders, docs = both.call(_TREE, _MOVES, "apply_sidebar_layout", _STALE)
    assert {folder["id"]: folder["parent_id"] for folder in folders if folder["id"] != "archive"} == {folder["id"]: folder["parent_id"] for folder in _TREE}
    assert {doc["id"]: doc["folder_id"] for doc in docs} == {doc["id"]: doc["folder_id"] for doc in _MOVES}
    # Every roster is what its folder holds, the leader first and the nested group as one row;
    # a ducky filed outside its group's folder is on its own.
    assert _teams(docs) == {"hub": (["lead", "m1", "hub2"], "lead"), "hub2": (["m2"], "m2"), "hub3": ([], "")}
    assert (_placed(docs)["m1"], _placed(docs)["far"]) == (("g1", "hub"), ("", ""))

    _, _, _, docs = both.call(_TREE, _MOVES, "apply_sidebar_layout", {"folders": [], "chats": [_chat("free", "g3", 5), _chat("lead", "")]})
    assert (_placed(docs)["free"], _placed(docs)["lead"]) == (("g3", "hub3"), ("", ""))
    # A leader dragged out of its group's folder still leads it, from outside.
    assert _teams(docs) == {"hub": (["lead", "m1", "hub2"], "lead"), "hub2": (["m2"], "m2"), "hub3": (["free"], "")}


@pytest.mark.parametrize(
    ("layout", "error"),
    [
        ({"folders": []}, "layout payload must include folders and chats arrays"),
        ({"folders": [], "chats": {}}, "layout payload must include folders and chats arrays"),
        ({"folders": [], "chats": [_chat("free", "plain")], "project_slug": "nope"}, "Unknown project: nope"),
    ],
)
def test_a_layout_that_cannot_be_saved_says_why(both, layout: dict, error: str) -> None:
    answer, events, _, docs = both.call(_TREE, _MOVES, "apply_sidebar_layout", layout)
    assert answer == [200, {"ok": False, "error": error}] and events == []
    assert _placed(docs)["free"] == ("", "")


# --------------------------------------------------------------------------- group rosters

# The outer group lists its nested group, and the outer lead is on the nested group's roster too.
_NESTED = [
    dict(doc, group_members=[*_ROSTER, {"member_conv_id": "hub2", "name": "Sub", "is_group": True}]) if doc["id"] == "hub"
    else dict(doc, group_members=[{"member_conv_id": "m2", "write_allowed": []}, {"member_conv_id": " lead ", "name": "Lead: build"}]) if doc["id"] == "hub2"
    else doc
    for doc in _TEAM
]


@pytest.mark.parametrize(
    ("group_id", "member_id", "error"),
    [
        ("hub", "m1", ""), ("hub", " lead ", ""), ("hub2", "lead", ""), ("hub2", "m2", ""), ("hub", "hub2", ""),
        ("hub", "free", "Member not in this group"), ("hub", " ", "member_conv_id required"), ("hub", None, "member_conv_id required"),
        ("free", "m1", "Not a group chat"), ("nope", "m1", "Not a group chat"), ("hub3", "m1", "Member not in this group"),
    ],
)
def test_a_groups_leader_is_set_the_same(both, group_id: str, member_id: Any, error: str) -> None:
    answer, events, _, docs = both.call(_TREE, _NESTED, "group_set_leader", group_id, member_id)
    result = answer[1]["result"]
    assert answer[1]["ok"] is True and result.get("error", "") == error, answer
    assert [event["type"] for event in events] == ([] if error else ["chats_changed"])
    if not error:
        assert result["leader_conv_id"] == member_id.strip() == _teams(docs)[group_id][1]


@pytest.mark.parametrize(
    ("group_id", "member_id", "leader"),
    [("hub", "lead", "m1"), ("hub", " m1 ", "lead"), ("hub", "hub2", "lead"), ("hub", "nope", "lead"), ("hub", "", "lead"), ("hub2", "m2", "lead"), ("hub2", "lead", "m2"), ("hub3", "x", "")],
)
def test_a_member_is_removed_the_same_and_a_leader_hands_over(both, group_id: str, member_id: str, leader: str) -> None:
    answer, events, folders, docs = both.call(_TREE, _NESTED, "group_remove", group_id, member_id)
    result = answer[1]["result"]
    assert result["ok"] is True and result["leader_conv_id"] == leader == _teams(docs)[group_id][1], answer
    assert member_id.strip() not in _teams(docs)[group_id][0] and [event["conv_id"] for event in events] == [group_id]
    if member_id.strip() in ("lead", "m1", "m2"):  # out of the group's folder, on its own
        assert _placed(docs)[member_id.strip()] == ("", "")
    if member_id == "hub2":  # a nested group's folder goes to the top level
        assert next(folder["parent_id"] for folder in folders if folder["id"] == "g2") == ""


def test_only_a_group_has_members_to_remove(both) -> None:
    answer, events, _, _ = both.call(_TREE, _NESTED, "group_remove", "free", "m1")
    assert answer == [200, {"ok": True, "result": {"ok": False, "error": "Not a group chat"}}] and events == []


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


# --------------------------------------------------------------------------- messages

_TURNS = [
    {"role": "user", "text": "hello", "attachments": [{"kind": "image", "name": "a.png", "data_base64": "QQ=="}, "junk"]},
    {
        "role": "assistant", "content": "Done é", "thinking": "plan", "tours": ["intro", " ", 3],
        "author": {"name": " Dev ", "tts_speed": "1.5", "color": None},
        "blocks": [
            {"type": "thinking", "text": "first"}, {"type": "text", "text": "  "}, {"type": "text", "text": "Looking"},
            {"type": "tool_call", "id": "t1", "name": "ducky_find", "arguments": {"query": "x" * 200}, "status": "success",
             "duration_ms": 12, "result": {"data": "é" * 8001, "hint": "narrow it", "exit_code": 0}, "llm_tokens": 40},
            {"type": "user", "text": "also this"},
            {"type": "tool_call", "id": "t2", "name": "Write", "arguments": {}, "status": "error", "result": {"error": "no"},
             "file_edits": [{"path": "a.verse", "before": "a" * 3000, "after": "b" * 1001, "linesAdded": 1, "linesRemoved": 1},
                            {"path": "b.verse", "before": "", "after": "x"}]},
            {"type": "tool_call", "id": "t3", "name": "Edit", "arguments": {"path": "c"}, "status": "success",
             "result": {"ok": True, "n": 1.5}, "file_edit": {"path": "c", "before": "1", "after": "2"}},
        ],
    },
    {"role": "system", "content": "never a row"},
    {"role": "user", "content": "an older message"},
    {"role": "assistant", "content": "", "incomplete": True, "error": "boom", "run_id": "r1",
     "blocks": [{"type": "tool_call", "name": "x", "status": "pending"}]},
    {"role": "assistant", "content": "half", "incomplete": True, "run_id": "r2"},
]


def test_a_chats_messages_are_the_same_rows_page_by_page(seed, python, rust_store) -> None:
    """No attachment here is a stored file and every write tool has its edits stored:
    opening those is still Python's (the note in `api/chats/messages.rs`)."""
    seed(_FOLDERS, [])
    chats.conv_save(_LIST, _doc("m"), messages=_TURNS)
    chats.conv_save(_LIST, _doc("long"), messages=[{"role": "user", "text": f"message {n}"} for n in range(80)])

    rows = _same(python, rust_store, "load_messages", "m")
    assert [row["role"] for row in rows] == [
        "user", "assistant", "assistant", "tool", "success", "user", "tool", "error", "tool", "success", "assistant",
        "user", "tool", "error", "assistant", "assistant",
    ]
    found = rows[4]["tool"]
    assert (len(found["result"]), found["resultTruncated"], found["resultBytes"], found["exitCode"]) == (8000, True, 16002, 0)
    assert rows[7]["tool"]["fileEdits"][0]["contentOmitted"] is True and rows[7]["tool"]["fileEdit"]["before"] == ""
    assert rows[9]["tool"]["result"] == '{"ok": true, "n": 1.5}' and rows[9]["tool"]["fileEdit"]["after"] == "2"
    assert (rows[14]["error"], rows[14]["live_run"], rows[15]["text"]) == ("boom", False, "half")

    whole = _same(python, rust_store, "load_messages", "m", None, None, True)
    assert len(whole[4]["tool"]["result"]) == 8001 and whole[7]["tool"]["fileEdit"]["before"] == "a" * 3000
    for limit in (1, 3, 16, 100, 0, -2):
        _same(python, rust_store, "load_messages", "m", None, limit)
    for row in rows:
        assert _same(python, rust_store, "load_messages", "m", row["id"], 2) == [r for r in rows if r["id"] < row["id"]][-2:]
        _same(python, rust_store, "load_messages", "m", row["id"])
        _same(python, rust_store, "load_tool_result", "m", row["id"])
        _same(python, rust_store, "load_tool_file_edits", "m", row["id"])
    assert _same(python, rust_store, "load_tool_result", "m", rows[4]["id"]) == {"ok": True, "result": "é" * 8001}
    assert _same(python, rust_store, "load_tool_file_edits", "m", rows[7]["id"])["fileEdits"][0]["before"] == "a" * 3000
    assert _same(python, rust_store, "load_tool_file_edits", "m", rows[4]["id"]) == {"ok": False, "error": "No file edit in that row"}
    assert _same(python, rust_store, "load_tool_result", "m", rows[0]["id"]) == {"ok": False, "error": "No tool result in that row"}

    # A page wider than the first read reads further back.
    assert [row["text"] for row in _same(python, rust_store, "load_messages", "long", None, 70)] == [f"message {n}" for n in range(10, 80)]
    assert len(_same(python, rust_store, "load_messages", "long", 60 * 2**32, 40)) == 40

    assert _same(python, rust_store, "load_messages", "nope") == _same(python, rust_store, "load_messages", "nope", None, 5) == []
    for method in ("load_tool_result", "load_tool_file_edits"):
        assert _same(python, rust_store, method, "nope", 0) == {"ok": False, "error": "Chat not found"}
