"""Chats made signed out stay within reach after a sign-in: listed, moved into the account
(same rows, sealed again for it) or deleted. Before, they were gone from the list until the
person signed out again (Nov 2026)."""

from __future__ import annotations

import pytest

from backend.store import db, sealed_files
from backend.store.repos import chats, duckies, local_chats
from backend.store.test_account_rows import (  # noqa: F401 — who is a fixture
    ALICE,
    BOB,
    PROJECT,
    WORDS,
    _everything_stored,
    _json,
    _transcript,
    who,
)
from backend.uefn_plugins import data_crypto
from backend.uefn_plugins.scopes import LOCAL


def _chat(conv_id: str, *, folder: str = "", parent: str = "", title: str = "", updated: float = 2.0) -> list[dict]:
    messages = _transcript(" ".join(WORDS))
    chats.conv_save(PROJECT, {
        "id": conv_id, "title": title or f"Chat {conv_id}", "created": 1.0, "updated": updated,
        "folder_id": folder, "parent_conv_id": parent, "context_summary": "pelican harbour so far",
    }, messages=messages)
    return messages


def _folders(*rows: dict) -> None:
    chats.folders_replace(PROJECT, [{"id": "archive", "name": "Archive"}, *rows])


def _ids() -> set[str]:
    return {c["id"] for c in chats.conv_list(PROJECT)}


def _folder_ids() -> set[str]:
    return {f["id"] for f in chats.folders_get(PROJECT)}


def test_signed_out_they_are_the_list_and_nothing_waits(who) -> None:
    who.be("")
    _chat("made-signed-out")
    assert _ids() == {"made-signed-out"}
    assert local_chats.waiting() == []
    assert local_chats.adopt() == {"moved": [], "failed": []}
    assert local_chats.remove(["made-signed-out"]) == []
    assert _ids() == {"made-signed-out"}


def test_a_chat_made_signed_out_is_listed_after_sign_in_and_moves_whole(who) -> None:
    who.be("")
    messages = _chat("made-signed-out", title="Harbour notes", updated=7.0)
    alice = who.be(ALICE)
    assert _ids() == set(), "the account's own list is the account's"
    assert local_chats.waiting() == [
        {"id": "made-signed-out", "title": "Harbour notes", "updated": 7.0, "chats": 1, "group": False}
    ]

    assert local_chats.adopt(["made-signed-out"]) == {"moved": ["made-signed-out"], "failed": []}
    assert local_chats.waiting() == []
    assert _ids() == {"made-signed-out"}
    assert _json(chats.messages_get("made-signed-out")) == _json(messages)
    doc = chats.conv_get("made-signed-out", with_messages=False)
    assert doc["title"] == "Harbour notes" and doc["context_summary"] == "pelican harbour so far"
    assert chats.search_messages(PROJECT, "pelican")[0]["conv_id"] == "made-signed-out"
    # It saves like any chat of the account from here on.
    more = [*messages, {"role": "user", "content": "and the lighthouse?", "ts": 3.0}]
    assert not chats.conv_save(PROJECT, {**doc, "id": "made-signed-out"}, messages=more).get("skipped")
    assert len(chats.messages_get("made-signed-out")) == 3

    # Every row is the account's and sealed for it: nothing readable in the file, nothing
    # left under this PC's own key, no payload of before left behind.
    conn = db.connect()
    assert {a for (a,) in conn.execute("SELECT account_id FROM conversations")} == {alice}
    assert {a for (a,) in conn.execute("SELECT account_id FROM messages")} == {alice}
    stored = _everything_stored()
    assert all(word not in stored for word in WORDS)
    title = conn.execute("SELECT title FROM conversations").fetchone()[0]
    with pytest.raises(OSError):
        data_crypto.open_text(title, LOCAL)
    assert conn.execute(
        "SELECT count(*) FROM message_parts p WHERE NOT EXISTS (SELECT 1 FROM message_part_refs r WHERE r.hash = p.hash)"
    ).fetchone()[0] == 0

    who.be(BOB)
    assert chats.conv_get("made-signed-out") is None and local_chats.waiting() == []
    who.be("")
    assert _ids() == set(), "it is the account's now"


def test_a_group_moves_with_everything_in_it(who) -> None:
    who.be("")
    _chat("hub", folder="team", updated=5.0, title="Build team")
    _chat("seat-1", folder="team", parent="hub")
    _chat("seat-2", folder="team-sub", parent="hub")
    _chat("alone", updated=9.0)
    _folders(
        {"id": "team", "name": "Build team", "group_hub_id": "hub"},
        {"id": "team-sub", "name": "Helpers", "parent_id": "team"},
    )
    who.be(ALICE)
    assert [(r["id"], r["chats"], r["group"]) for r in local_chats.waiting()] == [("alone", 1, False), ("hub", 3, True)]

    out = local_chats.adopt(["seat-2"])  # any chat of the group brings the group
    assert sorted(out["moved"]) == ["hub", "seat-1", "seat-2"] and out["failed"] == []
    assert _ids() == {"hub", "seat-1", "seat-2"}
    mine = {f["id"]: f for f in chats.folders_get(PROJECT)}
    assert mine["team"]["group_hub_id"] == "hub" and mine["team"]["name"] == "Build team"
    assert mine["team-sub"]["parent_id"] == "team" and mine["team-sub"]["name"] == "Helpers"
    assert [r["id"] for r in local_chats.waiting()] == ["alone"]

    who.be("")
    assert _ids() == {"alone"} and _folder_ids() == {"archive"}, "no empty group is left behind"


def test_a_sub_agent_chat_follows_its_parent_and_a_shared_folder_stays_for_both(who) -> None:
    who.be("")
    _chat("parent", folder="work")
    _chat("child", folder="work", parent="parent")
    _chat("other", folder="work")
    _folders({"id": "work", "name": "Work"})
    who.be(ALICE)
    assert sorted(r["id"] for r in local_chats.waiting()) == ["other", "parent"]

    assert sorted(local_chats.adopt(["parent"])["moved"]) == ["child", "parent"]
    assert _ids() == {"parent", "child"} and "work" in _folder_ids()
    who.be("")
    assert _ids() == {"other"} and "work" in _folder_ids(), "the folder still holds a chat made signed out"

    who.be(ALICE)
    assert local_chats.adopt(["other"])["moved"] == ["other"]
    who.be("")
    assert _ids() == set() and _folder_ids() == {"archive"}


def test_archived_ones_are_not_listed_and_add_all_brings_everything(who) -> None:
    who.be("")
    _chat("kept")
    _chat("archived", folder="archive")
    who.be(ALICE)
    assert [r["id"] for r in local_chats.waiting()] == ["kept"]
    assert sorted(local_chats.adopt()["moved"]) == ["archived", "kept"]
    assert _ids() == {"kept", "archived"}
    assert chats.conv_get("archived", with_messages=False)["folder_id"] == "archive"


def test_deleting_one_removes_it_for_good(who) -> None:
    who.be("")
    _chat("hub", folder="team")
    _chat("seat", folder="team", parent="hub")
    _chat("keep")
    _folders({"id": "team", "name": "Team", "group_hub_id": "hub"})
    who.be(ALICE)
    assert sorted(local_chats.remove(["hub"])) == [("hub", PROJECT), ("seat", PROJECT)]
    assert [r["id"] for r in local_chats.waiting()] == ["keep"]
    conn = db.connect()
    assert {i for (i,) in conn.execute("SELECT id FROM conversations")} == {"keep"}
    assert {c for (c,) in conn.execute("SELECT DISTINCT conv_id FROM messages")} == {"keep"}
    assert conn.execute("SELECT count(*) FROM message_part_refs").fetchone()[0] == 1
    who.be("")
    assert _ids() == {"keep"} and _folder_ids() == {"archive"}
    # A turn still holding the deleted chat never brings it back.
    assert chats.conv_save(PROJECT, {"id": "hub", "title": "back?"}, messages=[]).get("skipped") == 1


def test_a_chat_that_cannot_move_stays_whole_and_the_others_still_move(who, monkeypatch) -> None:
    who.be("")
    messages = _chat("stuck", updated=3.0)
    _chat("fine", updated=4.0)
    who.be(ALICE)
    real = chats._write_row

    def write_row(conn, conv_id, *args, **kwargs):
        if conv_id == "stuck" and args[0] == 1:
            raise OSError("disk full")
        return real(conn, conv_id, *args, **kwargs)

    monkeypatch.setattr(chats, "_write_row", write_row)
    assert local_chats.adopt() == {"moved": ["fine"], "failed": ["stuck"]}
    assert [r["id"] for r in local_chats.waiting()] == ["stuck"]
    monkeypatch.setattr(chats, "_write_row", real)
    who.be("")
    assert _json(chats.messages_get("stuck")) == _json(messages), "rolled back whole"
    who.be(ALICE)
    assert local_chats.adopt(["stuck"]) == {"moved": ["stuck"], "failed": []}
    assert _json(chats.messages_get("stuck")) == _json(messages)


def test_a_row_no_read_could_open_does_not_block_the_chat(who) -> None:
    who.be("")
    messages = _chat("odd")
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE messages SET body='not json' WHERE conv_id='odd' AND seq=0")
    chats._opened_forget("odd")
    assert len(chats.messages_get("odd")) == 1
    who.be(ALICE)
    assert local_chats.adopt(["odd"])["moved"] == ["odd"]
    assert _json(chats.messages_get("odd")) == _json(messages[1:])


def test_the_chats_own_ducky_comes_along_as_a_copy(who) -> None:
    who.be("")
    duckies.save("my-ducky", "custom", {"name": "Harbour pilot", "system_prompt": "pelican harbour"})
    chats.conv_save(PROJECT, {"id": "with-ducky", "title": "t", "profile_id": "my-ducky"}, messages=[])
    who.be(ALICE)
    assert duckies.get("my-ducky") is None
    local_chats.adopt(["with-ducky"])
    assert duckies.get("my-ducky")["data"]["name"] == "Harbour pilot"
    who.be("")
    assert duckies.get("my-ducky")["data"]["name"] == "Harbour pilot", "a copy: this PC keeps its own"


def test_attachments_sealed_while_signed_in_open_for_the_new_owner(who, tmp_path) -> None:
    who.be("")
    sealed_files.ensure_keypair(LOCAL)
    folder = tmp_path / "attachments"
    folder.mkdir()
    (folder / "shot.png").write_bytes(b"pelican picture" * 1000)
    alice = who.be(ALICE)
    sealed_files.ensure_keypair(alice)
    assert sealed_files.seal_file(folder / "shot.png", LOCAL)  # what a sign-in does to this PC's files
    (folder / "plain.txt").write_text("never sealed", "utf-8")
    assert sealed_files.seal_file(tmp_path / "attachments" / "plain.txt", alice)

    assert sealed_files.open_local_folder(folder) == 1
    assert (folder / "shot.png").read_bytes() == b"pelican picture" * 1000
    assert sealed_files.sealed_for(folder / "plain.txt") == alice, "only this PC's own are opened"
    assert sealed_files.open_local_folder(folder) == 0 and sealed_files.open_local_folder(tmp_path / "none") == 0
