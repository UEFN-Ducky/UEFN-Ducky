"""Copies of the other Duckies' chats: sealed for their account, read by both backends."""

from __future__ import annotations

from backend.store.repos import ducky_mirrors

from test_store_chats import _sql

_ROWS = [{"role": "user", "content": "hello é"}, {"role": "assistant", "content": "hi", "n": [1.5, None]}]


def _stored(ducky: str) -> list:
    return _sql("SELECT conv_id, title, updated, rows_of, gone, rows FROM ducky_mirror_chats WHERE ducky=? ORDER BY conv_id", ducky)


def _steps(call, ducky: str) -> list:
    """One Ducky's copies made through either backend: what each call answered."""
    return [
        call("note_peer", ducky=ducky, name="Laptop é"),
        call("note_chat", ducky=ducky, conv_id="c1", title="First é", updated=100.0),  # never pulled
        call("put_rows", ducky=ducky, conv_id="c1", rows=_ROWS, of=100.0),
        call("note_chat", ducky=ducky, conv_id="c1", title="First é", updated=100.0),  # as pulled
        call("note_chat", ducky=ducky, conv_id="c1", title="Renamed", updated=150.5),  # changed there
        call("note_chat", ducky=ducky, conv_id="c2", title="", updated=50.0),
        call("note_chat", ducky=ducky, conv_id="c3", title="Third", updated=75.0),
        call("put_rows", ducky=ducky, conv_id="c3", rows=[], of=80.0),
        call("put_rows", ducky=ducky, conv_id="none", rows=_ROWS, of=1.0),  # not listed: nothing stored
        call("mark_gone", ducky=ducky, listed=["c1", "c3", "c9"]),
        call("mark_gone", ducky=ducky, listed=["c1", "c3"]),
        call("note_peer", ducky=ducky, name="Laptop"),
    ]


def _shape(peer: dict, ducky: str) -> dict:
    """A peer as read, without what differs by nature: its id and when it synced."""
    assert peer["ducky"] == ducky and peer["synced"] > 0
    return {**peer, "ducky": "", "synced": 0, "chats": [{**chat, "synced": bool(chat["synced"])} for chat in peer["chats"]]}


def test_copies_are_stored_sealed_and_read_the_same(rust_store, account: str) -> None:
    def python(function: str, **args):
        return getattr(ducky_mirrors, function)(account, **args)

    def rust(function: str, **args):
        return rust_store.store("ducky_mirrors", function, **args)

    theirs = _steps(python, "d-py")
    assert _steps(rust, "d-rs") == theirs and theirs[1] is True and theirs[3] is False and theirs[4] is True
    assert theirs[9:11] == [1, 0]

    # Sealed at rest by both, and the stored shape is the same.
    ours, yours = _stored("d-rs"), _stored("d-py")
    assert [row[0] for row in ours] == ["c1", "c2", "c3"] and [(row[0], *row[2:5]) for row in ours] == [(row[0], *row[2:5]) for row in yours]
    for row in ours + yours:
        assert (row[1] or "").startswith("enc1:") == (row[0] != "c2")  # an empty title stays empty
        assert "hello" not in str(row[5]) and "First" not in str(row[1])

    # Each reads what the other wrote.
    peers = ducky_mirrors.peers(account)
    assert rust("peers") == peers and [peer["ducky"] for peer in peers] == ["d-py", "d-rs"]
    assert _shape(peers[0], "d-py") == _shape(peers[1], "d-rs")
    assert [chat["conv_id"] for chat in peers[0]["chats"]] == ["c1", "c3"] and peers[0]["chats"][0]["title"] == "Renamed"
    for ducky in ("d-py", "d-rs", "none"):
        for conv_id in ("c1", "c2", "c3", "none"):
            assert rust("rows", ducky=ducky, conv_id=conv_id) == ducky_mirrors.rows(account, ducky, conv_id)
    assert ducky_mirrors.rows(account, "d-rs", "c1")["rows"] == _ROWS == ducky_mirrors.rows(account, "d-py", "c1")["rows"]

    # A title that did not change is not sealed again.
    before = _stored("d-py")
    assert rust("note_chat", ducky="d-py", conv_id="c1", title="Renamed", updated=150.5) is True
    assert _stored("d-py") == before

    # Copies an older build stored plain still read.
    _sql("UPDATE ducky_mirror_chats SET title='plain title', rows='[1, 2]' WHERE ducky='d-py' AND conv_id='c1'")
    _sql("UPDATE ducky_mirror_chats SET rows='not json' WHERE ducky='d-py' AND conv_id='c3'")
    for conv_id in ("c1", "c3"):
        assert rust("rows", ducky="d-py", conv_id=conv_id) == ducky_mirrors.rows(account, "d-py", conv_id)
    assert rust("peers") == ducky_mirrors.peers(account)

    assert rust("forget_peer", ducky="d-rs") == 3 == ducky_mirrors.forget_peer(account, "d-py")
    assert rust("forget_peer", ducky="d-rs") == 0 and rust("peers") == [] == ducky_mirrors.peers(account)
