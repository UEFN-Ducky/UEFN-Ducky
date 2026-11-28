"""Chats made signed out, as the account that signs in sees them: listed, taken, deleted.

Every chat exists twice, ``r-…`` and ``p-…``: Rust acts on the first set, Python on the
second, and the answers and the rows left behind must be the same.
"""

from __future__ import annotations

import copy

import pytest

from backend.store.repos import chats, duckies, local_chats
from backend.uefn_plugins import data_crypto

from .conftest import LOGIN
from .parity_harness import plain
from .test_store_chat_messages import _ALL, _payloads, _rows
from .test_store_chats import _doc, _sql

_SETS = ("r", "p")
_NAMES = ("hub", "seat-1", "seat-2", "alone", "child", "arch")


def _world() -> None:
    """Signed out: a group of three, a chat with a sub-agent chat, an archived chat."""
    folders = [{"id": "archive", "name": "Archive"}]
    for x in _SETS:
        for name, fields in (
            ("hub", {"folder_id": f"{x}-team", "updated": 5.0, "title": "Build team é"}),
            ("seat-1", {"folder_id": f"{x}-team", "parent_conv_id": f"{x}-hub", "updated": 6.0}),
            ("seat-2", {"folder_id": f"{x}-sub", "parent_conv_id": f"{x}-hub", "updated": 7.0}),
            ("alone", {"folder_id": f"{x}-work", "updated": 9.0}),
            ("child", {"folder_id": f"{x}-work", "parent_conv_id": f"{x}-alone", "updated": 3.0}),
            ("arch", {"folder_id": "archive", "updated": 4.0}),
        ):
            chats.conv_save("p1", _doc(f"{x}-{name}", **fields), messages=copy.deepcopy(_ALL))
        folders += [
            {"id": f"{x}-team", "name": "Build team", "group_hub_id": f"{x}-hub", "sort_order": 2.0},
            {"id": f"{x}-sub", "name": "Helpers é", "parent_id": f"{x}-team"},
            {"id": f"{x}-work", "name": "Work"},
        ]
    chats.folders_replace("p1", folders)


@pytest.fixture
def signed_in(rust_store, login) -> str:
    """The world made signed out, then a sign-in with the data key on this PC."""
    _world()
    account = login(LOGIN)
    assert data_crypto.adk(account)
    return account


def _waiting(rust_store) -> list:
    got = rust_store.store("local_chats", "waiting")
    assert got == plain(local_chats.waiting())
    return [row["id"] for row in got]


def _bare(ids) -> list:
    """Chat ids without their set."""
    return [conv_id[2:] for conv_id in ids]


def _take(rust_store, names) -> dict:
    """Rust takes the ``r`` chats named, Python the ``p`` ones: the same answer."""
    ours = rust_store.store("local_chats", "adopt", conv_ids=names and [f"r-{n}" for n in names])
    theirs = local_chats.adopt(names and [f"p-{n}" for n in names])
    assert (_bare(ours["moved"]), _bare(ours["failed"])) == (_bare(theirs["moved"]), _bare(theirs["failed"]))
    return ours


def _same_rows(rust_store, account: str, names) -> None:
    for name in names:
        r, p = f"r-{name}", f"p-{name}"
        assert _rows(r) == _rows(p) and _payloads(r) == _payloads(p)
        assert _sql("SELECT DISTINCT account_id FROM messages WHERE conv_id IN (?, ?)", r, p) == [(account,)]
        assert chats.messages_get(r) == _ALL and chats.messages_get(p) == _ALL
        for conv_id in (r, p):
            got = rust_store.store("chats", "conv_get", conv_id=conv_id, with_messages=True)
            assert got == plain(chats.conv_get(conv_id)) and got["messages"] == plain(_ALL)
    assert rust_store.store("chats", "folders_get", project_id="p1") == plain(chats.folders_get("p1"))


def test_signed_out_nothing_waits(rust_store, login) -> None:
    _world()
    assert rust_store.store("local_chats", "waiting") == [] == local_chats.waiting()
    assert rust_store.store("local_chats", "adopt") == {"moved": [], "failed": []} == local_chats.adopt()
    assert rust_store.store("local_chats", "remove", conv_ids=["r-hub"]) == [] == local_chats.remove(["p-hub"])
    assert len(chats.conv_list("p1")) == 12


def test_they_are_listed_and_move_whole(rust_store, signed_in) -> None:
    assert _waiting(rust_store) == ["p-alone", "r-alone", "p-hub", "r-hub"]  # newest first; archived ones left out
    assert chats.conv_list("p1") == []

    out = _take(rust_store, ["seat-2"])  # any chat of the group brings the group
    assert sorted(out["moved"]) == ["r-hub", "r-seat-1", "r-seat-2"] and out["failed"] == []
    _same_rows(rust_store, signed_in, ["hub", "seat-1", "seat-2"])
    mine = {f["id"]: f for f in chats.folders_get("p1")}
    assert mine["r-team"] == {**mine["p-team"], "id": "r-team", "group_hub_id": "r-hub"}
    assert mine["r-sub"] == {**mine["p-sub"], "id": "r-sub", "parent_id": "r-team"}
    assert _waiting(rust_store) == ["p-alone", "r-alone"]

    out = _take(rust_store, ["child", "arch"])  # a sub-agent chat brings its parent
    assert sorted(out["moved"]) == ["r-alone", "r-arch", "r-child"]
    _same_rows(rust_store, signed_in, _NAMES)
    assert _waiting(rust_store) == []
    # Nothing is left under this PC's own key but its Archive folder; no payload is orphaned.
    assert _sql("SELECT count(*) FROM conversations WHERE account_id='_local'") == [(0,)]
    assert _sql("SELECT id FROM folders WHERE account_id='_local'") == [("archive",)]
    assert _sql(
        "SELECT count(*) FROM message_parts p WHERE NOT EXISTS (SELECT 1 FROM message_part_refs r WHERE r.hash = p.hash)"
    ) == [(0,)]
    # A moved chat saves like any chat of the account, from either backend.
    more = [*_ALL, {"role": "user", "text": "and the lighthouse?", "ts": 9.0}]
    assert not rust_store.store("chats", "conv_save", project_id="p1", doc=_doc("p-hub"), messages=more).get("skipped")
    assert not chats.conv_save("p1", _doc("r-hub"), messages=copy.deepcopy(more)).get("skipped")
    assert chats.messages_get("p-hub") == more == chats.messages_get("r-hub")


def test_taking_everything_brings_the_archived_ones_too(rust_store, signed_in) -> None:
    out = rust_store.store("local_chats", "adopt")
    assert sorted(out["moved"]) == sorted(f"{x}-{name}" for x in _SETS for name in _NAMES) and out["failed"] == []
    assert local_chats.adopt() == {"moved": [], "failed": []} and _waiting(rust_store) == []
    _same_rows(rust_store, signed_in, _NAMES)
    assert chats.conv_get("r-arch", with_messages=False)["folder_id"] == "archive"


def test_deleting_one_removes_it_for_good(rust_store, signed_in) -> None:
    ours = rust_store.store("local_chats", "remove", conv_ids=["r-seat-1"])
    theirs = plain(local_chats.remove(["p-seat-1"]))
    assert sorted(ours) == [["r-hub", "p1"], ["r-seat-1", "p1"], ["r-seat-2", "p1"]]
    assert sorted(_bare(i for i, _ in ours)) == sorted(_bare(i for i, _ in theirs))
    assert _waiting(rust_store) == ["p-alone", "r-alone"]
    left = {i for (i,) in _sql("SELECT id FROM conversations")}
    assert left == {f"{x}-{name}" for x in _SETS for name in ("alone", "child", "arch")}
    assert {c for (c,) in _sql("SELECT DISTINCT conv_id FROM messages")} == left
    assert {i for (i,) in _sql("SELECT id FROM folders")} == {"archive", "r-work", "p-work"}
    # A turn still holding a deleted chat never brings it back, whoever deleted it.
    for conv_id in ("r-hub", "p-hub"):
        assert chats.conv_save("p1", _doc(conv_id), messages=[]).get("skipped") == 1
        assert rust_store.store("chats", "conv_save", project_id="p1", doc=_doc(conv_id), messages=[]).get("skipped") == 1


def test_a_row_no_read_could_open_goes_along(rust_store, login) -> None:
    for conv_id in ("r-odd", "p-odd"):
        chats.conv_save("p1", _doc(conv_id), messages=copy.deepcopy(_ALL))
        _sql("UPDATE messages SET body='not json' WHERE conv_id=? AND seq=0", conv_id)
        chats._opened_forget(conv_id)
    account = login(LOGIN)
    assert data_crypto.adk(account)
    assert _take(rust_store, ["odd"])["moved"] == ["r-odd"]
    for conv_id in ("r-odd", "p-odd"):
        assert chats.messages_get(conv_id) == _ALL[1:]
        assert rust_store.store("chats", "messages_get", conv_id=conv_id) == plain(_ALL[1:])
        (body,) = _sql("SELECT body FROM messages WHERE conv_id=? AND seq=0", conv_id)[0]
        assert data_crypto.open_text(body, account) == "not json"


def test_the_chats_own_ducky_comes_along_as_a_copy(rust_store, login) -> None:
    duckies.save("my-ducky", "custom", {"name": "Harbour pilot", "system_prompt": "pelican harbour"})
    chats.conv_save("p1", _doc("r-with-ducky", profile_id="my-ducky"), messages=[])
    account = login(LOGIN)
    assert data_crypto.adk(account) and duckies.get("my-ducky") is None
    assert rust_store.store("local_chats", "adopt", conv_ids=["r-with-ducky"])["moved"] == ["r-with-ducky"]
    mine = duckies.get("my-ducky")
    assert mine["kind"] == "custom" and mine["data"] == {"name": "Harbour pilot", "system_prompt": "pelican harbour"}
    assert rust_store.store("duckies", "get", ducky_id="my-ducky")["data"] == mine["data"]
    assert _sql("SELECT count(*) FROM duckies WHERE ducky_id='my-ducky'") == [(2,)]  # a copy: this PC keeps its own
