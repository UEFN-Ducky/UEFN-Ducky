"""The chats repo: search tokens, the chat key, folders and chat rows, written by one
backend and read by the other. Tests taking ``account`` run signed out and signed in.
"""

from __future__ import annotations

import base64

import pytest

from backend.store import account_rows, db
from backend.store.repos import chats

from .parity_harness import plain

_KEY = bytes(range(32))
_TEXTS = [
    "Hello, World_42 – Straße café",
    "ÀÉÎõü naïve ﬁnance ①② x²",
    "日本語のテキスト 한국어 中文",
    "x_y-z  a\tb\nc\x1cd\x85e f",
    "İstanbul Ǆ ǅ ΣΊΣΥΦΟΣ ς",
    "देवनागरी हिन्दी ไทย עברית العربية",
    "é ½ ⅷ ٣ emoji 😀 ok",
    "",
    "   ",
    "one",
]
_MESSAGES = [
    {"role": "user", "text": "hello world", "ts": 1.0},
    {"role": "assistant", "content": "hi there", "ts": 2.0, "run_id": "r1"},
]


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _doc(conv_id: str, **fields) -> dict:
    return {
        "id": conv_id, "title": "Chat é", "folder_id": "", "created": 10.0, "updated": 20.0, "sort_order": 1.0,
        "provider": "anthropic", "model": "m", "coding_agent": "ducky", "profile_id": "", "file_path": "",
        "parent_conv_id": "", "leader_conv_id": "", "is_group": False, "tool_call_count": 2, "file_count": 1,
        "skill_snapshot": "the skills index", "extra": {"nested": [1, 2.5, None, "é"]}, "settings_edited": 50.0,
        **fields,
    }


def _sql(statement: str, *params) -> list:
    conn = db.connect()
    if statement.lstrip().upper().startswith("SELECT"):
        return [tuple(row) for row in conn.execute(statement, params)]
    with db.write_txn(conn):
        conn.execute(statement, params)
    return []


# --------------------------------------------------------------------------- tokens


@pytest.mark.parametrize("text", _TEXTS)
def test_search_tokens_are_pythons(rust_store, text: str) -> None:
    key = _b64(_KEY)
    assert rust_store.store("tokens", "words", text=text) == account_rows.words(text)
    assert rust_store.store("tokens", "blind_text", text=text, key=key) == account_rows.blind_text(text, _KEY)
    assert rust_store.store("tokens", "blind_query", query=text, key=key) == account_rows.blind_query(text, _KEY)
    assert rust_store.store("tokens", "part_digest", text=text, key=key) == account_rows.part_digest(text, _KEY)


def test_rust_reads_the_chat_key_python_made(rust_store, account) -> None:
    key = account_rows.chat_key(account)
    assert rust_store.store("chats", "chat_key") == _b64(key) and len(key) == 32


def test_python_reads_the_chat_key_rust_made(rust_store, account) -> None:
    key = base64.b64decode(rust_store.store("chats", "chat_key"))
    assert len(key) == 32 and rust_store.store("chats", "chat_key") == _b64(key)
    assert account_rows.chat_key(account) == key
    stored = _sql("SELECT value FROM meta WHERE key=?", "chat_key:" + account)
    assert len(stored) == 1 and stored[0][0].startswith("enc1:")


# --------------------------------------------------------------------------- folders

_FOLDERS = [
    {"id": "f1", "name": "Work é", "parent_id": "", "sort_order": 1.0, "group_hub_id": ""},
    {"id": "f2", "name": "", "parent_id": "f1", "sort_order": 0, "group_hub_id": "hub"},
    {"id": "", "name": "no id: skipped"},
    {"id": "f3"},
]


def _folders_same(rust_store, project: str) -> list:
    ours = rust_store.store("chats", "folders_get", project_id=project)
    assert ours == plain(chats.folders_get(project))
    return ours


def test_rust_reads_the_folders_python_saved(rust_store, account) -> None:
    chats.folders_replace("p1", _FOLDERS)
    chats.folders_replace("p2", [{"id": "g1", "name": "Other"}])
    assert [(f["id"], f["name"], f["parent_id"]) for f in _folders_same(rust_store, "p1")] == [
        ("f3", "", ""), ("f1", "Work é", ""), ("f2", "", "f1"),
    ]
    assert rust_store.store("chats", "folders_list_all_projects") == plain(chats.folders_list_all_projects())
    assert rust_store.store("chats", "folder_project_id", folder_id="g1") == chats.folder_project_id("g1") == "p2"
    for missing in ("", "archive", "nope"):
        assert rust_store.store("chats", "folder_project_id", folder_id=missing) is None


def test_python_reads_the_folders_rust_saved_and_names_are_sealed(rust_store, account) -> None:
    chats.folders_replace("p1", [{"id": "old", "name": "replaced"}])
    assert rust_store.store("chats", "folders_replace", project_id="p1", folders=_FOLDERS) is None
    assert [f["id"] for f in _folders_same(rust_store, "p1")] == ["f3", "f1", "f2"]
    names = dict(_sql("SELECT id, name FROM folders WHERE account_id=?", account))
    assert names["f1"].startswith("enc1:") and names["f2"] == "" and "Work" not in names["f1"]
    assert rust_store.store("chats", "folders_list_all_projects") == plain(chats.folders_list_all_projects())


def test_a_deleted_folder_is_never_written_back(rust_store, account) -> None:
    assert rust_store.store("chats", "folders_mark_deleted", folder_ids=["f1", " ", ""]) is None
    chats.folders_mark_deleted(["f2"])
    chats.folders_replace("p1", _FOLDERS)
    assert [f["id"] for f in _folders_same(rust_store, "p1")] == ["f3"]
    rust_store.store("chats", "folders_replace", project_id="p2", folders=_FOLDERS)
    assert [f["id"] for f in _folders_same(rust_store, "p2")] == ["f3"]
    marks = _sql("SELECT key, value, updated FROM workspace_state WHERE key LIKE 'deleted_folder:%' ORDER BY key")
    assert [m[0] for m in marks] == ["deleted_folder:f1", "deleted_folder:f2"]
    assert all(float(value) == updated > 0 for _, value, updated in marks)


def test_folders_and_chats_move_to_another_project(rust_store, account) -> None:
    for backend_moves, target in ((False, "py"), (True, "rs")):
        chats.folders_replace("src", _FOLDERS)
        chats.conv_save("src", _doc("c-" + target, folder_id="f1"), messages=None)
        folders, convs = [("src", "f1", "new-parent"), ("elsewhere", "f2", "x")], [("c-" + target, "f9"), ("nope", "f1")]
        if backend_moves:
            assert rust_store.store("chats", "move_to_project", target=target, folders=folders, convs=convs) is None
        else:
            chats.move_to_project(target, folders, convs)
        moved = _folders_same(rust_store, target)
        assert [(f["id"], f["parent_id"], f["name"]) for f in moved] == [("f1", "new-parent", "Work é")]
        assert [f["id"] for f in _folders_same(rust_store, "src")] == ["f3", "f2"]
        assert chats.conv_project_id("c-" + target) == target
        assert chats.conv_get("c-" + target, with_messages=False)["folder_id"] == "f9"


# --------------------------------------------------------------------------- chats


def _three_chats() -> None:
    chats.conv_save("p1", _doc("c1"), messages=_MESSAGES)
    chats.conv_save("p1", _doc("c2", title="", sort_order=0.5, parent_conv_id="c1", is_group=True, skill_snapshot=""), messages=[])
    chats.conv_save("p2", _doc("c3", folder_id="f1", sort_order=7.0, parent_conv_id="c1"), messages=_MESSAGES[:1])


def test_rust_reads_the_chats_python_saved(rust_store, account) -> None:
    assert rust_store.store("chats", "has_rows") is chats.has_rows() is False
    _three_chats()
    assert rust_store.store("chats", "has_rows") is chats.has_rows() is True
    for conv_id in ("c1", "c2", "c3", "missing"):
        theirs = plain(chats.conv_get(conv_id, with_messages=False))
        assert rust_store.store("chats", "conv_get", conv_id=conv_id) == theirs
        for project in ("p1", "p2"):
            assert rust_store.store("chats", "conv_get", conv_id=conv_id, project_id=project) == plain(
                chats.conv_get(conv_id, project_id=project, with_messages=False)
            )
        assert rust_store.store("chats", "conv_project_id", conv_id=conv_id) == chats.conv_project_id(conv_id)
        assert rust_store.store("chats", "conv_account", conv_id=conv_id) == chats.conv_account(conv_id)
        assert rust_store.store("chats", "conv_message_summary", conv_id=conv_id) == plain(chats.conv_message_summary(conv_id))
        assert rust_store.store("chats", "conv_msg_rev", conv_id=conv_id) == chats.conv_msg_rev(conv_id)
        for seq in (-3, 0, 1, 9):
            assert rust_store.store("chats", "message_hashes_since", conv_id=conv_id, seq=seq) == plain(
                chats.message_hashes_since(conv_id, seq)
            )
    got = rust_store.store("chats", "conv_get", conv_id="c1")
    assert (got["title"], got["extra"], got["skill_snapshot"], got["is_group"], got["messages"]) == (
        "Chat é", {"nested": [1, 2.5, None, "é"]}, "the skills index", False, [],
    )
    assert list(got) == list(chats.conv_get("c1", with_messages=False))  # the keys in Python's order
    assert rust_store.store("chats", "conv_message_summary", conv_id="c1") == [2, True]
    title, state = _sql("SELECT title, state FROM conversations WHERE id='c1'")[0]
    assert title.startswith("enc1:") and state.startswith("enc1:")


def test_chat_lists_and_lookups_are_pythons(rust_store, account) -> None:
    _three_chats()
    for project in ("p1", "p2", "none"):
        ours = rust_store.store("chats", "conv_list", project_id=project)
        assert ours == plain(chats.conv_list(project))
        assert rust_store.store("chats", "conv_count", project_id=project) == chats.conv_count(project)
        by_parent = rust_store.store("chats", "conv_ids_by_parent", project_id=project)
        assert {k: sorted(v) for k, v in by_parent.items()} == {k: sorted(v) for k, v in chats.conv_ids_by_parent(project).items()}
        for folder in ("", "f1"):
            assert rust_store.store("chats", "conv_max_sort_order", project_id=project, folder_id=folder) == (
                chats.conv_max_sort_order(project, folder)
            )
    assert [c["id"] for c in rust_store.store("chats", "conv_list", project_id="p1")] == ["c2", "c1"]
    assert rust_store.store("chats", "conv_list_all_projects") == plain(chats.conv_list_all_projects())
    assert rust_store.store("chats", "conv_max_sort_order", project_id="p2", folder_id="f1") == 7.0
    assert rust_store.store("chats", "conv_max_sort_order", project_id="p2", folder_id="") == -1.0


def test_settings_newer_and_counters(rust_store, account) -> None:
    _three_chats()
    for edited in (49.0, 50.0, 51.0, 0.0):
        assert rust_store.store("chats", "conv_if_settings_newer", conv_id="c1", edited=edited) == plain(
            chats.conv_if_settings_newer("c1", edited)
        )
    assert rust_store.store("chats", "conv_if_settings_newer", conv_id="c1", edited=49.0)["id"] == "c1"
    assert rust_store.store("chats", "conv_if_settings_newer", conv_id="missing", edited=0.0) is None
    assert rust_store.store("chats", "conv_set_counts", conv_id="c1", tool_call_count=11, file_count=4) is None
    got = chats.conv_get("c1", with_messages=False)
    assert (got["tool_call_count"], got["file_count"]) == (11, 4)
    chats.conv_set_counts("c3", tool_call_count=5, file_count=6)
    assert rust_store.store("chats", "conv_get", conv_id="c3") == plain(chats.conv_get("c3", with_messages=False))


def test_a_chat_rust_deleted_is_gone_and_never_comes_back(rust_store, account) -> None:
    _three_chats()
    assert rust_store.store("chats", "conv_delete", conv_id="c1") is True
    assert rust_store.store("chats", "conv_delete", conv_id="c1") is False
    assert chats.conv_get("c1") is None and chats.messages_get("c1") == []
    assert _sql("SELECT count(*) FROM messages WHERE conv_id='c1'") == [(0,)]
    assert chats.conv_save("p1", _doc("c1"), messages=_MESSAGES).get("skipped") == 1  # a late save
    assert rust_store.store("chats", "conv_get", conv_id="c1") is None
    # The other way round: Python deletes, and both agree on what is left.
    assert chats.conv_delete("c3") is True
    assert rust_store.store("chats", "conv_get", conv_id="c3") is None
    assert [c["id"] for _, c in rust_store.store("chats", "conv_list_all_projects")] == ["c2"]
    marks = [m[0] for m in _sql("SELECT key FROM workspace_state WHERE key LIKE 'deleted_chat:%' ORDER BY key")]
    assert marks == ["deleted_chat:c1", "deleted_chat:c3"]


def test_another_accounts_chats_and_folders_are_left_alone(rust_store, account) -> None:
    _three_chats()
    chats.folders_replace("p1", _FOLDERS)
    other = "_local" if account != "_local" else "a_0000000000000000"
    _sql("UPDATE conversations SET account_id=? WHERE id='c1'", other)
    _sql("UPDATE folders SET account_id=? WHERE id='f1'", other)
    assert rust_store.store("chats", "conv_get", conv_id="c1") is None
    assert rust_store.store("chats", "conv_project_id", conv_id="c1") is None
    assert rust_store.store("chats", "conv_msg_rev", conv_id="c1") is None
    assert rust_store.store("chats", "conv_account", conv_id="c1") == other
    assert rust_store.store("chats", "message_hashes_since", conv_id="c1", seq=0) == [None, 0, []]
    assert rust_store.store("chats", "conv_message_summary", conv_id="c1") == [0, False]
    assert rust_store.store("chats", "conv_delete", conv_id="c1") is False
    assert rust_store.store("chats", "conv_count", project_id="p1") == 2  # every account's
    assert [c["id"] for c in rust_store.store("chats", "conv_list", project_id="p1")] == ["c2"]
    assert [f["id"] for f in rust_store.store("chats", "folders_get", project_id="p1")] == ["f3", "f2"]
    rust_store.store("chats", "folders_replace", project_id="p1", folders=[])
    assert _sql("SELECT account_id, id FROM folders") == [(other, "f1")]
    assert _sql("SELECT count(*) FROM messages WHERE conv_id='c1'") == [(2,)]
